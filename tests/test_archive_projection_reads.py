import asyncio
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from custom_components.ha_sauna import archive as archive_module
from custom_components.ha_sauna.archive import Archive, plain
from custom_components.ha_sauna.core.phases import project_archive

T0 = datetime(2030, 1, 1, tzinfo=UTC)


def session(session_id, *, base_phases=None, ended_at=None, retracted=(), cooling=()):
    value = {
        "timeline": {
            "session_started_at": T0.isoformat(),
            "completed": [],
            "active": None,
            "retracted": list(retracted),
        },
        "ended_at": ended_at.isoformat() if ended_at else None,
        "cooling_history": list(cooling),
    }
    if base_phases is not None:
        value["base_phases"] = base_phases
    return value


def legacy_read(archive, session_id, *, after=0, limit=1000):
    """The prior read path, used only to compare unchanged output."""
    with closing(sqlite3.connect(archive.path)) as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT payload,updated_at FROM sessions WHERE entry_id=? AND session_id=?",
            (archive.entry_id, session_id),
        ).fetchone()
        records = db.execute(
            "SELECT * FROM records WHERE entry_id=? AND session_id=? AND id>? "
            "ORDER BY id LIMIT ?",
            (archive.entry_id, session_id, after, limit),
        ).fetchall()
        stored_session = json.loads(row["payload"])
        evidence = [
            {
                "kind": record["kind"],
                "received_at": record["received_at"],
                "payload": json.loads(record["payload"]),
            }
            for record in db.execute(
                "SELECT kind,received_at,payload FROM records WHERE entry_id=? "
                "AND session_id=? AND kind IN ('phase','source_state','session') "
                "ORDER BY id",
                (archive.entry_id, session_id),
            )
        ]
    return {
        "session": stored_session,
        "phase_projection": plain(
            project_archive(stored_session, evidence, row["updated_at"])
        ),
        "records": [
            {**dict(record), "payload": json.loads(record["payload"])}
            for record in records
        ],
        "next_after": records[-1]["id"] if len(records) == limit else None,
    }


class ArchiveProjectionReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_filtered_pages_skip_revisions_without_losing_original_points(self):
        stored = session("filtered", base_phases=[
            {"at": T0.isoformat(), "phase": "bereit", "operation_enabled": True}
        ])
        await self.write("session", 0, stored, "filtered")
        for number in range(6):
            self.archive.append("session", T0, stored, "filtered")
            self.archive.append("measurement", T0, {"value": number}, "filtered")
        await self.archive.flush()
        expected = self.archive.read("filtered")
        first = self.archive.read("filtered", kinds=("measurement",), limit=3)
        second = self.archive.read("filtered", kinds=("measurement",), limit=3,
                                   after=first["next_after"])
        last = self.archive.read("filtered", kinds=("measurement",), limit=3,
                                 after=second["next_after"])
        self.assertEqual(first["records"] + second["records"], [
            record for record in expected["records"] if record["kind"] == "measurement"
        ])
        self.assertEqual(first["phase_projection"], expected["phase_projection"])
        self.assertEqual(last["session"], stored)
        self.assertEqual(last["records"], [])
        self.assertIsNone(last["next_after"])

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.archive = Archive(Path(self.temp.name) / "archive.sqlite", "entry")
        await self.archive.start()

    async def asyncTearDown(self):
        await self.archive.close()
        self.temp.cleanup()

    async def write(self, kind, second, payload, session_id):
        self.archive.append(kind, T0 + timedelta(seconds=second), payload, session_id)
        await self.archive.flush()

    def traced_read(self, session_id, *, after=0, limit=1):
        queries = []
        connect = archive_module.sqlite3.connect

        def traced_connect(*args, **kwargs):
            db = connect(*args, **kwargs)
            db.set_trace_callback(queries.append)
            return db

        with (
            patch.object(archive_module.sqlite3, "connect", side_effect=traced_connect),
            patch.object(archive_module.json, "loads", wraps=json.loads) as loads,
        ):
            result = self.archive.read(session_id, after=after, limit=limit)
        return (
            result,
            loads.call_count,
            [query for query in queries if query.startswith("SELECT")],
        )

    async def test_current_snapshots_skip_full_evidence_on_every_page(self):
        session_id = "current"
        first = session(
            session_id,
            base_phases=[
                {"at": T0.isoformat(), "phase": "bereit", "operation_enabled": True}
            ],
        )
        await self.write("session", 0, first, session_id)
        for number in range(3000):
            self.archive.append(
                "source_state",
                T0 + timedelta(microseconds=number),
                {"role": "heater", "state": False},
                session_id,
            )
        latest = session(
            session_id,
            base_phases=first["base_phases"],
        )
        await self.write("session", 10, latest, session_id)

        cursor = 0
        for _ in range(2):
            expected = legacy_read(self.archive, session_id, after=cursor, limit=1)
            actual, loads, queries = self.traced_read(session_id, after=cursor, limit=1)
            self.assertEqual(actual, expected)
            self.assertEqual(loads, 2)
            self.assertEqual(len(queries), 2)
            self.assertFalse(
                any(
                    "kind IN ('phase','source_state','session')" in query
                    for query in queries
                )
            )
            cursor = actual["next_after"]

    async def test_missing_and_empty_base_phases_keep_legacy_projection_on_pages(self):
        for session_id, base_phases in (("missing", None), ("empty", [])):
            with self.subTest(session_id=session_id):
                gang_id = f"{session_id}-candidate"
                stored = session(
                    session_id,
                    base_phases=base_phases,
                    ended_at=T0 + timedelta(seconds=35),
                    retracted=[
                        {
                            "gang_id": gang_id,
                            "started_at": (T0 + timedelta(seconds=10)).isoformat(),
                            "ended_at": (T0 + timedelta(seconds=20)).isoformat(),
                        }
                    ],
                    cooling=[
                        {
                            "cycle_id": "historical-cooling",
                            "started_at": (T0 + timedelta(seconds=15)).isoformat(),
                            "ends_at": (T0 + timedelta(seconds=30)).isoformat(),
                            "paused_at": (T0 + timedelta(seconds=25)).isoformat(),
                        }
                    ],
                )
                await self.write("phase", 0, {"phase": "aufheizen"}, session_id)
                await self.write("phase", 10, {"phase": "saunagang"}, session_id)
                candidate_snapshot = session(session_id, base_phases=base_phases)
                candidate_snapshot["timeline"]["active"] = {"gang_id": gang_id}
                await self.write(
                    "session",
                    10,
                    candidate_snapshot,
                    session_id,
                )
                await self.write("diagnostic", 12, {"page": True}, session_id)
                await self.write("session", 35, stored, session_id)

                cursor = 0
                for page_number in range(2):
                    expected = legacy_read(
                        self.archive, session_id, after=cursor, limit=1
                    )
                    actual, loads, queries = self.traced_read(
                        session_id, after=cursor, limit=1
                    )
                    self.assertEqual(actual, expected)
                    self.assertEqual(loads, 6 if page_number == 0 else 2)
                    self.assertEqual(
                        sum(query.startswith("SELECT kind,received_at,payload")
                            for query in queries),
                        1 if page_number == 0 else 0,
                    )
                    phases = [
                        item["phase"]
                        for item in actual["phase_projection"]["intervals"]
                    ]
                    self.assertIn("aufheizen", phases)
                    self.assertIn("zwangskühlung", phases)
                    cursor = actual["next_after"]

    async def test_cached_projection_tracks_new_evidence_and_snapshot_at_same_time(self):
        identity = "changing"
        stored = session(identity, ended_at=T0 + timedelta(seconds=40))
        await self.write("phase", 0, {"phase": "aufheizen"}, identity)
        await self.write("session", 40, stored, identity)
        initial = self.archive.read(identity, after=2**63 - 1)
        await self.write("measurement", 20, {"value": 78.123456789}, identity)
        actual, loads, queries = self.traced_read(identity, after=2**63 - 1)
        self.assertEqual(actual, initial)
        self.assertEqual(loads, 1)
        self.assertFalse(any(query.startswith("SELECT kind,received_at,payload")
                             for query in queries))

        await self.write("phase", 10, {"phase": "bereit"}, identity)
        await self.write("source_state", 15, {"role": "heater", "state": "off"}, identity)
        updated = self.archive.read(identity, after=2**63 - 1)
        self.assertEqual(updated, legacy_read(self.archive, identity, after=2**63 - 1))
        self.assertNotEqual(updated["phase_projection"], initial["phase_projection"])
        stored["timeline"]["completed"] = [
            {"gang_id": "g", "started_at": (T0 + timedelta(seconds=20)).isoformat(),
             "ended_at": (T0 + timedelta(seconds=30)).isoformat()}
        ]
        await self.write("session", 40, stored, identity)
        self.assertEqual(self.archive.read(identity), legacy_read(self.archive, identity))
        self.assertIn("g", [p["source_id"] for p in self.archive.read(identity)["phase_projection"]["intervals"]])

    async def test_old_reader_cannot_leave_a_reusable_stale_projection(self):
        identity = "race"
        await self.write("phase", 0, {"phase": "aufheizen"}, identity)
        await self.write("session", 40, session(identity), identity)
        # WAL permits a real old read snapshot to outlive a committed writer.
        with closing(sqlite3.connect(self.archive.path)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.row_factory = sqlite3.Row
            generation = self.archive._projection_generation
            db.execute("BEGIN")
            row = db.execute("SELECT payload,updated_at FROM sessions WHERE session_id=?",
                             (identity,)).fetchone()
            await self.write("phase", 10, {"phase": "bereit"}, identity)
            old = self.archive._legacy_projection(
                db, identity, json.loads(row["payload"]), row, generation=generation
            )
            self.assertEqual([p.phase for p in old.intervals], ["aufheizen"])
            self.assertNotIn(identity, self.archive._projection_cache)
        current = self.archive.read(identity)
        self.assertEqual(current, legacy_read(self.archive, identity))
        self.assertEqual([p["phase"] for p in current["phase_projection"]["intervals"]],
                         ["aufheizen", "bereit"])

    async def assert_inflight_read_cannot_restore_invalidated_cache(self, change):
        identity = "inflight"
        await self.write("phase", 0, {"phase": "aufheizen"}, identity)
        await self.write("session", 40, session(identity), identity)
        before = self.archive.read(identity)
        with closing(sqlite3.connect(self.archive.path)) as db:
            db.execute("PRAGMA journal_mode=WAL")
        ready, resume = threading.Event(), threading.Event()
        project = self.archive._legacy_projection

        def gate(*args, **kwargs):
            if not ready.is_set():
                ready.set()
                if not resume.wait(5):
                    raise TimeoutError("projection test gate")
            return project(*args, **kwargs)

        with patch.object(self.archive, "_legacy_projection", side_effect=gate):
            reading = asyncio.create_task(asyncio.to_thread(self.archive.read, identity))
            try:
                self.assertTrue(await asyncio.to_thread(ready.wait, 5))
                if change == "append":
                    await self.write("phase", 10, {"phase": "bereit"}, identity)
                else:
                    await self.archive.erase(identity, reset=change == "reset")
                self.assertNotIn(identity, self.archive._projection_cache)
                if change == "reinsert":
                    # A new writer can receive the same imported ID. Its
                    # lifecycle is independent of the old writer's tombstones.
                    writer = Archive(self.archive.path, self.archive.entry_id)
                    for kind, second, value in (
                        ("phase", 0, {"phase": "bereit"}),
                        ("session", 40, session(identity)),
                    ):
                        writer._write((kind, (T0 + timedelta(seconds=second)).isoformat(),
                                       archive_module.encoded(value), identity))
                current = self.archive.read(identity)
                if change in {"erase", "reset"}:
                    self.assertIsNone(current)
                else:
                    self.assertEqual(current, legacy_read(self.archive, identity))
                    self.assertNotEqual(current["phase_projection"], before["phase_projection"])
                cached = dict(self.archive._projection_cache)
                resume.set()
                self.assertEqual(await reading, before)
                self.assertEqual(dict(self.archive._projection_cache), cached)
            finally:
                resume.set()
                await reading
        if current is None:
            self.assertIsNone(self.archive.read(identity))
            self.assertFalse(self.archive._projection_cache)
        else:
            actual, loads, _ = self.traced_read(identity, after=2**63 - 1)
            self.assertEqual(actual["phase_projection"], current["phase_projection"])
            self.assertEqual(loads, 1)

    async def test_inflight_read_cannot_restore_cache_after_erase(self):
        await self.assert_inflight_read_cannot_restore_invalidated_cache("erase")

    async def test_inflight_read_cannot_restore_cache_after_reset(self):
        await self.assert_inflight_read_cannot_restore_invalidated_cache("reset")

    async def test_inflight_read_cannot_replace_cache_for_reinserted_id(self):
        await self.assert_inflight_read_cannot_restore_invalidated_cache("reinsert")

    async def test_inflight_read_cannot_replace_cache_after_append(self):
        await self.assert_inflight_read_cannot_restore_invalidated_cache("append")

    async def test_cached_projection_is_private_and_deletion_reset_evict_it(self):
        for identity in ("first", "second"):
            await self.write("phase", 0, {"phase": "bereit"}, identity)
            await self.write("session", 40, session(identity), identity)
            actual = self.archive.read(identity)
            actual["phase_projection"]["intervals"][0]["phase"] = "changed by caller"
            self.assertEqual(self.archive.read(identity), legacy_read(self.archive, identity))
        self.assertEqual(set(self.archive._projection_cache), {"first", "second"})
        await self.archive.erase("first")
        self.assertIsNone(self.archive.read("first"))
        self.assertNotIn("first", self.archive._projection_cache)
        self.archive.read("second")
        await self.archive.erase(reset=True)
        self.assertEqual(self.archive.read(), [])
        self.assertFalse(self.archive._projection_cache)

    async def test_legacy_cache_evicts_least_recent_session_at_configured_capacity(self):
        count = self.archive._projection_cache_entries
        for number in range(count):
            identity = f"session-{number}"
            await self.write("session", 40, session(identity), identity)
            self.archive.read(identity, after=2**63 - 1)
        self.archive.read("session-0", after=2**63 - 1)
        await self.write("session", 40, session("new"), "new")
        self.archive.read("new", after=2**63 - 1)
        self.assertEqual(len(self.archive._projection_cache), count)
        self.assertIn("session-0", self.archive._projection_cache)
        self.assertNotIn("session-1", self.archive._projection_cache)
        actual, loads, _ = self.traced_read("session-1", after=2**63 - 1)
        self.assertEqual(loads, 2)
        self.assertEqual(actual, legacy_read(self.archive, "session-1", after=2**63 - 1))
