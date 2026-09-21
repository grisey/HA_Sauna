import asyncio
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile

from custom_components.ha_sauna.archive import Archive
from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.timeline import Kind
from custom_components.ha_sauna.core.models import Position, Quantity
from test_foundation import T0, event, parameters, bindings
from test_detector import measurement


class ArchiveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.archive = Archive(Path(self.temp.name) / "ha_sauna" / "sessions.sqlite", "entry")
        await self.archive.start()
        self.c = Controller(parameters())
        self.c.begin_session("s", T0)
        self.config = {"parameters": parameters().as_dict(), "bindings": bindings().as_dict()}
        self.archive.save_session(self.c.session, T0, self.config)
        await self.archive.flush()

    async def asyncTearDown(self):
        await self.archive.close()
        self.temp.cleanup()

    def record(self, n):
        m = measurement(Position.UPPER, Quantity.TEMPERATURE, 70 + n / 1000, n / 1000)
        self.archive.append("measurement", m.received_at, m, "s")

    async def test_full_resolution_references_and_earlier_assignments_survive(self):
        for n in range(30):
            self.record(n)
        self.c.process(event("close", Kind.DOOR_CLOSE, 1))
        self.c.process(event("person", Kind.PERSON_STRONG, 2))
        provisional = self.c.session.timeline.active
        self.archive.save_session(self.c.session, T0 + timedelta(seconds=2), self.config)
        self.c.process(event("infusion", Kind.INFUSION, 3))
        self.archive.save_session(self.c.session, T0 + timedelta(seconds=3), self.config)
        await self.archive.flush()
        stored = await asyncio.to_thread(self.archive.read, "s")
        values = [r for r in stored["records"] if r["kind"] == "measurement"]
        self.assertEqual(len(values), 30)
        self.assertEqual(len({v["received_at"] for v in values}), 30)
        gang = stored["session"]["timeline"]["active"]
        self.assertEqual(gang["gang_id"], provisional.gang_id)
        self.assertEqual(gang["start_source_event_id"], "close")
        self.assertEqual(gang["infusion_events"][0]["event_id"], "infusion")
        earlier = [r["payload"] for r in stored["records"] if r["kind"] == "session"]
        self.assertEqual(earlier[-2]["timeline"]["active"]["infusion_events"], [])
        self.assertEqual(stored["session"]["configuration"], self.config)
        self.assertFalse(str(self.archive.path).endswith("www"))

    async def test_backup_pause_buffers_without_blocking_received_measurements(self):
        self.record(1)
        await self.archive.pre_backup()
        for n in range(2, 12):
            self.record(n)
        frozen = await asyncio.to_thread(self.archive.read, "s")
        self.assertEqual(sum(r["kind"] == "measurement" for r in frozen["records"]), 1)
        await self.archive.post_backup()
        final = await asyncio.to_thread(self.archive.read, "s")
        self.assertEqual(sum(r["kind"] == "measurement" for r in final["records"]), 11)

    async def test_export_during_capture_is_consistent_and_contains_originals(self):
        for n in range(30):
            self.record(n)
        task = asyncio.create_task(self.archive.export())
        for n in range(30, 60):
            self.record(n)
        path = await task
        try:
            with zipfile.ZipFile(path) as export:
                self.assertEqual(set(export.namelist()), {"manifest.json", "records.jsonl", "sessions.jsonl", "measurements.csv"})
                rows = [json.loads(line) for line in export.read("records.jsonl").decode().splitlines()]
                count = sum(r["kind"] == "measurement" for r in rows)
                self.assertGreaterEqual(count, 30)
                self.assertLessEqual(count, 60)
                csv_rows = export.read("measurements.csv").decode().splitlines()
                self.assertEqual(len(csv_rows), count + 1)
                self.assertEqual(len({r["id"] for r in rows}), len(rows))
        finally:
            path.unlink()

    async def test_cancelled_reader_does_not_poison_archive_writer(self):
        await self.archive.pre_backup()
        waiter = asyncio.create_task(self.archive.flush())
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.record(2)
        await self.archive.post_backup()
        self.assertIsNone(self.archive.failure)
        saved = await asyncio.to_thread(self.archive.read, "s")
        self.assertEqual(sum(r["kind"] == "measurement" for r in saved["records"]), 1)

    async def test_transient_sqlite_lock_retries_oldest_record_before_newer_records(self):
        original_write = self.archive._write
        attempts = []

        def write(record):
            attempts.append(record)
            if len(attempts) == 1:
                raise sqlite3.OperationalError("database is locked")
            original_write(record)

        self.archive._write = write
        self.record(1)
        await self.archive.queue.join()
        self.assertIsInstance(self.archive.failure, sqlite3.OperationalError)
        self.record(2)
        await self.archive.flush()

        saved = await asyncio.to_thread(self.archive.read, "s")
        measurements = [
            r["payload"]["received_at"]
            for r in saved["records"]
            if r["kind"] == "measurement"
        ]
        self.assertEqual(
            measurements,
            [
                (T0 + timedelta(milliseconds=1)).isoformat(),
                (T0 + timedelta(milliseconds=2)).isoformat(),
            ],
        )
        self.assertEqual(len(attempts), 3)
        self.assertEqual(attempts[0], attempts[1])
        self.assertIsNone(self.archive.failure)
        self.assertFalse(self.archive.failed_records)

    async def test_permanent_writer_failure_remains_reported_until_recovered(self):
        original_write = self.archive._write

        def write(record):
            raise sqlite3.DatabaseError("disk failure")

        self.archive._write = write
        self.record(1)
        await self.archive.queue.join()
        with self.assertRaisesRegex(sqlite3.DatabaseError, "disk failure"):
            await self.archive.flush()
        self.assertIsInstance(self.archive.failure, sqlite3.DatabaseError)
        self.assertEqual(len(self.archive.failed_records), 1)

        self.archive._write = original_write
        await self.archive.flush()
        self.assertIsNone(self.archive.failure)

    async def test_recovery_does_not_clear_fault_before_current_record_is_saved(self):
        original_write = self.archive._write
        failure_visible_during_new_write = []
        calls = 0

        def write(record):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise sqlite3.OperationalError("database is locked")
            if calls == 3:
                failure_visible_during_new_write.append(self.archive.failure is not None)
                raise sqlite3.OperationalError("database is locked again")
            original_write(record)

        self.archive._write = write
        self.record(1)
        await self.archive.queue.join()
        self.record(2)
        await self.archive.queue.join()

        self.assertEqual(failure_visible_during_new_write, [True])
        self.assertIsNotNone(self.archive.failure)
        self.archive._write = original_write
        await self.archive.flush()
        self.assertIsNone(self.archive.failure)

    async def test_recovered_session_snapshot_does_not_overwrite_newer_snapshot(self):
        original_write = self.archive._write
        failed_once = False

        def write(record):
            nonlocal failed_once
            if record[0] == "session" and not failed_once:
                failed_once = True
                raise sqlite3.OperationalError("database is busy")
            original_write(record)

        self.archive._write = write
        for second, revision in ((1, "older"), (2, "newer")):
            self.archive.append(
                "session",
                T0 + timedelta(seconds=second),
                {
                    "timeline": {"session_started_at": T0.isoformat()},
                    "ended_at": None,
                    "revision": revision,
                },
                "s",
            )
        await self.archive.queue.join()
        await self.archive.flush()

        saved = await asyncio.to_thread(self.archive.read, "s")
        self.assertEqual(saved["session"]["revision"], "newer")
        self.assertEqual(
            [
                record["payload"].get("revision")
                for record in saved["records"]
                if record["kind"] == "session"
            ],
            [None, "older", "newer"],
        )

    async def test_failed_record_burst_retries_once_while_catching_up(self):
        original_write = self.archive._write
        attempts = []

        def write(record):
            attempts.append(record)
            raise sqlite3.OperationalError("database is locked")

        self.archive._write = write
        for number in range(100):
            self.record(number)
        await self.archive.queue.join()

        self.assertEqual(len(attempts), 2)
        self.assertEqual(len(self.archive.failed_records), 100)
        self.assertIsInstance(self.archive.failure, sqlite3.OperationalError)
        self.archive._write = original_write

    async def test_cancelled_pause_request_does_not_pause_the_writer(self):
        future = asyncio.get_running_loop().create_future()
        self.archive.queue.put_nowait(("pause", future))
        future.cancel()
        await self.archive.flush()
        self.assertTrue(self.archive.resume.is_set())
        self.record(1)
        await self.archive.flush()
        saved = await asyncio.to_thread(self.archive.read, "s")
        self.assertEqual(sum(r["kind"] == "measurement" for r in saved["records"]), 1)

    async def test_latest_completed_warmup_reads_only_initial_heating_measurements(self):
        for second, phase in ((0, "aufheizen"), (181, "bereit"), (220, "kuehlung")):
            self.archive.append("phase", T0 + timedelta(seconds=second), {"phase": phase}, "s")
        for second, value in ((0, 20), (60, 26), (120, 32), (180, 38), (240, 25)):
            m = measurement(Position.UPPER, Quantity.TEMPERATURE, value, second)
            self.archive.append("measurement", m.received_at, m, "s")
        self.archive.save_session(
            replace(self.c.session, ended_at=T0 + timedelta(seconds=300)),
            T0 + timedelta(seconds=300),
            self.config,
        )
        await self.archive.flush()

        history = await asyncio.to_thread(
            self.archive.latest_completed_warmup, "sensor.upper_temperature", 60
        )

        self.assertEqual(history["session_id"], "s")
        self.assertEqual(
            history["measurements"],
            tuple((T0 + timedelta(seconds=second), value) for second, value in ((0, 20), (60, 26), (120, 32), (180, 38))),
        )

    async def test_temperature_change_at_session_expiry_keeps_original_archive_configuration(self):
        from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime
        from custom_components.ha_sauna.core.parameters import Parameters
        from custom_components.ha_sauna.settings import apply_temperature_parameters
        now = T0
        runtime = SaunaRuntime(Configuration(bindings(), parameters()), lambda: now)
        runtime.archive = self.archive
        await runtime.set_operation(True)
        session_id = runtime.session.session_id
        old_target = runtime.configuration.parameters.values["target_temperature_c"]
        await runtime.set_operation(False)
        now += timedelta(seconds=runtime.configuration.parameters.seconds("session_gap_minutes"))
        updated = Parameters({**runtime.configuration.parameters.as_dict(), "target_temperature_c": 91})
        async with runtime._lock:
            await apply_temperature_parameters(runtime, updated, explicit_target=True)
        await self.archive.flush()
        stored = await asyncio.to_thread(self.archive.read, session_id)
        self.assertEqual(stored["session"]["configuration"]["parameters"]["target_temperature_c"], old_target)
        self.assertEqual(runtime.controller.target_temperature, 91)
        self.assertIsNone(runtime.session)
