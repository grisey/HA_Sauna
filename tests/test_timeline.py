"""Vorläufige Erkennung, Aufgussbestätigung und Zeitzuordnung ohne Home Assistant."""
import unittest
from datetime import UTC, datetime

from custom_components.ha_sauna.core.timeline import (
    Confirmation, Door, Event, Kind, Timeline, UnresolvedTransition, apply,
)


def t(clock):
    return datetime.fromisoformat(f"2026-09-17T{clock}+02:00")


def e(event_id, kind, detected, effective=None, session="s"):
    return Event(event_id, session, kind, t(effective or detected), t(detected))


class TimelineTests(unittest.TestCase):
    def setUp(self):
        self.empty = Timeline("s", t("18:55:42"))
        opened = apply(self.empty, e("prepare-open", Kind.DOOR_OPEN, "21:14:20"))
        self.vent = e("prepare-vent", Kind.VENTILATION, "21:15:20")
        self.ventilated = apply(opened, self.vent)
        self.closed = apply(self.ventilated, e("close", Kind.DOOR_CLOSE, "21:15:51"))
        self.person = e("person", Kind.PERSON_WEAK, "21:19:15")
        self.active = apply(self.closed, self.person)

    def test_ventilation_and_closure_are_preparation_only(self):
        self.assertIsNone(self.ventilated.active)
        self.assertIsNone(self.closed.active)
        self.assertEqual(self.closed.completed, ())
        self.assertEqual(self.closed.preparation, self.vent)
        self.assertIsNone(self.closed.open_ventilation)

    def test_person_creates_provisional_gang_with_provenance(self):
        gang = self.active.active
        self.assertEqual(gang.confirmation, Confirmation.PROVISIONAL)
        self.assertIsNone(gang.confirmed_at)
        self.assertIsNone(gang.confirmation_event_id)
        self.assertEqual(gang.recognition_kind, Kind.PERSON_WEAK)
        self.assertEqual(gang.preparation_event_id, "prepare-vent")
        self.assertEqual(self.active.completed, ())

    def test_backdated_begin_keeps_detection_time(self):
        gang = self.active.active
        self.assertEqual(gang.started_at, t("21:15:51"))
        self.assertEqual(gang.detected_at, t("21:19:15"))
        self.assertEqual(gang.elapsed_seconds(t("21:19:15")), 204)
        self.assertEqual(gang.start_source_event_id, "close")
        self.assertEqual(gang.start_basis, "door_close")
        self.assertIsNone(self.closed.active)

    def test_infusion_confirms_the_same_gang(self):
        previous = self.active.active
        state = apply(self.active, e("water", Kind.INFUSION, "21:22:52"))
        gang = state.active
        self.assertEqual(gang.confirmation, Confirmation.CONFIRMED)
        self.assertEqual(gang.confirmed_at, t("21:22:52"))
        self.assertEqual(gang.confirmation_event_id, "water")
        self.assertEqual(gang.gang_id, previous.gang_id)
        self.assertEqual(gang.started_at, previous.started_at)
        self.assertEqual(gang.detected_at, previous.detected_at)
        self.assertEqual(gang.recognition_event_id, "person")
        self.assertEqual(gang.elapsed_seconds(t("21:22:52")), 421)
        self.assertEqual(state.completed, ())
        self.assertEqual(previous.confirmation, Confirmation.PROVISIONAL)

    def test_infusion_recovers_missed_person(self):
        state = apply(self.closed, e("water", Kind.INFUSION, "21:22:52"))
        self.assertEqual(state.active.started_at, t("21:15:51"))
        self.assertEqual(state.active.elapsed_seconds(t("21:22:52")), 421)
        self.assertEqual(state.active.confirmation, Confirmation.CONFIRMED)
        self.assertEqual(state.active.detected_at, state.active.confirmed_at)
        self.assertEqual(state.active.recognition_kind, Kind.INFUSION)

    def test_infusion_does_not_require_preparation(self):
        closed = apply(self.empty, e("c", Kind.DOOR_CLOSE, "21:15:51"))
        state = apply(closed, e("water", Kind.INFUSION, "21:22:52"))
        self.assertEqual(state.active.confirmation, Confirmation.CONFIRMED)
        self.assertIsNone(state.active.preparation_event_id)

    def test_strong_signal_is_provisional_without_preparation(self):
        closed = apply(self.empty, e("c", Kind.DOOR_CLOSE, "21:15:51"))
        state = apply(closed, e("p", Kind.PERSON_STRONG, "21:19:15"))
        self.assertEqual(state.active.confirmation, Confirmation.PROVISIONAL)
        self.assertIsNone(state.active.preparation_event_id)

    def test_weak_start_uses_close_anchor_without_preparation(self):
        closed = apply(self.empty, e("c", Kind.DOOR_CLOSE, "21:15:51"))
        state = apply(closed, self.person)
        self.assertEqual(state.active.start_source_event_id, "c")
        self.assertIsNone(state.active.preparation_event_id)

    def test_weak_start_requires_actual_close_anchor(self):
        known_closed = Timeline("s", t("18:55:42"), door=Door.CLOSED)
        with self.assertRaises(ValueError):
            apply(known_closed, self.person)

    def test_new_opening_replaces_weak_start_anchor(self):
        opened = apply(self.closed, e("o", Kind.DOOR_OPEN, "21:16:00"))
        self.assertIsNone(opened.preparation)
        closed = apply(opened, e("c2", Kind.DOOR_CLOSE, "21:17:00"))
        self.assertIsNone(closed.preparation)
        state = apply(closed, self.person)
        self.assertEqual(state.active.start_source_event_id, "c2")

    def test_current_episode_replaces_previous_preparation(self):
        state = apply(self.closed, e("o", Kind.DOOR_OPEN, "21:16:00"))
        state = apply(state, e("v2", Kind.VENTILATION, "21:17:00"))
        state = apply(state, e("c2", Kind.DOOR_CLOSE, "21:18:00"))
        state = apply(state, self.person)
        self.assertEqual(state.active.preparation_event_id, "v2")
        self.assertEqual(state.active.start_source_event_id, "c2")

    def test_repeated_person_signal_does_not_confirm(self):
        state = apply(self.active, e("p2", Kind.PERSON_STRONG, "21:20:00"))
        self.assertEqual(state.active, self.active.active)
        self.assertEqual(state.active.confirmation, Confirmation.PROVISIONAL)

    def test_short_exit_preserves_identity_start_and_infusion(self):
        state = apply(self.active, e("water", Kind.INFUSION, "21:22:52"))
        gang = state.active
        state = apply(state, e("exit", Kind.DOOR_OPEN, "21:23:00"))
        state = apply(state, e("return", Kind.DOOR_CLOSE, "21:23:40"))
        state = apply(state, e("person2", Kind.PERSON_STRONG, "21:24:00"))
        self.assertEqual(state.active, gang)
        self.assertEqual(state.active.preparation_event_id, "prepare-vent")
        self.assertEqual(len(state.completed), 0)

    def test_short_exit_preserves_provisional_gang(self):
        state = apply(self.active, e("o", Kind.DOOR_OPEN, "21:20:00"))
        state = apply(state, e("c2", Kind.DOOR_CLOSE, "21:20:40"))
        self.assertEqual(state.active, self.active.active)
        state = apply(state, e("water", Kind.INFUSION, "21:22:52"))
        self.assertEqual(state.active.started_at, t("21:15:51"))
        self.assertEqual(state.active.start_source_event_id, "close")

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
        self.assertEqual(state.completed[0].confirmation, Confirmation.CONFIRMED)
        self.assertIs(apply(state, vent), state)
        again = apply(state, e("vent-again", Kind.VENTILATION, "21:28:30"))
        self.assertEqual(again.completed, state.completed)
        self.assertEqual(again.open_ventilation, vent)

    def test_repeated_infusion_keeps_first_confirmation_time(self):
        state = apply(self.active, e("w1", Kind.INFUSION, "21:22:52"))
        state = apply(state, e("w2", Kind.INFUSION, "21:25:02"))
        self.assertEqual(state.active.gang_id, self.active.active.gang_id)
        self.assertEqual(len(state.active.infusion_events), 2)
        self.assertEqual(state.active.confirmed_at, t("21:22:52"))
        self.assertEqual(state.active.confirmation_event_id, "w1")

    def test_confirmation_uses_detection_not_effective_time(self):
        state = apply(self.active, e("w", Kind.INFUSION, "21:22:52", "21:22:49"))
        self.assertEqual(state.active.confirmed_at, t("21:22:52"))
        self.assertEqual(state.active.infusion_events[0].effective_at, t("21:22:49"))

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
        opened = apply(self.empty, e("o", Kind.DOOR_OPEN, "21:00:00"))
        for state in (self.empty, opened):
            with self.subTest(door=state.door), self.assertRaises(ValueError):
                apply(state, self.person)

    def test_no_fabricated_closure_when_anchor_missing(self):
        known_closed = Timeline("s", t("18:55:42"), door=Door.CLOSED)
        state = apply(known_closed, e("p", Kind.PERSON_STRONG, "21:19:15"))
        self.assertEqual(state.active.started_at, t("21:19:15"))
        self.assertEqual(state.active.start_basis, "recognition_only")

    def test_new_opening_replaces_unconsumed_start_anchor(self):
        state = apply(self.closed, e("o", Kind.DOOR_OPEN, "21:16:00"))
        state = apply(state, e("c2", Kind.DOOR_CLOSE, "21:17:00"))
        state = apply(state, e("p", Kind.PERSON_STRONG, "21:19:15"))
        self.assertEqual(state.active.started_at, t("21:17:00"))

    def test_effective_closure_differs_from_its_confirmation(self):
        state = apply(self.empty, e("close", Kind.DOOR_CLOSE, "21:15:51", "21:15:49"))
        state = apply(state, e("p", Kind.PERSON_STRONG, "21:19:15"))
        self.assertEqual(state.active.started_at, t("21:15:49"))
        self.assertEqual(state.processed[0].detected_at, t("21:15:51"))

    def test_no_historical_activation(self):
        with self.assertRaises(ValueError):
            self.active.active.elapsed_seconds(t("21:18:00"))

    def test_ventilation_retracts_unconfirmed_gang_without_completion(self):
        state = apply(self.active, e("o", Kind.DOOR_OPEN, "21:27:15"))
        result = apply(state, e("v", Kind.VENTILATION, "21:28:25"))
        self.assertIsNone(result.active)
        self.assertEqual(result.completed, ())
        self.assertEqual(result.gang_count, 0)
        self.assertEqual(result.retracted, (self.active.active,))
        self.assertIsNotNone(result.open_ventilation)

    def test_ventilation_requires_an_open_episode(self):
        with self.assertRaises(ValueError):
            apply(self.closed, e("v", Kind.VENTILATION, "21:18:00"))
        with self.assertRaises(UnresolvedTransition):
            apply(Timeline("s", t("18:55:42"), door=Door.OPEN), self.vent)


if __name__ == "__main__":
    unittest.main()
