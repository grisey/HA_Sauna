"""Synthetische Tests fuer die zustandslose Lichtkurve."""
import unittest

from custom_components.ha_sauna.core.light import (
    linear,
    normal_brightness,
    phase_target,
    temperature_brightness,
)
from custom_components.ha_sauna.core.parameters import Parameters


class LightCurveTests(unittest.TestCase):
    def setUp(self):
        self.parameters = Parameters({})
        self.cold = self.parameters.values["light_reference_temperature_c"]
        self.base = self.parameters.values["cooling_brightness_percent"]
        self.day = self.parameters.values["operation_brightness_percent"]
        self.night = self.parameters.values["night_brightness_percent"]

    def test_cold_point_and_missing_temperature_use_base_brightness(self):
        for temperature in (None, self.cold):
            with self.subTest(temperature=temperature):
                self.assertEqual(temperature_brightness(temperature, 85, self.parameters, self.day), self.base)

    def test_temperature_midpoint_is_linear(self):
        self.assertEqual(temperature_brightness((self.cold + 85) / 2, 85, self.parameters, self.day),
                         (self.base + self.day) / 2)

    def test_warm_restart_has_the_same_target_without_history(self):
        self.assertEqual(phase_target("aufheizen", 90, 85, self.parameters, self.day), self.day)

    def test_twilight_midpoint_is_linear(self):
        self.assertEqual(normal_brightness(-3, self.parameters), (self.night + self.day) / 2)
        self.assertEqual(normal_brightness(None, self.parameters), self.night)

    def test_elevation_and_temperature_are_clamped(self):
        self.assertEqual(normal_brightness(-10, self.parameters), self.night)
        self.assertEqual(normal_brightness(2, self.parameters), self.day)
        self.assertEqual(temperature_brightness(0, 85, self.parameters, self.day), self.base)
        self.assertEqual(temperature_brightness(100, 85, self.parameters, self.day), self.day)

    def test_readiness_keeps_the_continuous_temperature_curve(self):
        for temperature in (76.9, 77.1, 79.9, 80):
            with self.subTest(temperature=temperature):
                self.assertEqual(
                    phase_target("bereit", temperature, 80, self.parameters, self.day),
                    phase_target("aufheizen", temperature, 80, self.parameters, self.day),
                )
        self.assertAlmostEqual(
            phase_target("bereit", 77.1, 80, self.parameters, self.day)
            - phase_target("bereit", 76.9, 80, self.parameters, self.day),
            (self.day - self.base) * .2 / (80 - self.cold),
        )

    def test_gang_holds_normal_brightness(self):
        self.assertEqual(phase_target("saunagang", None, 85, self.parameters, self.day), self.day)

    def test_linear_transition_handles_zero_and_negative_time(self):
        self.assertEqual(linear(5, 40, -1, 30), 5)
        self.assertEqual(linear(5, 40, 0, 30), 5)
        self.assertEqual(linear(5, 40, 0, 0), 40)
        self.assertEqual(linear(5, 40, 30, 0), 40)


if __name__ == "__main__":
    unittest.main()
