"""Eintritt braucht beide Türkanten; Austritt Öffnung, Abwesenheit und Lüftung."""
from dataclasses import replace
import unittest

from test_cooling import at, controller
from test_foundation import event, selected_occupancy

from custom_components.ha_sauna.archive import plain, session_has_gangs
from custom_components.ha_sauna.core.presence import binary_presence
from custom_components.ha_sauna.core.timeline import Confirmation, Door, Event, Kind


class DirectPresenceControlTests(unittest.TestCase):
    def setUp(self):
        self.c = controller()

    def report(self, state, second, *, effective=None, source=None):
        return self.c.consume_occupancy_input(selected_occupancy(binary_presence(
            source or "binary_sensor.presence", state,
            at(second if effective is None else effective), at(second),
        ), at(second)))

    def door(self, kind, second):
        return self.c.process(
            event(f"{kind}:{second}", kind, second), person_evidence_qualified=False,
        )

    def start(self):
        self.door(Kind.DOOR_OPEN, 1)
        self.report("on", 2)
        self.assertIsNone(self.c.session.timeline.active)
        self.door(Kind.DOOR_CLOSE, 3)
        self.assertEqual(self.c.session.timeline.active.confirmation, Confirmation.CONFIRMED)

    def test_presence_and_unpaired_closure_cannot_start(self):
        self.report("on", 1)
        with self.assertRaises(ValueError):
            self.door(Kind.DOOR_CLOSE, 2)
        self.c.advance(at(200))
        self.assertIsNone(self.c.session.timeline.active)
        self.assertFalse(self.c.regulation_inputs.temporary_door_heat)

    def test_complete_entry_starts_without_delay_or_infusion(self):
        self.start()
        gang = self.c.session.timeline.active
        self.assertEqual(gang.started_at, at(3))
        self.assertEqual(gang.detected_at, at(3))
        self.assertEqual(gang.infusion_events, ())
        self.assertTrue(session_has_gangs(plain(self.c.session)))
        self.c.advance(at(800))
        self.assertEqual(self.c.session.timeline.active.gang_id, gang.gang_id)
        self.assertEqual(sum(e.kind == "gang_confirmed" for e in self.c.consumer_events), 1)

    def test_absence_without_new_door_never_ends_round(self):
        self.start()
        self.report("off", 4)
        self.c.advance(at(1000))
        self.assertIsNotNone(self.c.session.timeline.active)
        self.assertEqual(self.c.session.timeline.gang_count, 0)
        self.assertTrue(self.c.regulation_inputs.gang_heat_demand)

    def test_exit_opening_and_absence_end_without_close_and_count_once(self):
        self.start()
        self.door(Kind.DOOR_OPEN, 5)
        self.assertIsNotNone(self.c.session.timeline.active)
        self.report("off", 6)
        self.assertIsNotNone(self.c.session.timeline.active)
        self.assertIsNone(self.c.session.after_run)
        self.door(Kind.VENTILATION, 7)
        self.assertIsNone(self.c.session.timeline.active)
        self.assertEqual(self.c.session.timeline.gang_count, 1)
        self.assertEqual(self.c.session.timeline.completed[0].ended_at, at(7))
        self.assertEqual(self.c.session.after_run.requested_at, at(7))
        self.door(Kind.DOOR_CLOSE, 8)
        self.assertTrue(self.c.session.timeline.entry_cycle_available)
        self.report("on", 9)
        self.assertIsNone(self.c.session.timeline.active)
        self.assertEqual(self.c.session.timeline.gang_count, 1)

    def test_delayed_absence_after_exit_close(self):
        self.start()
        self.door(Kind.DOOR_OPEN, 5)
        self.door(Kind.VENTILATION, 5.5)
        self.door(Kind.DOOR_CLOSE, 6)
        self.assertIsNotNone(self.c.session.timeline.active)
        self.report("off", 7, effective=5.5)
        self.assertEqual(self.c.session.timeline.completed[0].ended_at, at(7))
        self.assertEqual(self.c.session.after_run.requested_at, at(7))



    def test_delayed_opening_matches_absence_without_a_close(self):
        self.start()
        self.report("off", 6)
        self.assertIsNotNone(self.c.session.timeline.active)
        opening = Event("late-exit-open", "s", Kind.DOOR_OPEN, at(5), at(7))
        self.c.process(opening)
        self.assertIsNotNone(self.c.session.timeline.active)
        self.door(Kind.VENTILATION, 8)
        self.assertIsNone(self.c.session.timeline.active)
        self.assertEqual(self.c.session.timeline.completed[0].ended_at, at(8))
        self.assertEqual(self.c.session.timeline.gang_count, 1)

    def test_leaving_is_seen_before_the_thermal_opening_hint(self):
        self.start()
        # The occupant has left; thermal door recognition follows the faster
        # presence report. Neither report alone establishes ventilation.
        self.report("off", 4)
        self.door(Kind.DOOR_OPEN, 5)
        self.c.advance(at(6))
        self.assertIsNotNone(self.c.session.timeline.active)
        self.assertIsNone(self.c.session.after_run)
        self.door(Kind.VENTILATION, 6.5)
        self.assertIsNone(self.c.session.timeline.active)
        self.assertEqual(self.c.session.timeline.completed[0].ended_at, at(6.5))
        self.assertEqual(self.c.session.after_run.requested_at, at(6.5))

    def test_unknown_never_finishes_and_cannot_heat_from_round(self):
        self.start()
        self.door(Kind.DOOR_OPEN, 5)
        self.report("unavailable", 6)
        self.door(Kind.VENTILATION, 6.5)
        self.door(Kind.DOOR_CLOSE, 7)
        self.assertIsNotNone(self.c.session.timeline.active)
        self.assertFalse(self.c.regulation_inputs.gang_heat_demand)
        self.assertEqual(self.c.session.timeline.gang_count, 0)

    def test_ventilation_before_absence_ends_at_presence_receipt(self):
        self.start()
        self.door(Kind.DOOR_OPEN, 5)
        self.door(Kind.VENTILATION, 6)
        self.assertIsNotNone(self.c.session.timeline.active)
        self.assertIsNone(self.c.session.after_run)
        self.report("off", 9, effective=7)
        self.assertEqual(self.c.session.timeline.completed[0].ended_at, at(9))
        self.assertEqual(self.c.session.after_run.requested_at, at(9))
        self.assertEqual(self.c.session.timeline.door, Door.OPEN)

    def test_current_presence_must_be_absent_when_ventilation_completes(self):
        for state in ("on", "unknown", "unavailable"):
            with self.subTest(state=state):
                self.setUp()
                self.start()
                self.door(Kind.DOOR_OPEN, 5)
                self.report("off", 6)
                self.report(state, 7)
                self.door(Kind.VENTILATION, 8)
                self.assertIsNotNone(self.c.session.timeline.active)
                self.assertEqual(self.c.session.timeline.completed, ())
                self.assertIsNone(self.c.session.after_run)
                self.assertFalse(any(e.kind == "gang_ended" for e in self.c.consumer_events))
                self.report("off", 9)
                self.assertEqual(self.c.session.timeline.completed[0].ended_at, at(9))
                self.assertEqual(self.c.session.after_run.requested_at, at(9))

    def test_ventilation_with_present_or_unavailable_sensor_does_not_end(self):
        for state in ("on", "unknown", "unavailable"):
            with self.subTest(state=state):
                self.setUp()
                self.start()
                self.door(Kind.DOOR_OPEN, 5)
                self.report(state, 6)
                self.door(Kind.VENTILATION, 7)
                self.c.advance(at(8))
                self.assertIsNotNone(self.c.session.timeline.active)
                self.assertEqual(self.c.session.timeline.gang_count, 0)
                self.assertIsNone(self.c.session.after_run)

    def test_previous_opening_ventilation_cannot_complete_new_exit(self):
        self.start()
        self.door(Kind.DOOR_OPEN, 5)
        self.door(Kind.VENTILATION, 6)
        self.door(Kind.DOOR_CLOSE, 7)
        self.door(Kind.DOOR_OPEN, 8)
        self.report("off", 9)
        self.assertIsNotNone(self.c.session.timeline.active)
        self.assertIsNone(self.c.session.after_run)
        self.door(Kind.VENTILATION, 10)
        self.assertEqual(self.c.session.timeline.completed[0].ended_at, at(10))
        self.assertEqual(self.c.session.after_run.requested_at, at(10))

    def test_duplicate_exit_inputs_do_not_repeat_completion_or_cooling(self):
        for absence_first in (True, False):
            with self.subTest(absence_first=absence_first):
                self.setUp()
                self.start()
                self.door(Kind.DOOR_OPEN, 5)
                if absence_first:
                    self.report("off", 6)
                    ventilation = event("exit-vent", Kind.VENTILATION, 7)
                    self.c.process(ventilation)
                else:
                    ventilation = event("exit-vent", Kind.VENTILATION, 6)
                    self.c.process(ventilation)
                    self.report("off", 7)
                phase = self.c.session.after_run
                self.assertIsNotNone(phase)
                self.assertFalse(self.c.process(ventilation).changed)
                self.report("off", 8)
                self.report("off", 8)
                self.door(Kind.VENTILATION, 9)
                self.assertEqual(self.c.session.timeline.gang_count, 1)
                self.assertEqual(len(self.c.session.timeline.completed), 1)
                self.assertEqual(self.c.session.timeline.completed[0].ended_at, at(7))
                self.assertEqual(self.c.session.after_run.requested_at, phase.requested_at)
                self.assertEqual(self.c.session.after_run.phase_id, phase.phase_id)
                self.door(Kind.DOOR_CLOSE, 10)
                self.report("on", 11)
                self.assertIsNone(self.c.session.timeline.active)
                self.assertEqual(sum(e.kind == "gang_ended" for e in self.c.consumer_events), 1)
                self.assertEqual(sum(e.kind == "gang_confirmed" for e in self.c.consumer_events), 1)

    def test_delayed_close_cannot_finish_using_historical_absence(self):
        self.start()
        self.door(Kind.DOOR_OPEN, 5)
        self.report("off", 6)
        self.report("on", 8)
        self.c.process(Event("late-vent", "s", Kind.VENTILATION, at(6.5), at(9)))
        self.assertIsNotNone(self.c.session.timeline.active)
        self.c.process(Event("late-exit-close", "s", Kind.DOOR_CLOSE, at(7), at(10)))
        self.assertIsNotNone(self.c.session.timeline.active)
        self.assertEqual(self.c.session.timeline.completed, ())
        self.assertIsNone(self.c.session.after_run)
        self.assertFalse(any(e.kind == "gang_ended" for e in self.c.consumer_events))

    def test_initial_closed_door_does_not_establish_an_entry_cycle(self):
        timeline = self.c.session.timeline
        self.assertEqual(timeline.door, Door.CLOSED)
        self.assertIsNone(timeline.anchor)
        self.assertIsNone(timeline.opening)
        self.assertIsNone(timeline.closed_opening)
        self.assertFalse(timeline.entry_cycle_available)
        self.report("on", 1)
        self.assertIsNone(self.c.session.timeline.active)

    def test_existing_presence_at_complete_entry_starts_at_close(self):
        self.report("on", 1)
        self.door(Kind.DOOR_OPEN, 2)
        self.assertIsNone(self.c.session.timeline.active)
        self.door(Kind.DOOR_CLOSE, 3)
        self.assertEqual(self.c.session.timeline.active.started_at, at(3))
        self.assertEqual(self.c.session.timeline.active.confirmation, Confirmation.CONFIRMED)

    def test_presence_before_delayed_entry_does_not_use_old_door_cycle(self):
        self.report("off", 0)
        self.door(Kind.DOOR_OPEN, 1)
        self.door(Kind.DOOR_CLOSE, 2)
        self.assertIsNone(self.c.session.timeline.active)

        # The real opening precedes ON, but reaches the controller later.
        self.report("on", 302)
        self.assertIsNone(self.c.session.timeline.active)
        self.assertFalse(any(e.kind == "gang_confirmed" for e in self.c.consumer_events))
        self.c.process(Event("real-open", "s", Kind.DOOR_OPEN, at(301), at(303)))
        self.assertIsNone(self.c.session.timeline.active)
        self.c.process(Event("real-close", "s", Kind.DOOR_CLOSE, at(304), at(305)))

        gang = self.c.session.timeline.active
        self.assertIsNotNone(gang)
        self.assertEqual(gang.confirmation, Confirmation.CONFIRMED)
        self.assertEqual(gang.start_source_event_id, "real-close")
        self.assertEqual(gang.started_at, at(304))
        self.assertEqual(gang.detected_at, at(305))
        self.assertEqual(sum(e.kind == "gang_confirmed" for e in self.c.consumer_events), 1)

    def test_presence_at_closure_is_valid_when_processed_after_closure(self):
        for received in (2, 20):
            with self.subTest(received=received):
                self.setUp()
                self.door(Kind.DOOR_OPEN, 1)
                self.door(Kind.DOOR_CLOSE, 3)
                self.assertIsNone(self.c.session.timeline.active)
                self.c.consume_occupancy_input(selected_occupancy(binary_presence(
                    "binary_sensor.presence", "on", at(2), at(received),
                ), at(20)))
                gang = self.c.session.timeline.active
                self.assertIsNotNone(gang)
                self.assertEqual(gang.started_at, at(3))
                self.assertEqual(gang.detected_at, at(20))
                self.assertEqual(gang.start_source_event_id, f"{Kind.DOOR_CLOSE}:3")
                self.c.advance(at(21))
                self.assertEqual(sum(e.kind == "gang_confirmed"
                                     for e in self.c.consumer_events), 1)

    def test_cold_entry_interruption_survives_detector_history_trimming(self):
        for state in ("off", "unknown", "unavailable"):
            with self.subTest(state=state):
                self.setUp()
                minimum = self.c.parameters.values["sauna_min_temperature_c"]
                self.c.set_temperature(minimum - 1, at(0))
                self.door(Kind.DOOR_OPEN, 1)
                self.report("on", 2)
                self.door(Kind.DOOR_CLOSE, 3)
                self.report(state, 4)
                self.report("on", 5)
                self.c.discard_recognition_context_before(at(6))
                self.c.set_temperature(minimum, at(7))
                self.assertIsNone(self.c.session.timeline.active)
                self.door(Kind.DOOR_OPEN, 8)
                self.door(Kind.DOOR_CLOSE, 9)
                self.assertEqual(self.c.session.timeline.active.started_at, at(9))

    def test_presence_level_at_close_controls_entry(self):
        for state, expected in (("on", True), ("off", False),
                                ("unknown", False), ("unavailable", False)):
            with self.subTest(state=state):
                self.setUp()
                self.report("on", 0)
                self.door(Kind.DOOR_OPEN, 1)
                self.report(state, 2)
                self.assertIsNone(self.c.session.timeline.active)
                self.door(Kind.DOOR_CLOSE, 3)
                self.assertEqual(self.c.session.timeline.active is not None, expected)
                self.assertEqual(sum(e.kind == "gang_confirmed" for e in self.c.consumer_events),
                                 int(expected))
                if expected:
                    self.assertEqual(self.c.session.timeline.active.started_at, at(3))

    def test_delayed_close_uses_presence_level_at_effective_closure(self):
        for before_close, after_close, expected in (("on", "off", True),
                                                   ("off", "on", False)):
            with self.subTest(before_close=before_close, after_close=after_close):
                self.setUp()
                self.door(Kind.DOOR_OPEN, 1)
                self.report(before_close, 2)
                self.report(after_close, 5)
                self.c.process(Event("late-close", "s", Kind.DOOR_CLOSE, at(4), at(6)))
                self.assertEqual(self.c.session.timeline.active is not None, expected)
                self.assertEqual(sum(e.kind == "gang_confirmed" for e in self.c.consumer_events),
                                 int(expected))
                self.assertEqual(self.c.session.timeline.completed, ())
                if expected:
                    self.assertEqual(self.c.session.timeline.active.started_at, at(4))
                    self.assertEqual(self.c.session.timeline.active.detected_at, at(6))
                    self.assertEqual(self.c.session.timeline.active.start_source_event_id,
                                     "late-close")
                    self.c.advance(at(7))
                    self.assertIsNotNone(self.c.session.timeline.active)
                    self.assertEqual(self.c.session.timeline.gang_count, 0)

    def test_minimum_temperature_blocks_entry_but_not_exit(self):
        self.c.set_temperature(25, at(0))
        self.door(Kind.DOOR_OPEN, 1)
        self.report("on", 2)
        self.door(Kind.DOOR_CLOSE, 3)
        self.c.set_temperature(70, at(4))
        self.assertEqual(self.c.session.timeline.active.started_at, at(4))
        self.door(Kind.DOOR_OPEN, 5)
        self.report("on", 6)
        self.door(Kind.DOOR_CLOSE, 7)
        self.assertIsNotNone(self.c.session.timeline.active)
        self.c.set_temperature(25, at(8))
        self.door(Kind.DOOR_OPEN, 9)
        self.report("off", 10)
        self.door(Kind.VENTILATION, 10.5)
        self.door(Kind.DOOR_CLOSE, 11)
        self.assertEqual(self.c.session.timeline.gang_count, 1)

    def test_proxy_cannot_start_and_infusion_only_annotates(self):
        with self.assertRaises(ValueError):
            self.door(Kind.DOOR_CLOSE, 1)
        for i, kind in enumerate((Kind.PERSON_STRONG, Kind.INFUSION), 2):
            self.assertFalse(self.door(kind, i).changed)
        self.door(Kind.DOOR_OPEN, 5)
        self.report("on", 6)
        self.door(Kind.DOOR_CLOSE, 7)
        self.door(Kind.INFUSION, 8)
        self.assertEqual(len(self.c.session.timeline.active.infusion_events), 1)
        self.assertEqual(sum(e.kind == "gang_confirmed" for e in self.c.consumer_events), 1)

    def test_door_episode_across_operation_pause_cannot_start(self):
        self.door(Kind.DOOR_OPEN, 1)
        self.c.set_operation(False, at(2))
        self.report("on", 3)
        self.c.set_operation(True, at(4))
        self.door(Kind.DOOR_CLOSE, 5)
        self.assertIsNone(self.c.session.timeline.active)
        self.door(Kind.DOOR_OPEN, 6)
        self.report("on", 7)
        self.door(Kind.DOOR_CLOSE, 8)
        self.assertIsNotNone(self.c.session.timeline.active)

    def test_complete_door_episode_while_off_cannot_start_on_resume(self):
        self.c.set_operation(False, at(1))
        self.door(Kind.DOOR_OPEN, 2)
        self.report("on", 3)
        self.door(Kind.DOOR_CLOSE, 4)
        self.c.set_operation(True, at(5))
        self.assertIsNone(self.c.session.timeline.active)
        self.door(Kind.DOOR_OPEN, 6)
        self.report("off", 7)
        self.report("on", 8)
        self.door(Kind.DOOR_CLOSE, 9)
        self.assertIsNotNone(self.c.session.timeline.active)

    def test_old_selected_report_does_not_replace_current(self):
        self.start()
        self.assertFalse(self.report("off", 5, effective=1))
        self.assertTrue(self.c.regulation_inputs.gang_heat_demand)


    def test_state_gang_requires_current_known_available_qualified_assertion(self):
        for occupancy, available, qualified, demand in (
            ("present", True, True, True),
            ("absent", True, True, True),
            ("unknown", True, True, False),
            ("unknown", False, True, False),
            ("present", True, False, False),
        ):
            with self.subTest(occupancy=occupancy, available=available, qualified=qualified):
                self.setUp()
                self.start()
                gang = self.c.session.timeline.active
                value = selected_occupancy(binary_presence(
                    "binary_sensor.presence", "on", at(4), at(4),
                ), at(4))
                self.c.consume_occupancy_input(replace(
                    value, occupancy=occupancy, available=available,
                    qualified_presence=qualified,
                ))
                self.assertEqual(self.c.regulation_inputs.gang_heat_demand, demand)
                self.assertEqual(self.c.session.timeline.active.gang_id, gang.gang_id)
                if not demand:
                    self.assertNotEqual(self.c.last_decision.reason, "gang_heat_demand")

    def test_unqualified_projection_preserves_qualified_entry_history(self):
        self.c.set_temperature(self.c.parameters.values["sauna_min_temperature_c"] - 1, at(0))
        self.door(Kind.DOOR_OPEN, 1)
        self.report("on", 2)
        self.door(Kind.DOOR_CLOSE, 3)
        history = tuple(self.c._direct_presence_history)
        entry = self.c._presence_entry
        value = selected_occupancy(binary_presence(
            "binary_sensor.presence", "unknown", at(4), at(4),
        ), at(4))
        self.c.consume_occupancy_input(replace(value, qualified_presence=False))
        self.assertEqual(tuple(self.c._direct_presence_history), history)
        self.assertEqual(self.c._presence_entry, entry)
        self.assertIsNone(self.c.session.timeline.active)


    def test_first_usability_preserves_same_time_input_order_across_later_ticks(self):
        for before_close in (False, True):
            with self.subTest(before_close=before_close):
                self.setUp()
                self.door(Kind.DOOR_OPEN, 1)
                value = selected_occupancy(binary_presence(
                    "binary_sensor.presence", "on", at(2), at(2),
                ), at(3))
                value = replace(value, timing=replace(value.timing, first_usable_at=at(3)))
                if before_close:
                    self.c.consume_occupancy_input(value)
                self.door(Kind.DOOR_CLOSE, 3)
                if not before_close:
                    self.c.consume_occupancy_input(value)
                self.c.advance(at(4))
                self.assertEqual(self.c.session.timeline.active is not None, before_close)
                if not before_close:
                    self.door(Kind.DOOR_OPEN, 5)
                    self.door(Kind.DOOR_CLOSE, 6)
                    self.assertEqual(self.c.session.timeline.active.started_at, at(6))


