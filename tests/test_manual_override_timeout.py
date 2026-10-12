"""Automatic-mode heater overrides return to thermostat control on time."""

import unittest
from datetime import timedelta

from test_foundation import T0, bindings, parameters

from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.parameters import BY_KEY, Parameters
from custom_components.ha_sauna.runtime import Configuration


def at(seconds):
    return T0 + timedelta(seconds=seconds)


class ManualOverrideTimeoutTests(unittest.TestCase):
    def automatic_parameters(self, **overrides):
        return Parameters({
            **parameters().as_dict(),
            "target_temperature_c": 80,
            **overrides,
        })

    def automatic(self):
        controller = Controller(self.automatic_parameters())
        controller.set_temperature(60, at(0))
        controller.set_operation(True, at(0), session_id="s")
        return controller

    def test_override_expires_at_configured_duration_and_fresh_choice_restarts_deadline(self):
        controller = self.automatic()
        controller.set_heater_override(False, at(0))
        duration = controller.parameters.seconds("manual_override_minutes")
        self.assertEqual(controller.heater_override_ends_at, at(duration))
        controller.advance(at(duration - 1))
        self.assertFalse(controller.heater_override)
        controller.set_heater_override(True, at(duration - 1))
        self.assertEqual(controller.heater_override_ends_at, at(2 * duration - 1))
        controller.advance(at(2 * duration - 1))
        self.assertIsNone(controller.heater_override)
        self.assertIsNone(controller.heater_override_ends_at)

    def test_loaded_override_duration_returns_to_current_automatic_demand(self):
        maximum = BY_KEY["manual_override_minutes"].maximum
        fractional = min(maximum / 2, 0.5)
        for stored, seconds in (
            (maximum, maximum * 60),
            (maximum * 2, maximum * 60),
            (fractional, fractional * 60),
        ):
            with self.subTest(stored=stored):
                configuration = Configuration.from_options(
                    {
                        "bindings": bindings().as_dict(),
                        "parameters": {
                            **self.automatic_parameters(
                                manual_override_minutes=maximum
                            ).as_dict(),
                            "manual_override_minutes": stored,
                        },
                    }
                )
                controller = Controller(configuration.parameters)
                controller.set_temperature(60, at(0))
                controller.set_operation(True, at(0), session_id="loaded")
                controller.set_heater_override(False, at(1))
                self.assertEqual(controller.heater_override_ends_at, at(1 + seconds))
                controller.advance(at(seconds))
                self.assertFalse(controller.heater_override)
                self.assertFalse(controller.last_decision.heat)
                controller.advance(at(1 + seconds))
                self.assertIsNone(controller.heater_override)
                self.assertIsNone(controller.heater_override_ends_at)
                self.assertTrue(controller.last_decision.heat)
                self.assertEqual(
                    controller.last_decision.heat, controller.automatic_decision.heat
                )

    def test_explicit_return_clears_deadline_and_manual_mode_has_none(self):
        controller = self.automatic()
        controller.set_heater_override(False, at(1))
        controller.set_heater_override(None, at(2))
        self.assertIsNone(controller.heater_override_ends_at)

        controller = Controller(parameters(), control_mode="manual")
        controller.set_temperature(60, at(0))
        controller.set_heater_override(False, at(1))
        controller.advance(at(1000))
        self.assertFalse(controller.heater_override)
        self.assertIsNone(controller.heater_override_ends_at)

    def test_manual_off_during_warmup_cannot_strand_the_automatic_heater(self):
        controller = self.automatic()
        controller.set_heater_override(False, at(0))
        controller.report_heating(False, at(0))
        duration = controller.parameters.seconds("manual_override_minutes")
        controller.set_temperature(55, at(duration / 2))
        controller.advance(at(duration - 1))
        self.assertEqual(controller.phase, "aufheizen")
        self.assertFalse(controller.last_decision.heat)
        controller.advance(at(duration))
        self.assertEqual(controller.phase, "aufheizen")
        self.assertIsNone(controller.heater_override)
        self.assertTrue(controller.last_decision.heat)

    def test_manual_heat_handoff_only_keeps_a_real_running_minimum_interval(self):
        configured = self.automatic_parameters()
        duration = configured.seconds("manual_override_minutes")
        minimum = configured.seconds("minimum_heating_minutes")
        cases = (
            ("explicit return with real feedback", 85, True, True, False),
            ("missing feedback", 85, False, True, False),
            ("manual off never revives", 85, True, False, False),
            ("deadline return", 85, True, True, True),
            ("low temperature still uses thermostat", 60, True, False, False),
        )
        for name, temperature, feedback, manual_on, deadline in cases:
            with self.subTest(name=name):
                controller = Controller(configured)
                controller.set_temperature(temperature, at(0))
                controller.set_operation(True, at(0), session_id=name)
                controller.set_heater_override(manual_on, at(1))
                original_deadline = controller.heater_override_ends_at
                if feedback:
                    feedback_at = 1 + max(0, duration - minimum / 2) if deadline else 1
                    controller.report_heating(True, at(feedback_at))
                    if manual_on:
                        self.assertEqual(
                            controller.heater_override_ends_at, original_deadline
                        )
                if deadline:
                    controller.advance(at(1 + duration))
                else:
                    controller.set_heater_override(None, at(1 + minimum / 2))

                if manual_on and feedback:
                    self.assertTrue(controller.last_decision.heat)
                    self.assertEqual(controller.last_decision.reason, "minimum_heating")
                    self.assertEqual(
                        controller.session.heating.intervals[-1].started_at, at(feedback_at)
                    )
                    self.assertIsNone(controller.heater_override_ends_at)
                elif name == "low temperature still uses thermostat":
                    self.assertTrue(controller.last_decision.heat)
                else:
                    self.assertFalse(controller.last_decision.heat)

        for name, priority in (("ready-state decision change", False), ("protection", True)):
            with self.subTest(name=name):
                controller = Controller(configured)
                controller.set_temperature(85, at(0))
                controller.set_operation(True, at(0), session_id=name)
                controller.set_heater_override(True, at(1))
                controller.report_heating(True, at(1))
                if priority:
                    controller.protection.add("test")
                    controller._evaluate(at(1 + minimum / 2))
                    self.assertFalse(controller.last_decision.heat)
                else:
                    controller.set_temperature(60, at(1 + minimum / 2))
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
        configured = self.automatic_parameters()
        duration = configured.seconds("manual_override_minutes")
        minimum = configured.seconds("minimum_heating_minutes")
        for name, deadline in (("phase", False), ("deadline", True)):
            with self.subTest(name=name):
                controller = Controller(configured)
                controller.set_temperature(85 if deadline else 60, at(0))
                controller.set_operation(True, at(0), session_id=name)
                controller.report_heating(True, at(0))
                controller.set_heater_override(False, at(1))
                if deadline:
                    controller.advance(at(1 + duration))
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
