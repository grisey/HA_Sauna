"""Automatic-mode heater overrides return to thermostat control on time."""

import unittest
from datetime import timedelta

from test_foundation import T0, parameters

from custom_components.ha_sauna.core.controller import Controller


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


if __name__ == "__main__":
    unittest.main()
