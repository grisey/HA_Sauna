"""Reine Parameterpruefung fuer die temperaturbezogene Lichtkonfiguration."""
import unittest

from custom_components.ha_sauna.core.parameters import ParameterError, Parameters


class LightParameterTests(unittest.TestCase):
    def test_new_light_defaults_are_available(self):
        values = Parameters({}).values
        self.assertEqual(values["light_reference_temperature_c"], 30)
        self.assertEqual(values["light_transition_seconds"], 30)
        self.assertEqual(values["night_brightness_percent"], 25)
        self.assertEqual(values["operation_brightness_percent"], 40)
        self.assertEqual(values["after_run_brightness_percent"], 15)
        self.assertEqual(values["cooling_brightness_percent"], 5)
        self.assertEqual(values["session_light_brightness_percent"], 50)

    def test_saved_light_values_take_precedence_over_changed_defaults(self):
        values = Parameters({
            "operation_brightness_percent": 35,
            "cooling_brightness_percent": 7,
        }).values
        self.assertEqual(values["operation_brightness_percent"], 35)
        self.assertEqual(values["cooling_brightness_percent"], 7)
        self.assertEqual(values["night_brightness_percent"], 25)

    def test_temperature_targets_accept_100_but_reject_values_above_it(self):
        for key in ("target_temperature_c", "final_temperature_c"):
            with self.subTest(key=key):
                values = {"target_temperature_c": 100, key: 100}
                self.assertEqual(Parameters(values).values[key], 100)
                values[key] = 100.1
                with self.assertRaises(ParameterError) as raised:
                    Parameters(values)
                self.assertEqual(raised.exception.key, key)


if __name__ == "__main__":
    unittest.main()
