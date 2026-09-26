"""Synthetic phase regressions independent of live actor behaviour."""
from datetime import UTC, datetime, timedelta
import unittest

from custom_components.ha_sauna.core.phases import project_archive, project_session

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def at(n):
    return T0 + timedelta(seconds=n)


def session():
    return {
        "timeline": {"session_started_at": T0, "completed": [], "retracted": []},
        "base_phases": [
            {"at": at(0), "phase": "aufheizen", "operation_enabled": True},
            {"at": at(10), "phase": "bereit", "operation_enabled": True},
        ],
        "contactor_history": [{"at": at(0), "state": True}, {"at": at(12), "state": False}],
    }


class PhaseTests(unittest.TestCase):
    def test_retroactive_gang_overlays_base_and_retraction_restores_ready(self):
        state = session()
        gang = {"gang_id": "g", "started_at": at(5), "detected_at": at(15)}
        state["timeline"]["active"] = gang
        projected = project_session(state, at(20))
        self.assertEqual([(p.phase, p.started_at, p.ended_at) for p in projected.intervals],
                         [("aufheizen", at(0), at(5)), ("saunagang", at(5), at(20))])
        self.assertFalse(projected.readiness_pauses)
        state["timeline"]["active"] = None
        state["timeline"]["retracted"] = [gang]
        projected = project_session(state, at(20))
        self.assertEqual([p.phase for p in projected.intervals], ["aufheizen", "bereit"])
        self.assertEqual(projected.readiness_pauses[0].duration_seconds, 8)
        self.assertTrue(projected.complete)

    def test_operation_off_and_cooling_pauses_have_exact_boundaries(self):
        state = session()
        state["base_phases"] += [
            {"at": at(25), "phase": "aus", "operation_enabled": False},
            {"at": at(35), "phase": "bereit", "operation_enabled": True},
        ]
        state["after_run_history"] = [{"phase_id": "cool", "active_intervals": [(at(20), at(25)), (at(35), at(40))]}]
        projected = project_session(state, at(50))
        self.assertEqual([(p.phase, (p.ended_at-p.started_at).total_seconds()) for p in projected.intervals],
                         [("aufheizen", 10), ("bereit", 10), ("nachlauf", 5), ("aus", 10), ("nachlauf", 5), ("bereit", 10)])
        self.assertEqual([(p.started_at, p.ended_at) for p in projected.readiness_pauses], [(at(12), at(20)), (at(40), at(50))])
        self.assertEqual(sum((p.ended_at-p.started_at).total_seconds() for p in projected.intervals), 50)

    def test_unknown_feedback_is_not_a_pause_and_same_timestamp_last_wins(self):
        state = session()
        state["contactor_history"] += [{"at": at(14), "state": True}, {"at": at(14), "state": None}, {"at": at(18), "state": False}]
        projected = project_session(state, at(20))
        self.assertEqual([p.duration_seconds for p in projected.readiness_pauses], [2, 2])

    def test_ended_session_is_bounded_and_missing_evidence_explicit(self):
        state = {"timeline": {"session_started_at": T0}, "ended_at": at(30)}
        projected = project_session(state, at(50))
        self.assertEqual(projected.intervals[0].phase, "unknown")
        self.assertEqual(projected.intervals[0].ended_at, at(30))
        self.assertFalse(projected.complete)
        self.assertFalse(projected.readiness_pauses)

    def test_legacy_projection_preserves_originals_and_does_not_invent_hidden_ready(self):
        state = {"timeline": {"session_started_at": T0, "retracted": [{"gang_id": "g"}]}}
        records = [
            {"kind": "phase", "received_at": at(0), "payload": {"phase": "aufheizen"}},
            {"kind": "phase", "received_at": at(5), "payload": {"phase": "saunagang"}},
            {"kind": "phase", "received_at": at(15), "payload": {"phase": "bereit"}},
            {"kind": "source_state", "received_at": at(0), "payload": {"role": "heater", "state": "off"}},
        ]
        projected = project_archive(state, records, at(20))
        self.assertFalse(projected.complete)
        self.assertEqual([p.phase for p in projected.intervals], ["aufheizen", "unknown", "bereit"])
        self.assertEqual(projected.readiness_pauses[0].started_at, at(15))
        self.assertNotIn("base_phases", state)

    def test_legacy_gang_is_evidence_even_without_base_track(self):
        state = {"timeline": {"session_started_at": T0, "completed": [
            {"gang_id": "g", "started_at": at(5), "ended_at": at(10)}
        ]}}
        projected = project_archive(state, [], at(20))
        self.assertEqual([p.phase for p in projected.intervals], ["unknown", "saunagang", "unknown"])
        self.assertFalse(projected.complete)
