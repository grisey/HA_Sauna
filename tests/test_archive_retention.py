"""Retention and deletion use the real queued SQLite writer."""
import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import timedelta
from pathlib import Path

from test_foundation import T0, bindings, event, parameters

from custom_components.ha_sauna.archive import Archive
from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.timeline import Kind
from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime


class ArchiveRetentionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.archive = Archive(Path(self.temp.name) / "archive.sqlite", "e", sessions_only=True)
        await self.archive.start()

    async def asyncTearDown(self):
        await self.archive.close()
        self.temp.cleanup()

    def session(self, identity, confirmed=False):
        c = Controller(parameters())
        c.set_temperature(80, T0)
        c.begin_session(identity, T0)
        if confirmed:
            for kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE, Kind.INFUSION):
                c.process(event(kind.value, kind, 0, session=identity))
        return c

    def save(self, session):
        self.archive.save_session(session, T0, {})

    async def test_empty_attempt_and_late_records_are_removed_but_live_data_exist(self):
        c = self.session("empty")
        self.save(c.session)
        self.archive.append("measurement", T0, {"value": 80}, "empty")
        self.archive.append("measurement", T0, {"value": 30})
        await self.archive.flush()
        self.assertEqual(len(self.archive.read("empty")["records"]), 2)
        c.finish_session(T0 + timedelta(seconds=1))
        self.save(c.completed_sessions[-1])
        self.archive.append("decision", T0, {}, "empty")
        await self.archive.flush()
        self.assertIsNone(self.archive.read("empty"))
        with closing(sqlite3.connect(self.archive.path)) as db:
            records = db.execute("SELECT kind,session_id FROM records").fetchall()
            self.assertEqual(records, [("measurement", None), ("measurement", None)])
        self.assertEqual(self.archive.revision, 1)

    async def test_single_delete_reset_and_backup_order_keep_other_sessions(self):
        for identity in ("keep", "remove"):
            c = self.session(identity, True)
            c.finish_session(T0 + timedelta(seconds=1))
            self.save(c.completed_sessions[-1])
            self.archive.append("measurement", T0, {"value": 80}, identity)
        await self.archive.flush()
        expected = self.archive.read("keep")
        await self.archive.pre_backup()
        deleting = asyncio.create_task(self.archive.erase("remove"))
        await asyncio.sleep(0)
        self.assertFalse(deleting.done())
        self.archive.release_backup()
        self.assertEqual(await deleting, 1)
        self.assertEqual(self.archive.read("keep"), expected)
        self.assertIsNone(self.archive.read("remove"))
        self.archive.append("light_command", T0, {}, "remove")
        await self.archive.flush()
        self.assertEqual(await self.archive.erase(reset=True), 1)
        self.assertEqual(self.archive.read(), [])
        self.assertEqual(self.archive.consumer_event_ids(), set())
        self.assertEqual(self.archive.revision, 2)
        c = self.session("fresh", True)
        c.finish_session(T0 + timedelta(seconds=1))
        self.save(c.completed_sessions[-1])
        await self.archive.flush()
        self.assertIsNotNone(self.archive.read("fresh"))

    async def test_startup_removes_empty_old_sessions_and_keeps_confirmed_interruption(self):
        empty = self.session("empty")
        confirmed = self.session("confirmed", True)
        self.save(empty.session)
        self.save(confirmed.session)
        await self.archive.close()
        self.archive = Archive(self.archive.path, "e", sessions_only=True)
        await self.archive.start()
        self.assertIsNone(self.archive.read("empty"))
        original = self.archive.read("confirmed")
        self.assertIsNone(original["session"]["ended_at"])
        self.assertEqual(await self.archive.erase("confirmed"), 1)
        self.assertIsNone(self.archive.read("confirmed"))

    async def test_unknown_session_rejection_leaves_archive_usable(self):
        self.save(self.session("historical", True).session)
        await self.archive.flush()
        with self.assertRaises(KeyError):
            await self.archive.erase("missing")
        self.assertIsNone(self.archive.failure)
        self.assertEqual(await self.archive.erase("historical"), 1)
        await self.archive.flush()
        self.assertIsNone(self.archive.failure)

    async def test_runtime_live_and_resume_pause_guard_both_deletion_requests(self):
        runtime = SaunaRuntime(Configuration(bindings(), parameters()), lambda: T0)
        runtime.archive = self.archive
        runtime.controller = self.session("live", True)
        runtime.persist()
        for paused in (False, True):
            if paused:
                runtime.controller.set_operation(False, T0 + timedelta(seconds=1))
            for options in ({"session_id": "live"}, {"reset": True}):
                with self.assertRaises(ValueError):
                    await runtime.erase_archive(**options)
            await self.archive.flush()
            self.assertIsNotNone(self.archive.read("live"))

    async def test_manual_on_off_recording_keeps_only_confirmed_gangs(self):
        for confirmed in (False, True):
            with self.subTest(confirmed=confirmed):
                now = T0
                runtime = SaunaRuntime(
                    Configuration(bindings(), parameters(), control_mode="manual"), lambda: now,
                )
                runtime.archive = self.archive
                c = runtime.controller
                c.set_temperature(c.target_temperature - 5, now)
                await runtime.set_heater_override(True)
                identity = runtime.session.session_id
                self.assertIsNotNone(runtime.detector)
                if confirmed:
                    for second, kind in enumerate((Kind.DOOR_OPEN, Kind.DOOR_CLOSE, Kind.INFUSION), 1):
                        now = T0 + timedelta(seconds=second)
                        await runtime.receive(event(kind.value, kind, second, session=identity))
                for second, temperature in ((4, c.thermostat_target),
                                            (5, c.thermostat_restart_temperature)):
                    now = T0 + timedelta(seconds=second)
                    c.set_temperature(temperature, now)
                    await runtime.tick()
                    self.assertEqual(runtime.session.session_id, identity)
                now = T0 + timedelta(seconds=6)
                await runtime.set_heater_override(False)
                stopped_at = now
                deadline = next(d for d in runtime.session.deadlines if d.purpose == "session_gap")
                self.assertEqual(runtime.session.session_id, identity)
                self.assertFalse(runtime.session.operation_enabled)
                self.assertEqual(c.completed_sessions, ())
                now = deadline.due_at
                await runtime.tick()
                self.assertIsNone(runtime.session)
                self.assertIsNone(runtime.detector)
                self.assertIsNone(c.light_after_run)
                self.assertEqual(c.completed_sessions[-1].ended_at, now)
                await self.archive.flush()
                saved = self.archive.read(identity)
                if not confirmed:
                    self.assertIsNone(saved)
                    continue
                self.assertEqual(saved["session"]["configuration"]["control_mode"], "manual")
                self.assertEqual(saved["session"]["operation_off_at"], stopped_at.isoformat())
                self.assertEqual(saved["session"]["ended_at"], deadline.due_at.isoformat())
                self.assertEqual(len(saved["session"]["timeline"]["completed"]), 1)
                commands = [r for r in saved["records"] if r["kind"] == "manual_heater"]
                self.assertEqual([r["payload"]["value"] for r in commands], [True, False])
                self.assertEqual({r["session_id"] for r in commands}, {identity})

    async def test_physical_start_keeps_manual_archive_revisions_in_the_same_recording(self):
        from dataclasses import replace

        now = T0
        config = Configuration(bindings(), parameters(), control_mode="manual")
        program = config.temperature_programs[0]
        config = replace(config, button_program=program.id)
        runtime = SaunaRuntime(config, lambda: now)
        runtime.archive = self.archive
        runtime.controller.set_temperature(80, now)
        await runtime.set_heater_override(True)
        identity = runtime.session.session_id
        started_at = runtime.session.timeline.session_started_at
        await self.archive.flush()
        manual_records = self.archive.read(identity)["records"]
        self.assertEqual(self.archive.read(identity)["session"]["configuration"]["control_mode"], "manual")
        now = T0 + timedelta(seconds=4)
        async with runtime.serialized():
            await runtime._handle_button_event("long", now)
            await runtime._cycle()
        await self.archive.flush()
        saved = self.archive.read(identity)
        self.assertEqual(saved["session"]["configuration"]["control_mode"], "automatic")
        self.assertIsNone(saved["session"]["ended_at"])
        self.assertEqual(saved["session"]["timeline"]["session_started_at"], started_at.isoformat())
        self.assertEqual(saved["records"][:len(manual_records)], manual_records)
        self.assertTrue(any(r["kind"] == "session" and
                            r["payload"]["configuration"]["control_mode"] == "manual"
                            for r in saved["records"]))
        self.assertEqual(runtime.configuration.control_mode, "automatic")
        self.assertEqual(runtime.configuration.selected_program_id, program.id)
        self.assertEqual(runtime.controller.target_temperature, program.start_c)
        self.assertEqual(runtime.session.session_id, identity)
        self.assertEqual(runtime.controller.completed_sessions, ())
        self.assertIsNone(runtime.controller.light_after_run)

    async def test_consumer_event_lookup_uses_partial_index_and_same_identities(self):
        self.archive.append("consumer_event", T0, {"event_id": "receipt"})
        self.archive.append("consumer_event", T0, {"event_id": "in-session"}, "s")
        self.archive.append("measurement", T0, {"event_id": "not-an-event"}, "s")
        await self.archive.flush()
        self.assertEqual(self.archive.consumer_event_ids(), {"receipt", "in-session"})
        with closing(sqlite3.connect(self.archive.path)) as db:
            plan = db.execute(
                "EXPLAIN QUERY PLAN SELECT payload FROM records "
                "WHERE entry_id=? AND kind='consumer_event'", (self.archive.entry_id,)
            ).fetchall()
        self.assertTrue(any("records_consumer_events" in row[-1] for row in plan), plan)
