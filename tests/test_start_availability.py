"""Die Übersicht zeigt nur die Zeit bis zum möglichen Gangbeginn."""
import unittest

from custom_components.ha_sauna.core.display import start_availability
from custom_components.ha_sauna.core.timeline import Kind
from test_cooling import at, controller
from test_foundation import T0, event


class StartAvailabilityTests(unittest.TestCase):
    def test_ready_exposes_remaining_heating_budget_as_a_minimum_window(self):
        c = controller(target_temperature_c=70, readiness_offset_c=4,
                       readiness_hysteresis_c=2, heating_minutes=1)
        c.set_temperature(74, T0)
        availability = start_availability(c, T0)

        self.assertEqual(availability["until_ready_seconds"], 0)
        self.assertFalse(availability["ready_estimated"])
        self.assertEqual(availability["start_window_seconds"], 60)
        self.assertEqual(availability["start_window_label"], "mindestens")
        self.assertNotIn("Mindestheizzeit", availability["message"])

    def test_after_run_prospectively_credits_its_complete_duration_to_cooling(self):
        c = controller(forced_cooling_minutes=15, after_run_minutes=8)
        c.report_heating(True, at(0))
        c.advance(at(60))
        c.set_heater_override(True, at(360))  # Fünf Minuten Kühlung sind echt abgelaufen.
        self.assertEqual(c.cooling_remaining_seconds, 600)

        c.process(event("close", Kind.DOOR_CLOSE, 361))
        c.process(event("person", Kind.PERSON_STRONG, 362))
        c.process(event("infusion", Kind.INFUSION, 363))
        c.process(event("open", Kind.DOOR_OPEN, 364))
        c.process(event("vent", Kind.VENTILATION, 365))
        availability = start_availability(c, at(725))  # Zwei Minuten Nachlauf bleiben.

        self.assertIsNone(availability["until_ready_seconds"])
        self.assertEqual(availability["minimum_wait_seconds"], 240)
        self.assertEqual(availability["blocker"], {
            "kind": "after_run", "seconds": 120, "following_cooling_seconds": 120})
        self.assertTrue(availability["pending_cooling"])

    def test_heating_without_a_valid_rate_does_not_invent_an_eta(self):
        c = controller()
        availability = start_availability(c, T0)

        self.assertIsNone(availability["until_ready_seconds"])
        self.assertFalse(availability["ready_estimated"])
        self.assertIn("Noch nicht abschätzbar", availability["message"])

    def test_active_round_has_its_own_elapsed_display(self):
        c = controller()
        c.process(event("close", Kind.DOOR_CLOSE, 1))
        c.process(event("person", Kind.PERSON_STRONG, 2))
        availability = start_availability(c, at(62))

        self.assertEqual(availability["gang_elapsed_seconds"], 61)
        self.assertIsNone(availability["until_ready_seconds"])
        self.assertIsNone(availability["start_window_seconds"])

    def test_paused_cooling_has_no_made_up_expiry(self):
        c = controller()
        c.report_heating(True, T0)
        c.advance(at(60))
        c.set_heater_override(True, at(61))

        availability = start_availability(c, at(61))

        self.assertEqual(availability["blocker"], {"kind": "cooling_paused"})
        self.assertIsNone(availability["minimum_wait_seconds"])
        self.assertIsNone(availability["until_ready_seconds"])

    def test_protection_never_reports_ready_even_at_the_temperature_target(self):
        c = controller(target_temperature_c=70, readiness_offset_c=4)
        c.set_temperature(74, T0)
        c.protection.add("sensor_fault")

        availability = start_availability(c, T0)

        self.assertEqual(availability["blocker"]["kind"], "protection")
        self.assertIsNone(availability["until_ready_seconds"])
        self.assertIsNone(availability["start_window_seconds"])