class ProxyDoorControlTests(unittest.TestCase):
    def test_every_proxy_start_needs_a_complete_unused_cycle(self):
        for kind in (Kind.PERSON_STRONG, Kind.PERSON_WEAK, Kind.INFUSION):
            with self.subTest(kind=kind):
                c = controller()
                with self.assertRaises(ValueError):
                    c.process(event("bare-close", Kind.DOOR_CLOSE, 1))
                self.assertFalse(c.recognition_allowed(kind))
                self.assertEqual(c.process(event("bare-signal", kind, 2)).reason,
                                 "entry_context_missing")
                c.process(event("entry-open", Kind.DOOR_OPEN, 3))
                self.assertFalse(c.recognition_allowed(kind))
                self.assertEqual(c.process(event("open-signal", kind, 4)).reason,
                                 "entry_context_missing")
                c.process(event("entry-close", Kind.DOOR_CLOSE, 5))
                self.assertTrue(c.recognition_allowed(kind))
                c.process(event("valid-signal", kind, 6))
                self.assertEqual(c.session.timeline.active.started_at, at(5))
                self.assertEqual(c.session.timeline.active.start_basis, "door_close")
                self.assertFalse(c.session.timeline.entry_cycle_available)

    def test_proxy_cycle_cannot_cross_operation_off(self):
        c = controller()
        c.process(event("o", Kind.DOOR_OPEN, 1))
        c.set_operation(False, at(2))
        c.set_operation(True, at(3))
        c.process(event("c", Kind.DOOR_CLOSE, 4))
        self.assertFalse(c.recognition_allowed(Kind.INFUSION))
        self.assertFalse(c.process(event("stale", Kind.INFUSION, 5)).changed)
        self.assertIsNone(c.session.timeline.active)
        c.process(event("new-o", Kind.DOOR_OPEN, 6))
        c.process(event("new-c", Kind.DOOR_CLOSE, 7))
        c.process(event("new-water", Kind.INFUSION, 8))
        self.assertEqual(c.session.timeline.active.started_at, at(7))

    def test_expired_proxy_entry_cannot_be_restarted_by_infusion(self):
        c = controller()
        c.process(event("o", Kind.DOOR_OPEN, 1))
        c.process(event("c", Kind.DOOR_CLOSE, 2))
        c.process(event("p", Kind.PERSON_STRONG, 3))
        expiry = 2 + c.parameters.seconds("confirmation_minutes")
        c.advance(at(expiry))
        self.assertIsNone(c.session.timeline.active)
        self.assertFalse(c.recognition_allowed(Kind.INFUSION))
        self.assertEqual(c.process(event("water", Kind.INFUSION, expiry + 1)).reason,
                         "entry_context_missing")
        c.process(event("new-o", Kind.DOOR_OPEN, expiry + 2))
        c.process(event("new-c", Kind.DOOR_CLOSE, expiry + 3))
        c.process(event("new-water", Kind.INFUSION, expiry + 4))
        self.assertEqual(c.session.timeline.active.started_at, at(expiry + 3))

    def test_unused_entry_expires_for_all_proxy_recognition_routes(self):
        for kind in (Kind.PERSON_STRONG, Kind.PERSON_WEAK, Kind.INFUSION):
            with self.subTest(kind=kind):
                c = controller()
                c.process(event("o", Kind.DOOR_OPEN, 1))
                c.process(event("c", Kind.DOOR_CLOSE, 2))
                expiry = 3 + c.parameters.seconds("confirmation_minutes")
                c.advance(at(expiry))
                self.assertFalse(c.recognition_allowed(kind))
                self.assertEqual(c.process(event("late", kind, expiry)).reason,
                                 "entry_context_expired")
                self.assertIsNone(c.session.timeline.active)

    def test_ventilation_ends_with_open_door_at_booking_time(self):
        c = controller()
        c.process(event("entry-o", Kind.DOOR_OPEN, 1))
        c.process(event("entry-c", Kind.DOOR_CLOSE, 2))
        c.process(event("person", Kind.PERSON_STRONG, 3))
        original = c.session.timeline.active
        c.process(event("water", Kind.INFUSION, 4))
        self.assertEqual(c.session.timeline.active.gang_id, original.gang_id)
        c.process(event("exit-o", Kind.DOOR_OPEN, 5))
        vent = Event("vent", "s", Kind.VENTILATION, at(6), at(10), at(8))
        c.process(vent)
        self.assertIsNone(c.session.timeline.active)
        self.assertEqual(c.session.timeline.gang_count, 1)
        self.assertEqual(c.session.timeline.completed[0].ended_at, at(8))
        self.assertEqual(c.session.after_run.requested_at, at(8))
        close = Event("exit-c", "s", Kind.DOOR_CLOSE, at(21), at(25), at(23))
        c.process(close)
        self.assertIsNone(c.session.timeline.active)
        self.assertEqual(c.session.timeline.gang_count, 1)
        self.assertEqual(c.session.timeline.completed[0].ended_at, at(8))
        self.assertEqual(c.session.after_run.requested_at, at(8))
        self.assertIn(vent, c.session.timeline.processed)
        self.assertIn(close, c.session.timeline.processed)
        self.assertFalse(c.process(close).changed)
        self.assertEqual(c.session.timeline.gang_count, 1)


