import json
import sqlite3
import tempfile
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
                for _ in range(2):
                    expected = legacy_read(
                        self.archive, session_id, after=cursor, limit=1
                    )
                    actual, _, queries = self.traced_read(
                        session_id, after=cursor, limit=1
                    )
                    self.assertEqual(actual, expected)
                    self.assertEqual(len(queries), 3)
                    self.assertTrue(
                        any(
                            "kind IN ('phase','source_state','session')" in query
                            for query in queries
                        )
                    )
                    phases = [
                        item["phase"]
                        for item in actual["phase_projection"]["intervals"]
                    ]
                    self.assertIn("aufheizen", phases)
                    self.assertIn("zwangskühlung", phases)
                    cursor = actual["next_after"]
