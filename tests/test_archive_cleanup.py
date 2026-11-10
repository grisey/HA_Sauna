"""N2 (Nutzer 10.11.2026): Werkzeug zum Bereinigen des Sitzungsarchivs."""
import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from custom_components.ha_sauna.archive import Archive

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import archive_cleanup

T0 = datetime(2026, 10, 1, 16, 0, tzinfo=UTC)


def state(phase, elapsed, energy, cooling=None):
    return {
        "timeline": {"session_id": "s", "session_started_at": T0.isoformat(), "phase": phase},
        "heating": {"elapsed_seconds": elapsed, "accounted_at": f"t{elapsed}", "intervals": []},
        "energy": {"estimated_kwh": energy},
        "after_run": None if cooling is None else {
            "phase_id": "g1", "elapsed_seconds": cooling, "remaining_seconds": 60 - cooling,
            "accounted_at": f"c{cooling}", "ends_at": "x"},
    }


class ArchiveCleanupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.source = Path(self.directory.name) / "archiv.sqlite"
        Archive(self.source, "entry")._initialize(T0)
        rows = [
            ("session", state("aufheizen", 0, 0.0)),                 # erster Stand: bleibt
            ("session", state("aufheizen", 5, 0.1)),                 # nur Zähler
            ("session", state("saunagang", 6, 0.2)),                 # Inhalt: bleibt
            ("session", state("nachlauf", 7, 0.3, cooling=0)),       # Inhalt: bleibt
            ("session", state("nachlauf", 8, 0.4, cooling=10)),      # nur Kühlzähler
            ("session", state("nachlauf", 9, 0.5, cooling=20)),      # letzter Stand: bleibt
            ("detector_trace", {"t": 1}),
            ("measurement", {"position": "upper", "quantity": "temperature", "value": 80.0}),
        ]
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("INSERT INTO sessions(session_id,entry_id,started_at,updated_at,ended_at,"
                       "payload) VALUES('s','entry',?,?,?,?)",
                       (T0.isoformat(), T0.isoformat(), T0.isoformat(),
                        json.dumps(state("aus", 9, 0.5))))
            db.executemany(
                "INSERT INTO records(entry_id,session_id,kind,received_at,payload) "
                "VALUES('entry','s',?,?,?)",
                [(kind, T0.isoformat(), json.dumps(payload)) for kind, payload in rows])
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('revision', '4')")
        self.digest = hashlib.sha256(self.source.read_bytes()).hexdigest()

    def tearDown(self):
        self.directory.cleanup()

    def test_thins_states_moves_the_trace_and_keeps_everything_else(self):
        target = Path(self.directory.name) / "bereinigt.sqlite"
        trace = Path(self.directory.name) / "protokoll.sqlite"
        result = archive_cleanup.clean(self.source, target, trace)
        self.assertEqual(result["before"]["session"], 6)
        self.assertEqual(result["after"]["session"], 4)
        self.assertNotIn("detector_trace", result["after"])
        self.assertEqual(result["after"]["measurement"], 1)
        with closing(sqlite3.connect(target)) as db:
            phases = [json.loads(p)["timeline"]["phase"] for (p,) in db.execute(
                "SELECT payload FROM records WHERE kind='session' ORDER BY id")]
            self.assertEqual(phases, ["aufheizen", "saunagang", "nachlauf", "nachlauf"])
            last = json.loads(db.execute("SELECT payload FROM records WHERE kind='session' "
                                         "ORDER BY id DESC LIMIT 1").fetchone()[0])
            self.assertEqual(last["energy"], {"estimated_kwh": 0.5})
            self.assertEqual(db.execute("SELECT count(*) FROM sessions").fetchone()[0], 1)
            self.assertEqual(db.execute(
                "SELECT value FROM metadata WHERE key='revision'").fetchone()[0], "5")
        with closing(sqlite3.connect(trace)) as db:
            self.assertEqual(db.execute(
                "SELECT kind,payload FROM records").fetchall(), [("detector_trace", '{"t": 1}')])
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.digest)
        # Das Produkt liest das bereinigte Archiv wie vorher.
        archive = Archive(target, "entry")
        archive._initialize(T0)
        self.assertEqual(sorted(r["kind"] for r in archive.read("s", limit=100)["records"]),
                         ["measurement", "session", "session", "session", "session"])

    def test_existing_targets_are_never_overwritten(self):
        target = Path(self.directory.name) / "bereinigt.sqlite"
        target.write_bytes(b"x")
        with self.assertRaises(FileExistsError):
            archive_cleanup.clean(self.source, target)
        self.assertEqual(target.read_bytes(), b"x")


if __name__ == "__main__":
    unittest.main()
