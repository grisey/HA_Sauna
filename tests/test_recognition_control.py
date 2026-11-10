"""B4b Details · Erkennung: Erkennungskontrolle aus gespeicherten Sitzungsdaten."""

import json
import sqlite3
import unittest
from contextlib import closing

import test_key_figures as fixtures

from custom_components.ha_sauna.core import key_figures as kf
from custom_components.ha_sauna.core.recognition_control import recognition_control


class RecognitionControlArchiveTests(unittest.TestCase):
    setUp = fixtures.ArchiveInputTests.setUp
    tearDown = fixtures.ArchiveInputTests.tearDown
    at = staticmethod(fixtures.ArchiveInputTests.at)
    write = fixtures.ArchiveInputTests.write

    def payload(self, configuration=True):
        payload = fixtures.ArchiveInputTests.payload(self, configuration)
        payload["timeline"]["completed"][0]["end_event_id"] = self.event(
            "ventilation_confirmed", 2640)["event_id"]
        return payload

    def event(self, kind, seconds):
        # Erkannte Ereignisse tragen die Kennung der Erkennung wie im Betrieb.
        return {"event_id": f"recognition:s1:{kind}:{seconds}", "session_id": "s1", "kind": kind,
                "effective_at": self.at(seconds), "detected_at": self.at(seconds + 5)}

    def records(self, rows):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executemany(
                "INSERT INTO records(entry_id,session_id,kind,received_at,payload) "
                "VALUES('entry','s1',?,?,?)",
                [(kind, self.at(t), json.dumps(data)) for kind, t, data in rows],
            )

    def test_session_without_state_history_is_marked_and_has_events(self):
        self.write()
        control = self.archive.recognition_control("s1")
        self.assertFalse(control["recorded"])
        self.assertIsNone(control["states"])
        self.assertIsNone(control["rejected"])
        self.assertEqual(control["summary"], {
            "gangs": 1, "infusions": 1, "infusions_in_gangs": 1, "infusions_outside": 0,
            "door_operations": 2, "rejected_candidates": None, "retracted_gangs": 0,
        })
        self.assertEqual(
            [(e["kind"], e["meaning"]) for e in control["events"]],
            [("door_open", "Eintritt Gang 1"), ("door_close", "Eintritt Gang 1"),
             ("infusion", "Aufguss im Gang 1"), ("door_open", "Austritt Gang 1"),
             ("ventilation_confirmed", "Gangende Gang 1"), ("door_close", "Austritt Gang 1")],
        )
        self.assertEqual({e["decision_seconds"] for e in control["events"]}, {5.0})
        self.assertEqual({e["source"] for e in control["events"]}, {"Erkennung"})

    def test_stored_probability_belongs_to_its_event(self):
        self.write()
        self.records([("detection", 1705, {"event": self.event("door_open", 1700),
                                           "probability": 0.998})])
        control = self.archive.recognition_control("s1")
        probabilities = {e["event_id"]: e["probability"] for e in control["events"]}
        self.assertEqual(probabilities["recognition:s1:door_open:1700"], 0.998)
        self.assertIsNone(probabilities["recognition:s1:door_close:1800"])

    def test_state_track_rejected_candidates_and_episodes(self):
        self.write()
        self.records([
            ("recognition_state", 0, {"at": self.at(0), "state": "closed", "probability": 0.9}),
            ("recognition_state", 1700, {"at": self.at(1700), "state": "open", "probability": 0.8}),
            ("recognition_state", 1800, {"at": self.at(1800), "state": "recovery", "probability": 0.7}),
            *[("recognition_rejected", t, {
                "kind": "door_open", "effective_at": self.at(t), "decided_at": self.at(t + 8),
                "reason": "opening_form", "state": "open", "against": "closed", "ratio": 12.5,
            }) for t in (1200, 1300, 4000)],
            ("recognition_episode", 1260, {
                "opened_at": self.at(1200), "closed_at": self.at(1210), "close_candidate_at": None,
                "ended_at": self.at(1250), "decided_at": self.at(1260),
                "candidates": [self.at(1200)], "result": "door"}),
            ("recognition_episode", 1360, {
                "opened_at": self.at(1300), "closed_at": self.at(1310), "close_candidate_at": None,
                "ended_at": self.at(1350), "decided_at": self.at(1360),
                "candidates": [self.at(1300)], "result": "rejected"}),
        ])
        control = self.archive.recognition_control("s1")
        self.assertTrue(control["recorded"])
        self.assertEqual(
            [(s["label"], s["at"], s["until"]) for s in control["states"]],
            [("Ruhe", self.at(0), self.at(1700)), ("offen", self.at(1700), self.at(1800)),
             ("Erholung", self.at(1800), self.at(7200))],
        )
        self.assertEqual(
            [(c["result"], c["decision_seconds"], c["state"], c["against"], c["ratio"])
             for c in control["rejected"]],
            [("als Türvorgang übernommen", 8.0, "offen", "Ruhe", 12.5),
             ("verworfen in einer Episode", 8.0, "offen", "Ruhe", 12.5),
             ("verworfen", 8.0, "offen", "Ruhe", 12.5)],
        )
        self.assertEqual(control["rejected"][0]["reason_label"], "Öffnungsform passt nicht")
        # K-E2 wie in den Kennzahlen derselben Sitzung
        figures = self.archive.key_figures()[0]["figures"]
        self.assertEqual(control["summary"]["rejected_candidates"], figures["rejected_candidates"])
        self.assertEqual(control["summary"]["rejected_candidates"]["rejected"], 2)
        self.assertEqual(control["summary"]["rejected_candidates"]["taken_as_door"], 1)

    def test_unknown_session(self):
        self.assertIsNone(self.archive.recognition_control("missing"))