class DirectPresenceRuntimeTests(unittest.TestCase):
    def test_pending_detector_ventilation_uses_new_presence_before_catchup(self):
        import asyncio
        from dataclasses import replace
        from datetime import timedelta
        from test_detector import DetectorTests, measurement
        from test_foundation import T0
        from test_presence_regressions import _runtime
        from custom_components.ha_sauna.bindings import Bindings
        from custom_components.ha_sauna.core.models import Position, Quantity
        from custom_components.ha_sauna.runtime import SaunaRuntime

        async def exercise(state):
            clock = [T0]
            configuration = _runtime(clock).configuration
            configuration = replace(configuration, presence_source="ha_presence",
                bindings=Bindings({**configuration.bindings.values,
                                   "presence": "binary_sensor.presence"}))
            runtime = SaunaRuntime(configuration, clock=lambda: clock[0])
            runtime.controller.set_temperature(70, T0)
            await runtime.set_operation(True)
            sid = runtime.session.session_id

            async def presence(value, effective, received):
                clock[0] = T0 + timedelta(seconds=received)
                await runtime.accept_presence(binary_presence(
                    "binary_sensor.presence", value,
                    T0 + timedelta(seconds=effective), clock[0]))

            clock[0] = T0 + timedelta(seconds=1)
            await runtime.receive(Event("entry-open", sid, Kind.DOOR_OPEN, clock[0], clock[0]))
            await presence("on", 2, 2)
            clock[0] = T0 + timedelta(seconds=3)
            await runtime.receive(Event("entry-close", sid, Kind.DOOR_CLOSE, clock[0], clock[0]))
            gang_id = runtime.session.timeline.active.gang_id

            # Reuse the opening's water fall; its later plateau with continuing
            # cooling stays queued until the new presence arrives.
            fixture = DetectorTests()
            detector = fixture._ventilation_detector()
            _, plateau, end, values = fixture._ventilation_curve(detector)
            found = fixture._feed_ventilation(detector, plateau, values)
            self.assertTrue(detector.open)
            self.assertFalse(any(item.kind == Kind.VENTILATION for item in found))
            runtime.detector = detector
            opening_end = plateau
            opening_index = detector.index
            clock[0] = T0 + timedelta(seconds=opening_end)
            runtime.controller.set_temperature(values(plateau)[0], clock[0])
            for index, item in enumerate(found):
                await runtime.receive(Event(f"fixture:{index}", sid, item.kind,
                                            item.effective_at, clock[0]))
            self.assertEqual(runtime.session.timeline.door, Door.OPEN)
            await presence("off", opening_end + .2, opening_end + .2)
            self.assertIsNotNone(runtime.session.timeline.active)
            step = detector.grid_seconds
            for second in range(plateau + step, end + step, step):
                temperature, humidity = values(second)
                for position in (Position.UPPER, Position.LOWER):
                    self.assertTrue(detector.accept(measurement(
                        position, Quantity.TEMPERATURE, temperature, second,
                        measured=T0 + timedelta(seconds=second))))
                    self.assertTrue(detector.accept(measurement(
                        position, Quantity.HUMIDITY, humidity, second,
                        measured=T0 + timedelta(seconds=second))))
            self.assertEqual(detector.index, opening_index)
            self.assertEqual(len(detector.pending), 4 * ((end - plateau) // step))

            received = end + 1
            await presence(state, opening_end + .7, received)
            ventilation = [item for item in runtime.session.timeline.processed
                           if item.kind == Kind.VENTILATION]
            self.assertEqual(len(ventilation), 1)
            self.assertGreater(ventilation[0].effective_at, T0 + timedelta(seconds=opening_end))
            self.assertLess(ventilation[0].effective_at, T0 + timedelta(seconds=received))
            self.assertEqual(ventilation[0].detected_at, T0 + timedelta(seconds=received))
            if state == "off":
                finish = received
            else:
                self.assertEqual(runtime.session.timeline.active.gang_id, gang_id)
                self.assertEqual(runtime.session.timeline.completed, ())
                self.assertIsNone(runtime.session.after_run)
                self.assertFalse(any(item.kind == "gang_ended" for item in runtime.consumer_events))
                await presence("off", received + .5, received + .5)
                finish = received + .5
            self.assertIsNone(runtime.session.timeline.active)
            self.assertEqual(runtime.session.timeline.gang_count, 1)
            self.assertEqual(runtime.session.timeline.completed[0].gang_id, gang_id)
            self.assertEqual(runtime.session.timeline.completed[0].ended_at,
                             T0 + timedelta(seconds=finish))
            self.assertEqual(runtime.session.after_run.requested_at,
                             T0 + timedelta(seconds=finish))
            await presence("off", received + .5, received + .5)
            self.assertEqual(runtime.session.timeline.gang_count, 1)
            self.assertEqual(runtime.session.timeline.completed[0].ended_at,
                             T0 + timedelta(seconds=finish))
            self.assertEqual(runtime.session.after_run.requested_at,
                             T0 + timedelta(seconds=finish))
            self.assertEqual(sum(item.kind == "gang_ended" for item in runtime.consumer_events), 1)
            self.assertEqual(sum(item.kind == "gang_confirmed" for item in runtime.consumer_events), 1)

        for state in ("off", "on", "unknown", "unavailable"):
            with self.subTest(state=state):
                asyncio.run(exercise(state))

    def test_runtime_requires_same_opening_ventilation_and_current_absence(self):
        import asyncio
        from dataclasses import replace
        from datetime import timedelta
        from test_presence_regressions import START, _runtime
        from custom_components.ha_sauna.bindings import Bindings
        from custom_components.ha_sauna.runtime import SaunaRuntime

        async def exercise(scenario):
            clock = [START]
            configuration = _runtime(clock).configuration
            configuration = replace(configuration, presence_source="ha_presence",
                bindings=Bindings({**configuration.bindings.values,
                                   "presence": "binary_sensor.presence"}))
            runtime = SaunaRuntime(configuration, clock=lambda: clock[0])
            runtime.controller.set_temperature(70, START)
            await runtime.set_operation(True)
            sid = runtime.session.session_id

            async def door(name, kind, second, *, effective=None):
                clock[0] = START + timedelta(seconds=second)
                item = Event(name, sid, kind,
                    START + timedelta(seconds=second if effective is None else effective),
                    clock[0])
                await runtime.receive(item)
                return item

            async def presence(state, second, *, effective=None):
                clock[0] = START + timedelta(seconds=second)
                await runtime.accept_presence(binary_presence(
                    "binary_sensor.presence", state,
                    START + timedelta(seconds=second if effective is None else effective),
                    clock[0]))

            await door("entry-open", Kind.DOOR_OPEN, 1)
            await presence("on", 2)
            await door("entry-close", Kind.DOOR_CLOSE, 3)
            gang_id = runtime.session.timeline.active.gang_id
            await door("exit-open", Kind.DOOR_OPEN, 5)
            if scenario == "ventilation_first":
                ventilation = await door("exit-vent", Kind.VENTILATION, 6)
                finish = 7
            elif scenario == "closed_delayed_absence":
                ventilation = await door("exit-vent", Kind.VENTILATION, 6)
                await door("exit-close", Kind.DOOR_CLOSE, 7)
                finish = 9
            elif scenario == "old_ventilation":
                await door("old-vent", Kind.VENTILATION, 6)
                await door("old-close", Kind.DOOR_CLOSE, 7)
                await door("new-exit-open", Kind.DOOR_OPEN, 8)
                await presence("off", 9)
                self.assertIsNotNone(runtime.session.timeline.active)
                self.assertIsNone(runtime.session.after_run)
                ventilation = await door("exit-vent", Kind.VENTILATION, 10)
                finish = 10
            else:
                await presence("off", 6)
                if scenario in ("on", "unknown", "unavailable"):
                    await presence(scenario, 7)
                    ventilation = await door("exit-vent", Kind.VENTILATION, 8)
                    finish = 9
                else:
                    self.assertIsNotNone(runtime.session.timeline.active)
                    self.assertIsNone(runtime.session.after_run)
                    ventilation = await door("exit-vent", Kind.VENTILATION, 7)
                    finish = 7
            if scenario in ("ventilation_first", "closed_delayed_absence",
                            "on", "unknown", "unavailable"):
                self.assertIsNotNone(runtime.session.timeline.active)
                self.assertIsNone(runtime.session.after_run)
                await presence("off", finish,
                               effective=6.5 if scenario == "closed_delayed_absence" else None)
            self.assertIsNone(runtime.session.timeline.active)
            self.assertEqual(runtime.session.timeline.gang_count, 1)
            self.assertEqual(runtime.session.timeline.completed[0].gang_id, gang_id)
            self.assertEqual(runtime.session.timeline.completed[0].ended_at,
                             START + timedelta(seconds=finish))
            self.assertEqual(runtime.session.after_run.requested_at,
                             START + timedelta(seconds=finish))
            await runtime.receive(ventilation)
            await presence("off", finish + 1)
            await presence("off", finish + 1)
            self.assertEqual(runtime.session.timeline.gang_count, 1)
            self.assertEqual(runtime.session.after_run.requested_at,
                             START + timedelta(seconds=finish))
            self.assertEqual(sum(e.kind == "gang_ended" for e in runtime.consumer_events), 1)
            self.assertEqual(sum(e.kind == "gang_confirmed" for e in runtime.consumer_events), 1)

        for scenario in ("absence_first", "ventilation_first", "closed_delayed_absence",
                         "old_ventilation", "on", "unknown", "unavailable"):
            with self.subTest(scenario=scenario):
                asyncio.run(exercise(scenario))


    def test_presence_before_delayed_entry_uses_only_the_real_door_cycle(self):
        import asyncio
        from dataclasses import replace
        from datetime import timedelta
        from test_presence_regressions import START, _runtime
        from custom_components.ha_sauna.bindings import Bindings
        from custom_components.ha_sauna.runtime import SaunaRuntime

        async def exercise():
            clock = [START]
            configuration = _runtime(clock).configuration
            configuration = replace(configuration, presence_source="ha_presence",
                bindings=Bindings({**configuration.bindings.values,
                                   "presence": "binary_sensor.presence"}))
            runtime = SaunaRuntime(configuration, clock=lambda: clock[0])
            runtime.controller.set_temperature(70, START)
            await runtime.set_operation(True)
            sid = runtime.session.session_id

            async def door(name, kind, effective, detected):
                clock[0] = START + timedelta(seconds=detected)
                await runtime.receive(Event(name, sid, kind,
                    START + timedelta(seconds=effective), clock[0]))

            async def presence(state, second):
                clock[0] = START + timedelta(seconds=second)
                await runtime.accept_presence(binary_presence(
                    "binary_sensor.presence", state, clock[0], clock[0]))

            await presence("off", 0)
            await door("unused-open", Kind.DOOR_OPEN, 1, 1)
            await door("unused-close", Kind.DOOR_CLOSE, 2, 2)
            self.assertIsNone(runtime.session.timeline.active)
            await presence("on", 302)
            self.assertIsNone(runtime.session.timeline.active)
            self.assertFalse(any(e.kind == "gang_confirmed" for e in runtime.consumer_events))
            await door("real-open", Kind.DOOR_OPEN, 301, 303)
            self.assertIsNone(runtime.session.timeline.active)
            await door("real-close", Kind.DOOR_CLOSE, 304, 305)
            gang = runtime.session.timeline.active
            self.assertIsNotNone(gang)
            self.assertEqual(gang.confirmation, Confirmation.CONFIRMED)
            self.assertEqual(gang.start_source_event_id, "real-close")
            self.assertEqual(gang.started_at, START + timedelta(seconds=304))
            self.assertEqual(gang.detected_at, START + timedelta(seconds=305))
            self.assertEqual(sum(e.kind == "gang_confirmed" for e in runtime.consumer_events), 1)

            await door("exit-open", Kind.DOOR_OPEN, 400, 400)
            await presence("off", 401)
            self.assertIsNotNone(runtime.session.timeline.active)
            await door("exit-vent", Kind.VENTILATION, 402, 402)
            self.assertIsNone(runtime.session.timeline.active)
            self.assertEqual(runtime.session.timeline.gang_count, 1)
            self.assertEqual(runtime.session.timeline.completed[0].start_source_event_id,
                             "real-close")
            self.assertEqual(runtime.session.timeline.completed[0].ended_at,
                             START + timedelta(seconds=402))
            self.assertEqual(sum(e.kind == "gang_ended" for e in runtime.consumer_events), 1)

        asyncio.run(exercise())

    def test_selected_source_drives_controller_archive_and_status(self):
        import asyncio
        from dataclasses import replace
        from datetime import timedelta
        from test_presence_regressions import START, _runtime
        from custom_components.ha_sauna.bindings import Bindings
        from custom_components.ha_sauna.runtime import SaunaRuntime
        from custom_components.ha_sauna.core.timeline import Event

        async def exercise():
            clock = [START]
            configuration = _runtime(clock).configuration
            configuration = replace(configuration, presence_source="ha_presence",
                bindings=Bindings({**configuration.bindings.values,
                                   "presence":"binary_sensor.presence"}))
            runtime = SaunaRuntime(configuration, clock=lambda:clock[0])
            runtime.controller.set_temperature(70, START)
            await runtime.set_operation(True)
            sid = runtime.session.session_id
            clock[0] = START + timedelta(seconds=1)
            runtime._process_event(Event("entry-open", sid, Kind.DOOR_OPEN, clock[0], clock[0]))
            clock[0] += timedelta(seconds=1)
            await runtime.accept_presence(binary_presence("binary_sensor.presence", "on", clock[0], clock[0]))
            self.assertIsNone(runtime.session.timeline.active)
            clock[0] += timedelta(seconds=1)
            runtime._process_event(Event("entry-close", sid, Kind.DOOR_CLOSE, clock[0], clock[0]))
            runtime.persist()
            self.assertEqual(runtime.session.timeline.active.confirmation, Confirmation.CONFIRMED)
            self.assertEqual(runtime.configuration_model.presence_source, "ha_presence")
            self.assertEqual(runtime.presence_status["current"].occupancy, "present")
            self.assertEqual(sum(e.kind == "gang_confirmed" for e in runtime.consumer_events), 1)
        asyncio.run(exercise())
