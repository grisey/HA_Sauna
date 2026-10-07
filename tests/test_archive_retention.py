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
            for kind in (Kind.DOOR_CLOSE, Kind.INFUSION):
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
            self.assertEqual(db.execute("SELECT count(*) FROM records").fetchone()[0], 0)
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
