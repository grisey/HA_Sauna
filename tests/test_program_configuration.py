"""Konfiguration der Temperaturprogramme ohne Laufzeitkopplung."""
import asyncio
import unittest
from types import SimpleNamespace

from custom_components.ha_sauna import async_options_updated
from custom_components.ha_sauna.bindings import ROLES, Bindings
from custom_components.ha_sauna.const import CONF_BINDINGS, CONF_PARAMETERS
from custom_components.ha_sauna.core.parameters import (
    EDITABLE_DEFINITIONS,
    LIVE_TEMPERATURE_KEYS,
    ParameterError,
    Parameters,
)
from custom_components.ha_sauna.core.program_catalog import NamedTemperatureProgram
from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime
from custom_components.ha_sauna.settings import (
    ConfigurationLocked,
    async_reset_parameters,
    async_set_button_program,
    async_set_parameters,
    async_set_program_catalog,
    async_set_temperature_steps,
    program_parameters,
)


def bindings():
    return {role.key: f"{role.domains[0]}.test_{role.key}" for role in ROLES if not role.optional}


def options(parameters=None, **configuration):
    return {
        CONF_BINDINGS: bindings(),
        CONF_PARAMETERS: {} if parameters is None else parameters,
        **configuration,
    }


class ProgramConfigurationTests(unittest.TestCase):
    def test_common_temperature_minimum_validates_live_targets_and_ui_metadata(self):
        for key in ("preset_start_c", "target_temperature_c", "final_temperature_c"):
            with self.subTest(key=key, value=59):
                with self.assertRaisesRegex(ParameterError, f"{key}: too_small"):
                    Parameters({key: 59})
            with self.subTest(key=key, value=60):
                self.assertEqual(Parameters({key: 60}).values[key], 60)
            with self.subTest(key=key, value=100):
                self.assertEqual(Parameters({key: 100}).values[key], 100)

        parameters = Parameters({"sauna_min_temperature_c": 65})
        self.assertEqual(parameters.minimum_for("target_temperature_c"), 65)
        self.assertEqual(parameters.minimum_for("final_temperature_c"), 65)
        self.assertEqual(parameters.minimum_for("preset_start_c"), 65)
        for key in ("preset_start_c", "target_temperature_c", "final_temperature_c"):
            with self.subTest(key=key, minimum=65):
                with self.assertRaisesRegex(ParameterError, f"{key}: too_small"):
                    Parameters({"sauna_min_temperature_c": 65, key: 64})

    def test_legacy_profiles_and_additional_door_limit_remain_loadable_but_hidden(self):
        values = Parameters(
            {
                "program_1_start_c": 45,
                "program_1_end_c": 55,
                "door_heating_max_temperature_c": 70,
            }
        ).values
        self.assertEqual((values["program_1_start_c"], values["program_1_end_c"]), (45, 55))
        editable_keys = {definition.key for definition in EDITABLE_DEFINITIONS}
        self.assertNotIn("door_heating_max_temperature_c", editable_keys)
        self.assertTrue(
            {
                "program_1_start_c",
                "program_1_end_c",
                "program_1_gangs",
                "program_2_start_c",
                "program_2_end_c",
                "program_2_gangs",
            }.isdisjoint(editable_keys)
        )

    def test_legacy_without_final_temperature_is_constant(self):
        configuration = Configuration.from_options(options())
        self.assertEqual(configuration.program_mode, "constant")
        self.assertEqual(configuration.parameters.values["final_temperature_c"], 95)

    def test_button_constant_temperature_is_frozen_and_legacy_current_is_normalized(self):
        configuration = Configuration.from_options(options({"target_temperature_c": 83}))
        self.assertEqual((configuration.button_program, configuration.button_temperature_c), ("constant", 83))
        current = Configuration.from_options(
            options(
                {"target_temperature_c": 83},
                button_program="current",
                selected_program_id="gipfelstuermer",
            )
        )
        self.assertEqual(current.button_program, "gipfelstuermer")
        fallback = Configuration.from_options(
            options({"target_temperature_c": 83}, button_program="current")
        )
        self.assertEqual(
            (fallback.button_program, fallback.button_temperature_c), ("constant", 83)
        )

    def test_button_constant_temperature_is_independent_of_live_ui_target(self):
        configuration = Configuration(
            Bindings(bindings()), Parameters({"target_temperature_c": 80}), button_temperature_c=72
        )
        runtime = SaunaRuntime(configuration)
        entry = SimpleNamespace(runtime_data=runtime, options=configuration.as_options())
        hass = _FakeHass()

        asyncio.run(async_set_button_program(hass, entry, "constant", 74))
        self.assertIsNone(runtime.session)
        self.assertEqual(runtime.controller.target_temperature, 80)
        self.assertEqual(runtime.configuration.button_temperature_c, 74)
        asyncio.run(
            async_set_parameters(
                hass, entry, {"target_temperature_c": 86}, partial=True
            )
        )

        self.assertEqual(runtime.configuration.parameters.values["target_temperature_c"], 86)
        self.assertEqual(runtime.configuration.button_temperature_c, 74)
        self.assertEqual(entry.options["button_temperature_c"], 74)

    def test_invalid_button_temperature_has_no_side_effects(self):
        configuration = Configuration(Bindings(bindings()), Parameters({}))
        runtime = SaunaRuntime(configuration)
        entry = SimpleNamespace(runtime_data=runtime, options=configuration.as_options())
        hass = _FakeHass()
        before_configuration = runtime.configuration
        before_options = dict(entry.options)

        with self.assertRaisesRegex(ParameterError, "target_temperature_c: too_small"):
            asyncio.run(async_set_button_program(hass, entry, "constant", 59))

        self.assertEqual(runtime.configuration, before_configuration)
        self.assertEqual(entry.options, before_options)

    def test_raising_minimum_rejects_button_or_catalog_before_writing(self):
        for configuration, minimum, error in (
            (
                Configuration(
                    Bindings(bindings()),
                    Parameters({"target_temperature_c": 90}),
                    button_temperature_c=74,
                ),
                75,
                "button_temperature_invalid",
            ),
            (
                Configuration(
                    Bindings(bindings()),
                    Parameters({"target_temperature_c": 100}),
                    button_temperature_c=80,
                ),
                75,
                "program_catalog_invalid",
            ),
        ):
            with self.subTest(minimum=minimum):
                runtime = SaunaRuntime(configuration)
                entry = SimpleNamespace(
                    runtime_data=runtime, options=configuration.as_options()
                )
                before_configuration = runtime.configuration
                before_options = dict(entry.options)

                with self.assertRaisesRegex(ParameterError, error):
                    asyncio.run(
                        async_set_parameters(
                            _FakeHass(),
                            entry,
                            {
                                "sauna_min_temperature_c": minimum,
                                "preset_start_c": minimum,
                            },
                            partial=True,
                        )
                    )

                self.assertEqual(runtime.configuration, before_configuration)
                self.assertEqual(entry.options, before_options)

    def test_button_program_change_observes_session_and_reconfiguration_locks(self):
        configuration = Configuration(Bindings(bindings()), Parameters({}))
        runtime = SaunaRuntime(configuration)
        entry = SimpleNamespace(runtime_data=runtime, options=configuration.as_options())
        hass = _FakeHass()
        runtime.reconfiguring = True
        with self.assertRaises(ConfigurationLocked):
            asyncio.run(async_set_button_program(hass, entry, "constant", 75))
        runtime.reconfiguring = False
        runtime._set_operation(True)
        with self.assertRaises(ConfigurationLocked):
            asyncio.run(async_set_button_program(hass, entry, "constant", 75))

    def test_legacy_session_light_duration_is_ignored(self):
        configuration = Configuration.from_options(
            options({"session_light_minutes": 10, "session_gap_minutes": 17})
        )
        self.assertNotIn("session_light_minutes", configuration.parameters.values)
        self.assertEqual(configuration.parameters.values["session_gap_minutes"], 17)
        with self.assertRaisesRegex(ParameterError, "base: unknown_parameter"):
            Configuration.from_options(options({"unexpected_parameter": 1}))

    def test_legacy_with_final_temperature_is_progressive(self):
        configuration = Configuration.from_options(options({"final_temperature_c": 92}))
        self.assertEqual(configuration.program_mode, "progressive")

    def test_legacy_target_below_new_minimum_is_preserved(self):
        configuration = Configuration.from_options(options({"target_temperature_c": 50}))
        self.assertEqual(configuration.parameters.values["sauna_min_temperature_c"], 50)
        self.assertEqual(configuration.parameters.values["target_temperature_c"], 50)

    def test_legacy_program_below_new_minimum_keeps_button_selection(self):
        configuration = Configuration.from_options(
            options({"program_1_start_c": 45}, button_program="program_1")
        )
        self.assertEqual(configuration.parameters.values["sauna_min_temperature_c"], 45)
        self.assertEqual(configuration.parameters.values["program_1_start_c"], 45)
        self.assertEqual(configuration.button_program, "program_1")
        self.assertIn("program_1", {program.id for program in configuration.temperature_programs})

    def test_explicit_minimum_remains_strict_for_legacy_target(self):
        with self.assertRaisesRegex(ParameterError, "target_temperature_c: too_small"):
            Configuration.from_options(
                options({"sauna_min_temperature_c": 60, "target_temperature_c": 50})
            )

    def test_legacy_defaults_keep_new_minimum(self):
        configuration = Configuration.from_options(options())
        self.assertEqual(configuration.parameters.values["sauna_min_temperature_c"], 60)

    def test_roundtrip_preserves_program_choices(self):
        configuration = Configuration(
            Bindings(bindings()), Parameters({"program_1_gangs": 6}),
            program_mode="progressive", button_program="genusszeit",
            selected_program_id="genusszeit",
        )
        self.assertEqual(Configuration.from_options(configuration.as_options()), configuration)

    def test_direct_legacy_button_construction_serializes_a_valid_catalog(self):
        configuration = Configuration(
            Bindings(bindings()), Parameters({}), button_program="program_1"
        )
        restored = Configuration.from_options(configuration.as_options())
        self.assertEqual(restored.button_program, "program_1")
        self.assertIn("program_1", {program.id for program in restored.temperature_programs})

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

    def test_catalog_program_supplies_existing_controller_values(self):
        configuration = Configuration.from_options(options())
        selected, mode = program_parameters(
            configuration.parameters,
            "gipfelstuermer",
            catalog=configuration.temperature_programs,
        )
        self.assertEqual(mode, "progressive")
        self.assertEqual(
            tuple(
                selected.values[key]
                for key in ("target_temperature_c", "final_temperature_c", "temperature_gangs")
            ),
            (84, 100, 3),
        )

    def test_catalog_and_selected_name_roundtrip(self):
        configuration = Configuration.from_options(
            options(
                temperature_programs=[
                    {
                        "id": "ruhig",
                        "name": "Ruhig",
                        "start_c": 70,
                        "end_c": 85,
                        "distribution_gangs": 2,
                    }
                ],
                selected_program_id="ruhig",
                button_program="ruhig",
                control_mode="manual",
            )
        )
        self.assertEqual(Configuration.from_options(configuration.as_options()), configuration)

    def test_free_manual_steps_roundtrip_and_respect_the_common_bounds(self):
        configuration = Configuration(
            Bindings(bindings()), Parameters({}), temperature_steps=(80, 86, 90)
        )
        self.assertEqual(Configuration.from_options(configuration.as_options()), configuration)
        with self.assertRaises(ValueError):
            Configuration(
                Bindings(bindings()), Parameters({}), temperature_steps=(59, 80)
            )

    def test_manual_steps_use_the_live_program_path_and_direct_target_clears_them(self):
        configuration = Configuration(Bindings(bindings()), Parameters({}))
        runtime = SaunaRuntime(configuration)
        entry = SimpleNamespace(runtime_data=runtime, options=configuration.as_options())
        hass = _FakeHass()
        asyncio.run(async_set_temperature_steps(hass, entry, [80, 86, 90]))
        self.assertEqual(runtime.configuration.temperature_steps, (80, 86, 90))
        self.assertEqual(
            tuple(
                runtime.configuration.parameters.values[key]
                for key in ("target_temperature_c", "final_temperature_c", "temperature_gangs")
            ),
            (80, 90, 3),
        )
        asyncio.run(
            async_set_parameters(
                hass, entry, {"target_temperature_c": 82}, partial=True
            )
        )
        self.assertIsNone(runtime.configuration.temperature_steps)

    def test_even_program_request_explicitly_replaces_free_steps(self):
        configuration = Configuration(Bindings(bindings()), Parameters({}))
        runtime = SaunaRuntime(configuration)
        entry = SimpleNamespace(runtime_data=runtime, options=configuration.as_options())
        hass = _FakeHass()
        asyncio.run(async_set_temperature_steps(hass, entry, [80, 86, 90]))
        asyncio.run(
            async_set_parameters(
                hass,
                entry,
                {
                    "target_temperature_c": 70,
                    "final_temperature_c": 100,
                    "temperature_gangs": 4,
                },
                partial=True,
                explicit_target=False,
                program_mode="progressive",
                new_program=True,
            )
        )
        self.assertIsNone(runtime.configuration.temperature_steps)
        self.assertEqual(runtime.controller.target_temperature, 70)

    def test_explicit_catalog_does_not_accept_removed_legacy_profiles(self):
        parameters = Parameters({})
        with self.assertRaises(ValueError):
            program_parameters(
                parameters,
                "program_1",
                catalog=(NamedTemperatureProgram("neu", "Neu", 70, 80, 2),),
            )

    def test_renaming_selected_program_keeps_current_values(self):
        program = NamedTemperatureProgram("ruhig", "Ruhig", 70, 85, 2)
        configuration = Configuration(
            Bindings(bindings()),
            Parameters({"target_temperature_c": 82}),
            temperature_programs=(program,),
            selected_program_id="ruhig",
        )
        runtime = SaunaRuntime(configuration)
        entry = SimpleNamespace(runtime_data=runtime, options=configuration.as_options())
        hass = _FakeHass()
        asyncio.run(
            async_set_program_catalog(
                hass,
                entry,
                [{**program.as_dict(), "name": "Abendruhe"}],
            )
        )
        self.assertEqual(runtime.configuration.parameters.values["target_temperature_c"], 82)
        self.assertEqual(runtime.configuration.temperature_programs[0].name, "Abendruhe")

    def test_reset_restores_all_software_options_but_not_hardware_inputs(self):
        configuration = Configuration(
            Bindings(bindings()),
            Parameters({"nominal_power_kw": 7}),
            log_level="DEBUG",
            control_input_mode="button",
            button_event_type="press",
            button_program="genusszeit",
            selected_program_id="genusszeit",
            control_mode="manual",
        )
        runtime = SaunaRuntime(configuration)
        entry = SimpleNamespace(runtime_data=runtime, options=configuration.as_options())
        hass = _FakeHass()
        asyncio.run(async_reset_parameters(hass, entry))
        reset = Configuration.from_options(entry.options)
        self.assertEqual(reset.parameters.values["nominal_power_kw"], 4.5)
        self.assertEqual(reset.log_level, "INFO")
        self.assertEqual(reset.button_program, "constant")
        self.assertEqual(reset.selected_program_id, None)
        self.assertEqual(reset.control_mode, "automatic")
        self.assertEqual(reset.bindings, configuration.bindings)
        self.assertEqual(reset.control_input_mode, "button")
        self.assertEqual(reset.button_event_type, "press")
        self.assertTrue(runtime.reconfiguring)

    def test_reset_without_reload_allows_next_start(self):
        async def reset_and_start(parameters, level):
            configuration = Configuration(
                Bindings(bindings()), Parameters(parameters), log_level=level
            )
            runtime = SaunaRuntime(configuration)
            entry = SimpleNamespace(
                runtime_data=runtime,
                options=configuration.as_options(),
                entry_id="test-reset",
            )
            hass = _FakeHass()
            previous_options = entry.options
            await async_reset_parameters(hass, entry)
            # Home Assistant calls listeners only for changed options.
            if entry.options != previous_options:
                await async_options_updated(hass, entry)
            runtime = entry.runtime_data
            self.assertFalse(runtime.reconfiguring)
            self.assertEqual(runtime.configuration.parameters, Parameters({}))
            self.assertEqual(runtime.configuration.log_level, "INFO")
            self.assertEqual(runtime.configuration.bindings, configuration.bindings)
            await runtime.set_operation(True)
            self.assertTrue(runtime.session.operation_enabled)

        for parameters, level in (
            ({}, "INFO"),
            ({}, "DEBUG"),
            ({"target_temperature_c": 81}, "INFO"),
        ):
            with self.subTest(parameters=parameters, level=level):
                asyncio.run(reset_and_start(parameters, level))


class _FakeConfigEntries:
    def async_update_entry(self, entry, *, options):
        self.entry = entry
        if entry.options == options:
            return False
        entry.options = options
        return True

    async def async_reload(self, entry_id):
        assert entry_id == self.entry.entry_id
        self.entry.runtime_data = SaunaRuntime(
            Configuration.from_options(self.entry.options)
        )


class _FakeHass:
    config_entries = _FakeConfigEntries()


if __name__ == "__main__":
    unittest.main()
