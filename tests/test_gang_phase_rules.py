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
        self.c = controller()
        self.c.presence_source = "ha_presence"
        self.c.presence_entity = "binary_sensor.presence"
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

    def test_present_occupant_starts_at_minimum_without_another_door_cycle(self):
        self.c.set_temperature(self.minimum - 1, at(0))
        self.enter()
        self.assertIsNone(self.c.session.timeline.active)
        self.c.set_temperature(self.minimum, at(8))
        gang = self.c.session.timeline.active
        self.assertIsNotNone(gang)
        self.assertEqual(gang.started_at, at(8))
        self.assertEqual(gang.start_source_event_id, "door_close:3")
        self.assertEqual(self.c.phase, "saunagang")
        self.assertEqual(self.c.session.timeline.gang_count, 0)

        self.door(Kind.DOOR_OPEN, 10)
        self.presence("off", 11)
        self.c.set_temperature(self.minimum - 5, at(12))
        self.assertIsNotNone(self.c.session.timeline.active)
        self.door(Kind.VENTILATION, 13)
        self.assertIsNone(self.c.session.timeline.active)
        self.assertEqual(self.c.session.timeline.gang_count, 1)
        self.assertEqual(self.c.session.timeline.completed[0].ended_at, at(13))

    def test_return_through_open_door_ends_cooling_and_can_complete_next_gang(self):
        self.enter()
        self.leave_for_ventilation()
        cooling = self.c.session.after_run
        self.assertIsNotNone(cooling)

        self.presence("on", 8)
        self.assertIsNone(self.c.session.timeline.active)
        self.assertEqual(self.c.session.after_run.phase_id, cooling.phase_id)
        self.door(Kind.DOOR_CLOSE, 9)
        gang = self.c.session.timeline.active
        self.assertIsNotNone(gang)
        self.assertEqual(gang.started_at, at(9))
        self.assertIsNone(self.c.session.after_run)
        self.assertEqual(self.c.session.after_run_history[-1].ends_at, at(9))
        self.assertIsNone(self.c.session.last_completed_oven_cooling_at)
        self.assertEqual(self.c.phase, "saunagang")
        self.assertEqual(self.c.session.timeline.gang_count, 1)

        self.door(Kind.DOOR_OPEN, 11)
        self.presence("off", 12)
        self.door(Kind.VENTILATION, 13)
        self.assertEqual(self.c.session.timeline.gang_count, 2)
        self.assertEqual(self.c.session.timeline.completed[-1].gang_id, gang.gang_id)
        self.assertEqual(self.c.session.after_run.requested_at, at(13))

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
        self.c.presence_source = "proxy"
        self.c.set_temperature(self.minimum - 1, at(0))
        self.door(Kind.DOOR_OPEN, 1)
        self.door(Kind.DOOR_CLOSE, 2)
        self.assertEqual([item.kind for item in self.c.session.timeline.processed],
                         [Kind.DOOR_OPEN, Kind.DOOR_CLOSE])
        self.assertFalse(self.c.regulation_inputs.temporary_door_heat)
        self.assertIsNone(self.c.session.timeline.active)

    def test_indirect_return_during_cooling_needs_occupancy_evidence(self):
        self.c.presence_source = "proxy"
        self.door(Kind.DOOR_OPEN, 1)
        self.door(Kind.DOOR_CLOSE, 2)
        self.c.process(event("first-infusion", Kind.INFUSION, 3))
        self.door(Kind.DOOR_OPEN, 5)
        self.door(Kind.VENTILATION, 7)
        self.c.report_contactor(False, at(7))
        self.c.report_heating(False, at(7))
        self.door(Kind.DOOR_CLOSE, 9)
        self.assertIsNotNone(self.c.session.after_run)
        self.assertIsNone(self.c.session.timeline.active)
        self.c.process(event("return-person", Kind.PERSON_STRONG, 10))
        self.assertIsNotNone(self.c.session.timeline.active)
        self.assertIsNone(self.c.session.after_run)
        self.c.process(event("return-infusion", Kind.INFUSION, 11))
        self.door(Kind.DOOR_OPEN, 12)
        self.door(Kind.VENTILATION, 13)
        self.assertEqual(self.c.session.timeline.gang_count, 2)

