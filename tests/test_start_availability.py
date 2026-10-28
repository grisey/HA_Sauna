"""Temperaturbereitschaft und Ofenkühlung bleiben getrennte Anzeigen."""
from dataclasses import replace
import unittest

from custom_components.ha_sauna.core.display import phase_timer, start_availability
from custom_components.ha_sauna.core.timeline import Kind
from test_cooling import at, controller
from test_foundation import T0, event


def after_run():
    c = controller()
    c.report_heating(True, at(0))
    c.process(event("entry-open", Kind.DOOR_OPEN, 0))
    c.process(event("close", Kind.DOOR_CLOSE, 1))
    c.process(event("infusion", Kind.INFUSION, 2))
    c.process(event("open", Kind.DOOR_OPEN, 299))
    c.process(event("vent", Kind.VENTILATION, 300))
    c.report_contactor(False, at(300))
    c.report_heating(False, at(300))
    return c


class StartAvailabilityTests(unittest.TestCase):
    def test_ready_latch_has_no_budget_based_start_window(self):
        c = controller(target_temperature_c=70)
        c.set_temperature(70, T0)
        availability = start_availability(c, T0)

        self.assertEqual(availability["until_ready_seconds"], 0)
        self.assertFalse(availability["ready_estimated"])
        self.assertNotIn("start_window_seconds", availability)
        self.assertNotIn("Mindestheizzeit", availability["message"])

    def test_cooling_states_do_not_define_a_gang_start_blocker(self):
        c = after_run()
        cooling = c.session.after_run
        for mode, phase in (
            ("remaining", cooling),
            ("pending", replace(cooling, pending_start=True, ends_at=None)),
            ("paused", replace(cooling, paused_at=at(301), ends_at=None)),
        ):
            with self.subTest(mode=mode):
                c._session = replace(c.session, after_run=phase)
                session = c.session
                availability = start_availability(c, at(301))
                self.assertIsNone(availability["minimum_wait_seconds"])
                self.assertIsNone(availability["blocker"])
                self.assertNotIn("Saunagang", availability["message"])
                self.assertEqual(phase_timer(c, at(301))["mode"], mode)
                self.assertIs(c.session, session)

    def test_heating_without_a_valid_rate_does_not_invent_an_eta(self):
        c = controller()
        availability = start_availability(c, T0)

        self.assertIsNone(availability["until_ready_seconds"])
        self.assertFalse(availability["ready_estimated"])
        self.assertIn("Noch nicht abschätzbar", availability["message"])

    def test_eta_uses_the_prepared_estimate(self):
        c = controller(target_temperature_c=80)
        c.set_temperature(70, T0)

        availability = start_availability(c, T0, estimated_ready_seconds=123)

        self.assertEqual(availability["until_ready_seconds"], 123)
        self.assertTrue(availability["ready_estimated"])

    def test_active_round_has_its_own_elapsed_display(self):
        c = controller()
        c.process(event("entry-open", Kind.DOOR_OPEN, 0))
        c.process(event("close", Kind.DOOR_CLOSE, 1))
        c.process(event("person", Kind.PERSON_STRONG, 2))
        # Bei erfüllten Startbedingungen beginnt der Gang am Türschluss.
        # Beide Anzeigen konsumieren diesen belegten Beginn unverändert.
        active = c.session.timeline.active
        availability = start_availability(c, at(62))

        self.assertEqual(availability["gang_elapsed_seconds"], 61)
        self.assertEqual(phase_timer(c, at(62))["seconds"], 61)
        self.assertIsNone(availability["until_ready_seconds"])
        self.assertNotIn("start_window_seconds", availability)
        self.assertIs(c.session.timeline.active, active)
        self.assertEqual(active.started_at, at(1))

        # Ohne Türquelle erfinden beide Anzeigen keine physische Dauer.
        c._session = replace(c.session, timeline=replace(c.session.timeline, processed=()))
        self.assertIsNone(start_availability(c, at(62))["gang_elapsed_seconds"])
        self.assertIsNone(phase_timer(c, at(62))["seconds"])

    def test_cooling_keeps_its_remaining_time_in_the_phase_timer(self):
        c = after_run()
        timer = phase_timer(c, at(301))
        self.assertEqual(timer["kind"], "after_run")
        self.assertGreater(timer["seconds"], 0)

    def test_protection_never_reports_ready_even_at_the_temperature_target(self):
        c = controller(target_temperature_c=70)
        c.set_temperature(74, T0)
        c.protection.add("sensor_fault")

        availability = start_availability(c, T0)

        self.assertEqual(availability["blocker"]["kind"], "protection")
        self.assertEqual(availability["message"], "Der Ofen ist wegen einer Schutzsperre gesperrt.")
        self.assertIsNone(availability["until_ready_seconds"])
        self.assertNotIn("start_window_seconds", availability)

    def test_inhibit_describes_the_heater_without_prohibiting_a_gang(self):
        c = controller()
        c.inhibits.add("heater_unavailable")
        availability = start_availability(c, T0)
        self.assertEqual(availability["blocker"]["kind"], "inhibit")
        self.assertEqual(availability["message"], "Der Ofen ist derzeit gesperrt.")

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

    def test_reheating_availability_no_longer_announces_ready(self):
        c = controller(target_temperature_c=70)
        c.set_temperature(70, T0)
        changed = type(c.parameters)({**c.parameters.values, "target_temperature_c": 80})

        c.update_temperature_parameters(changed, at(1), explicit_target=True)

        availability = start_availability(c, at(1))
        self.assertIsNone(availability["until_ready_seconds"])
        self.assertFalse(availability["ready_estimated"])
        self.assertIn("Noch nicht abschätzbar", availability["message"])

    def test_lower_setpoint_uses_the_current_valid_temperature_immediately(self):
        c = controller(target_temperature_c=90)
        c.set_temperature(85, T0)
        changed = type(c.parameters)({**c.parameters.values, "target_temperature_c": 80})

        c.update_temperature_parameters(changed, at(1), explicit_target=True)

        self.assertEqual(start_availability(c, at(1))["until_ready_seconds"], 0)
