"""Vorläufige Erkennung, Aufgussbestätigung und Zeitzuordnung ohne Home Assistant."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from test_cooling import at, controller
from test_foundation import bindings, event, selected_occupancy

from custom_components.ha_sauna.archive import Archive, plain
from custom_components.ha_sauna.core.presence import binary_presence
from custom_components.ha_sauna.core.timeline import (
    Confirmation, Door, Event, Kind, Timeline, UnresolvedTransition, apply,
    active_gang_duration_seconds, project_door_cycles, project_gang_intervals,
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

    def test_initial_closed_door_does_not_invent_an_entry_cycle(self):
        self.assertEqual(self.empty.door, Door.CLOSED)
        self.assertIsNone(self.empty.anchor)
        self.assertIsNone(self.empty.opening)
        self.assertIsNone(self.empty.closed_opening)
        self.assertEqual(self.empty.processed, ())
        self.assertFalse(self.empty.entry_cycle_available)
        with self.assertRaises(ValueError):
            apply(self.empty, e("unpaired-close", Kind.DOOR_CLOSE, "21:15:00"))

    def test_explicit_historical_unknown_door_remains_unknown(self):
        unknown = Timeline("s", t("18:55:42"), door=Door.UNKNOWN)
        self.assertEqual(unknown.door, Door.UNKNOWN)
        self.assertFalse(unknown.entry_cycle_available)
        with self.assertRaises(ValueError):
            apply(unknown, self.person)

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
        opened = apply(self.empty, e("o", Kind.DOOR_OPEN, "21:15:00"))
        closed = apply(opened, e("c", Kind.DOOR_CLOSE, "21:15:51"))
        state = apply(closed, e("water", Kind.INFUSION, "21:22:52"))
        self.assertEqual(state.active.confirmation, Confirmation.CONFIRMED)
        self.assertIsNone(state.active.preparation_event_id)

    def test_strong_signal_is_provisional_without_preparation(self):
        opened = apply(self.empty, e("o", Kind.DOOR_OPEN, "21:15:00"))
        closed = apply(opened, e("c", Kind.DOOR_CLOSE, "21:15:51"))
        state = apply(closed, e("p", Kind.PERSON_STRONG, "21:19:15"))
        self.assertEqual(state.active.confirmation, Confirmation.PROVISIONAL)
        self.assertIsNone(state.active.preparation_event_id)

    def test_weak_start_uses_close_anchor_without_preparation(self):
        opened = apply(self.empty, e("o", Kind.DOOR_OPEN, "21:15:00"))
        closed = apply(opened, e("c", Kind.DOOR_CLOSE, "21:15:51"))
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

    def direct_exit(self, *, closed, ventilated=True):
        state = apply(self.closed, e("present", Kind.PRESENCE_CONFIRMED, "21:15:51"))
        state = apply(state, e("exit-open", Kind.DOOR_OPEN, "21:16:10"))
        if ventilated:
            state = apply(state, e("exit-vent", Kind.VENTILATION, "21:16:12"))
        if closed:
            state = apply(state, e("exit-close", Kind.DOOR_CLOSE, "21:16:20"))
        return state

    def test_direct_exit_requires_ventilation_of_its_opening(self):
        for closed in (False, True):
            with self.subTest(closed=closed):
                state = self.direct_exit(closed=closed, ventilated=False)
                self.assertIsNone(state.exit_ventilation)
                with self.assertRaises(ValueError):
                    apply(state, e("absence", Kind.PRESENCE_ENDED,
                                   "21:17:00", effective="21:16:15"))
                self.assertIsNotNone(state.active)
                self.assertEqual(state.completed, ())

    def test_direct_ventilation_is_evidence_without_ending_the_gang(self):
        for closed in (False, True):
            with self.subTest(closed=closed):
                state = self.direct_exit(closed=closed)
                self.assertEqual(state.exit_ventilation.event_id, "exit-vent")
                self.assertIsNotNone(state.active)
                self.assertEqual(state.completed, ())

    def test_exit_ventilation_rejects_original_time_before_opening(self):
        state = self.direct_exit(closed=False, ventilated=False)
        state = apply(state, e("stale-vent", Kind.VENTILATION,
                               "21:16:15", effective="21:16:09"))
        self.assertIsNone(state.exit_ventilation)
        with self.assertRaises(ValueError):
            apply(state, e("absence", Kind.PRESENCE_ENDED, "21:16:16"))
        state = apply(state, e("exit-close", Kind.DOOR_CLOSE, "21:16:20"))
        self.assertIsNone(state.exit_ventilation)
        with self.assertRaises(ValueError):
            apply(state, e("delayed-absence", Kind.PRESENCE_ENDED,
                           "21:17:00", effective="21:16:16"))

    def test_closed_exit_ventilation_rejects_original_time_after_closure(self):
        state = self.direct_exit(closed=False, ventilated=False)
        state = apply(state, e("vent", Kind.VENTILATION, "21:16:22"))
        state = apply(state, e("delayed-close", Kind.DOOR_CLOSE,
                               "21:16:23", effective="21:16:20"))
        self.assertIsNone(state.exit_ventilation)
        with self.assertRaises(ValueError):
            apply(state, e("delayed-absence", Kind.PRESENCE_ENDED,
                           "21:17:00", effective="21:16:15"))

    def test_new_opening_discards_previous_exit_ventilation(self):
        state = self.direct_exit(closed=True)
        self.assertIsNotNone(state.exit_ventilation)
        state = apply(state, e("next-open", Kind.DOOR_OPEN, "21:16:30"))
        self.assertIsNone(state.exit_ventilation)
        self.assertIsNone(state.preparation)
        self.assertIsNone(state.open_ventilation)
        with self.assertRaises(ValueError):
            apply(state, e("absence", Kind.PRESENCE_ENDED, "21:16:31"))
        state = apply(state, e("next-vent", Kind.VENTILATION, "21:16:32"))
        self.assertEqual(state.exit_ventilation.event_id, "next-vent")
        ended = apply(state, e("current-absence", Kind.PRESENCE_ENDED, "21:16:33"))
        self.assertIsNone(ended.active)
        self.assertEqual(ended.gang_count, 1)

    def test_exit_ventilation_needs_the_corresponding_opening(self):
        for door in (Door.OPEN, Door.CLOSED, Door.UNKNOWN):
            with self.subTest(door=door):
                state = Timeline("s", t("18:55:42"), door=door,
                                 open_ventilation=self.vent, preparation=self.vent,
                                 anchor=self.closed.anchor)
                self.assertIsNone(state.exit_ventilation)

    def test_exit_booking_combines_sensor_time_with_the_complete_evidence(self):
        state = self.direct_exit(closed=True)
        for sensor_time, combined_time in (
            ("21:16:10", "21:16:12"),
            ("21:16:15", "21:16:15"),
            ("21:16:20", "21:16:20"),
        ):
            with self.subTest(sensor_time=sensor_time):
                self.assertEqual(state.presence_exit_at(t(sensor_time)), t(combined_time))
                ended = apply(state, e("delayed-absence", Kind.PRESENCE_ENDED,
                                       "21:17:00", effective=combined_time))
                self.assertEqual(ended.completed[0].ended_at, t("21:17:00"))

    def test_delayed_exit_ventilation_uses_original_time_within_closed_episode(self):
        for effective in ("21:16:10", "21:16:15", "21:16:20"):
            with self.subTest(effective=effective):
                state = self.direct_exit(closed=False, ventilated=False)
                ventilation = e("delayed-vent", Kind.VENTILATION,
                                "21:16:30", effective=effective)
                state = apply(state, ventilation)
                state = apply(state, e("delayed-close", Kind.DOOR_CLOSE,
                                       "21:16:31", effective="21:16:20"))
                self.assertEqual(state.exit_ventilation, ventilation)
                ended = apply(state, e("delayed-absence", Kind.PRESENCE_ENDED,
                                       "21:17:00", effective="21:16:20"))
                self.assertIsNone(ended.active)
                self.assertEqual(ended.completed[0].ended_at, t("21:17:00"))

    def test_direct_exit_with_open_door_needs_no_closure(self):
        state = self.direct_exit(closed=False)
        ended = apply(state, e("absence", Kind.PRESENCE_ENDED, "21:16:15"))
        self.assertEqual(ended.door, Door.OPEN)
        self.assertIsNone(ended.active)
        self.assertEqual(ended.gang_count, 1)

    def test_direct_exit_rejects_absence_from_before_its_opening(self):
        state = self.direct_exit(closed=True)
        with self.assertRaises(ValueError):
            apply(state, e("earlier-absence", Kind.PRESENCE_ENDED,
                           "21:17:00", effective="21:16:09"))

    def test_confirmed_ventilation_finishes_once(self):
        state = apply(self.active, e("water", Kind.INFUSION, "21:22:52"))
        state = apply(state, e("open", Kind.DOOR_OPEN, "21:27:15"))
        vent = e("vent", Kind.VENTILATION, "21:28:25")
        state = apply(state, vent)
        self.assertIsNone(state.active)
        self.assertEqual(len(state.completed), 1)
        self.assertEqual(state.completed[0].ended_at, vent.booking_at)
        self.assertEqual(state.completed[0].end_event_id, vent.event_id)
        self.assertEqual(state.completed[0].confirmation, Confirmation.CONFIRMED)
        self.assertIs(apply(state, vent), state)
        again = apply(state, e("vent-again", Kind.VENTILATION, "21:28:30"))
        self.assertEqual(again.completed, state.completed)
        self.assertEqual(again.open_ventilation, vent)
        close = e("exit-close", Kind.DOOR_CLOSE, "21:29:00", "21:28:50")
        state = apply(again, close)
        self.assertIsNone(state.active)
        self.assertEqual(len(state.completed), 1)
        self.assertEqual(state.completed[0].ended_at, vent.booking_at)
        self.assertIs(apply(state, close), state)
        self.assertIn(vent, state.processed)
        self.assertTrue(state.entry_cycle_available)
        for kind in (Kind.PERSON_STRONG, Kind.PERSON_WEAK, Kind.INFUSION):
            with self.subTest(kind=kind):
                returned = apply(state, e("return", kind, "21:29:01"))
                self.assertEqual(returned.active.started_at, close.effective_at)
                self.assertEqual(returned.completed, state.completed)

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
        unknown = Timeline("s", t("18:55:42"), door=Door.UNKNOWN)
        opened = apply(self.empty, e("o", Kind.DOOR_OPEN, "21:00:00"))
        for state in (unknown, opened):
            with self.subTest(door=state.door), self.assertRaises(ValueError):
                apply(state, self.person)

    def test_every_proxy_start_requires_both_door_edges(self):
        known_closed = Timeline("s", t("18:55:42"), door=Door.CLOSED)
        unknown = Timeline("s", t("18:55:42"), door=Door.UNKNOWN)
        bare_close = apply(unknown, e("close-only", Kind.DOOR_CLOSE, "21:15:00"))
        opened = apply(self.empty, e("open-only", Kind.DOOR_OPEN, "21:15:00"))
        for state in (self.empty, known_closed, bare_close, opened):
            for kind in (Kind.PERSON_STRONG, Kind.PERSON_WEAK, Kind.INFUSION):
                with self.subTest(door=state.door, kind=kind), self.assertRaises(ValueError):
                    apply(state, e("signal", kind, "21:19:15"))

    def test_new_closure_survives_retraction_of_an_older_provisional_anchor(self):
        for close_before_expiry in (False, True):
            with self.subTest(close_before_expiry=close_before_expiry):
                state = apply(self.active, e("o", Kind.DOOR_OPEN, "21:20:00"))
                if close_before_expiry:
                    state = apply(state, e("c", Kind.DOOR_CLOSE, "21:20:10"))
                state = apply(state, e("expire", Kind.CONFIRMATION_EXPIRED, "21:20:20"))
                if not close_before_expiry:
                    state = apply(state, e("c", Kind.DOOR_CLOSE, "21:20:30"))
                self.assertIsNone(state.active)
                self.assertIn(self.active.active.start_source_event_id, state.rejected_start_sources)
                self.assertNotIn("c", state.rejected_start_sources)
                for kind in (Kind.PERSON_STRONG, Kind.PERSON_WEAK, Kind.INFUSION):
                    with self.subTest(kind=kind):
                        entered = apply(state, e("new-entry", kind, "21:20:40"))
                        self.assertEqual(entered.active.start_source_event_id, "c")
                        self.assertEqual(entered.active.started_at,
                                         t("21:20:10" if close_before_expiry else "21:20:30"))
                state = apply(state, e("new-o", Kind.DOOR_OPEN, "21:21:00"))
                state = apply(state, e("new-c", Kind.DOOR_CLOSE, "21:21:10"))
                state = apply(state, e("new-water", Kind.INFUSION, "21:21:20"))
                self.assertEqual(state.active.started_at, t("21:21:10"))

    def test_new_opening_replaces_unconsumed_start_anchor(self):
        state = apply(self.closed, e("o", Kind.DOOR_OPEN, "21:16:00"))
        state = apply(state, e("c2", Kind.DOOR_CLOSE, "21:17:00"))
        state = apply(state, e("p", Kind.PERSON_STRONG, "21:19:15"))
        self.assertEqual(state.active.started_at, t("21:17:00"))

    def test_effective_closure_differs_from_its_confirmation(self):
        state = apply(self.empty, e("open", Kind.DOOR_OPEN, "21:15:00"))
        state = apply(state, e("close", Kind.DOOR_CLOSE, "21:15:51", "21:15:49"))
        state = apply(state, e("p", Kind.PERSON_STRONG, "21:19:15"))
        self.assertEqual(state.active.started_at, t("21:15:49"))
        self.assertEqual(state.processed[1].detected_at, t("21:15:51"))

    def test_no_historical_activation(self):
        with self.assertRaises(ValueError):
            self.active.active.elapsed_seconds(t("21:18:00"))

    def test_booking_defaults_to_detection_and_requires_valid_bounds(self):
        legacy = e("water", Kind.INFUSION, "21:15:50", "21:15:10")
        self.assertEqual(legacy.booking_at, legacy.detected_at)
        for booking in ("21:15:09", "21:15:51"):
            with self.subTest(booking=booking), self.assertRaises(ValueError):
                Event("invalid", "s", Kind.INFUSION,
                      t("21:15:10"), t("21:15:50"), t(booking))

    def test_ventilation_retracts_unconfirmed_gang_without_completion(self):
        state = apply(self.active, e("o", Kind.DOOR_OPEN, "21:27:15"))
        result = apply(state, e("v", Kind.VENTILATION, "21:28:25"))
        self.assertIsNone(result.active)
        self.assertEqual(result.completed, ())
        self.assertEqual(result.gang_count, 0)
        self.assertEqual(result.retracted, (self.active.active,))
        self.assertIsNotNone(result.open_ventilation)
        result = apply(result, e("c", Kind.DOOR_CLOSE, "21:28:30"))
        self.assertTrue(result.entry_cycle_available)
        result = apply(result, e("return", Kind.PERSON_STRONG, "21:28:31"))
        self.assertEqual(result.active.started_at, t("21:28:30"))
        self.assertEqual(result.retracted, (self.active.active,))

    def test_ventilation_requires_an_open_episode(self):
        with self.assertRaises(ValueError):
            apply(self.closed, e("v", Kind.VENTILATION, "21:18:00"))
        with self.assertRaises(UnresolvedTransition):
            apply(Timeline("s", t("18:55:42"), door=Door.OPEN), self.vent)

    def test_complete_ventilation_only_adds_idempotent_processed_evidence(self):
        complete = e("complete", Kind.VENTILATION_COMPLETE, "21:17:00")
        for state in (self.ventilated, self.direct_exit(closed=False)):
            with self.subTest(active_direct_gang=state.active is not None):
                result = apply(state, complete)
                self.assertEqual(result, replace(
                    state, processed=state.processed + (complete,),
                ))
                self.assertIs(apply(result, complete), result)

    def test_complete_ventilation_requires_current_opening_stage_one_and_order(self):
        opened = apply(self.empty, e("open", Kind.DOOR_OPEN, "21:14:20"))
        ventilation = Event(
            "vent", "s", Kind.VENTILATION, t("21:15:00"), t("21:16:00"),
            t("21:15:50"), t("21:15:30"),
        )
        state = apply(opened, ventilation)
        complete = Event(
            "complete", "s", Kind.VENTILATION_COMPLETE,
            t("21:15:10"), t("21:17:00"), t("21:17:00"), t("21:15:40"),
        )
        closed = apply(state, e("close", Kind.DOOR_CLOSE, "21:16:30"))
        next_open = apply(closed, e("next", Kind.DOOR_OPEN, "21:16:40"))
        for label, source, evidence in (
            ("before_stage_one", opened, complete),
            ("closed", closed, complete),
            ("new_opening", next_open, complete),
            ("foreign_opening", replace(state, opening=e(
                "foreign", Kind.DOOR_OPEN, "21:14:30",
            )), complete),
            ("unprocessed_opening", replace(state, processed=(ventilation,)), complete),
            ("foreign_stage_one", replace(state, open_ventilation=self.vent), complete),
            ("physical_time", state, replace(complete, effective_at=t("21:14:59"))),
            ("confirmation_time", state, replace(complete, trace_at=t("21:15:29"))),
            ("detected_time", state, replace(
                complete, booking_at=t("21:15:50"), detected_at=t("21:15:59"),
            )),
        ):
            with self.subTest(case=label), self.assertRaises(ValueError):
                apply(source, evidence)


class DoorCycleProjectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_duration_uses_physical_entry_and_keeps_unknown_source_unknown(self):
        c = controller()
        for source in (
            event("entry", Kind.DOOR_OPEN, 1),
            Event("entry-close", "s", Kind.DOOR_CLOSE, at(2), at(3), at(3), at(3)),
            event("infusion", Kind.INFUSION, 4),
        ):
            c.process(source)
        before = plain(c.session.timeline)
        spans = project_gang_intervals(c.session.timeline)
        self.assertEqual(active_gang_duration_seconds(c.session.timeline, at(7)), 5)
        self.assertEqual(active_gang_duration_seconds(before, at(7), intervals=spans), 5)
        self.assertIsNone(active_gang_duration_seconds(before, at(1)))
        self.assertIsNone(active_gang_duration_seconds({**before, "processed": []}, at(7)))
        self.assertIsNone(active_gang_duration_seconds({**before, "active": None}, at(7)))
        self.assertEqual(plain(c.session.timeline), before)

    async def test_gang_bounds_end_at_confirmed_ventilation_without_retiming_control(self):
        from custom_components.ha_sauna.core.phases import project_session

        c = controller()
        for source in (
            event("entry", Kind.DOOR_OPEN, 1),
            Event("entry-close", "s", Kind.DOOR_CLOSE, at(2), at(3), at(3), at(3)),
            event("infusion", Kind.INFUSION, 4),
            Event("exit", "s", Kind.DOOR_OPEN, at(5), at(6), at(6), at(6)),
            Event("vent", "s", Kind.VENTILATION, at(7), at(8), at(8), at(8)),
        ):
            c.process(source)
        before, decisions = plain(c.session), tuple(c.decisions)
        gang = c.session.timeline.completed[0]
        expected = [{
            "gang_id": gang.gang_id,
            "started_at": at(2).isoformat(), "ended_at": at(8).isoformat(),
            "start_source_event_id": "entry-close", "end_source_event_id": "vent",
            "complete": True,
        }]
        self.assertEqual(project_gang_intervals(c.session.timeline), expected)
        self.assertEqual(project_gang_intervals(before["timeline"]), expected)
        visible = project_session(c.session, at(9))
        control = project_session(c.session, at(9), physical_gangs=False)
        self.assertEqual([(item.started_at, item.ended_at) for item in visible.intervals
                          if item.phase == "saunagang"], [(at(2), at(8))])
        self.assertEqual([(item.started_at, item.ended_at) for item in control.intervals
                          if item.phase == "saunagang"], [(at(2), at(8))])
        self.assertEqual(gang.ended_at, at(8))
        self.assertEqual(c.session.after_run.requested_at, at(8))
        self.assertEqual(plain(c.session), before)
        self.assertEqual(tuple(c.decisions), decisions)

    async def test_open_exit_keeps_gang_duration_running_until_ventilation(self):
        c = controller()
        for source in (event("entry", Kind.DOOR_OPEN, 1),
                       event("close", Kind.DOOR_CLOSE, 2),
                       event("water", Kind.INFUSION, 3),
                       event("exit", Kind.DOOR_OPEN, 5)):
            c.process(source)
        self.assertIsNone(project_gang_intervals(c.session.timeline)[0]["ended_at"])
        self.assertEqual(active_gang_duration_seconds(c.session.timeline, at(7)), 5)
        self.assertEqual(c.phase, "saunagang")
        c.process(event("vent", Kind.VENTILATION, 8))
        self.assertEqual(project_gang_intervals(c.session.timeline)[0]["ended_at"],
                         at(8).isoformat())

    async def test_operation_off_and_missing_source_do_not_invent_statistical_exit(self):
        c = controller()
        for source in (event("entry", Kind.DOOR_OPEN, 1),
                       event("close", Kind.DOOR_CLOSE, 2),
                       event("water", Kind.INFUSION, 3)):
            c.process(source)
        c.set_operation(False, at(4))
        result = project_gang_intervals(c.session.timeline)
        self.assertEqual(result[0]["started_at"], at(2).isoformat())
        self.assertIsNone(result[0]["ended_at"])
        self.assertIsNone(result[0]["end_source_event_id"])
        self.assertFalse(result[0]["complete"])
        legacy = plain(c.session.timeline)
        legacy["processed"] = []
        self.assertIsNone(project_gang_intervals(legacy)[0]["started_at"])

    @staticmethod
    def cycle(
        opening, closing, started, ended, *, links=(), role=None, gang_id=None,
        ventilation=None, ventilation_complete=None,
    ):
        return {
            "id": opening,
            "event_ids": [opening, closing] if closing is not None else [opening],
            "started_at": at(started).isoformat(),
            "ended_at": at(ended).isoformat() if ended is not None else None,
            "open": ended is None,
            "role": role,
            "gang_id": gang_id,
            "gang_links": list(links),
            "ventilation_at": at(ventilation[0]).isoformat() if ventilation else None,
            "ventilation_event_id": ventilation[1] if ventilation else None,
            "ventilation_complete_at": (
                at(ventilation_complete[0]).isoformat() if ventilation_complete else None
            ),
            "ventilation_complete_event_id": (
                ventilation_complete[1] if ventilation_complete else None
            ),
        }

    def assert_fresh_projection(self, timeline, expected):
        source = deepcopy(plain(timeline))
        first = project_door_cycles(timeline)
        second = project_door_cycles(timeline)
        self.assertEqual(first, expected)
        self.assertEqual(second, expected)
        self.assertIsNot(first, second)
        for left, right in zip(first, second, strict=True):
            self.assertIsNot(left, right)
            self.assertIsNot(left["event_ids"], right["event_ids"])
            self.assertIsNot(left["gang_links"], right["gang_links"])
            for left_link, right_link in zip(
                left["gang_links"], right["gang_links"], strict=True,
            ):
                self.assertIsNot(left_link, right_link)
        first[0]["started_at"] = "changed copy"
        first[0]["event_ids"].append("copy-only")
        if first[0]["gang_links"]:
            first[0]["gang_links"][0]["role"] = "changed copy"
        first[0]["gang_links"].append({"gang_id": "copy-only", "role": "exit"})
        self.assertEqual(second, expected)
        self.assertEqual(project_door_cycles(timeline), expected)
        self.assertEqual(plain(timeline), source)

    def assert_object_and_plain(self, session, expected):
        original = deepcopy(session)
        payload = plain(session)
        before = deepcopy(payload)
        self.assert_fresh_projection(session.timeline, expected)
        self.assert_fresh_projection(payload["timeline"], expected)
        restored = json.loads(json.dumps(payload))
        self.assert_fresh_projection(restored["timeline"], expected)
        self.assertEqual(session, original)
        self.assertEqual(payload, before)
        self.assertEqual(restored, before)

    async def test_proxy_cycle_keeps_exit_and_next_entry_with_original_times(self):
        c = controller()
        o1, c1, w1 = (
            event("o1", Kind.DOOR_OPEN, 1),
            event("c1", Kind.DOOR_CLOSE, 2),
            event("w1", Kind.INFUSION, 3),
        )
        for source in (o1, c1, w1):
            c.process(source)
        g1 = c.session.timeline.active
        self.assertEqual(g1.started_at, at(2))
        self.assertEqual(g1.start_source_event_id, "c1")
        self.assertEqual(g1.recognition_kind, Kind.INFUSION)
        self.assertEqual(g1.confirmation, Confirmation.CONFIRMED)
        self.assertEqual(g1.infusion_events, (w1,))
        entry = self.cycle(
            "o1", "c1", 1, 2, role="entry", gang_id=g1.gang_id,
            links=({"gang_id": g1.gang_id, "role": "entry"},),
        )
        self.assert_object_and_plain(c.session, [entry])

        o2 = event("o2", Kind.DOOR_OPEN, 4)
        v2 = Event(
            "v2", "s", Kind.VENTILATION,
            at(5), at(5.1875), at(5.125), at(5.0625),
        )
        c.process(o2)
        c.process(v2)
        self.assertEqual(c.session.timeline.gang_count, 1)
        self.assertEqual(c.session.timeline.completed[0].ended_at, v2.booking_at)
        self.assertEqual(c.session.after_run.requested_at, at(5.125))
        self.assert_object_and_plain(c.session, [entry, self.cycle(
            "o2", None, 4, None, role="exit", gang_id=g1.gang_id,
            links=({"gang_id": g1.gang_id, "role": "exit"},),
            ventilation=(5, "v2"),
        )])

        complete = Event(
            "vc2", "s", Kind.VENTILATION_COMPLETE,
            at(5.25), at(5.75), at(5.5), at(5.375),
        )
        c.advance(complete.booking_at)
        before_complete = c.session
        inputs_before_complete = c.regulation_inputs
        decisions_before_complete = tuple(c.decisions)
        c.process(complete)
        self.assertEqual(c.session, replace(
            before_complete, timeline=replace(
                before_complete.timeline,
                processed=before_complete.timeline.processed + (complete,),
            ),
        ))
        self.assertEqual(c.regulation_inputs, inputs_before_complete)
        self.assertEqual(tuple(c.decisions), decisions_before_complete)
        # The equally booked closure backdates physical time past stage two.
        c2 = Event(
            "c2", "s", Kind.DOOR_CLOSE,
            at(5.2), at(6), at(5.5), at(5.4),
        )
        self.assertLess(c2.effective_at, complete.effective_at)
        self.assertEqual(c2.booking_at, complete.booking_at)
        w2 = event("w2", Kind.INFUSION, 7)
        c.process(c2)
        c.process(w2)
        timeline = c.session.timeline
        g2 = timeline.active
        self.assertNotEqual(g1.gang_id, g2.gang_id)
        self.assertEqual(g2.started_at, at(5.2))
        self.assertEqual(g2.start_source_event_id, "c2")
        self.assertEqual(g2.infusion_events, (w2,))
        self.assertEqual(timeline.completed[0].infusion_events, (w1,))
        self.assertEqual(timeline.completed[0].ended_at, at(5.125))
        self.assertEqual(timeline.completed[0].end_event_id, "v2")
        self.assertEqual(timeline.gang_count, 1)
        self.assertEqual(timeline.processed, (o1, c1, w1, o2, v2, complete, c2, w2))
        expected = [entry, self.cycle(
            "o2", "c2", 4, 5.2, links=(
                {"gang_id": g1.gang_id, "role": "exit"},
                {"gang_id": g2.gang_id, "role": "entry"},
            ), ventilation=(5, "v2"), ventilation_complete=(5.25, "vc2"),
        )]
        self.assert_object_and_plain(c.session, expected)

        o3 = event("o3", Kind.DOOR_OPEN, 8)
        c.process(o3)
        self.assertEqual(c.session.timeline.active, g2)
        self.assertEqual(c.session.timeline.gang_count, 1)
        self.assertEqual(c.session.timeline.processed, timeline.processed + (o3,))
        expected.append(self.cycle("o3", None, 8, None))
        self.assert_object_and_plain(c.session, expected)

        # One queued archive roundtrip covers the same dual-role source sequence.
        original = deepcopy(c.session)
        configuration = {
            "bindings": bindings().as_dict(), "parameters": c.parameters.as_dict(),
        }
        intervals = [{
            "started_at": at(0).isoformat(), "ended_at": at(8).isoformat(),
            "source": "unknown", "power_kw": None,
        }]
        self.assertEqual(plain(original.energy.intervals), intervals)
        self.assertEqual(original.energy.unknown_seconds, 8)
        self.assertEqual(original.energy.accounted_at, at(8))
        expected_record = {**plain(original), "configuration": configuration}
        expected_record["energy"]["interval_delta"] = {
            "from": None, "to": at(8).isoformat(), "intervals": intervals,
        }
        # Original records carry the delta; the read snapshot also marks
        # its availability.
        expected_session = deepcopy(expected_record)
        expected_session["energy"]["intervals_available"] = True
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(Path(directory) / "cycles.sqlite", "entry")
            await archive.start()
            try:
                archive.save_session(c.session, at(8), configuration)
                await archive.flush()
                stored = await asyncio.to_thread(archive.read, "s", now=at(8))
                before = deepcopy(stored)
                self.assertEqual(
                    [record["kind"] for record in stored["records"]], ["session"],
                )
                for representation, payload, expected_payload in (
                    ("session", stored["session"], expected_session),
                    ("record", stored["records"][0]["payload"], expected_record),
                ):
                    with self.subTest(representation=representation):
                        self.assertEqual(payload, expected_payload)
                        self.assert_fresh_projection(payload["timeline"], expected)
                self.assertEqual(stored, before)
                self.assertEqual(
                    await asyncio.to_thread(archive.read, "s", now=at(8)), before,
                )
                self.assertEqual(c.session, original)
            finally:
                await archive.close()

    def test_complete_projection_requires_current_stage_one_and_unique_sources(self):
        c = controller()
        for source in (
            event("open", Kind.DOOR_OPEN, 1),
            event("vent", Kind.VENTILATION, 2),
            # Scale the observed COMPLETE 53 / CLOSE 52 ordering to this fixture.
            Event("complete", "s", Kind.VENTILATION_COMPLETE,
                  at(5.3), at(5.4), at(5.3)),
            Event("close", "s", Kind.DOOR_CLOSE, at(5.2), at(5.5), at(5.3)),
            event("next-open", Kind.DOOR_OPEN, 6),
        ):
            c.process(source)
        original = plain(c.session.timeline)
        self.assert_object_and_plain(c.session, [
            self.cycle(
                "open", "close", 1, 5.2,
                ventilation=(2, "vent"), ventilation_complete=(5.3, "complete"),
            ),
            self.cycle("next-open", None, 6, None),
        ])
        for label, missing, ambiguous in (
            ("missing_stage_one", "vent", None),
            ("new_opening_without_stage_one", "complete", None),
            ("ambiguous_opening", None, "open"),
            ("ambiguous_stage_one", None, "vent"),
            ("ambiguous_stage_two", None, "complete"),
        ):
            with self.subTest(case=label):
                payload = deepcopy(original)
                expected = [
                    self.cycle(
                        "open", "close", 1, 5.2,
                        ventilation=(2, "vent") if label in (
                            "new_opening_without_stage_one", "ambiguous_stage_two",
                        ) else None,
                    ),
                    self.cycle("next-open", None, 6, None),
                ]
                if missing is not None:
                    source = next(
                        item for item in payload["processed"]
                        if item["event_id"] == missing
                    )
                    payload["processed"].remove(source)
                    if missing == "complete":
                        # A delayed record cannot borrow the preceding stage one.
                        payload["processed"].append(source)
                else:
                    source = next(
                        item for item in payload["processed"]
                        if item["event_id"] == ambiguous
                    )
                    payload["processed"].append({**source, "kind": Kind.INFUSION.value})
                self.assert_fresh_projection(payload, expected)
        self.assertEqual(plain(c.session.timeline), original)

    async def test_delayed_direct_absence_links_exit_at_booking_after_door_close(self):
        c = controller()
        c.process(event("o1", Kind.DOOR_OPEN, 1))
        c.consume_occupancy_input(selected_occupancy(
            binary_presence("binary_sensor.presence", "on", at(2), at(2)), at(2),
        ))
        c.process(event("c1", Kind.DOOR_CLOSE, 2))
        g1 = c.session.timeline.active
        self.assertEqual(g1.started_at, at(2))
        self.assertEqual(g1.recognition_kind, Kind.PRESENCE_CONFIRMED)
        self.assertEqual(g1.confirmation, Confirmation.CONFIRMED)
        for source in (
            event("o2", Kind.DOOR_OPEN, 4),
            event("v2", Kind.VENTILATION, 5),
            event("c2", Kind.DOOR_CLOSE, 6),
        ):
            c.process(source)
        self.assertEqual(c.session.timeline.active.gang_id, g1.gang_id)
        self.assertEqual(c.session.timeline.completed, ())
        self.assertIsNone(c.session.after_run)

        absence = binary_presence("binary_sensor.presence", "off", at(5.5), at(8))
        self.assertTrue(c.consume_occupancy_input(selected_occupancy(absence, at(8))))
        timeline = c.session.timeline
        self.assertIsNone(timeline.active)
        self.assertEqual(len(timeline.completed), 1)
        ended = timeline.completed[0]
        end_events = [e for e in timeline.processed if e.kind == Kind.PRESENCE_ENDED]
        self.assertEqual(len(end_events), 1)
        end = end_events[0]
        self.assertEqual(end.effective_at, absence.effective_at)
        self.assertEqual(end.detected_at, absence.received_at)
        self.assertEqual(end.booking_at, at(8))
        self.assertEqual(ended.gang_id, g1.gang_id)
        self.assertEqual(ended.end_event_id, end.event_id)
        self.assertEqual(ended.end_reason, "presence_exit")
        self.assertEqual(ended.ended_at, at(8))
        self.assertEqual(timeline.gang_count, 1)
        self.assertEqual(c.session.after_run.requested_at, at(8))
        expected = [self.cycle(
            "o1", "c1", 1, 2, role="entry", gang_id=g1.gang_id,
            links=({"gang_id": g1.gang_id, "role": "entry"},),
        ), self.cycle(
            "o2", "c2", 4, 6, role="exit", gang_id=g1.gang_id,
            links=({"gang_id": g1.gang_id, "role": "exit"},),
            ventilation=(5, "v2"),
        )]
        self.assert_object_and_plain(c.session, expected)
        self.assertFalse(c.consume_occupancy_input(selected_occupancy(absence, at(8))))
        self.assertEqual(c.session.timeline, timeline)
        self.assert_object_and_plain(c.session, expected)

    async def test_retracted_provisional_cycle_has_no_gang_roles(self):
        c = controller()
        for source in (
            event("o1", Kind.DOOR_OPEN, 1),
            event("c1", Kind.DOOR_CLOSE, 2),
            event("person", Kind.PERSON_STRONG, 3),
        ):
            c.process(source)
        provisional = c.session.timeline.active
        self.assertEqual(provisional.confirmation, Confirmation.PROVISIONAL)
        deadline = provisional.started_at + timedelta(
            seconds=c.parameters.seconds("confirmation_minutes"),
        )
        c.advance(deadline)
        timeline = c.session.timeline
        self.assertIsNone(timeline.active)
        self.assertEqual(timeline.retracted, (provisional,))
        self.assertEqual(timeline.completed, ())
        self.assertEqual(timeline.gang_count, 0)
        self.assertIsNone(c.session.after_run)
        self.assert_object_and_plain(c.session, [self.cycle("o1", "c1", 1, 2)])

    async def test_operation_off_preserves_entry_without_an_exit_role(self):
        c = controller()
        for source in (
            event("o1", Kind.DOOR_OPEN, 1),
            event("c1", Kind.DOOR_CLOSE, 2),
            event("w1", Kind.INFUSION, 3),
        ):
            c.process(source)
        g1 = c.session.timeline.active
        c.set_operation(False, at(4))
        timeline = c.session.timeline
        self.assertIsNone(timeline.active)
        self.assertEqual(len(timeline.completed), 1)
        self.assertEqual(timeline.gang_count, 1)
        ended = timeline.completed[0]
        off = timeline.processed[-1]
        self.assertEqual(off.kind, Kind.OPERATION_OFF)
        self.assertEqual(ended.gang_id, g1.gang_id)
        self.assertEqual(ended.ended_at, off.booking_at)
        self.assertEqual(ended.ended_at, at(4))
        self.assertEqual(ended.end_event_id, off.event_id)
        self.assertEqual(ended.end_reason, "ausgeschaltet")
        self.assertEqual(timeline.door, Door.CLOSED)
        self.assertFalse(c.session.operation_enabled)
        self.assert_object_and_plain(c.session, [self.cycle(
            "o1", "c1", 1, 2, role="entry", gang_id=g1.gang_id,
            links=({"gang_id": g1.gang_id, "role": "entry"},),
        )])


if __name__ == "__main__":
    unittest.main()
