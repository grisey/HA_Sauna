"""Eintritt braucht beide Türkanten und Präsenz beim Schluss; Austritt durch Lüften oder leere Kabine."""
from dataclasses import replace
import unittest

from test_cooling import at, controller
from test_foundation import event, selected_occupancy

from custom_components.ha_sauna.archive import plain, session_has_gangs
from custom_components.ha_sauna.core.display import phase_timer, start_availability
from custom_components.ha_sauna.core.light_control import light_phase
from custom_components.ha_sauna.core.presence import binary_presence
from custom_components.ha_sauna.core.timeline import Confirmation, Door, DoorCloseEvidence, Event, Kind


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

    def enter(self):
        self.door(Kind.DOOR_OPEN, 1)
        self.report("on", 2)
        self.assertIsNone(self.c.session.timeline.active)
        self.door(Kind.DOOR_CLOSE, 3)
        gang = self.c.session.timeline.active
        self.assertEqual(gang.recognition_kind, Kind.PRESENCE_ENTRY)
        self.assertEqual(gang.confirmation, Confirmation.PROVISIONAL)
        return gang

    def start(self):
        self.enter()
        self.door(Kind.INFUSION, 3.5)
        self.assertEqual(self.c.session.timeline.active.confirmation, Confirmation.CONFIRMED)

    def test_presence_and_unpaired_closure_cannot_start(self):
        self.report("on", 1)
        with self.assertRaises(ValueError):
            self.door(Kind.DOOR_CLOSE, 2)
        self.c.advance(at(200))
        self.assertIsNone(self.c.session.timeline.active)
        self.assertFalse(self.c.regulation_inputs.temporary_door_heat)

    def test_presence_at_the_closing_opens_a_provisional_gang_without_delay(self):
        gang = self.enter()
        self.assertEqual(gang.started_at, at(3))
        self.assertEqual(gang.detected_at, at(3))
        # Like every provisional gang, it carries the gang heat demand.
        self.assertTrue(self.c.regulation_inputs.gang_heat_demand)
        self.assertFalse(any(e.kind == "gang_confirmed" for e in self.c.consumer_events))
        self.door(Kind.INFUSION, 4)
        gang = self.c.session.timeline.active
        self.assertEqual(gang.confirmation, Confirmation.CONFIRMED)
        self.assertEqual(gang.started_at, at(3))
        self.assertTrue(session_has_gangs(plain(self.c.session)))
        self.assertTrue(self.c.regulation_inputs.gang_heat_demand)
        self.c.advance(at(800))
        self.assertEqual(self.c.session.timeline.active.gang_id, gang.gang_id)
        self.assertEqual(sum(e.kind == "gang_confirmed" for e in self.c.consumer_events), 1)

    def effects(self, second):
        """Gang heat, phase, light and display of the active gang at ``second``."""
        return (
            self.c.regulation_inputs.gang_heat_demand,
            self.c.phase == "saunagang",
            light_phase(self.c, at(second))[1] == "saunagang",
            phase_timer(self.c, at(second))["kind"] == "gang",
            start_availability(self.c, at(second))["blocker"] == {"kind": "gang"},
        )

    def test_unconfirmed_gang_has_no_deadline_in_sensor_operation(self):
        gang = self.enter()
        self.assertNotIn("confirmation", {d.purpose for d in self.c.session.deadlines})
        late = 3 + 2 * self.c.parameters.seconds("confirmation_minutes")
        self.c.advance(at(late))
        self.assertEqual(self.c.session.timeline.active.gang_id, gang.gang_id)
        self.assertEqual(self.c.session.timeline.retracted, ())
        self.assertEqual(self.effects(late), (True,) * 5)
        # A late first infusion still confirms the stay from its closing.
        self.door(Kind.INFUSION, late + 1)
        gang = self.c.session.timeline.active
        self.assertEqual(gang.confirmation, Confirmation.CONFIRMED)
        self.assertEqual(gang.started_at, at(3))

    def test_provisional_gang_acts_only_while_presence_is_reported(self):
        gang = self.enter()
        self.assertEqual(self.effects(3), (True,) * 5)
        self.report("off", 4)
        self.assertEqual(self.effects(4), (False,) * 5)
        self.assertEqual(self.c.phase, "aufheizen")
        self.assertNotEqual(self.c.last_decision.reason, "gang_heat_demand")
        # The gang itself remains until its infusion, replacement or ventilation.
        late = 4 + 2 * self.c.parameters.seconds("confirmation_minutes")
        self.c.advance(at(late))
        self.assertEqual(self.c.session.timeline.active.gang_id, gang.gang_id)
        self.assertEqual(self.effects(late), (False,) * 5)
        self.report("on", late + 1)
        self.assertEqual(self.effects(late + 1), (True,) * 5)
        self.assertEqual(self.c.last_decision.reason, "gang_heat_demand")
        # A confirmed gang acts without that condition.
        self.report("off", late + 2)
        self.door(Kind.INFUSION, late + 3)
        self.assertEqual(self.c.session.timeline.active.confirmation, Confirmation.CONFIRMED)
        self.assertEqual(self.effects(late + 3), (True,) * 5)

    def test_unknown_keeps_the_last_presence_for_the_provisional_effect(self):
        self.enter()
        self.report("unavailable", 4)
        self.assertEqual(self.c.phase, "saunagang")
        # The gang heat still needs a current known state.
        self.assertFalse(self.c.regulation_inputs.gang_heat_demand)
        self.report("off", 5)
        self.report("unknown", 6)
        self.assertEqual(self.c.phase, "aufheizen")
        self.report("on", 7)
        self.assertEqual(self.effects(7), (True,) * 5)

    def test_absent_provisional_gang_ends_by_ventilation(self):
        gang = self.enter()
        self.report("off", 4)
        self.c.advance(at(999))
        self.assertEqual(self.c.session.timeline.active.gang_id, gang.gang_id)
        self.door(Kind.DOOR_OPEN, 1000)
        self.door(Kind.VENTILATION, 1001)
        self.assertIsNone(self.c.session.timeline.active)
        self.assertEqual(self.c.session.timeline.retracted[-1].gang_id, gang.gang_id)
        self.assertEqual(self.c.session.timeline.gang_count, 0)
        self.assertIsNone(self.c.session.after_run)

    def test_infusion_after_presence_through_the_exit_opens_a_gang_without_deadline(self):
        self.start()
        self.door(Kind.DOOR_OPEN, 5)
        self.door(Kind.VENTILATION, 6)
        self.door(Kind.DOOR_CLOSE, 7)
        late = 7 + 2 * self.c.parameters.seconds("confirmation_minutes")
        self.c.advance(at(late))
        self.assertIsNone(self.c.session.timeline.active)
        self.door(Kind.INFUSION, late + 1)
        gang = self.c.session.timeline.active
        self.assertEqual(gang.confirmation, Confirmation.CONFIRMED)
        self.assertEqual(gang.started_at, at(7))
        self.assertIsNone(self.c.session.after_run)

    def test_ventilation_retracts_an_unconfirmed_gang(self):
        gang = self.enter()
        self.door(Kind.DOOR_OPEN, 5)
        self.door(Kind.VENTILATION, 6)
        self.assertIsNone(self.c.session.timeline.active)
        self.assertEqual(self.c.session.timeline.retracted[-1].gang_id, gang.gang_id)
        self.assertEqual(self.c.session.timeline.gang_count, 0)
        self.assertIsNone(self.c.session.after_run)

    def test_absence_without_new_door_never_ends_round(self):
        self.start()
        self.report("off", 4)
        self.c.advance(at(1000))
        self.assertIsNotNone(self.c.session.timeline.active)
        self.assertEqual(self.c.session.timeline.gang_count, 0)
        self.assertTrue(self.c.regulation_inputs.gang_heat_demand)

    def test_absence_after_an_opening_does_not_end_the_gang(self):
        self.start()
        self.door(Kind.DOOR_OPEN, 5)
        self.report("off", 6)
        self.door(Kind.DOOR_CLOSE, 7)
        self.c.advance(at(4000))
        self.assertIsNotNone(self.c.session.timeline.active)
        self.assertEqual(self.c.session.timeline.completed, ())
        self.assertIsNone(self.c.session.after_run)

    def test_absence_after_an_opening_ends_only_the_gang_heat_until_presence(self):
        self.start()
        gang = self.c.session.timeline.active
        self.door(Kind.DOOR_OPEN, 5)
        self.assertTrue(self.c.regulation_inputs.gang_heat_demand)
        self.report("off", 6)
        self.assertFalse(self.c.regulation_inputs.gang_heat_demand)
        self.assertNotEqual(self.c.last_decision.reason, "gang_heat_demand")
        # Neither a closing without presence nor a further opening restores it.
        self.door(Kind.DOOR_CLOSE, 7)
        self.door(Kind.DOOR_OPEN, 8)
        self.assertFalse(self.c.regulation_inputs.gang_heat_demand)
        self.report("on", 9)
        self.assertTrue(self.c.regulation_inputs.gang_heat_demand)
        self.assertEqual(self.c.last_decision.reason, "gang_heat_demand")
        self.c.advance(at(4000))
        self.assertEqual(self.c.session.timeline.active.gang_id, gang.gang_id)
        self.assertEqual(self.c.session.timeline.completed, ())
        self.assertIsNone(self.c.session.after_run)

    def test_new_closing_with_presence_restores_the_gang_heat(self):
        self.start()
        self.door(Kind.DOOR_OPEN, 5)
        self.report("off", 6)
        self.door(Kind.DOOR_CLOSE, 7)
        self.door(Kind.DOOR_OPEN, 8)
        self.report("on", 9)
        self.door(Kind.DOOR_CLOSE, 10)
        self.assertTrue(self.c.regulation_inputs.gang_heat_demand)
        # Absence after the latest opening of the gang ends it again.
        self.report("off", 11)
        self.assertFalse(self.c.regulation_inputs.gang_heat_demand)

    def test_absence_reported_before_an_opening_keeps_the_gang_heat(self):
        self.start()
        self.report("off", 4)
        self.door(Kind.DOOR_OPEN, 5)
        self.door(Kind.DOOR_CLOSE, 6)
        self.assertTrue(self.c.regulation_inputs.gang_heat_demand)

    def test_absence_counts_from_a_later_booked_opening(self):
        self.start()
        self.report("off", 6)
        self.assertTrue(self.c.regulation_inputs.gang_heat_demand)
        self.c.process(Event("late-open", "s", Kind.DOOR_OPEN, at(5), at(7)))
        self.assertFalse(self.c.regulation_inputs.gang_heat_demand)
        self.assertIsNotNone(self.c.session.timeline.active)

    def test_exit_opening_and_ventilation_end_without_close_and_count_once(self):
        self.start()
        self.door(Kind.DOOR_OPEN, 5)
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

    def test_delayed_opening_matches_ventilation_without_a_close(self):
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

    def test_unknown_never_finishes_and_cannot_heat_from_round(self):
        self.start()
        self.door(Kind.DOOR_OPEN, 5)
        self.report("unavailable", 6)
        self.door(Kind.DOOR_CLOSE, 7)
        self.c.advance(at(200))
        self.assertIsNotNone(self.c.session.timeline.active)
        self.assertFalse(self.c.regulation_inputs.gang_heat_demand)
        self.assertEqual(self.c.session.timeline.gang_count, 0)

    def test_ventilation_ends_the_gang_with_every_sensor_state(self):
        for state in ("off", "on", "unknown", "unavailable"):
            with self.subTest(state=state):
                self.setUp()
                self.start()
                self.door(Kind.DOOR_OPEN, 5)
                self.report(state, 5.5)
                self.door(Kind.VENTILATION, 6)
                self.assertIsNone(self.c.session.timeline.active)
                self.assertEqual(self.c.session.timeline.completed[0].ended_at, at(6))
                self.assertEqual(self.c.session.timeline.completed[0].end_reason, "ventilation")
                self.assertEqual(self.c.session.after_run.requested_at, at(6))
                self.assertEqual(self.c.session.timeline.door, Door.OPEN)

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
                self.assertEqual(self.c.session.timeline.completed[0].ended_at,
                                 at(7) if absence_first else at(6))
                self.assertEqual(self.c.session.after_run.requested_at, phase.requested_at)
                self.assertEqual(self.c.session.after_run.phase_id, phase.phase_id)
                self.door(Kind.DOOR_CLOSE, 10)
                self.report("on", 11)
                self.assertIsNone(self.c.session.timeline.active)
                self.assertEqual(sum(e.kind == "gang_ended" for e in self.c.consumer_events), 1)
                self.assertEqual(sum(e.kind == "gang_confirmed" for e in self.c.consumer_events), 1)

    def test_open_door_presence_pulse_does_not_change_the_ventilated_exit(self):
        self.start()
        self.door(Kind.DOOR_OPEN, 5)
        self.report("off", 6)
        self.report("on", 7)
        self.c.discard_recognition_context_before(at(7.5))
        self.door(Kind.VENTILATION, 8)
        self.assertIsNone(self.c.session.timeline.active)
        self.assertEqual(self.c.session.timeline.completed[0].ended_at, at(8))
        self.assertEqual(self.c.session.after_run.requested_at, at(8))
        self.report("off", 9)
        self.assertEqual(self.c.session.timeline.gang_count, 1)

    def test_presence_at_return_close_starts_a_new_stay_confirmed_by_infusion(self):
        self.start()
        first = self.c.session.timeline.active.gang_id
        self.door(Kind.DOOR_OPEN, 5)
        self.report("off", 6)
        self.report("on", 7)
        self.door(Kind.VENTILATION, 8)
        self.door(Kind.DOOR_CLOSE, 9)
        second = self.c.session.timeline.active
        self.assertNotEqual(second.gang_id, first)
        self.assertEqual(second.started_at, at(9))
        self.assertEqual(second.confirmation, Confirmation.PROVISIONAL)
        self.assertEqual(self.c.session.timeline.completed[0].ended_at, at(8))
        # Already the provisional gang ends the cooling (Nutzer 02.11.).
        self.assertIsNone(self.c.session.after_run)
        self.door(Kind.INFUSION, 10)
        self.assertEqual(self.c.session.timeline.active.confirmation, Confirmation.CONFIRMED)
        self.report("off", 11)
        self.assertEqual(self.c.session.timeline.active.gang_id, second.gang_id)

    def test_presence_through_the_ventilated_exit_is_no_new_entry(self):
        self.start()
        self.door(Kind.DOOR_OPEN, 5)
        self.door(Kind.VENTILATION, 6)
        self.door(Kind.DOOR_CLOSE, 7)
        # The departing person's absence is reported only after the closing.
        self.report("off", 140)
        self.c.advance(at(900))
        self.assertIsNone(self.c.session.timeline.active)
        self.assertEqual(self.c.session.timeline.retracted, ())
        self.assertEqual(self.c.session.timeline.gang_count, 1)

    def test_delayed_close_preserves_completed_exit_and_uses_presence_at_close(self):
        self.start()
        self.door(Kind.DOOR_OPEN, 5)
        self.report("off", 6)
        self.report("on", 8)
        self.c.process(Event("late-vent", "s", Kind.VENTILATION, at(6.5), at(9)))
        self.assertEqual(self.c.session.timeline.completed[0].ended_at, at(9))
        self.c.process(Event("late-exit-close", "s", Kind.DOOR_CLOSE, at(8.5), at(10),
                                     door_close_evidence=DoorCloseEvidence(at(8.5), at(9.5))))
        self.assertEqual(self.c.session.timeline.active.started_at, at(9))
        self.assertEqual(self.c.session.timeline.active.start_source_event_id, "late-exit-close")
        self.assertEqual(self.c.session.timeline.gang_count, 1)

    def test_late_closed_entry_ends_cooling_on_its_confirmation(self):
        self.start()
        self.door(Kind.DOOR_OPEN, 5)
        self.report("off", 6)
        self.door(Kind.VENTILATION, 7)
        self.report("on", 8)
        self.report("off", 10)
        self.c.process(Event("late-return-close", "s", Kind.DOOR_CLOSE, at(9), at(11),
                             door_close_evidence=DoorCloseEvidence(at(9), at(9.5))))
        self.assertEqual(self.c.session.timeline.active.started_at, at(9))
        self.assertIsNotNone(self.c.session.after_run)
        self.door(Kind.INFUSION, 12)
        self.assertIsNone(self.c.session.after_run)
        self.assertEqual(self.c.phase, "saunagang")

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
        self.assertEqual(self.c.session.timeline.active.recognition_kind, Kind.PRESENCE_ENTRY)
        self.assertEqual(self.c.session.timeline.active.confirmation, Confirmation.PROVISIONAL)

    def test_entry_presence_uses_door_time_not_later_confirmation_grid(self):
        for present_at in (9, 11):
            with self.subTest(present_at=present_at):
                self.setUp()
                self.door(Kind.DOOR_OPEN, 5)
                self.report("on", present_at)
                self.c.process(Event("delayed-close", "s", Kind.DOOR_CLOSE,
                                     at(10), at(13), at(13), at(12),
                                     door_close_evidence=DoorCloseEvidence(at(10), at(12))))
                self.assertEqual(self.c.session.timeline.active is not None, present_at == 9)
                if present_at == 9:
                    self.assertEqual(self.c.session.timeline.active.started_at, at(10))

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
        self.c.process(Event("real-close", "s", Kind.DOOR_CLOSE, at(304), at(305),
                                     door_close_evidence=DoorCloseEvidence(at(304), at(304.5))))

        gang = self.c.session.timeline.active
        self.assertIsNotNone(gang)
        self.assertEqual(gang.confirmation, Confirmation.PROVISIONAL)
        self.assertEqual(gang.start_source_event_id, "real-close")
        self.assertEqual(gang.started_at, at(304))
        self.assertEqual(gang.detected_at, at(305))
        self.assertEqual(sum(e.kind == "gang_started" for e in self.c.consumer_events), 1)

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
                self.assertEqual(sum(e.kind == "gang_started"
                                     for e in self.c.consumer_events), 1)

    def test_closed_cold_entry_keeps_occupancy_until_a_new_door_opening(self):
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
                self.assertEqual(self.c.session.timeline.active.started_at, at(7))
                gang_id = self.c.session.timeline.active.gang_id
                self.door(Kind.INFUSION, 7.5)
                self.door(Kind.DOOR_OPEN, 8)
                self.door(Kind.DOOR_CLOSE, 9)
                self.assertEqual(self.c.session.timeline.active.gang_id, gang_id)

    def test_later_entry_with_presence_replaces_the_unconfirmed_gang(self):
        first = self.enter()
        self.door(Kind.DOOR_OPEN, 5)
        self.door(Kind.DOOR_CLOSE, 6)
        second = self.c.session.timeline.active
        self.assertNotEqual(second.gang_id, first.gang_id)
        self.assertEqual(second.started_at, at(6))
        self.assertEqual(second.confirmation, Confirmation.PROVISIONAL)
        self.assertEqual(self.c.session.timeline.retracted[-1].gang_id, first.gang_id)
        # The replacing gang waits for its infusion without a deadline.
        late = 6 + 2 * self.c.parameters.seconds("confirmation_minutes")
        self.c.advance(at(late - 1))
        self.door(Kind.INFUSION, late - 0.5)
        gang = self.c.session.timeline.active
        self.assertEqual(gang.gang_id, second.gang_id)
        self.assertEqual(gang.confirmation, Confirmation.CONFIRMED)
        self.assertEqual(gang.started_at, at(6))
        self.assertEqual(sum(e.kind == "gang_retracted" for e in self.c.consumer_events), 1)

    def test_later_closing_without_presence_keeps_the_unconfirmed_gang(self):
        first = self.enter()
        self.door(Kind.DOOR_OPEN, 5)
        self.report("off", 6)
        self.door(Kind.DOOR_CLOSE, 7)
        self.assertEqual(self.c.session.timeline.active.gang_id, first.gang_id)
        self.assertEqual(self.c.session.timeline.retracted, ())

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
                self.assertEqual(sum(e.kind == "gang_started" for e in self.c.consumer_events),
                                 int(expected))
                if expected:
                    self.assertEqual(self.c.session.timeline.active.started_at, at(3))

    def test_delayed_close_uses_presence_level_at_effective_closure(self):
        for before_close, after_close, expected in (("on", None, True),
                                                   ("on", "off", True),
                                                   ("off", "on", False)):
            with self.subTest(before_close=before_close, after_close=after_close):
                self.setUp()
                self.door(Kind.DOOR_OPEN, 1)
                self.report(before_close, 2)
                if after_close is not None:
                    self.report(after_close, 5)
                self.c.process(Event("late-close", "s", Kind.DOOR_CLOSE, at(4), at(6),
                                     door_close_evidence=DoorCloseEvidence(at(4), at(4.5))))
                self.assertEqual(self.c.session.timeline.active is not None, expected)
                self.assertEqual(sum(e.kind == "gang_started" for e in self.c.consumer_events),
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
        self.door(Kind.INFUSION, 4.5)
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

    def test_unqualified_absence_after_an_opening_keeps_the_proxy_gang_heat(self):
        c = controller()
        c.process(event("o", Kind.DOOR_OPEN, 1))
        c.process(event("c", Kind.DOOR_CLOSE, 2))
        c.process(event("water", Kind.INFUSION, 3))
        c.process(event("exit-open", Kind.DOOR_OPEN, 4))
        value = selected_occupancy(binary_presence(
            "binary_sensor.presence", "off", at(5), at(5),
        ), at(5))
        c.consume_occupancy_input(replace(value, qualified_presence=False))
        self.assertTrue(c.regulation_inputs.gang_heat_demand)

    def test_proxy_provisional_gang_keeps_its_deadline_and_effects(self):
        c = controller()
        c.process(event("o", Kind.DOOR_OPEN, 1))
        c.process(event("c", Kind.DOOR_CLOSE, 2))
        c.process(event("p", Kind.PERSON_STRONG, 3))
        value = selected_occupancy(binary_presence(
            "binary_sensor.presence", "off", at(4), at(4),
        ), at(4))
        c.consume_occupancy_input(replace(value, qualified_presence=False))
        self.assertEqual(c.session.timeline.active.confirmation, Confirmation.PROVISIONAL)
        self.assertEqual(c.phase, "saunagang")
        self.assertTrue(c.regulation_inputs.gang_heat_demand)
        self.assertIn("confirmation", {d.purpose for d in c.session.deadlines})

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
        from unittest.mock import patch
        from test_foundation import T0
        from test_presence_regressions import _runtime
        from custom_components.ha_sauna.bindings import Bindings
        from custom_components.ha_sauna.core.recognition import Detection
        from custom_components.ha_sauna.runtime import SaunaRuntime

        opening, opening_end, plateau, end = 100, 130, 160, 190

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
            await runtime.receive(Event("entry-close", sid, Kind.DOOR_CLOSE, clock[0], clock[0],
                                        door_close_evidence=DoorCloseEvidence(clock[0], clock[0])))
            clock[0] = T0 + timedelta(seconds=4)
            await runtime.receive(Event("water", sid, Kind.INFUSION, clock[0], clock[0]))
            gang_id = runtime.session.timeline.active.gang_id

            # The exit opening is booked; its ventilation plateau lies in measurements the
            # recognizer has received but not yet evaluated.  It is delivered once the
            # recognizer is advanced over the plateau's confirmation.
            clock[0] = T0 + timedelta(seconds=opening)
            await runtime.receive(Event("exit-open", sid, Kind.DOOR_OPEN, clock[0], clock[0]))
            self.assertEqual(runtime.session.timeline.door, Door.OPEN)
            delivered = []

            def advance(through, **callbacks):
                if through >= T0 + timedelta(seconds=end) and not delivered:
                    delivered.append(through)
                    callbacks["on_detection"](Detection(
                        Kind.VENTILATION, T0 + timedelta(seconds=plateau), through,
                        ("Tu", "Tl", "Au", "Al"), trace_at=through, probability=0.95,
                    ))
                return []

            with patch.object(runtime.detector, "advance", side_effect=advance):
                await presence("off", opening_end + .2, opening_end + .2)
                self.assertIsNotNone(runtime.session.timeline.active)
                self.assertFalse(any(item.kind == Kind.VENTILATION
                                     for item in runtime.session.timeline.processed))

                received = end + 1
                await presence(state, opening_end + .7, received)
                ventilation = [item for item in runtime.session.timeline.processed
                               if item.kind == Kind.VENTILATION]
                self.assertEqual(len(ventilation), 1)
                self.assertGreater(ventilation[0].effective_at, T0 + timedelta(seconds=opening_end))
                self.assertLess(ventilation[0].effective_at, T0 + timedelta(seconds=received))
                self.assertEqual(ventilation[0].detected_at, T0 + timedelta(seconds=received))
                # The pending ventilation ends the gang before the queued
                # report is evaluated, whatever the sensor reports.
                finish = received
                self.assertEqual(runtime.session.timeline.completed[0].end_reason,
                                 "ventilation")
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

    def test_runtime_ends_only_by_ventilation_of_the_exit_opening(self):
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
                    clock[0], door_close_evidence=DoorCloseEvidence(
                        START + timedelta(seconds=second if effective is None else effective),
                        clock[0]) if kind == Kind.DOOR_CLOSE else None)
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
            await door("water", Kind.INFUSION, 4)
            gang_id = runtime.session.timeline.active.gang_id
            await door("exit-open", Kind.DOOR_OPEN, 5)
            if scenario == "absence":
                await presence("off", 6)
            elif scenario == "on":
                await presence("off", 6)
                await presence("on", 7)
            else:
                await presence("off", 6)
                await presence(scenario, 7)
            await presence("off", 900)
            self.assertEqual(runtime.session.timeline.active.gang_id, gang_id)
            self.assertIsNone(runtime.session.after_run)
            ventilation = await door("exit-vent", Kind.VENTILATION, 901)
            self.assertIsNone(runtime.session.timeline.active)
            self.assertEqual(runtime.session.timeline.gang_count, 1)
            completed = runtime.session.timeline.completed[0]
            self.assertEqual(completed.gang_id, gang_id)
            self.assertEqual(completed.end_reason, "ventilation")
            self.assertEqual(completed.ended_at, START + timedelta(seconds=901))
            self.assertEqual(runtime.session.after_run.requested_at,
                             START + timedelta(seconds=901))
            await runtime.receive(ventilation)
            await presence("off", 902)
            self.assertEqual(runtime.session.timeline.gang_count, 1)
            self.assertEqual(sum(e.kind == "gang_ended" for e in runtime.consumer_events), 1)
            self.assertEqual(sum(e.kind == "gang_confirmed" for e in runtime.consumer_events), 1)

        for scenario in ("absence", "on", "unknown", "unavailable"):
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
                    START + timedelta(seconds=effective), clock[0],
                    door_close_evidence=DoorCloseEvidence(
                        START + timedelta(seconds=effective), clock[0])
                    if kind == Kind.DOOR_CLOSE else None))

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
            self.assertEqual(gang.confirmation, Confirmation.PROVISIONAL)
            self.assertEqual(gang.start_source_event_id, "real-close")
            self.assertEqual(gang.started_at, START + timedelta(seconds=304))
            self.assertEqual(gang.detected_at, START + timedelta(seconds=305))
            await door("water", Kind.INFUSION, 306, 306)
            self.assertEqual(runtime.session.timeline.active.confirmation, Confirmation.CONFIRMED)
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
            runtime = SaunaRuntime(configuration, clock=lambda: clock[0])
            runtime.controller.set_temperature(70, START)
            await runtime.set_operation(True)
            sid = runtime.session.session_id
            clock[0] = START + timedelta(seconds=1)
            runtime._process_event(Event("entry-open", sid, Kind.DOOR_OPEN, clock[0], clock[0]))
            clock[0] += timedelta(seconds=1)
            await runtime.accept_presence(binary_presence("binary_sensor.presence", "on", clock[0], clock[0]))
            self.assertIsNone(runtime.session.timeline.active)
            clock[0] += timedelta(seconds=1)
            runtime._process_event(Event("entry-close", sid, Kind.DOOR_CLOSE, clock[0], clock[0],
                                        door_close_evidence=DoorCloseEvidence(clock[0], clock[0])))
            runtime.persist()
            self.assertEqual(runtime.session.timeline.active.recognition_kind,
                             Kind.PRESENCE_ENTRY)
            self.assertEqual(runtime.session.timeline.active.confirmation,
                             Confirmation.PROVISIONAL)
            self.assertEqual(runtime.configuration_model.presence_source, "ha_presence")
            self.assertEqual(runtime.presence_status["current"].occupancy, "present")
            self.assertEqual(sum(e.kind == "gang_started" for e in runtime.consumer_events), 1)
        asyncio.run(exercise())
