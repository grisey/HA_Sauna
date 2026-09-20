"""Konfiguration der Temperaturprogramme ohne Laufzeitkopplung."""
import unittest

from custom_components.ha_sauna.bindings import Bindings, ROLES
from custom_components.ha_sauna.const import CONF_BINDINGS, CONF_PARAMETERS
from custom_components.ha_sauna.core.parameters import LIVE_TEMPERATURE_KEYS, ParameterError, Parameters
from custom_components.ha_sauna.runtime import Configuration
from custom_components.ha_sauna.settings import program_parameters


def bindings():
    return {role.key: f"{role.domains[0]}.test_{role.key}" for role in ROLES if not role.optional}


def options(parameters=None, **configuration):
    return {
        CONF_BINDINGS: bindings(),
        CONF_PARAMETERS: {} if parameters is None else parameters,
        **configuration,
    }


class ProgramConfigurationTests(unittest.TestCase):
    def test_legacy_without_final_temperature_is_constant(self):
        configuration = Configuration.from_options(options())
        self.assertEqual(configuration.program_mode, "constant")
        self.assertEqual(configuration.parameters.values["final_temperature_c"], 95)

    def test_legacy_with_final_temperature_is_progressive(self):
        configuration = Configuration.from_options(options({"final_temperature_c": 92}))
        self.assertEqual(configuration.program_mode, "progressive")

    def test_roundtrip_preserves_program_choices(self):
        configuration = Configuration(
            Bindings(bindings()), Parameters({"program_1_gangs": 6}),
            program_mode="progressive", button_program="program_1",
        )
        self.assertEqual(Configuration.from_options(configuration.as_options()), configuration)

    def test_invalid_program_option_is_rejected(self):
        for key, value in (("program_mode", "automatic"), ("program_mode", None),
                           ("button_program", "program_3")):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    Configuration.from_options(options(**{key: value}))

    def test_descending_program_temperatures_are_valid(self):
        values = Parameters({
            "target_temperature_c": 100,
            "final_temperature_c": 95,
            "program_1_start_c": 100,
            "program_1_end_c": 95,
        }).values
        self.assertEqual((values["target_temperature_c"], values["final_temperature_c"]), (100, 95))
        self.assertEqual((values["program_1_start_c"], values["program_1_end_c"]), (100, 95))

    def test_program_defaults_and_live_gang_count(self):
        values = Parameters({}).values
        self.assertEqual(values["temperature_gangs"], 4)
        self.assertEqual((values["program_1_start_c"], values["program_1_end_c"], values["program_1_gangs"]), (80, 95, 4))
        self.assertEqual((values["program_2_start_c"], values["program_2_end_c"], values["program_2_gangs"]), (70, 90, 3))
        self.assertIn("temperature_gangs", LIVE_TEMPERATURE_KEYS)
        with self.assertRaises(ParameterError):
            Parameters({"program_2_gangs": 21})

    def test_explicit_program_profiles_supply_start_end_and_distribution(self):
        parameters = Parameters({
            "program_1_start_c": 72, "program_1_end_c": 94, "program_1_gangs": 5,
        })
        selected, mode = program_parameters(parameters, "program_1")
        self.assertEqual(mode, "progressive")
        self.assertEqual(tuple(selected.values[key] for key in (
            "target_temperature_c", "final_temperature_c", "temperature_gangs")), (72, 94, 5))
        selected, mode = program_parameters(parameters, "progressive")
        self.assertEqual(mode, "progressive")
        self.assertEqual(selected, parameters)


if __name__ == "__main__":
    unittest.main()
