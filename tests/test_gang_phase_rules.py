"""Eintritt, Aufenthalt und Rückkehr; Bereitschaft gibt die Nutzung nicht vor.

Die Folgen werden an Türbewegung, Belegungszustand und Temperatur geprüft.
Abgeleitete Gangereignisse werden nicht als Testeingang vorgegeben.
"""

import unittest

from test_cooling import at, controller
from test_foundation import event

from custom_components.ha_sauna.core.presence import binary_presence
from custom_components.ha_sauna.core.timeline import Kind


class GangPhaseRulesTests(unittest.TestCase):
    def setUp(self):
        self.c = controller(
            presence_source="ha_presence", presence_entity="binary_sensor.presence",
        )
        self.minimum = self.c.parameters.values["sauna_min_temperature_c"]

    def presence(self, state, second):
        self.c.observe_direct_presence(binary_presence(
            "binary_sensor.presence", state, at(second), at(second),
        ), at(second))

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

    def test_lost_presence_before_minimum_does_not_leave_a_pending_entry(self):
        for state in ("off", "unknown", "unavailable"):
            with self.subTest(state=state):
                self.setUp()
                self.c.set_temperature(self.minimum - 1, at(0))
                self.enter()
                self.presence(state, 4)
                self.presence("on", 5)
                self.c.set_temperature(self.minimum, at(6))
                self.assertIsNone(self.c.session.timeline.active)

    def test_cold_door_cycle_is_observed_without_extra_door_heating(self):
        self.c = controller(
            presence_source="proxy", presence_entity="binary_sensor.presence",
        )
        self.c.set_temperature(self.minimum - 1, at(0))
        self.door(Kind.DOOR_OPEN, 1)
        self.door(Kind.DOOR_CLOSE, 2)
        self.assertEqual([item.kind for item in self.c.session.timeline.processed],
                         [Kind.DOOR_OPEN, Kind.DOOR_CLOSE])
        self.assertFalse(self.c.regulation_inputs.temporary_door_heat)
        self.assertIsNone(self.c.session.timeline.active)

    def test_temperature_at_closure_controls_door_heating_not_door_observation(self):
        self.c = controller(
            presence_source="proxy", presence_entity="binary_sensor.presence",
        )
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
        self.c = controller(
            presence_source="proxy", presence_entity="binary_sensor.presence",
        )
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
