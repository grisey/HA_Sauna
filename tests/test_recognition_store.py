"""Nachlernen: Sitzungsbelege aus der eigenen Erkennung, Freigabe, Schätzung und Rückmeldung."""

from datetime import timedelta
import json
import unittest

from custom_components.ha_sauna.core.models import Position
from custom_components.ha_sauna.core.recognition import ClimateRecognizer
from custom_components.ha_sauna.core.recognition_learning import estimate, merge_evidence
from custom_components.ha_sauna.core.timeline import Kind, utc
from custom_components.ha_sauna.recognition_parameters import shipped_evidence, shipped_parameters
from custom_components.ha_sauna.recognition_store import RecognitionLearning

from test_recognition_model import ROOM_AIR, T0, cabin, measurements


def recognized(positions=(Position.UPPER, Position.LOWER)):
    times, values = cabin(n=1100, door=(700.0, 900.0), infusion=1400.0, seed=8)
    recognizer = ClimateRecognizer(shipped_parameters(), T0, positions=positions, outside=ROOM_AIR)
    for m in measurements(times, values):
        recognizer.accept(m)
    recognizer.advance(T0 + timedelta(seconds=float(times[-1])))
    return recognizer


def canonical(params):
    return json.dumps(params, sort_keys=True)


class RecognitionLearningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recognizer = recognized()
        # estimate from the learning recordings alone (no session released)
        cls.initial = canonical(RecognitionLearning().estimate())

    def test_without_sessions_the_shipped_quantities_apply(self):
        learning = RecognitionLearning()
        self.assertEqual(learning.current_parameters(), shipped_parameters())
        self.assertIn("exchange", shipped_evidence())

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

    def test_stored_evidence_is_not_released_and_stays_out_of_the_estimate(self):
        learning = RecognitionLearning()
        learning.add_session("s1", self.recognizer)
        events = learning.sessions[0]["events"]
        self.assertEqual(learning.overview(), [{
            "session_id": "s1",
            "started_at": utc(T0).isoformat(),
            "door_openings": sum(e[1] == Kind.DOOR_OPEN.value for e in events),
            "infusions": sum(e[1] == Kind.INFUSION.value for e in events),
            "released": False,
        }])
        self.assertEqual(canonical(learning.estimate()), self.initial)
        # evidence stored before releases existed carries no status and counts as not released
        older = {key: value for key, value in learning.sessions[0].items() if key not in ("released", "started_at")}
        learning = RecognitionLearning({"sessions": [older]})
        self.assertFalse(learning.overview()[0]["released"])
        self.assertIsNone(learning.overview()[0]["started_at"])
        self.assertEqual(canonical(learning.estimate()), self.initial)

    def test_release_and_withdrawal_of_a_stored_session(self):
        learning = RecognitionLearning()
        learning.add_session("s1", self.recognizer)
        self.assertTrue(learning.release("s1", True))
        self.assertEqual(learning.pending, 2)
        self.assertTrue(learning.overview()[0]["released"])
        restored = RecognitionLearning(json.loads(json.dumps(learning.as_dict())))
        self.assertTrue(restored.overview()[0]["released"])
        # an unchanged status is no change
        self.assertFalse(learning.release("s1", True))
        self.assertEqual(learning.pending, 2)
        self.assertTrue(learning.release("s1", False))
        self.assertFalse(learning.overview()[0]["released"])
        self.assertEqual(learning.pending, 3)
        with self.assertRaises(KeyError):
            learning.release("unknown", True)
        # new evidence of the same session is not covered by the earlier release
        learning.release("s1", True)
        learning.add_session("s1", self.recognizer)
        self.assertFalse(learning.overview()[0]["released"])

    def test_estimate_uses_only_released_evidence(self):
        learning = RecognitionLearning()
        learning.add_session("s1", self.recognizer)
        learning.add_session("s2", self.recognizer)
        learning.release("s1", True)
        expected = estimate(
            merge_evidence(shipped_evidence(), learning.sessions[0]["evidence"]), base=shipped_parameters(),
        )
        params = canonical(learning.estimate())
        self.assertEqual(params, canonical(expected))
        self.assertNotEqual(params, self.initial)
        # the unreleased second session is what the selection kept out
        learning.release("s2", True)
        self.assertNotEqual(canonical(learning.estimate()), params)
        learning.release("s1", False)
        learning.release("s2", False)
        self.assertEqual(canonical(learning.estimate()), self.initial)

    def test_rejection_removes_the_event_from_learning(self):
        learning = RecognitionLearning()
        learning.add_session("s1", self.recognizer)
        learning.release("s1", True)
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
