"""Konfiguration der Temperaturprogramme ohne Laufzeitkopplung."""
import asyncio
from datetime import timedelta
import unittest
from types import SimpleNamespace

from custom_components.ha_sauna import async_options_updated
from custom_components.ha_sauna.bindings import ROLES, Bindings
from custom_components.ha_sauna.const import CONF_BINDINGS, CONF_PARAMETERS
from custom_components.ha_sauna.core.defaults import instance_default
from custom_components.ha_sauna.core.parameters import (
    BY_KEY,
    EDITABLE_DEFINITIONS,
    LIVE_TEMPERATURE_KEYS,
    ParameterError,
    Parameters,
)
from custom_components.ha_sauna.core.program_catalog import NamedTemperatureProgram
from custom_components.ha_sauna.core.timeline import Kind
from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime
from custom_components.ha_sauna.settings import (
    ConfigurationLocked,
    async_reset_parameters,
    async_set_button_program,
    async_set_button_gesture,
    async_set_parameters,
    async_set_program_catalog,
    async_set_temperature_steps,
    program_parameters,
)
from test_foundation import T0, event


def bindings():
    return {
        role.key: f"{role.domains[0]}.test_{role.key}"
        for role in ROLES
        if not role.optional or role.device_class in {"temperature", "humidity"}
    }


def options(parameters=None, **configuration):
    return {
        CONF_BINDINGS: bindings(),
        CONF_PARAMETERS: {} if parameters is None else parameters,
        **configuration,
    }