class RecognitionControlTimelineTests(unittest.TestCase):
    at = staticmethod(fixtures.ArchiveInputTests.at)

    def test_retracted_provisional_gang_with_its_cause(self):
        source = "recognition:s1:door_close:100"
        gang = {"gang_id": "g0", "started_at": self.at(100), "start_source_event_id": source,
                "recognition_kind": "presence_entry", "infusion_events": []}
        session = {"timeline": {
            "session_id": "s1", "session_started_at": self.at(0), "completed": [], "active": None,
            "retracted": [gang], "processed": [
                {"event_id": "recognition:s1:door_open:90", "session_id": "s1", "kind": "door_open",
                 "effective_at": self.at(90), "detected_at": self.at(95)},
                {"event_id": source, "session_id": "s1", "kind": "door_close",
                 "effective_at": self.at(100), "detected_at": self.at(104)},
                {"event_id": f"deadline:s1:{source}", "session_id": "s1",
                 "kind": "confirmation_expired", "effective_at": self.at(820),
                 "detected_at": self.at(821)},
            ],
        }}
        control = recognition_control(session, ended_at=self.at(900))
        self.assertEqual(control["retracted"], [{
            "gang_id": "g0", "started_at": self.at(100),
            "recognition": "Anwesenheit beim Türschluss", "retracted_at": self.at(820),
            "reason": "Bestätigungsfrist abgelaufen",
        }])
        self.assertEqual(control["summary"]["retracted_gangs"], 1)
        self.assertEqual(control["summary"]["gangs"], 0)
        # Die Frist ist kein Erkennungsereignis; Türen ohne Gang bleiben ohne Gang.
        self.assertEqual([e["meaning"] for e in control["events"]],
                         ["Türvorgang ohne Gang", "Türvorgang ohne Gang"])
        # Erkannte Tür ohne Erkennungseintrag: über die Anwesenheit übernommene Öffnung (G9)
        self.assertEqual({e["source"] for e in control["events"]},
                         {"verworfene Öffnung, über die Anwesenheit übernommen"})

    def test_rejected_summary_is_shared_with_the_key_figures(self):
        candidates, episodes = (1.0, 2.0, 3.0), ((True, (1.0,)), (False, (2.0,)))
        session = type("S", (), {
            "rejected_candidates": candidates,
            "rejected_episodes": tuple(kf.EpisodeInput(0, 1, door, members)
                                       for door, members in episodes),
        })()
        self.assertEqual(kf.rejected_candidates(session), kf.rejected_summary(candidates, episodes))


if __name__ == "__main__":
    unittest.main()
