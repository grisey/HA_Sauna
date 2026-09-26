"""Automatic-mode heater overrides return to thermostat control on time."""

import unittest
from datetime import timedelta

from test_foundation import T0, parameters

from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.parameters import Parameters


def at(seconds):
    return T0 + timedelta(seconds=seconds)


class ManualOverrideTimeoutTests(unittest.TestCase):
    def automatic(self):
        controller = Controller(parameters())
        controller.set_temperature(60, at(0))
        controller.set_operation(True, at(0), session_id="s")
        return controller

    def test_override_expires_at_ten_minutes_and_fresh_choice_restarts_deadline(self):
        controller = self.automatic()
        controller.set_heater_override(False, at(0))
        self.assertEqual(controller.heater_override_ends_at, at(600))
        controller.advance(at(599))
        self.assertFalse(controller.heater_override)
        controller.set_heater_override(True, at(599))
        self.assertEqual(controller.heater_override_ends_at, at(1199))
        controller.advance(at(1199))
        self.assertIsNone(controller.heater_override)
        self.assertIsNone(controller.heater_override_ends_at)

    def test_explicit_return_clears_deadline_and_manual_mode_has_none(self):
        controller = self.automatic()
        controller.set_heater_override(False, at(1))
        controller.set_heater_override(None, at(2))
        self.assertIsNone(controller.heater_override_ends_at)

        controller = Controller(parameters(), control_mode="manual")
        controller.set_temperature(60, at(0))
        controller.set_operation(True, at(0), session_id="manual")
        controller.set_heater_override(False, at(1))
        controller.advance(at(1000))
        self.assertFalse(controller.heater_override)
        self.assertIsNone(controller.heater_override_ends_at)

    def test_manual_off_during_warmup_cannot_strand_the_automatic_heater(self):
        controller = self.automatic()
        controller.set_heater_override(False, at(0))
        controller.report_heating(False, at(0))
        controller.set_temperature(55, at(300))
        controller.advance(at(599))
        self.assertEqual(controller.phase, "aufheizen")
        self.assertFalse(controller.last_decision.heat)
        controller.advance(at(600))
        self.assertEqual(controller.phase, "aufheizen")
        self.assertIsNone(controller.heater_override)
        self.assertTrue(controller.last_decision.heat)

    def test_manual_heat_handoff_only_keeps_a_real_running_minimum_interval(self):
        short_override = Parameters(
            {**parameters().values, "manual_override_minutes": 1}
        )
        cases = (
            ("explicit return with real feedback", 85, True, True, False),
            ("missing feedback", 85, False, True, False),
            ("manual off never revives", 85, True, False, False),
            ("deadline return", 85, True, True, True),
            ("low temperature still uses thermostat", 60, True, False, False),
        )
        for name, temperature, feedback, manual_on, deadline in cases:
            with self.subTest(name=name):
                controller = Controller(short_override)
                controller.set_temperature(temperature, at(0))
                controller.set_operation(True, at(0), session_id=name)
                controller.set_heater_override(manual_on, at(1))
                if feedback:
                    controller.report_heating(True, at(1))
                if deadline:
                    controller.advance(at(61))
                else:
                    controller.set_heater_override(None, at(60))

                if manual_on and feedback:
                    self.assertTrue(controller.last_decision.heat)
                    self.assertEqual(controller.last_decision.reason, "minimum_heating")
                elif name == "low temperature still uses thermostat":
                    self.assertTrue(controller.last_decision.heat)
                else:
                    self.assertFalse(controller.last_decision.heat)

        for name, priority in (("ready-state decision change", False), ("protection", True)):
            with self.subTest(name=name):
                controller = Controller(short_override)
                controller.set_temperature(85, at(0))
                controller.set_operation(True, at(0), session_id=name)
                controller.set_heater_override(True, at(1))
                controller.report_heating(True, at(1))
                if priority:
                    controller.protection.add("test")
                    controller._evaluate(at(60))
                    self.assertFalse(controller.last_decision.heat)
                else:
                    controller.set_temperature(60, at(60))
                    self.assertIsNone(controller.heater_override)
                    self.assertTrue(controller.last_decision.heat)
                    self.assertEqual(controller.last_decision.reason, "minimum_heating")

    def test_manual_off_ends_an_existing_minimum_run_before_automatic_return(self):
        controller = self.automatic()
        controller.set_temperature(70, at(0))
        controller.report_heating(True, at(0))
        controller.set_temperature(85, at(20))
        self.assertEqual(controller.last_decision.reason, "minimum_heating")

        controller.set_heater_override(False, at(30))
        controller.set_heater_override(None, at(31))

        self.assertFalse(controller.last_decision.heat)
        self.assertNotEqual(controller.last_decision.reason, "minimum_heating")

    def test_manual_off_cannot_revive_a_completed_handoff_or_delayed_feedback(self):
        for name, initially_reported in (("after handoff", True), ("delayed feedback", False)):
            with self.subTest(name=name):
                controller = self.automatic()
                controller.set_temperature(85, at(0))
                controller.set_heater_override(True, at(1))
                if initially_reported:
                    controller.report_heating(True, at(1))
                    controller.set_heater_override(None, at(30))
                    self.assertEqual(controller.last_decision.reason, "minimum_heating")
                    controller.set_heater_override(False, at(31))
                else:
                    controller.set_heater_override(False, at(30))
                controller.set_heater_override(None, at(32))
                controller.report_heating(True, at(33))

                self.assertFalse(controller.last_decision.heat)
                self.assertNotEqual(controller.last_decision.reason, "minimum_heating")

    def test_manual_off_handoff_clears_demand_on_phase_or_deadline(self):
        short_override = Parameters(
            {**parameters().values, "manual_override_minutes": 1}
        )
        for name, deadline in (("phase", False), ("deadline", True)):
            with self.subTest(name=name):
                controller = Controller(short_override)
                controller.set_temperature(85 if deadline else 60, at(0))
                controller.set_operation(True, at(0), session_id=name)
                controller.report_heating(True, at(0))
                controller.set_heater_override(False, at(1))
                if deadline:
                    controller.advance(at(61))
                else:
                    controller.set_temperature(85, at(30))
                self.assertIsNone(controller.heater_override)
                self.assertFalse(controller.last_decision.heat)
                self.assertNotEqual(controller.last_decision.reason, "minimum_heating")

    def test_repeated_automatic_choice_keeps_an_existing_minimum_run(self):
        controller = self.automatic()
        controller.set_temperature(70, at(0))
        controller.report_heating(True, at(0))
        controller.set_temperature(85, at(20))
        controller.set_heater_override(None, at(30))

        self.assertTrue(controller.last_decision.heat)
        self.assertEqual(controller.last_decision.reason, "minimum_heating")


if __name__ == "__main__":
    unittest.main()
