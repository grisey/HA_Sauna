"""Die Übersicht zeigt nur die Zeit bis zum möglichen Gangbeginn."""
import unittest

from custom_components.ha_sauna.core.display import start_availability
from custom_components.ha_sauna.core.timeline import Kind
from test_cooling import at, controller
from test_foundation import T0, event


def after_run():
    c = controller(after_run_minutes=2)
    c.report_heating(True, at(0))
    c.process(event("close", Kind.DOOR_CLOSE, 1))
    c.process(event("infusion", Kind.INFUSION, 2))
    c.process(event("open", Kind.DOOR_OPEN, 299))
    c.process(event("vent", Kind.VENTILATION, 300))
    c.report_contactor(False, at(300))
    c.report_heating(False, at(300))
    return c


class StartAvailabilityTests(unittest.TestCase):
    def test_ready_latch_has_no_budget_based_start_window(self):
        c = controller(target_temperature_c=70, readiness_offset_c=4,
                       readiness_hysteresis_c=2, heating_minutes=1)
        c.set_temperature(70, T0)
        availability = start_availability(c, T0)

        self.assertEqual(availability["until_ready_seconds"], 0)
        self.assertFalse(availability["ready_estimated"])
        self.assertNotIn("start_window_seconds", availability)
        self.assertNotIn("Mindestheizzeit", availability["message"])

    def test_after_run_wait_is_only_its_configured_remainder(self):
        c = after_run()
        availability = start_availability(c, at(301))
        self.assertIsNone(availability["until_ready_seconds"])
        self.assertGreater(availability["minimum_wait_seconds"], 0)
        self.assertEqual(availability["blocker"]["kind"], "after_run")
        self.assertNotIn("pending_cooling", availability)

    def test_heating_without_a_valid_rate_does_not_invent_an_eta(self):
        c = controller()
        availability = start_availability(c, T0)

        self.assertIsNone(availability["until_ready_seconds"])
        self.assertFalse(availability["ready_estimated"])
        self.assertIn("Noch nicht abschätzbar", availability["message"])

    def test_eta_uses_the_prepared_estimate(self):
        c = controller(target_temperature_c=80, readiness_offset_c=4)
        c.set_temperature(70, T0)

        availability = start_availability(c, T0, estimated_ready_seconds=123)

        self.assertEqual(availability["until_ready_seconds"], 123)
        self.assertTrue(availability["ready_estimated"])

    def test_active_round_has_its_own_elapsed_display(self):
        c = controller()
        c.process(event("close", Kind.DOOR_CLOSE, 1))
        c.process(event("person", Kind.PERSON_STRONG, 2))
        availability = start_availability(c, at(62))

        self.assertEqual(availability["gang_elapsed_seconds"], 61)
        self.assertIsNone(availability["until_ready_seconds"])
        self.assertNotIn("start_window_seconds", availability)

    def test_active_oven_cooling_has_a_real_remaining_wait(self):
        c = after_run()
        availability = start_availability(c, at(301))
        self.assertEqual(availability["blocker"]["kind"], "after_run")
        self.assertGreater(availability["minimum_wait_seconds"], 0)
        self.assertIsNone(availability["until_ready_seconds"])

    def test_protection_never_reports_ready_even_at_the_temperature_target(self):
        c = controller(target_temperature_c=70, readiness_offset_c=4)
        c.set_temperature(74, T0)
        c.protection.add("sensor_fault")

        availability = start_availability(c, T0)

        self.assertEqual(availability["blocker"]["kind"], "protection")
        self.assertIsNone(availability["until_ready_seconds"])
        self.assertNotIn("start_window_seconds", availability)

    def test_invalid_temperature_does_not_announce_an_existing_ready_latch(self):
        c = controller(target_temperature_c=70)
        c.set_temperature(70, T0)
        self.assertIsNotNone(c.session.ready_at)
        c.set_temperature(None, at(1))

        availability = start_availability(c, at(1))

        self.assertEqual(availability["blocker"], {"kind": "temperature_unavailable"})
        self.assertIsNone(availability["until_ready_seconds"])
        # Die Messstörung sperrt die Startankündigung, ohne einen künstlichen
        # Phasenwechsel und damit einen Rückkehrpunkt für Bedienungen zu bilden.
        self.assertEqual(c.phase, "bereit")

    def test_live_setpoint_change_does_not_clear_readiness(self):
        c = controller(target_temperature_c=70)
        c.set_temperature(70, T0)
        before = c.session.ready_at
        changed = type(c.parameters)({**c.parameters.values, "target_temperature_c": 80})

        c.update_temperature_parameters(changed, at(1), explicit_target=True)

        self.assertEqual(c.session.ready_at, before)
        self.assertEqual(c.phase, "bereit")

    def test_lower_setpoint_uses_the_current_valid_temperature_immediately(self):
        c = controller(target_temperature_c=90)
        c.set_temperature(85, T0)
        changed = type(c.parameters)({**c.parameters.values, "target_temperature_c": 80})

        c.update_temperature_parameters(changed, at(1), explicit_target=True)

        self.assertEqual(c.phase, "bereit")
        self.assertEqual(start_availability(c, at(1))["until_ready_seconds"], 0)