class ProgramConfigurationTests(unittest.TestCase):
    def test_button_session_gesture_roundtrip_and_validation(self):
        baseline = Configuration.from_options(options())
        self.assertEqual(baseline.button_session_gesture,
                         instance_default("button_session_gesture"))
        for gesture in ("long", "double", "triple"):
            loaded = Configuration.from_options(options(button_session_gesture=gesture))
            self.assertEqual(Configuration.from_options(loaded.as_options()), loaded)
        for value in ("short", "single", 1, True, None, [], "quadruple"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Configuration.from_options(options(button_session_gesture=value))

    def test_binary_input_cannot_select_native_multi_clicks(self):
        configured = options(control_input_mode="button")
        configured[CONF_BINDINGS]["control_input"] = "binary_sensor.button"
        binary = Configuration.from_options(configured)
        self.assertEqual(binary.available_button_session_gestures, ("long",))
        for gesture in ("double", "triple"):
            with self.subTest(gesture=gesture), self.assertRaises(ValueError):
                Configuration.from_options({**configured, "button_session_gesture": gesture})

    def test_operation_switch_cannot_choose_a_button_gesture(self):
        runtime = SaunaRuntime(Configuration.from_options(options(control_input_mode="switch")))
        entry = SimpleNamespace(runtime_data=runtime, options=runtime.configuration.as_options())
        self.assertEqual(runtime.available_button_session_gestures, ())
        with self.assertRaises(ValueError):
            asyncio.run(async_set_button_gesture(_FakeHass(), entry, "double"))
        self.assertEqual(entry.options["button_session_gesture"],
                         instance_default("button_session_gesture"))

    def test_button_gesture_save_resets_pending_input_and_keeps_session_lock(self):
        runtime = SaunaRuntime(
            Configuration.from_options(options(control_input_mode="button")), clock=lambda: T0
        )
        entry = SimpleNamespace(runtime_data=runtime, options=runtime.configuration.as_options())
        hass = _FakeHass()
        runtime._button.handle("press", False, T0)
        asyncio.run(async_set_button_gesture(hass, entry, "double"))
        self.assertEqual(entry.options["button_session_gesture"], "double")
        self.assertIsNone(runtime._button.handle("release", False, T0))
        asyncio.run(runtime._handle_button_event("double", T0))
        self.assertTrue(runtime.session.operation_enabled)
        with self.assertRaises(ConfigurationLocked):
            asyncio.run(async_set_button_gesture(hass, entry, "triple"))
        self.assertEqual(entry.options["button_session_gesture"], "double")

    def test_button_gesture_change_waits_for_end_hold_release(self):
        runtime = SaunaRuntime(
            Configuration.from_options(options(control_input_mode="button")), clock=lambda: T0
        )
        entry = SimpleNamespace(runtime_data=runtime, options=runtime.configuration.as_options())
        hass = _FakeHass()
        runtime.controller.set_operation(True, T0)
        asyncio.run(runtime._handle_button_event("press", T0))
        asyncio.run(runtime._handle_button_event("long", T0))
        self.assertIsNone(runtime.session)
        with self.assertRaises(ConfigurationLocked):
            asyncio.run(async_set_button_gesture(hass, entry, "double"))
        asyncio.run(runtime._handle_button_event("release", T0))
        self.assertIsNone(runtime._button_hold_session_id)
        asyncio.run(async_set_button_gesture(hass, entry, "double"))
        self.assertEqual(entry.options["button_session_gesture"], "double")

    def test_button_gesture_change_preserves_start_release_after_external_finish(self):
        runtime = SaunaRuntime(
            Configuration.from_options(options(control_input_mode="button")), clock=lambda: T0
        )
        entry = SimpleNamespace(runtime_data=runtime, options=runtime.configuration.as_options())
        asyncio.run(runtime._handle_button_event("press", T0))
        asyncio.run(runtime._handle_button_event("long", T0))
        asyncio.run(runtime.set_operation(False))
        token = next(deadline.token for deadline in runtime.session.deadlines
                     if deadline.purpose == "session_gap")
        asyncio.run(runtime.finish_session_gap(token))
        self.assertIsNone(runtime.session)
        with self.assertRaises(ConfigurationLocked):
            asyncio.run(async_set_button_gesture(_FakeHass(), entry, "double"))
        asyncio.run(runtime._handle_button_event("release", T0))
        self.assertIsNone(runtime._button_start_hold_session_id)
        asyncio.run(async_set_button_gesture(_FakeHass(), entry, "double"))
        self.assertEqual(entry.options["button_session_gesture"], "double")

    def test_legacy_cold_tolerance_remains_the_lower_setpoint_distance(self):
        loaded = Configuration.from_options(options({
            "cold_tolerance_c": 2, "hot_tolerance_c": 4,
        }))
        self.assertEqual(loaded.parameters.values["readiness_hysteresis_c"], 2)
        saved = loaded.as_options()
        self.assertNotIn("cold_tolerance_c", saved[CONF_PARAMETERS])
        self.assertNotIn("hot_tolerance_c", saved[CONF_PARAMETERS])
        self.assertEqual(Configuration.from_options(saved), loaded)
        explicit = Configuration.from_options(options({
            "cold_tolerance_c": 2, "hot_tolerance_c": 4,
            "readiness_hysteresis_c": .5,
        }))
        self.assertEqual(explicit.parameters.values["readiness_hysteresis_c"], .5)
        hot_only = Configuration.from_options(options({"hot_tolerance_c": 4}))
        self.assertEqual(hot_only.parameters.values["readiness_hysteresis_c"],
                         BY_KEY["readiness_hysteresis_c"].default)
        zero_cold = Configuration.from_options(options({
            "cold_tolerance_c": 0, "hot_tolerance_c": 4,
        }))
        self.assertEqual(zero_cold.parameters.values["readiness_hysteresis_c"], 0)
        self.assertEqual(Configuration.from_options(zero_cold.as_options()), zero_cold)

    def test_control_targets_round_and_other_parameters_and_measurements_do_not(self):
        parameters = Parameters({
            "target_temperature_c": 80.5, "final_temperature_c": 89.5,
            "preset_start_c": 70.5, "readiness_hysteresis_c": .25,
        })
        self.assertEqual(parameters.values["target_temperature_c"], 81)
        self.assertEqual(parameters.values["final_temperature_c"], 90)
        self.assertEqual(parameters.values["preset_start_c"], 71)
        self.assertEqual(parameters.values["readiness_hysteresis_c"], .25)
        configuration = Configuration(
            Bindings(bindings()), parameters, button_temperature_c=82.5,
            temperature_steps=(80.5, 81.49, 89.5),
        )
        self.assertEqual(configuration.button_temperature_c, 83)
        self.assertEqual(configuration.temperature_steps, (81, 81, 90))
        self.assertEqual(Configuration.from_options(configuration.as_options()), configuration)
        runtime = SaunaRuntime(configuration, clock=lambda: T0)
        runtime.controller.set_temperature(74.375, T0)
        self.assertEqual(runtime.controller.temperature, 74.375)

    def test_live_temperature_step_and_button_inputs_persist_whole_targets(self):
        runtime = SaunaRuntime(Configuration(Bindings(bindings()), Parameters({})))
        entry = SimpleNamespace(runtime_data=runtime, options=runtime.configuration.as_options())
        hass = _FakeHass()
        asyncio.run(async_set_parameters(hass, entry, {"target_temperature_c": 80.5}, partial=True))
        self.assertEqual(runtime.controller.target_temperature, 81)
        self.assertEqual(entry.options["parameters"]["target_temperature_c"], 81)
        asyncio.run(async_set_button_program(hass, entry, "constant", 82.5))
        self.assertEqual(entry.options["button_temperature_c"], 83)
        asyncio.run(async_set_temperature_steps(hass, entry, [80.5, 82.5, 89.49]))
        self.assertEqual(runtime.configuration.temperature_steps, (81, 83, 89))
        self.assertEqual(entry.options["temperature_steps"], (81, 83, 89))
        for value in (59.9, 100.1):
            before = dict(entry.options)
            with self.subTest(value=value), self.assertRaises(ValueError):
                asyncio.run(async_set_temperature_steps(hass, entry, [80, value]))
            self.assertEqual(entry.options, before)

    def test_target_bounds_apply_before_and_after_rounding(self):
        for value in (59.9, 100.1):
            for key in ("target_temperature_c", "final_temperature_c", "preset_start_c"):
                with self.subTest(value=value, key=key), self.assertRaises(ParameterError):
                    Parameters({key: value})
            with self.assertRaises(ParameterError):
                Configuration(Bindings(bindings()), Parameters({}), button_temperature_c=value)
        with self.assertRaises(ParameterError):
            Parameters({"sauna_min_temperature_c": 60.2, "target_temperature_c": 60.3})

    def test_saved_obsolete_timer_parameters_are_discarded_and_roundtrip(self):
        for value in (None, 0, 12, "obsolete"):
            saved_parameters = {
                "mechanical_timer_minutes": value,
                "mechanical_timer_warning_minutes": value,
                "session_gap_minutes": 17,
            }
            with self.subTest(value=value):
                saved = options(saved_parameters)
                original_parameters = dict(saved_parameters)
                loaded = Configuration.from_options(saved)
                self.assertEqual(loaded.parameters.values["session_gap_minutes"], 17)
                for key in ("mechanical_timer_minutes", "mechanical_timer_warning_minutes"):
                    self.assertNotIn(key, loaded.parameters.values)
                    self.assertNotIn(key, loaded.as_options()[CONF_PARAMETERS])
                self.assertEqual(Configuration.from_options(loaded.as_options()), loaded)
                self.assertEqual(saved[CONF_PARAMETERS], original_parameters)

    def test_new_configuration_stores_factory_session_gap_explicitly(self):
        key = "session_gap_minutes"
        new = Configuration(Bindings(bindings()), Parameters({}))
        expected = Parameters({}).values[key]
        self.assertGreater(expected, 0)
        self.assertEqual(new.parameters.values[key], expected)
        self.assertEqual(new.as_options()[CONF_PARAMETERS][key], expected)
        self.assertEqual(Configuration.from_options(new.as_options()), new)

    def test_saved_override_is_adopted_before_validation_and_roundtrips(self):
        initial = Configuration(
            Bindings(bindings()),
            Parameters({"nominal_power_kw": 7}),
            program_mode="progressive",
            button_program="genusszeit",
            selected_program_id="genusszeit",
            temperature_steps=(80, 85, 95),
            log_level="DEBUG",
            control_input_mode="button",
        )
        for old, expected in (
            (20, 10),
            (1_000_000, 10),
            (0.5, 0.5),
            (10, 10),
            (None, 10),
        ):
            with self.subTest(old=old):
                saved = initial.as_options()
                if old is None:
                    saved[CONF_PARAMETERS].pop("manual_override_minutes")
                else:
                    saved[CONF_PARAMETERS]["manual_override_minutes"] = old
                loaded = Configuration.from_options(saved)
                effective = loaded.as_options()
                self.assertEqual(
                    loaded.parameters.values["manual_override_minutes"], expected
                )
                self.assertEqual(
                    effective[CONF_PARAMETERS]["manual_override_minutes"], expected
                )
                self.assertEqual(Configuration.from_options(effective), loaded)
                self.assertEqual(
                    {
                        key: value
                        for key, value in effective.items()
                        if key != CONF_PARAMETERS
                    },
                    {
                        key: value
                        for key, value in initial.as_options().items()
                        if key != CONF_PARAMETERS
                    },
                )
                self.assertEqual(loaded.parameters.values["nominal_power_kw"], 7)
                if old is not None:
                    self.assertEqual(
                        saved[CONF_PARAMETERS]["manual_override_minutes"], old
                    )

    def test_saved_override_adoption_does_not_heal_previously_invalid_values(self):
        cases = (
            (True, "invalid_number"),
            ("20", "invalid_number"),
            (None, "invalid_number"),
            ([], "invalid_number"),
            (float("nan"), "invalid_number"),
            (float("inf"), "invalid_number"),
            (float("-inf"), "invalid_number"),
            (10**400, "invalid_number"),
            (0, "positive"),
            (-1, "positive"),
            (1_000_001, "too_large"),
        )
        for value, code in cases:
            with self.subTest(value=value), self.assertRaises(ParameterError) as raised:
                Configuration.from_options(options({"manual_override_minutes": value}))
            self.assertEqual(
                (raised.exception.key, raised.exception.code),
                ("manual_override_minutes", code),
            )

    def test_common_temperature_minimum_validates_live_targets_and_ui_metadata(self):
        minimum = BY_KEY["sauna_min_temperature_c"].default
        for key in ("preset_start_c", "target_temperature_c", "final_temperature_c"):
            maximum = BY_KEY[key].maximum
            with self.subTest(key=key, value=minimum - 1):
                with self.assertRaisesRegex(ParameterError, f"{key}: too_small"):
                    Parameters({key: minimum - 1})
            for value in (minimum, maximum):
                with self.subTest(key=key, value=value):
                    self.assertEqual(Parameters({key: value}).values[key], value)

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
        self.assertEqual(configuration.parameters.values["final_temperature_c"], BY_KEY["final_temperature_c"].default)

    def test_button_constant_temperature_is_frozen_and_legacy_current_is_normalized(self):
        default = instance_default("button_temperature_c")
        configuration = Configuration.from_options(options({"target_temperature_c": 83}))
        self.assertEqual((configuration.button_program, configuration.button_temperature_c), ("constant", default))
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
            (fallback.button_program, fallback.button_temperature_c), ("constant", default)
        )
        explicit = Configuration.from_options(options(
            {"target_temperature_c": 83}, button_temperature_c=74,
        ))
        self.assertEqual(explicit.button_temperature_c, 74)
        self.assertEqual(Configuration.from_options(explicit.as_options()), explicit)

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
        self.assertEqual(configuration.parameters.values["sauna_min_temperature_c"], BY_KEY["sauna_min_temperature_c"].default)

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
        for key in (
            "temperature_gangs", "program_1_start_c", "program_1_end_c",
            "program_1_gangs", "program_2_start_c", "program_2_end_c", "program_2_gangs",
        ):
            self.assertEqual(values[key], BY_KEY[key].default)
        self.assertIn("temperature_gangs", LIVE_TEMPERATURE_KEYS)
        with self.assertRaises(ParameterError):
            Parameters({"program_2_gangs": BY_KEY["program_2_gangs"].maximum + 1})

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
        program = configuration.temperature_programs[-1]
        selected, mode = program_parameters(
            configuration.parameters,
            program.id,
            catalog=configuration.temperature_programs,
        )
        self.assertEqual(mode, "progressive")
        self.assertEqual(
            tuple(
                selected.values[key]
                for key in ("target_temperature_c", "final_temperature_c", "temperature_gangs")
            ),
            (program.start_c, program.end_c, program.distribution_gangs),
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
        async def select_even(active):
            configuration = Configuration(Bindings(bindings()), Parameters({}))
            runtime = SaunaRuntime(configuration, clock=lambda: T0)
            entry = SimpleNamespace(
                runtime_data=runtime, options=configuration.as_options(), entry_id="even"
            )
            hass = _FakeHass()
            await async_set_temperature_steps(hass, entry, [80, 86, 90])
            if active:
                runtime._set_operation(True)
            await async_set_parameters(
                hass,
                entry,
                {
                    "target_temperature_c": 80,
                    "final_temperature_c": 90,
                    "temperature_gangs": 3,
                },
                partial=True,
                explicit_target=False,
                program_mode="progressive",
                new_program=True,
            )
            await async_options_updated(hass, entry)
            self.assertIs(entry.runtime_data, runtime)
            self.assertIsNone(runtime.configuration.temperature_steps)
            self.assertIsNone(entry.options["temperature_steps"])
            self.assertIsNone(runtime.controller.temperature_steps)
            if not active:
                runtime._set_operation(True)
            runtime.controller.set_temperature(
                80, T0, valid_until=T0 + timedelta(seconds=60)
            )
            for second, kind in enumerate(
                (Kind.DOOR_CLOSE, Kind.INFUSION, Kind.DOOR_OPEN, Kind.VENTILATION), 10
            ):
                runtime.controller.process(
                    event(str(second), kind, second, runtime.session.session_id)
                )
            self.assertEqual(runtime.session.timeline.gang_count, 1)
            self.assertEqual(runtime.controller.target_temperature, 85)

        for active in (False, True):
            with self.subTest(active=active):
                asyncio.run(select_even(active))

    def test_repeated_end_value_keeps_active_manual_steps_and_saved_shape(self):
        async def repeat_end():
            configuration = Configuration(Bindings(bindings()), Parameters({}))
            runtime = SaunaRuntime(configuration)
            entry = SimpleNamespace(runtime_data=runtime, options=configuration.as_options())
            hass = _FakeHass()
            await async_set_temperature_steps(hass, entry, [80, 86, 90])
            runtime._set_operation(True)
            before = runtime.session.temperature_program_steps
            await async_set_parameters(
                hass, entry, {"final_temperature_c": 90}, partial=True
            )
            self.assertEqual(runtime.session.temperature_program_steps, before)
            self.assertEqual(runtime.configuration.temperature_steps, (80, 86, 90))
            self.assertEqual(entry.options["temperature_steps"], (80, 86, 90))

        asyncio.run(repeat_end())

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
            Parameters({"nominal_power_kw": 7, "session_gap_minutes": 17}),
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
        self.assertEqual(
            reset.parameters.values["session_gap_minutes"],
            Parameters({}).values["session_gap_minutes"],
        )
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
        return True


class _FakeHass:
    config_entries = _FakeConfigEntries()


if __name__ == "__main__":
    unittest.main()
