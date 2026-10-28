"""Eintritt, Aufenthalt und Rückkehr; Bereitschaft gibt die Nutzung nicht vor.

Die Folgen werden an Türbewegung, Belegungszustand und Temperatur geprüft.
Abgeleitete Gangereignisse werden nicht als Testeingang vorgegeben.
"""

from dataclasses import replace
import unittest

from test_cooling import at, controller
from test_foundation import event, selected_occupancy

from custom_components.ha_sauna.core.presence import binary_presence
from custom_components.ha_sauna.core.timeline import DoorCloseEvidence, Event, Kind


class GangPhaseRulesTests(unittest.TestCase):
    def setUp(self):
        self.c = controller()
        self.minimum = self.c.parameters.values["sauna_min_temperature_c"]

    def presence(self, state, second):
        self.c.consume_occupancy_input(selected_occupancy(binary_presence(
            "binary_sensor.presence", state, at(second), at(second),
        ), at(second)))

    def door(self, kind, second):
        return self.c.process(event(f"{kind.value}:{second}", kind, second))

    def enter(self):
        self.presence("on", 1)
        self.door(Kind.DOOR_OPEN, 2)
        self.door(Kind.DOOR_CLOSE, 3)

    def leave_for_ventilation(self):
        self.door(Kind.DOOR_OPEN, 5)
        self.presence("off", 6)
        self.door(Kind.VENTILATION, 7)
        self.c.report_contactor(False, at(7))
        self.c.report_heating(False, at(7))

    def test_return_close_anchors_to_the_existing_unclosed_exit_episode(self):
        self.enter()
        self.leave_for_ventilation()
        cooling = self.c.session.after_run
        self.presence("on", 8)
        self.assertIsNone(self.c.session.timeline.active)
        self.assertEqual(self.c.session.after_run.phase_id, cooling.phase_id)
        self.door(Kind.DOOR_CLOSE, 9)
        gang = self.c.session.timeline.active
        self.assertEqual(gang.started_at, at(9))
        self.assertEqual(gang.start_source_event_id, "door_close:9")
        self.assertEqual(self.c.session.after_run_history[-1].ends_at, at(9))
        self.assertIsNone(self.c.session.last_completed_oven_cooling_at)

    def test_cooling_continues_until_return_also_reaches_minimum(self):
        self.enter()
        self.leave_for_ventilation()
        cooling = self.c.session.after_run
        self.c.set_temperature(self.minimum - 1, at(8))
        self.presence("on", 9)
        self.door(Kind.DOOR_CLOSE, 10)
        self.assertIsNone(self.c.session.timeline.active)
        self.assertEqual(self.c.session.after_run.phase_id, cooling.phase_id)
        self.c.set_temperature(self.minimum, at(11))
        self.assertEqual(self.c.session.timeline.active.started_at, at(11))
        self.assertIsNone(self.c.session.after_run)
        self.assertEqual(self.c.session.after_run_history[-1].ends_at, at(11))

    def test_an_empty_closure_cannot_become_entry_when_presence_arrives_later(self):
        self.c.set_temperature(self.minimum - 1, at(0))
        self.presence("off", 1)
        self.door(Kind.DOOR_OPEN, 2)
        self.door(Kind.DOOR_CLOSE, 3)
        self.presence("on", 4)
        self.c.set_temperature(self.minimum, at(5))
        self.assertIsNone(self.c.session.timeline.active)

    def test_new_opening_before_minimum_clears_the_previous_pending_entry(self):
        for state in ("off", "unknown", "unavailable"):
            with self.subTest(state=state):
                self.setUp()
                self.c.set_temperature(self.minimum - 1, at(0))
                self.enter()
                self.door(Kind.DOOR_OPEN, 3.5)
                self.presence(state, 4)
                self.presence("on", 5)
                self.c.set_temperature(self.minimum, at(6))
                self.assertIsNone(self.c.session.timeline.active)

    def test_cold_door_cycle_is_observed_without_extra_door_heating(self):
        self.c.set_temperature(self.minimum - 1, at(0))
        self.door(Kind.DOOR_OPEN, 1)
        self.door(Kind.DOOR_CLOSE, 2)
        self.assertEqual([item.kind for item in self.c.session.timeline.processed],
                         [Kind.DOOR_OPEN, Kind.DOOR_CLOSE])
        self.assertFalse(self.c.regulation_inputs.temporary_door_heat)
        self.assertIsNone(self.c.session.timeline.active)

    def test_temperature_at_closure_controls_door_heating_not_door_observation(self):
        self.c.set_temperature(self.minimum - 1, at(0))
        self.door(Kind.DOOR_OPEN, 1)
        self.c.set_temperature(self.minimum, at(2))
        self.door(Kind.DOOR_CLOSE, 3)
        self.assertTrue(self.c.regulation_inputs.temporary_door_heat)
        self.assertIsNone(self.c.session.timeline.active)

    def test_new_gang_ends_cooling_without_clearing_a_heater_fault(self):
        self.enter()
        self.leave_for_ventilation()
        self.c.protection.add("heater_service_unavailable")
        self.presence("on", 8)
        self.door(Kind.DOOR_CLOSE, 9)
        self.assertIsNotNone(self.c.session.timeline.active)
        self.assertIsNone(self.c.session.after_run)
        self.assertFalse(self.c.last_decision.heat)
        self.assertIn("heater_service_unavailable", self.c.protection)

    def test_proxy_return_preserves_cooling_identity_and_elapsed_off_time(self):
        self.door(Kind.DOOR_OPEN, 1)
        self.door(Kind.DOOR_CLOSE, 2)
        self.c.process(event("first-infusion", Kind.INFUSION, 3))
        self.door(Kind.DOOR_OPEN, 5)
        self.door(Kind.VENTILATION, 7)
        self.c.report_contactor(False, at(7))
        self.c.report_heating(False, at(7))
        cooling = self.c.session.after_run
        self.door(Kind.DOOR_CLOSE, 9)
        self.assertIsNotNone(self.c.session.after_run)
        self.assertIsNone(self.c.session.timeline.active)
        self.c.process(event("return-person", Kind.PERSON_STRONG, 10))
        self.assertIsNotNone(self.c.session.timeline.active)
        self.assertEqual(self.c.session.after_run.phase_id, cooling.phase_id)
        self.assertEqual(self.c.session.after_run.started_at, cooling.started_at)
        self.assertEqual(
            self.c.session.after_run.elapsed_seconds, (at(10) - at(7)).total_seconds(),
        )
        adjusted = self.c.session.after_run
        self.c.process(event("return-infusion", Kind.INFUSION, 11))
        self.door(Kind.DOOR_OPEN, 12)
        self.door(Kind.VENTILATION, 13)
        self.assertEqual(self.c.session.timeline.gang_count, 2)
        current = self.c.session.after_run
        self.assertEqual(
            (current.phase_id, current.started_at, current.ends_at, current.duration_seconds),
            (adjusted.phase_id, adjusted.started_at, adjusted.ends_at, adjusted.duration_seconds),
        )
        self.assertEqual(current.elapsed_seconds, (at(13) - at(7)).total_seconds())

    def test_one_time_gang_heat_survives_unknown_and_unavailable_projection(self):
        for confirmed in (False, True):
            for available in (False, True):
                with self.subTest(confirmed=confirmed, available=available):
                    self.setUp()
                    self.door(Kind.DOOR_OPEN, 1)
                    self.door(Kind.DOOR_CLOSE, 2)
                    self.c.process(event("person", Kind.PERSON_STRONG, 3))
                    if confirmed:
                        self.c.process(event("infusion", Kind.INFUSION, 4))
                    gang = self.c.session.timeline.active
                    value = selected_occupancy(binary_presence(
                        "binary_sensor.presence", "unknown", at(5), at(5),
                    ), at(5))
                    self.c.consume_occupancy_input(replace(
                        value, qualified_presence=False, available=available,
                    ))
                    self.assertTrue(self.c.regulation_inputs.gang_heat_demand)
                    self.assertEqual(self.c.last_decision.reason, "gang_heat_demand")
                    self.assertEqual(self.c.session.timeline.active.gang_id, gang.gang_id)
                    self.assertEqual(self.c._direct_presence_history, [])

    def test_proxy_projection_never_repeats_person_cooling_effect(self):
        self.enter()
        self.leave_for_ventilation()
        original = self.c.session.after_run
        self.door(Kind.DOOR_CLOSE, 8)
        value = selected_occupancy(binary_presence(
            "binary_sensor.presence", "on", at(9), at(9),
        ), at(9))
        self.c.consume_occupancy_input(replace(value, qualified_presence=False))
        self.assertFalse(self.c.session.after_run.minimum_duration_requested)
        self.assertEqual(self.c.session.after_run.phase_id, original.phase_id)
        self.c.process(event("new-person", Kind.PERSON_STRONG, 10))
        limited = self.c.session.after_run
        self.assertTrue(limited.minimum_duration_requested)
        self.assertEqual(limited.phase_id, original.phase_id)
        self.assertEqual(limited.started_at, original.started_at)
        self.c.consume_occupancy_input(replace(
            value, qualified_presence=False, report_id="later-projection",
            timing=replace(value.timing, effective_at=at(11), received_at=at(11),
                           evaluated_at=at(11)),
        ))
        self.c.process(event("new-infusion", Kind.INFUSION, 12))
        repeated = self.c.session.after_run
        self.assertIsNotNone(repeated)
        self.assertEqual((repeated.phase_id, repeated.started_at, repeated.ends_at,
                          repeated.required_seconds),
                         (limited.phase_id, limited.started_at, limited.ends_at,
                          limited.required_seconds))
        self.assertEqual(repeated.elapsed_seconds, (at(12) - at(7)).total_seconds())
        self.assertEqual(self.c.last_decision.reason, "after_run")

    def test_proven_return_close_ends_cooling_despite_later_presence_report(self):
        for state in ("off", "unknown", "unavailable"):
            with self.subTest(state=state):
                self.setUp()
                self.enter()
                self.leave_for_ventilation()
                phase = self.c.session.after_run
                self.presence("on", 8)
                self.presence(state, 11)
                self.c.process(Event(
                    "delayed-return-close", "s", Kind.DOOR_CLOSE,
                    at(9), at(12), trace_at=at(10),
                    door_close_evidence=DoorCloseEvidence(at(9), at(10)),
                ))
                self.assertIsNotNone(self.c.session.timeline.active)
                self.assertEqual(self.c.session.timeline.active.recognition_kind,
                                 Kind.PRESENCE_CONFIRMED)
                self.assertEqual(self.c.session.timeline.active.started_at, at(9))
                self.assertIsNone(self.c.session.after_run)
                completed = self.c.session.after_run_history[-1]
                self.assertEqual(completed.phase_id, phase.phase_id)
                self.assertEqual(completed.ends_at, at(12))
                self.assertFalse(completed.minimum_duration_requested)

    def test_return_without_closing_proof_keeps_cooling(self):
        self.enter()
        self.leave_for_ventilation()
        phase = self.c.session.after_run
        self.presence("on", 8)
        self.c.process(Event(
            "unproven-return-close", "s", Kind.DOOR_CLOSE,
            at(9), at(12), trace_at=at(10),
        ))
        self.assertIsNone(self.c.session.timeline.active)
        self.assertEqual(self.c.session.after_run.phase_id, phase.phase_id)
        self.assertFalse(self.c.session.after_run.minimum_duration_requested)
        self.assertEqual(self.c.last_decision.reason, "after_run")
