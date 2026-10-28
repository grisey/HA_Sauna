import unittest

from custom_components.ha_sauna.core.parameters import BY_KEY
from custom_components.ha_sauna.core.temperature_program import (
    TemperatureProgram,
    evenly_distributed,
    temperature_steps,
)


class TemperatureProgramTests(unittest.TestCase):
    def test_initial_targets_are_evenly_distributed(self):
        self.assertEqual(evenly_distributed(80, 95, 4), (80.0, 85.0, 90.0, 95.0))
        self.assertEqual(evenly_distributed(70, 90, 3), (70.0, 80.0, 90.0))

    def test_targets_and_computed_steps_round_half_up_before_control(self):
        self.assertEqual(evenly_distributed(88, 98, 4), (88, 91, 95, 98))
        self.assertEqual(evenly_distributed(80, 85, 3), (80, 83, 85))
        self.assertEqual(evenly_distributed(80.5, 84.4, 3), (81, 83, 84))
        self.assertEqual(temperature_steps((80.49, 80.5, 81.5)), (80, 81, 82))
        program = TemperatureProgram(80.5, 84.4, 3)
        self.assertEqual((program.start_c, program.end_c), (81, 84))
        self.assertEqual(tuple(program.target(i) for i in range(4)), (81, 83, 84, 84))

    def test_rounding_cannot_rescue_inputs_outside_the_temperature_limits(self):
        for value in (59.9, 100.1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                temperature_steps((80, value), minimum_c=60, maximum_c=100)
        with self.assertRaises(ValueError):
            temperature_steps((60.3, 80), minimum_c=60.2)

    def test_single_gang_reaches_a_different_end_after_first_actual_gang(self):
        program = TemperatureProgram(80, 95, 1)
        self.assertEqual(evenly_distributed(80, 95, 1), (80.0,))
        self.assertEqual(program.target(0), 80)
        self.assertEqual(program.target(1), 95)

    def test_invalid_values_are_rejected(self):
        maximum = BY_KEY["target_temperature_c"].maximum
        for args in (
            (0, maximum + .1, 1),
            (0, float("inf"), 1),
            (0, maximum, 0),
            (0, maximum, 1.0),
        ):
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    evenly_distributed(*args)
        for steps in ((), "80, 90", (80, True), (80, float("nan"))):
            with self.subTest(steps=steps):
                with self.assertRaises(ValueError):
                    TemperatureProgram(80, 90, 2, steps)
