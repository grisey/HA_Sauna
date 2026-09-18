"""Fachliche Zeitzuordnung und erlaubte Ganguebergaenge, ohne Home Assistant."""
import unittest
from datetime import UTC, datetime

from custom_components.ha_sauna.core.timeline import (
    Door, Event, Kind, Timeline, UnresolvedTransition, apply,
)


def t(clock):
    return datetime.fromisoformat(f"2026-09-17T{clock}+02:00")


def e(event_id, kind, detected, effective=None, session="s"):
    return Event(event_id, session, kind, t(effective or detected), t(detected))


class TimelineTests(unittest.TestCase):
    def setUp(self):
        self.empty = Timeline("s", t("18:55:42"))
        self.closed = apply(self.empty, e("close", Kind.DOOR_CLOSE, "21:15:51"))
        self.person = e("person", Kind.PERSON_WEAK, "21:19:15")
        self.active = apply(self.closed, self.person)

    def test_backdated_begin_keeps_detection_time(self):
        gang = self.active.active
        self.assertEqual(gang.started_at, t("21:15:51"))
        self.assertEqual(gang.detected_at, t("21:19:15"))
        self.assertEqual(gang.elapsed_seconds(t("21:19:15")), 204)
        self.assertEqual(gang.start_source_event_id, "close")
        self.assertEqual(gang.start_basis, "door_close")
        self.assertIsNone(self.closed.active)

    def test_infusion_recovers_missed_person(self):
        state = apply(self.closed, e("water", Kind.INFUSION, "21:22:52"))
        self.assertEqual(state.active.started_at, t("21:15:51"))
        self.assertEqual(state.active.elapsed_seconds(t("21:22:52")), 421)
        self.assertEqual(len(state.active.infusion_events), 1)

    def test_short_exit_preserves_identity_start_and_infusion(self):
        state = apply(self.active, e("water", Kind.INFUSION, "21:22:52"))
        gang = state.active
        state = apply(state, e("exit", Kind.DOOR_OPEN, "21:23:00"))
        state = apply(state, e("return", Kind.DOOR_CLOSE, "21:23:40"))
        state = apply(state, e("person2", Kind.PERSON_STRONG, "21:24:00"))
        self.assertEqual(state.active, gang)
        self.assertEqual(len(state.completed), 0)

    def test_opening_alone_does_not_finish(self):
        state = apply(self.active, e("water", Kind.INFUSION, "21:22:52"))
        state = apply(state, e("open", Kind.DOOR_OPEN, "21:27:15"))
        self.assertIsNotNone(state.active)
        self.assertEqual(len(state.completed), 0)

    def test_confirmed_ventilation_finishes_once(self):
        state = apply(self.active, e("water", Kind.INFUSION, "21:22:52"))
        state = apply(state, e("open", Kind.DOOR_OPEN, "21:27:15"))
        vent = e("vent", Kind.VENTILATION, "21:28:25")
        state = apply(state, vent)
        self.assertIsNone(state.active)
        self.assertEqual(len(state.completed), 1)
        self.assertEqual(state.completed[0].ended_at, t("21:28:25"))
        self.assertIs(apply(state, vent), state)

    def test_repeated_infusion_does_not_create_another_gang(self):
        state = apply(self.active, e("w1", Kind.INFUSION, "21:22:52"))
        state = apply(state, e("w2", Kind.INFUSION, "21:25:02"))
        self.assertEqual(state.active.gang_id, self.active.active.gang_id)
        self.assertEqual(len(state.active.infusion_events), 2)

    def test_duplicate_event_is_idempotent(self):
        self.assertIs(apply(self.active, self.person), self.active)

    def test_conflicting_duplicate_rejected(self):
        with self.assertRaises(ValueError):
            apply(self.active, e("person", Kind.PERSON_STRONG, "21:19:15"))

    def test_cross_session_event_rejected(self):
        with self.assertRaises(ValueError):
            apply(self.closed, e("x", Kind.PERSON_WEAK, "21:19:15", session="other"))

    def test_pre_session_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            apply(self.empty, e("x", Kind.DOOR_CLOSE, "18:56:00", "18:54:00"))

    def test_no_forecast_as_effective_time(self):
        with self.assertRaises(ValueError):
            e("x", Kind.DOOR_CLOSE, "21:15:51", "21:16:00")

    def test_no_naive_datetime(self):
        with self.assertRaises(ValueError):
            Timeline("s", datetime(2026, 9, 17))

    def test_timezone_normalized(self):
        self.assertEqual(self.active.active.started_at.tzinfo, UTC)

    def test_out_of_order_decision_rejected(self):
        with self.assertRaises(ValueError):
            apply(self.active, e("x", Kind.INFUSION, "21:18:00"))

    def test_no_activation_with_unknown_or_open_door(self):
        for state in (self.empty, apply(self.empty, e("o", Kind.DOOR_OPEN, "21:00:00"))):
            with self.subTest(door=state.door), self.assertRaises(ValueError):
                apply(state, self.person)

    def test_no_fabricated_closure_when_anchor_missing(self):
        known_closed = Timeline("s", t("18:55:42"), door=Door.CLOSED)
        state = apply(known_closed, self.person)
        self.assertEqual(state.active.started_at, t("21:19:15"))
        self.assertEqual(state.active.start_basis, "recognition_only")

    def test_new_opening_replaces_unconsumed_start_anchor(self):
        state = apply(self.closed, e("o", Kind.DOOR_OPEN, "21:16:00"))
        state = apply(state, e("c2", Kind.DOOR_CLOSE, "21:17:00"))
        state = apply(state, self.person)
        self.assertEqual(state.active.started_at, t("21:17:00"))

    def test_effective_closure_differs_from_its_confirmation(self):
        state = apply(self.empty, e("close", Kind.DOOR_CLOSE, "21:15:51", "21:15:49"))
        state = apply(state, self.person)
        self.assertEqual(state.active.started_at, t("21:15:49"))
        self.assertEqual(state.processed[0].detected_at, t("21:15:51"))

    def test_no_historical_activation(self):
        with self.assertRaises(ValueError):
            self.active.active.elapsed_seconds(t("21:18:00"))

    def test_unagreed_transition_remains_explicit(self):
        state = apply(self.active, e("o", Kind.DOOR_OPEN, "21:27:15"))
        with self.assertRaises(UnresolvedTransition):
            apply(state, e("v", Kind.VENTILATION, "21:28:25"))
        self.assertEqual(state.active, self.active.active)


if __name__ == "__main__":
    unittest.main()
