"""Synthetische Tests fuer die zustandslose Lichtkurve."""
import unittest

from custom_components.ha_sauna.core.light import (
    linear, normal_brightness, phase_target, temperature_brightness,
)
from custom_components.ha_sauna.core.parameters import Parameters


class LightCurveTests(unittest.TestCase):
    def setUp(self):
        self.parameters = Parameters({
            "light_reference_temperature_c": 30,
            "cooling_brightness_percent": 5,
            "operation_brightness_percent": 40,
            "night_brightness_percent": 25,
            "readiness_hysteresis_c": 3,
        })

    def test_cold_point_and_missing_temperature_use_base_brightness(self):
        for temperature in (None, 30):
            with self.subTest(temperature=temperature):
                self.assertEqual(temperature_brightness(temperature, 85, self.parameters, 40), 5)

    def test_temperature_midpoint_is_linear(self):
        self.assertEqual(temperature_brightness(57.5, 85, self.parameters, 40), 22.5)

    def test_warm_restart_has_the_same_target_without_history(self):
        self.assertEqual(phase_target("aufheizen", 90, 85, self.parameters, 40), 40)

    def test_twilight_midpoint_is_linear(self):
        self.assertEqual(normal_brightness(-3, self.parameters), 32.5)
        self.assertEqual(normal_brightness(None, self.parameters), 25)

    def test_elevation_and_temperature_are_clamped(self):
        self.assertEqual(normal_brightness(-10, self.parameters), 25)
        self.assertEqual(normal_brightness(2, self.parameters), 40)
        self.assertEqual(temperature_brightness(0, 85, self.parameters, 40), 5)
        self.assertEqual(temperature_brightness(100, 85, self.parameters, 40), 40)

    def test_readiness_band_and_gang_hold_normal_brightness(self):
        self.assertEqual(phase_target("bereit", 82, 85, self.parameters, 40), 40)
        self.assertEqual(phase_target("saunagang", None, 85, self.parameters, 40), 40)

    def test_linear_transition_handles_zero_and_negative_time(self):
        self.assertEqual(linear(5, 40, -1, 30), 5)
        self.assertEqual(linear(5, 40, 0, 30), 5)
        self.assertEqual(linear(5, 40, 0, 0), 40)
        self.assertEqual(linear(5, 40, 30, 0), 40)


if __name__ == "__main__":
    unittest.main()
