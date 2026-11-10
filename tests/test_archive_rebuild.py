"""N1a: Archiv per Replay mit dem aktuellen Stand neu aufbauen (Werkzeug außerhalb von HA)."""

import asyncio
import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import archive_rebuild

from custom_components.ha_sauna.archive import Archive
from custom_components.ha_sauna.core.parameters import Parameters

T0 = datetime(2026, 11, 2, 16, 0, tzinfo=UTC)
MINUTES = 12


def at(seconds):
    return (T0 + timedelta(seconds=seconds)).isoformat()


class ArchiveRebuildTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        base = Path(self.directory.name)
        self.source, self.target = base / "quelle.sqlite", base / "ziel.sqlite"
        Archive(self.source, "entry")._initialize(T0)
        payload = {
            "timeline": {"session_id": "s1", "session_started_at": at(0), "completed": [],
                         "active": None, "retracted": [], "processed": []},
            "configuration": {"control_mode": "automatic", "presence_source": "proxy",
                              "parameters": {"target_temperature_c": 80}},
            "contactor_history": [{"at": at(0), "state": True}, {"at": at(400), "state": False}],
            "operation_off_at": at(MINUTES * 60 - 120),
        }
        rows = [("session", at(0), {"operation_enabled": True}),
                ("session", at(MINUTES * 60 - 120), {"operation_enabled": False}),
                ("session_label", at(MINUTES * 60), {"test": True, "user_id": None})]
        rows += [("measurement", at(t), {"position": p, "quantity": q, "value": v, "raw_value": str(v),
                                         "source": f"sensor.{p}_{q}", "received_at": at(t)})
                 for t in range(0, MINUTES * 60, 2)
                 for p, q, v in (("upper", "temperature", 60 + t / 60), ("lower", "temperature", 50.0),
                                 ("upper", "humidity", 10.0), ("lower", "humidity", 12.0))]
        rows += [("detection", at(30), {"event": {"event_id": "alt", "kind": "door_open"}})]
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("INSERT INTO sessions(session_id,entry_id,started_at,updated_at,ended_at,payload) "
                       "VALUES('s1','entry',?,?,?,?)", (at(0), at(MINUTES * 60), at(MINUTES * 60),
                                                         json.dumps(payload)))
            db.executemany("INSERT INTO records(entry_id,session_id,kind,received_at,payload) "
                           "VALUES('entry','s1',?,?,?)",
                           [(kind, moment, json.dumps(data)) for kind, moment, data in rows])

    def tearDown(self):
        self.directory.cleanup()

    def kinds(self, path):
        with closing(sqlite3.connect(path)) as db:
            return dict(db.execute("SELECT kind,count(*) FROM records GROUP BY kind").fetchall())

    def test_rebuild_keeps_the_original_and_the_session_identity(self):
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        result, _log = asyncio.run(archive_rebuild.rebuild(
            self.source, self.target, stamp={"software": "0.9.0b6"}))
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), digest)
        self.assertEqual([session_id for session_id, _ in result], ["s1"])
        with closing(sqlite3.connect(self.target)) as db:
            sessions = db.execute("SELECT session_id,ended_at FROM sessions").fetchall()
        self.assertEqual([row[0] for row in sessions], ["s1"])
        self.assertIsNotNone(sessions[0][1])
        source, target = self.kinds(self.source), self.kinds(self.target)
        # Gemessenes und Kennzeichnungen unverändert übernommen, Geschlossenes neu
        self.assertEqual(target["measurement"], source["measurement"])
        self.assertEqual(target["session_label"], 1)
        self.assertEqual(target["rebuild"], 1)
        self.assertNotIn("detection", target)
        with closing(sqlite3.connect(self.target)) as db:
            stamp = db.execute("SELECT payload FROM records WHERE kind='rebuild'").fetchone()
            revision = db.execute("SELECT value FROM metadata WHERE key='revision'").fetchone()
        self.assertEqual(json.loads(stamp[0])["software"], "0.9.0b6")
        self.assertGreater(int(revision[0]), 0)
        archive = Archive(self.target, "entry", parameters=Parameters({}))
        self.assertTrue(archive.session_key_figures("s1")["test"])
        self.assertTrue(archive.recognition_control("s1")["recorded"])

    def test_existing_target_is_not_overwritten(self):
        self.target.write_bytes(b"x")
        with self.assertRaises(FileExistsError):
            asyncio.run(archive_rebuild.rebuild(self.source, self.target))
        self.assertEqual(self.target.read_bytes(), b"x")


if __name__ == "__main__":
    unittest.main()
