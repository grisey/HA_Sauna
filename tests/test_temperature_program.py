import unittest

from custom_components.ha_sauna.core.temperature_program import (
    TemperatureProgram,
    evenly_distributed,
)


class TemperatureProgramTests(unittest.TestCase):
    def test_initial_targets_are_evenly_distributed(self):
        self.assertEqual(evenly_distributed(80, 95, 4), (80.0, 85.0, 90.0, 95.0))
        self.assertEqual(evenly_distributed(70, 90, 3), (70.0, 80.0, 90.0))

    def test_single_gang_reaches_a_different_end_after_first_actual_gang(self):
        program = TemperatureProgram(80, 95, 1)
        self.assertEqual(evenly_distributed(80, 95, 1), (80.0,))
        self.assertEqual(program.target(0), 80)
        self.assertEqual(program.target(1), 95)

    def test_end_target_is_held_after_the_last_distribution_point(self):
        program = TemperatureProgram(80, 100, 4)
        self.assertEqual(program.target(4), 100.0)
        self.assertEqual(program.target(7), 100.0)

    def test_constant_program_holds_its_temperature(self):
        program = TemperatureProgram(100, 100, 3)
        self.assertEqual(evenly_distributed(100, 100, 3), (100.0, 100.0, 100.0))
        self.assertEqual(program.target(1), 100.0)

    def test_explicit_steps_hold_the_last_value_without_limiting_gangs(self):
        program = TemperatureProgram(80, 90, 3, (80, 86, 90))
        self.assertEqual(
            tuple(program.target(gang) for gang in range(6)), (80, 86, 90, 90, 90, 90)
        )

    def test_invalid_values_are_rejected(self):
        for args in ((0, 100.1, 1), (0, float("inf"), 1), (0, 100, 0), (0, 100, 1.0)):
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    evenly_distributed(*args)
        for steps in ((), "80, 90", (80, True), (80, float("nan"))):
            with self.subTest(steps=steps):
                with self.assertRaises(ValueError):
                    TemperatureProgram(80, 90, 2, steps)
