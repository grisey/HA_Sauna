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

    def rewrite_source(self, change, rows=()):
        """Sitzungsstand der Quelle ändern und Einträge ergänzen."""
        with closing(sqlite3.connect(self.source)) as db, db:
            payload = json.loads(db.execute("SELECT payload FROM sessions").fetchone()[0])
            change(payload)
            db.execute("UPDATE sessions SET payload=?", (json.dumps(payload),))
            db.executemany("INSERT INTO records(entry_id,session_id,kind,received_at,payload) "
                           "VALUES('entry','s1',?,?,?)",
                           [(kind, moment, json.dumps(data)) for kind, moment, data in rows])

    def rebuilt_session(self):
        with closing(sqlite3.connect(self.target)) as db:
            return json.loads(db.execute("SELECT payload FROM sessions").fetchone()[0])

    def test_contactor_from_heater_states_without_contactor_history(self):
        # KZ1: Sitzungen bis 22.09. haben keine Schützgeschichte, nur die Zustände des Ofenschalters.
        # Ohne sie blieb die Ofenkühlung ohne Beginn bis zum Sitzungsende stehen.
        def without_history(payload):
            payload.pop("contactor_history")
            payload["configuration"]["bindings"] = {"heater": "switch.ofen"}
        self.rewrite_source(without_history, [
            ("source_state", at(0), {"role": "heater", "source": "switch.ofen", "state": "on"}),
            ("source_state", at(200), {"role": "heater", "source": "switch.anderer", "state": "off"}),
            ("source_state", at(300), {"role": "heater", "source": "switch.ofen", "state": "unavailable"}),
            ("source_state", at(400), {"role": "heater", "source": "switch.ofen", "state": "off"}),
            ("source_state", at(410), {"role": "light", "source": "light.kabine", "state": "on"}),
        ])
        asyncio.run(archive_rebuild.rebuild(self.source, self.target))
        session = self.rebuilt_session()
        # Heizzeit und Energie folgen der aufgezeichneten Rückmeldung statt „unbekannt“.
        self.assertEqual(session["energy"]["unknown_seconds"], 0.0)
        self.assertEqual([(item["started_at"], item["ended_at"]) for item in session["heating"]["intervals"]],
                         [(at(0), at(400))])

    def test_controller_contactor_follows_its_own_command(self):
        # KZ3 (Nutzer 11.11.): Die Schützrückmeldung der Steuerung folgt ihrem Schaltbefehl, damit die
        # Ofenkühlung wie im Betrieb endet. Aufgezeichnet ist EIN bis 400 s; der Befehl bleibt bei
        # 60–72 °C unter dem Soll 80 °C bis Betrieb AUS bei 600 s EIN.
        asyncio.run(archive_rebuild.rebuild(self.source, self.target))
        session = self.rebuilt_session()
        self.assertEqual([(mark["at"], mark["state"]) for mark in session["contactor_history"]],
                         [(at(0), None), (at(0), True), (at(MINUTES * 60 - 120), False)])
        # Heizzeit und Energie bleiben bei der Aufzeichnung.
        self.assertEqual([(item["started_at"], item["ended_at"]) for item in session["heating"]["intervals"]],
                         [(at(0), at(400))])

    def test_program_mode_changes_are_replayed(self):
        # KZ3 (Nutzer 11.11.): Wechsel der Programmart gehen wie Solländerungen ein; die Sitzung
        # beginnt mit der Programmart vor dem ersten Wechsel, auch wenn am Ende eine andere gespeichert ist.
        def constant_at_end(payload):
            payload["configuration"]["program_mode"] = "constant"
        self.rewrite_source(constant_at_end, [
            ("parameter_change", at(100), {"changes": {"target_temperature_c": {"before": 80, "after": 85},
                                                       "temperature_gangs": {"before": 3, "after": 2}},
                                           "mode_before": "progressive", "mode_after": "progressive"}),
            ("parameter_change", at(200), {"changes": {"target_temperature_c": {"before": 85, "after": 90}},
                                           "mode_before": "progressive", "mode_after": "constant"}),
        ])
        _result, log = asyncio.run(archive_rebuild.rebuild(self.source, self.target))
        self.assertEqual(log, [])
        with closing(sqlite3.connect(self.target)) as db:
            changes = [json.loads(row[0]) for row in db.execute(
                "SELECT payload FROM records WHERE kind='parameter_change' ORDER BY id")]
        self.assertEqual([(item["mode_before"], item["mode_after"], item["target_after"]) for item in changes],
                         [("progressive", "progressive", 85.0), ("progressive", "constant", 90.0)])
        self.assertEqual(changes[0]["changes"]["temperature_gangs"]["after"], 2)

    def test_manual_cooling_end_is_replayed(self):
        # KZ1: „Ofenkühlung beenden“ von Hand geht wie eine Bedienung ein.
        self.rewrite_source(lambda payload: None, [
            ("manual_phase_end", at(500), {"purpose": "after_run", "token": "alt",
                                           "planned_ends_at": at(900), "ended_at": at(500)}),
        ])
        _result, log = asyncio.run(archive_rebuild.rebuild(self.source, self.target))
        self.assertEqual(log, [("s1", at(500), "finish_cooling", "keine laufende Ofenkühlung")])

    def add_restart(self, *, gap_seconds=180, interrupted=True, session_gap_minutes=None):
        """KZ2: s1 endet mit einem Neustart, s2 beginnt ``gap_seconds`` danach."""
        def change(payload):
            if session_gap_minutes is not None:
                payload["configuration"]["parameters"]["session_gap_minutes"] = session_gap_minutes
        end = MINUTES * 60
        self.rewrite_source(change, [("interruption", at(end), {"reason": "integration_unloaded"})]
                            if interrupted else [])
        start = end + gap_seconds
        payload = {
            "timeline": {"session_id": "s2", "session_started_at": at(start), "completed": [],
                         "active": None, "retracted": [], "processed": []},
            "configuration": {"control_mode": "automatic", "presence_source": "proxy",
                              "parameters": {"target_temperature_c": 80}},
            "contactor_history": [{"at": at(start), "state": True}],
            "operation_off_at": at(start + MINUTES * 60 - 120),
        }
        rows = [("session", at(start), {"operation_enabled": True}),
                ("session", at(start + MINUTES * 60 - 120), {"operation_enabled": False})]
        rows += [("measurement", at(t), {"position": p, "quantity": q, "value": v, "raw_value": str(v),
                                         "source": f"sensor.{p}_{q}", "received_at": at(t)})
                 for t in range(start, start + MINUTES * 60, 2)
                 for p, q, v in (("upper", "temperature", 72.0), ("lower", "temperature", 50.0),
                                 ("upper", "humidity", 10.0), ("lower", "humidity", 12.0))]
        with closing(sqlite3.connect(self.source)) as db, db:
            # Ende zwischen zwei Sekundenschritten; ein Messwert davor und einer danach gehören dazu.
            ended = start + MINUTES * 60 + 0.5
            rows += [("measurement", at(t), {"position": "upper", "quantity": "temperature", "value": 72.0,
                                             "raw_value": "72.0", "source": "sensor.upper_temperature",
                                             "received_at": at(t)}) for t in (ended - 0.2, ended + 0.3)]
            db.execute("INSERT INTO sessions(session_id,entry_id,started_at,updated_at,ended_at,payload) "
                       "VALUES('s2','entry',?,?,?,?)", (at(start), at(ended), at(ended), json.dumps(payload)))
            db.executemany("INSERT INTO records(entry_id,session_id,kind,received_at,payload) "
                           "VALUES('entry','s2',?,?,?)",
                           [(kind, moment, json.dumps(data)) for kind, moment, data in rows])
            # Sitzungsloser Messwert in der Lücke
            db.execute("INSERT INTO records(entry_id,session_id,kind,received_at,payload) "
                       "VALUES('entry',NULL,'measurement',?,?)",
                       (at(end + 60), json.dumps({"position": "upper", "quantity": "temperature",
                                                  "value": 71.0, "raw_value": "71.0",
                                                  "source": "sensor.upper_temperature",
                                                  "received_at": at(end + 60)})))
        return start

    def test_sessions_split_by_a_restart_are_merged(self):
        # KZ2: erneutes EIN nach einem Neustart innerhalb der Wiederaufnahmefrist setzt die Sitzung fort.
        start = self.add_restart()
        source = self.kinds(self.source)
        result, log = asyncio.run(archive_rebuild.rebuild(self.source, self.target,
                                                          stamp={"software": "0.9.0b6"}))
        self.assertEqual([session_id for session_id, _ in result], ["s1"])
        self.assertEqual(log[0][:3], ("s1", at(MINUTES * 60), "merge"))
        with closing(sqlite3.connect(self.target)) as db:
            sessions = db.execute("SELECT session_id,started_at,ended_at FROM sessions").fetchall()
            measurements = db.execute(
                "SELECT session_id,count(*) FROM records WHERE kind='measurement' GROUP BY session_id").fetchall()
            stamp = json.loads(db.execute("SELECT payload FROM records WHERE kind='rebuild'").fetchone()[0])
        self.assertEqual(sessions, [("s1", at(0), at(start + MINUTES * 60 + 0.5))])
        # Alle Messwerte beider Teile und der Messwert in der Lücke gehören zur Sitzung s1.
        self.assertEqual(measurements, [("s1", source["measurement"])])
        self.assertEqual(self.kinds(self.target)["session_label"], 1)
        self.assertEqual(stamp["merged_session_ids"], ["s1", "s2"])
        # Die Lücke gilt wie Betrieb AUS und erneutes EIN.
        phases = [(item["at"], item["phase"]) for item in self.rebuilt_session()["base_phases"]]
        self.assertIn((at(MINUTES * 60 - 120), "aus"), phases)
        self.assertTrue(any(moment == at(start) and phase != "aus" for moment, phase in phases))

    def test_restart_pairs_outside_the_rule_stay_separate(self):
        for name, options in (("ohne Neustart", {"interrupted": False}),
                              ("nach der Frist", {"gap_seconds": 16 * 60}),
                              ("Frist der Sitzung", {"session_gap_minutes": 4})):
            with self.subTest(name):
                self.tearDown()
                self.setUp()
                self.add_restart(**options)
                result, log = asyncio.run(archive_rebuild.rebuild(self.source, self.target))
                self.assertEqual([session_id for session_id, _ in result], ["s1", "s2"])
                self.assertFalse([item for item in log if item[2] == "merge"])

    def test_minimum_revision(self):
        asyncio.run(archive_rebuild.rebuild(self.source, self.target, minimum_revision=1000))
        with closing(sqlite3.connect(self.target)) as db:
            revision = db.execute("SELECT value FROM metadata WHERE key='revision'").fetchone()
        self.assertEqual(revision[0], "1000")

    def test_existing_target_is_not_overwritten(self):
        self.target.write_bytes(b"x")
        with self.assertRaises(FileExistsError):
            asyncio.run(archive_rebuild.rebuild(self.source, self.target))
        self.assertEqual(self.target.read_bytes(), b"x")


if __name__ == "__main__":
    unittest.main()
