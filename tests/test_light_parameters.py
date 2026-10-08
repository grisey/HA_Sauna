"""Reine Parameterpruefung fuer die temperaturbezogene Lichtkonfiguration."""
import unittest

from custom_components.ha_sauna.core.parameters import BY_KEY, ParameterError, Parameters


class LightParameterTests(unittest.TestCase):
    def test_new_light_defaults_are_available(self):
        values = Parameters({}).values
        self.assertEqual(values["light_reference_temperature_c"], BY_KEY["light_reference_temperature_c"].default)
        self.assertEqual(values["light_transition_seconds"], BY_KEY["light_transition_seconds"].default)
        self.assertEqual(values["night_brightness_percent"], BY_KEY["night_brightness_percent"].default)
        self.assertEqual(values["operation_brightness_percent"], BY_KEY["operation_brightness_percent"].default)
        self.assertEqual(values["after_run_brightness_percent"], BY_KEY["after_run_brightness_percent"].default)
        self.assertEqual(values["cooling_brightness_percent"], BY_KEY["cooling_brightness_percent"].default)
        self.assertEqual(values["session_light_brightness_percent"], BY_KEY["session_light_brightness_percent"].default)

    def test_saved_light_values_take_precedence_over_changed_defaults(self):
        values = Parameters({
            "operation_brightness_percent": 35,
            "cooling_brightness_percent": 7,
        }).values
        self.assertEqual(values["operation_brightness_percent"], 35)
        self.assertEqual(values["cooling_brightness_percent"], 7)
        self.assertEqual(values["night_brightness_percent"], BY_KEY["night_brightness_percent"].default)

    def test_temperature_targets_accept_catalog_maximum_but_reject_values_above_it(self):
        for key in ("target_temperature_c", "final_temperature_c"):
            with self.subTest(key=key):
                maximum = BY_KEY[key].maximum
                values = {"target_temperature_c": maximum, key: maximum}
                self.assertEqual(Parameters(values).values[key], maximum)
                values[key] = maximum + 0.1
                with self.assertRaises(ParameterError) as raised:
                    Parameters(values)
                self.assertEqual(raised.exception.key, key)


if __name__ == "__main__":
    unittest.main()
