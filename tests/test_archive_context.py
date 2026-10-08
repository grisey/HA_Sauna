"""Original measurement context around sessions uses the queued SQLite archive."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from custom_components.ha_sauna.archive import Archive
from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.history import HISTORY_CONTEXT_SECONDS
from custom_components.ha_sauna.core.models import Position, Quantity
from custom_components.ha_sauna.core.timeline import Kind
from test_detector import measurement
from test_foundation import T0, bindings, event, parameters


def at(seconds):
    return T0 + timedelta(seconds=seconds)


class ArchiveContextTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.archive = Archive(Path(self.temp.name) / "context.sqlite", "entry", sessions_only=True)
        await self.archive.start()
        self.config = {"bindings": bindings().as_dict(), "parameters": parameters().as_dict()}
        self.source = self.config["bindings"]["upper_temperature"]

    async def asyncTearDown(self):
        await self.archive.close()
        self.temp.cleanup()

    def save_session(self, identity, start, end=None):
        controller = Controller(parameters())
        controller.set_temperature(80, at(start))
        controller.begin_session(identity, at(start))
        for kind in (Kind.DOOR_CLOSE, Kind.INFUSION):
            controller.process(event(kind.value, kind, start, session=identity))
        session = controller.session
        if end is not None:
            controller.finish_session(at(end))
            session = controller.completed_sessions[-1]
        self.archive.save_session(session, at(end if end is not None else start), self.config)

    def record(self, seconds, *, session_id=None, source=None, position=Position.UPPER):
        value = replace(
            measurement(position, Quantity.TEMPERATURE, 73.125, seconds),
            source=self.source if source is None else source,
        )
        self.archive.append("measurement", at(seconds), value, session_id)
        return value

    async def test_context_preserves_originals_filters_sources_and_paginates_once(self):
        margin = HISTORY_CONTEXT_SECONDS
        self.record(-margin - 1)
        original = self.record(-margin)
        self.record(-5, source="sensor.unrelated")
        self.record(-4, position=Position.LOWER)
        self.save_session("first", 0, 60)
        self.record(0)
        self.record(10, session_id="first")
        self.record(60)
        self.save_session("second", 120, 180)
        self.record(130, session_id="second")
        self.record(60 + margin)
        self.record(61 + margin)
        await self.archive.flush()
        now = at(61 + margin)
        expected = self.archive.read("first", kinds=("measurement",), now=now)
        self.assertEqual([r["payload"]["received_at"] for r in expected["records"]],
                         [at(n).isoformat() for n in (-margin, 0, 10, 60, 130, 60 + margin)])
        self.assertEqual(expected["records"][0]["payload"]["raw_value"], original.raw_value)
        self.assertEqual(expected["records"][0]["payload"]["source"], original.source)
        ids, cursor = [], 0
        while True:
            page = self.archive.read("first", kinds=("measurement",), after=cursor, limit=2, now=now)
            ids.extend(record["id"] for record in page["records"])
            self.assertEqual(page["measurement_window"], expected["measurement_window"])
            cursor = page["next_after"]
            if cursor is None:
                break
        self.assertEqual(ids, [record["id"] for record in expected["records"]])
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(expected["measurement_window"]["complete"])
        self.assertFalse(self.archive.read("first", now=at(60 + margin - 1))[
            "measurement_window"]["complete"])
        self.save_session("open", 2000)
        await self.archive.flush()
        window = self.archive.read("open", now=at(2100))["measurement_window"]
        self.assertEqual(window["ended_at"], at(2100 + margin).isoformat())
        self.assertFalse(window["complete"])

    async def test_startup_prunes_old_buffer_and_keeps_retained_session_context(self):
        self.record(-800)
        self.save_session("retained", 0, 60)
        self.record(100)
        self.record(150, source="sensor.unrelated")
        self.record(2000)
        self.record(3000)
        self.archive.append("source_state", at(3000), {"state": "off"})
        await self.archive.flush()
        await self.archive.close()
        self.archive._initialize(now=at(3000))
        with closing(sqlite3.connect(self.archive.path)) as db:
            records = db.execute("SELECT kind,received_at FROM records WHERE session_id IS NULL ORDER BY id").fetchall()
        self.assertEqual(records, [("measurement", at(n).isoformat()) for n in (-800, 100, 3000)])

    async def test_delete_preserves_overlapping_context_and_reset_removes_everything(self):
        self.record(-850)
        self.save_session("first", 0, 60)
        self.record(10, session_id="first")
        self.save_session("second", 120, 180)
        self.record(130, session_id="second")
        await self.archive.flush()
        before = self.archive.read("second", kinds=("measurement",), now=at(2000))
        self.archive._erase("first", False, now=at(2000))
        after = self.archive.read("second", kinds=("measurement",), now=at(2000))
        self.assertEqual([r["id"] for r in after["records"]], [r["id"] for r in before["records"]])
        self.assertIsNone(self.archive.read("first"))
        with closing(sqlite3.connect(self.archive.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM records WHERE received_at=?", (at(-850).isoformat(),)).fetchone()[0], 0)
        await self.archive.erase(reset=True)
        with closing(sqlite3.connect(self.archive.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM records").fetchone()[0], 0)
