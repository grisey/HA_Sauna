"""Stabile ganze Stellwerte bei unverändert genauen Lichtkurven."""

import unittest
from itertools import pairwise

from custom_components.ha_sauna.core.light import phase_target
from custom_components.ha_sauna.core.light_output import LightPlan, LightQuantizer
from custom_components.ha_sauna.core.parameters import Parameters


class LightQuantizerTests(unittest.TestCase):
    def setUp(self):
        self.parameters = Parameters({
            "light_output_hysteresis_percent": 0.1,
            "cooling_brightness_percent": 5,
            "light_reference_temperature_c": 30,
        })
        self.quantizer = LightQuantizer()

    def output(self, value, *, automatic=True):
        return self.quantizer.quantize(
            LightPlan(value, automatic, not automatic),
            self.parameters.values["light_output_hysteresis_percent"],
        )

    def test_boundary_noise_does_not_repeat_neighboring_commands(self):
        targets = [
            phase_target("aufheizen", temperature, 80, self.parameters, 40)
            for temperature in (73.56, 73.58) * 5
        ]
        self.assertEqual([round(value) for value in targets], [35, 36] * 5)
        self.assertEqual([self.output(value) for value in targets], [35] * 10)
        # Auch aus der höheren Stufe bleibt derselbe Messbereich ruhig.
        self.assertEqual(self.output(36), 36)
        self.assertEqual([self.output(value) for value in targets], [36] * 10)

    def test_sustained_change_crosses_both_hysteresis_boundaries(self):
        self.assertEqual(
            [self.output(value) for value in (35, 35.55, 35.61, 35.45, 35.39)],
            [35, 35, 36, 36, 35],
        )

    def test_manual_selection_bypasses_hysteresis(self):
        self.assertEqual(self.output(35.4), 35)
        self.assertEqual(self.output(35.55, automatic=False), 36)
        self.assertEqual(self.output(35.45, automatic=False), 35)
        self.assertEqual(self.output(0, automatic=False), 0)

    def test_reset_and_automatic_off_remove_previous_step(self):
        self.output(35)
        self.quantizer.reset()
        self.assertEqual(self.output(35.55), 36)
        self.assertEqual(self.output(0), 0)
        self.assertEqual(self.output(0.55), 1)
        self.assertEqual(self.output(100), 100)

    def test_disabling_hysteresis_restores_rounding(self):
        self.output(35)
        self.assertEqual(
            self.quantizer.quantize(LightPlan(35.5, True, False), 0), 36
        )

    def test_quantization_does_not_limit_ramp_speed_or_extend_deadline(self):
        values = [self.output(100 - 67 * second / 30) for second in range(31)]
        self.assertTrue(all(isinstance(value, int) for value in values))
        self.assertEqual((values[0], values[-1]), (100, 33))
        self.assertGreater(max(a - b for a, b in pairwise(values)), 1)


if __name__ == "__main__":
    unittest.main()
