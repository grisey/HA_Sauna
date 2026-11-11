"""LZ6: gespeicherte Verlaufsansicht abgeschlossener Sitzungen im Archiv."""

import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from test_foundation import T0, bindings, parameters

from custom_components.ha_sauna import history_view
from custom_components.ha_sauna.archive import Archive, encoded
from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.models import (
    Measurement,
    Position,
    Quantity,
    SampleIdentity,
)
from custom_components.ha_sauna.history_view import HISTORY_KINDS, epoch_ms
from custom_components.ha_sauna.presentation import (
    archive_record_display,
    history_measurement_columns,
)
from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime

SECONDS = 600
CONTEXT = timedelta(seconds=900)


class HistoryViewArchiveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.archive = Archive(Path(self.directory.name) / "view.sqlite", "entry")
        await self.archive.start()
        self.configuration = {
            "parameters": parameters().as_dict(), "bindings": bindings().as_dict(),
        }

    async def asyncTearDown(self):
        await self.archive.close()
        self.directory.cleanup()

    def measurement(self, at, position, quantity, value, sample):
        source = self.configuration["bindings"][f"{position.value}_{quantity.value}"]
        return Measurement(
            position, quantity, value, str(value), source, at, at,
            SampleIdentity("sensors/test", "boot", sample, sample * 10**8),
        )

    async def finished(self, identity="view", start=T0):
        """Sitzung mit zwei Messwerten je Rolle und Sekunde, Phase, Ende nach SECONDS."""
        controller = Controller(parameters())
        controller.begin_session(identity, start)
        self.archive.save_session(controller.session, start, self.configuration)
        await self.archive.flush()
        # Viele Originale in einem Schreibvorgang, im Format von ``Archive.append``.
        rows, sample = [], 0
        for tenth in range(0, SECONDS * 10, 5):
            at = start + timedelta(milliseconds=tenth * 100)
            for position in (Position.UPPER, Position.LOWER):
                sample += 1
                wave = (tenth % 70) / 7 + (25 if tenth % 1200 == 600 else 0)
                for quantity, value in ((Quantity.TEMPERATURE, 70 + wave),
                                        (Quantity.HUMIDITY, 12 + wave / 3)):
                    rows.append(("measurement", at, self.measurement(
                        at, position, quantity, round(value, 2), sample)))
            if tenth % 600 == 0:
                rows.append(("phase", at, {"phase": "bereit"}))
        with closing(sqlite3.connect(self.archive.path)) as db, db:
            db.executemany(
                "INSERT INTO records(entry_id,session_id,kind,received_at,payload) "
                "VALUES(?,?,?,?,?)",
                [("entry", identity, kind, at.isoformat(), encoded(payload))
                 for kind, at, payload in rows],
            )
        ended = start + timedelta(seconds=SECONDS)
        controller.finish_session(ended, light_after_run=False)
        self.archive.save_session(controller.completed_sessions[-1], ended, self.configuration)
        await self.archive.flush()
        return ended

    def paged(self, identity, now):
        """Alle Seiten der Verlaufsprojektion wie bisher (Administrator)."""
        records, blocks, after = [], [], 0
        while True:
            page = self.archive.read(identity, after=after, limit=1000, kinds=HISTORY_KINDS,
                                     now=now, humidity_values=True)
            remaining, columns = history_measurement_columns(
                page["records"], configuration=self.configuration,
                displays=page["humidity_displays"],
            )
            for record in remaining:
                archive_record_display(record, history=True)
            records += remaining
            blocks.append(columns)
            if page["next_after"] is None:
                return records, history_view.join_columns(blocks)
            after = page["next_after"]

    def stored(self, identity):
        with closing(sqlite3.connect(self.archive.path)) as db:
            return db.execute(
                "SELECT part,identity FROM history_views WHERE session_id=? ORDER BY part",
                (identity,),
            ).fetchall()

    async def test_finished_session_is_computed_once_and_then_served_from_the_table(self):
        ended = await self.finished()
        self.assertIsNone(await asyncio.to_thread(self.archive.history_view, "view", now=ended + CONTEXT / 2))
        now = ended + CONTEXT
        view = await asyncio.to_thread(self.archive.history_view, "view", now=now)
        records, columns = await asyncio.to_thread(self.paged, "view", now)
        self.assertEqual(view["records"], records)
        self.assertTrue(view["measurement_window"]["complete"])
        resolution = view["measurement_resolution"]
        self.assertEqual(resolution["total_rows"], len(columns["id"]))
        self.assertEqual(resolution["columns"], history_view.OVERVIEW_COLUMNS)
        overview = view["measurement_columns"]
        self.assertTrue(set(overview["id"]) <= set(columns["id"]))
        everything = await asyncio.to_thread(self.archive.history_view_measurements, "view", now=now)
        self.assertEqual(everything["measurement_columns"], columns)
        # Gespeichert durch den Schreiber; danach ohne erneutes Lesen der Originale.
        await self.archive.flush()
        parts = self.stored("view")
        self.assertGreaterEqual(len(parts), 3)
        self.assertEqual({identity for _part, identity in parts}, {view["identity"]})
        with patch.object(Archive, "read", side_effect=AssertionError("kein Lesen")):
            again = await asyncio.to_thread(self.archive.history_view, "view", now=now)
            self.assertEqual(again, view)
            coarse = await asyncio.to_thread(self.archive.history_view, "view", now=now, columns=60)
            self.assertGreater(coarse["measurement_resolution"]["bin_ms"], resolution["bin_ms"])
            self.assertTrue(coarse["measurement_resolution"]["overview"])
            self.assertLess(len(coarse["measurement_columns"]["id"]), len(columns["id"]))
            self.assertEqual(
                await asyncio.to_thread(self.archive.history_view_measurements, "view", now=now), everything
            )
            start = epoch_ms((T0 + timedelta(seconds=100)).isoformat())
            end = epoch_ms((T0 + timedelta(seconds=130)).isoformat())
            part = await asyncio.to_thread(self.archive.history_view_measurements, "view", start, end, now=now)
        times = [epoch_ms(value) for value in columns["received_at"]]
        self.assertEqual(
            part["measurement_columns"]["id"],
            [identity for identity, time in zip(columns["id"], times, strict=True)
             if start <= time < end],
        )

    async def test_overview_keeps_every_peak_of_every_series(self):
        ended = await self.finished()
        view = await asyncio.to_thread(self.archive.history_view, "view", now=ended + CONTEXT, columns=60)
        _records, columns = await asyncio.to_thread(self.paged, "view", ended + CONTEXT)
        for source in (columns, view["measurement_columns"]):
            source["peaks"] = {}
            for role, value in zip(source["role"], source["value"], strict=True):
                role = source["roles"][role]
                key = (role["position"], role["quantity"])
                low, high = source["peaks"].get(key, (value, value))
                source["peaks"][key] = (min(low, value), max(high, value))
        self.assertEqual(view["measurement_columns"]["peaks"], columns["peaks"])

    async def test_revision_record_and_version_changes_make_the_view_invalid(self):
        ended = await self.finished()
        now = ended + CONTEXT
        builds = []
        original = Archive._build_history_view

        def counted(archive, *args):
            builds.append(args[0])
            return original(archive, *args)

        with patch.object(Archive, "_build_history_view", counted):
            first = await asyncio.to_thread(self.archive.history_view, "view", now=now)
            await self.archive.flush()
            await asyncio.to_thread(self.archive.history_view, "view", now=now)
            self.assertEqual(len(builds), 1)
            # Kennzeichnen: neue Archivrevision und neuer Datensatz der Sitzung.
            await self.archive.label_session("view", True, now)
            labelled = await asyncio.to_thread(self.archive.history_view, "view", now=now)
            self.assertEqual(len(builds), 2)
            self.assertNotEqual(labelled["identity"], first["identity"])
            await self.archive.flush()
            self.archive.append("notice", now, {"text": "später"}, "view")
            await self.archive.flush()
            await asyncio.to_thread(self.archive.history_view, "view", now=now)
            self.assertEqual(len(builds), 3)
            await self.archive.flush()
            with patch.object(history_view, "VERSION", history_view.VERSION + 1):
                await asyncio.to_thread(self.archive.history_view, "view", now=now)
            self.assertEqual(len(builds), 4)

    async def test_delete_and_reset_remove_stored_views(self):
        ended = await self.finished("first")
        await self.finished("second", start=ended + timedelta(hours=1))
        now = ended + timedelta(hours=2) + CONTEXT
        for identity in ("first", "second"):
            self.assertIsNotNone(await asyncio.to_thread(self.archive.history_view, identity, now=now))
        await self.archive.flush()
        self.assertTrue(self.stored("first") and self.stored("second"))
        await self.archive.erase("first")
        self.assertEqual(self.stored("first"), [])
        self.assertTrue(self.stored("second"))
        await self.archive.erase(reset=True)
        self.assertEqual(self.stored("second"), [])

    async def test_preparation_builds_finished_sessions_and_stops_when_asked(self):
        ended = await self.finished("first")
        later = await self.finished("second", start=ended + timedelta(hours=1))
        self.assertEqual(
            await asyncio.to_thread(self.archive.prepare_history_views, now=ended + CONTEXT),
            1,
        )
        self.assertTrue(self.stored("first"))
        self.assertEqual(self.stored("second"), [])
        # Jede Sitzung ist fällig, sobald ihr Messfenster voll ist.
        self.assertFalse(self.archive.history_views_due(ended + CONTEXT / 2))
        self.assertTrue(self.archive.history_views_due(ended + CONTEXT))
        self.assertFalse(self.archive.history_views_due(later + CONTEXT / 2))
        self.assertTrue(self.archive.history_views_due(later + CONTEXT))
        self.assertFalse(self.archive.history_views_due(later + CONTEXT))
        self.assertEqual(await asyncio.to_thread(
            self.archive.prepare_history_views, now=later + CONTEXT, stop=lambda: True), 0)
        self.assertEqual(await asyncio.to_thread(
            self.archive.prepare_history_views, now=later + CONTEXT), 1)
        self.assertTrue(self.stored("second"))
        self.assertEqual(await asyncio.to_thread(
            self.archive.prepare_history_views, now=later + CONTEXT), 0)
        # Neue Archivrevision: sofort fällig, alle Ansichten neu.
        await self.archive.label_session("first", True, later + CONTEXT)
        self.assertTrue(self.archive.history_views_due(later + CONTEXT))
        self.assertEqual(await asyncio.to_thread(
            self.archive.prepare_history_views, now=later + CONTEXT), 2)

    async def test_running_session_and_unknown_session_have_no_view(self):
        controller = Controller(parameters())
        controller.begin_session("running", T0)
        self.archive.save_session(controller.session, T0, self.configuration)
        await self.archive.flush()
        self.assertIsNone(await asyncio.to_thread(self.archive.history_view, "running", now=T0 + timedelta(days=1)))
        self.assertIsNone(await asyncio.to_thread(self.archive.history_view, "unknown", now=T0))
        self.assertIsNone(await asyncio.to_thread(self.archive.history_view_measurements, "unknown", now=T0))


class HistoryViewRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_prepares_after_start_and_survives_errors(self):
        runtime = SaunaRuntime(Configuration(bindings(), parameters()), lambda: T0)
        calls = []

        class Prepared:
            def __init__(self):
                self._view_due = {}

            def prepare_key_figures(self, **settings):
                calls.append("figures")

            def prepare_history_views(self, *, now):
                calls.append(("views", now))
                if len(calls) > 2:
                    raise sqlite3.OperationalError("database is locked")

        await runtime.prepare_history_views()
        self.assertEqual(calls, [])
        runtime.archive = Prepared()
        await runtime.prepare_after_start(object())
        self.assertEqual(calls, ["figures", ("views", T0)])
        self.assertTrue(runtime._history_views_started)
        await runtime.prepare_history_views()
        self.assertEqual(len(calls), 3)
        runtime.closed = True
        await runtime.prepare_history_views()
        self.assertEqual(len(calls), 3)
