"""Nachlernen: Sitzungsbelege aus der eigenen Erkennung, Schätzung und Rückmeldung."""

from datetime import timedelta
import json
import unittest

from custom_components.ha_sauna.core.models import Position
from custom_components.ha_sauna.core.recognition import ClimateRecognizer
from custom_components.ha_sauna.core.timeline import Kind
from custom_components.ha_sauna.recognition_parameters import shipped_evidence, shipped_parameters
from custom_components.ha_sauna.recognition_store import RecognitionLearning

from test_recognition_model import ROOM, T0, cabin, measurements


def recognized(positions=(Position.UPPER, Position.LOWER)):
    times, values = cabin(n=1100, door=(700.0, 900.0), infusion=1400.0, seed=8)
    recognizer = ClimateRecognizer(shipped_parameters(), T0, positions=positions, outside_reference=ROOM)
    for m in measurements(times, values):
        recognizer.accept(m)
    recognizer.advance(T0 + timedelta(seconds=float(times[-1])))
    return recognizer


class RecognitionLearningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recognizer = recognized()

    def test_without_sessions_the_shipped_quantities_apply(self):
        learning = RecognitionLearning()
        self.assertEqual(learning.current_parameters(), shipped_parameters())
        self.assertIn("exchange", shipped_evidence())

    def test_shipped_evidence_holds_no_measured_courses(self):
        evidence = shipped_evidence()
        for name in ("close_form", "open_form"):
            self.assertEqual(evidence[name], [])
            pooled = evidence[f"{name}_pooled"]
            self.assertGreater(pooled["events"], 0)
            for grid in pooled["grids"]:
                self.assertEqual(len(grid), len(grid[0]))         # a residual surface over the rate grid
        self.assertNotIn("_courses", evidence)

    def test_finished_session_adds_evidence_and_the_estimate_follows_on_request(self):
        learning = RecognitionLearning()
        self.assertTrue(learning.add_session("s1", self.recognizer))
        self.assertEqual(len(learning.sessions), 1)
        # the session only adds evidence; the quantities change when the estimate is requested
        self.assertEqual(learning.pending, 1)
        self.assertEqual(learning.current_parameters(), shipped_parameters())
        self.assertTrue(any(e[1] == Kind.INFUSION.value for e in learning.sessions[0]["events"]))
        params = learning.estimate()
        self.assertEqual(params["channels"], ["Tu", "Tl", "Au", "Al"])
        self.assertIs(learning.current_parameters(), params)
        self.assertEqual(learning.pending, 0)
        # the same session replaces its evidence instead of counting twice
        learning.add_session("s1", self.recognizer)
        self.assertEqual(len(learning.sessions), 1)

    def test_rejection_removes_the_event_from_learning(self):
        learning = RecognitionLearning()
        learning.add_session("s1", self.recognizer)
        infusion = next(e for e in learning.sessions[0]["events"] if e[1] == Kind.INFUSION.value)
        at = T0 + timedelta(seconds=1400)
        self.assertEqual(learning.find_event(kind=Kind.INFUSION, at=at), infusion[0])
        before = learning.estimate()["prior"]
        learning.feedback(infusion[0], "reject")
        self.assertEqual(learning.pending, 1)
        after = learning.estimate()["prior"]
        self.assertIn(infusion[0], learning.rejected)
        self.assertNotEqual(before, after)
        learning.feedback(infusion[0], "confirm")
        self.assertNotIn(infusion[0], learning.rejected)
        self.assertEqual(learning.estimate()["prior"], before)
        with self.assertRaises(ValueError):
            learning.feedback(infusion[0], "maybe")

    def test_state_round_trip_and_storage_bound(self):
        learning = RecognitionLearning()
        learning.add_session("s1", self.recognizer)
        restored = RecognitionLearning(json.loads(json.dumps(learning.as_dict())))
        self.assertEqual(restored.sessions, learning.sessions)
        self.assertEqual(restored.pending, learning.pending)
        self.assertIsNone(restored.find_event(event_id="unknown"))

    def test_a_single_position_does_not_learn(self):
        self.assertFalse(RecognitionLearning().add_session("s2", recognized((Position.UPPER,))))


if __name__ == "__main__":
    unittest.main()
