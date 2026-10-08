"""API-Smoketests gegen Home Assistant; keine laufende Nutzerinstallation.

Der HA-Manager und Entitätsbestand sind isolierte Testdoubles. Schema-, Flow-
und Selektorklassen stammen aus dem tatsächlich installierten Home Assistant.
"""
import importlib.util
import os
from math import floor, inf, nextafter
from pathlib import Path
from types import SimpleNamespace
import unittest
import tempfile
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

from custom_components.ha_sauna.core.parameters import BY_KEY, EDITABLE_DEFINITIONS, Parameters
from custom_components.ha_sauna.core.defaults import instance_default
from custom_components.ha_sauna.core.program_catalog import DEFAULT_PROGRAMS
from custom_components.ha_sauna.bindings import ROLES

HA_AVAILABLE = importlib.util.find_spec("homeassistant") is not None
if os.environ.get("HA_TEST_REQUIRED") and not HA_AVAILABLE:
    raise RuntimeError("Für diesen Testlauf muss Home Assistant installiert sein")


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant ist lokal nicht installiert")
class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from homeassistant.core import State
        from custom_components.ha_sauna.config_flow import SaunaConfigFlow
        self.module = __import__("custom_components.ha_sauna.config_flow", fromlist=["*"])
        self.inputs = {
            r.key: f"{r.domains[0]}.test_{r.key}"
            for r in ROLES
            if not r.optional or r.device_class in {"temperature", "humidity"}
        }
        self.values = {d.key: d.default for d in EDITABLE_DEFINITIONS}
        self.values.update(session_gap_minutes=2.5)
        self.expected_values = Parameters(self.values).as_dict()
        self.states = {
            self.inputs[r.key]: State(self.inputs[r.key], "unavailable", {
                "device_class": "button" if r.key == "control_input" else r.device_class,
                "event_types": ["single_push"], "unit_of_measurement": r.unit,
                "supported_color_modes": ["brightness"],
            })
            for r in ROLES
            if not r.optional or r.device_class in {"temperature", "humidity"}
        }
        self.entries = []
        self.entry = SimpleNamespace(
            entry_id="example", options={"bindings": self.inputs, "parameters": self.values},
            async_on_unload=lambda callback: None,
            add_update_listener=lambda listener: lambda: None,
        )
        self.hass = SimpleNamespace(
            data={},
            http=SimpleNamespace(register_view=MagicMock(), async_register_static_paths=AsyncMock()),
            bus=SimpleNamespace(async_listen=lambda *args: lambda: None, async_fire=MagicMock()),
            states=SimpleNamespace(get=self.states.get, async_all=lambda: list(self.states.values())),
            config_entries=SimpleNamespace(
                async_entries=lambda *a, **kw: self.entries,
                async_get_entry=lambda *a: self.entry,
                async_reload=AsyncMock(),
                async_update_entry=lambda entry, **kwargs: setattr(entry, "options", kwargs["options"]),
                async_forward_entry_setups=AsyncMock(),
                async_unload_platforms=AsyncMock(return_value=True),
            ),
        )
        self.registry_entries = {}
        registry = SimpleNamespace(async_get=self.registry_entries.get)
        registry_patch = patch.object(self.module.er, "async_get", return_value=registry)
        registry_patch.start()
        self.addCleanup(registry_patch.stop)
        self.flow = SaunaConfigFlow()
        self.flow.hass = self.hass
        self.flow.handler = "ha_sauna"
        self.flow.context = {"source": "user"}
        self.temp = tempfile.TemporaryDirectory()
        self.hass.config = SimpleNamespace(path=lambda *parts: str(Path(self.temp.name).joinpath(*parts)))

    async def asyncTearDown(self):
        if getattr(self.entry, "runtime_data", None) and not self.entry.runtime_data.closed:
            await self.entry.runtime_data.close()
        self.temp.cleanup()

    async def test_initial_form_and_real_selectors(self):
        form = await self.flow.async_step_user()
        self.assertEqual(form["step_id"], "user")
        validated = form["data_schema"]({"name": "Testsauna", **self.inputs})
        self.assertEqual(validated["heater"], self.inputs["heater"])

    async def test_binding_candidates_share_metadata_validation(self):
        from homeassistant.core import State
        self.states.update({
            "sensor.fahrenheit": State("sensor.fahrenheit", "80", {
                "device_class": "temperature", "unit_of_measurement": "°F"}),
            "sensor.status": State("sensor.status", "ok"),
            "light.onoff": State("light.onoff", "on", {"supported_color_modes": ["onoff"]}),
            "sensor.wind": State("sensor.wind", "W", {
                "device_class": "wind_direction", "unit_of_measurement": "°"}),
        })
        schema = self.module.binding_schema(self.hass)
        selectors = {str(key): value for key, value in schema.schema.items()}
        self.assertNotIn("sensor.fahrenheit", selectors["upper_temperature"].config["include_entities"])
        self.assertEqual(selectors["upper_status"].config["include_entities"], ["sensor.status"])
        self.assertNotIn("light.onoff", selectors["light"].config["include_entities"])
        self.assertEqual(selectors["environment_wind_direction"].config["include_entities"], ["sensor.wind"])
        self.assertEqual(selectors["environment_pressure"].config["include_entities"], [])
        from custom_components.ha_sauna.bindings import BindingError
        with self.assertRaises(BindingError):
            self.module.checked_bindings(self.hass, {
                **self.inputs, "environment_pressure": "sensor.fahrenheit",
            })

    async def test_internal_sauna_switch_is_not_hardware_or_feedback(self):
        from homeassistant.core import State
        from custom_components.ha_sauna.bindings import BindingError
        for identity in ("same", "other"):
            entity = f"switch.{identity}"
            self.states[entity] = State(entity, "off")
            self.registry_entries[entity] = SimpleNamespace(platform="ha_sauna", config_entry_id=identity)
        self.states["switch.hardware"] = State("switch.hardware", "off")
        self.registry_entries["switch.hardware"] = SimpleNamespace(platform="shelly", config_entry_id="hardware")
        selectors = {str(key): value for key, value in self.module.binding_schema(self.hass).schema.items()}
        for role in ("heater", "heater_feedback"):
            self.assertIn("switch.hardware", selectors[role].config["include_entities"])
            for identity in ("same", "other"):
                entity = f"switch.{identity}"
                self.assertNotIn(entity, selectors[role].config["include_entities"])
                values = {**self.inputs, role: entity}
                with self.assertRaises(BindingError) as caught:
                    self.module.checked_bindings(self.hass, values, saved=values)
                self.assertEqual(caught.exception.code, "internal_control_source")
        # An unavailable saved source cannot bypass the registry check.
        del self.states["switch.same"]
        values = {**self.inputs, "heater": "switch.same"}
        with self.assertRaises(BindingError):
            self.module.checked_bindings(self.hass, values, saved=values)

    async def test_unavailable_metadata_and_missing_saved_binding(self):
        from homeassistant.core import State
        from custom_components.ha_sauna.bindings import BindingError
        saved = dict(self.inputs)
        entity = saved["upper_temperature"]
        # Existing unavailable states retain their metadata and stay selectable.
        self.assertEqual(self.module.checked_bindings(self.hass, self.inputs).values["upper_temperature"], entity)
        del self.states[entity]
        selectors = {str(key): value for key, value in
                     self.module.binding_schema(self.hass, saved=saved).schema.items()}
        self.assertIn(entity, selectors["upper_temperature"].config["include_entities"])
        self.assertEqual(self.module.checked_bindings(self.hass, self.inputs, saved=saved).values["upper_temperature"], entity)
        with self.assertRaises(BindingError):
            self.module.checked_bindings(self.hass, self.inputs)
        self.states[entity] = State(entity, "unavailable", {
            "device_class": "temperature", "unit_of_measurement": "°F"})
        with self.assertRaises(BindingError):
            self.module.checked_bindings(self.hass, self.inputs, saved=saved)

    async def test_complete_flow_stores_one_source(self):
        form = await self.flow.async_step_user({"name": "Testsauna", **self.inputs})
        self.assertEqual(form["step_id"], "parameters")
        values = form["data_schema"](
            {
                **self.values,
                "button_program": "gipfelstuermer",
                "button_temperature_c": 82,
            }
        )
        result = await self.flow.async_step_parameters(values)
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"], {})
        self.assertEqual(result["options"]["parameters"], self.expected_values)
        self.assertEqual(result["options"]["bindings"], self.inputs)
        self.assertEqual(result["options"]["program_mode"], instance_default("program_mode", setup=True))
        self.assertEqual(result["options"]["button_program"], "gipfelstuermer")
        self.assertEqual(
            result["options"]["button_temperature_c"],
            82,
        )
        self.assertEqual(
            result["options"]["temperature_programs"],
            [program.as_dict() for program in DEFAULT_PROGRAMS],
        )

    async def test_setup_override_limit_is_field_specific_and_keeps_fractions(self):
        await self.flow.async_step_user({"name": "Testsauna", **self.inputs})
        maximum = BY_KEY["manual_override_minutes"].maximum
        for value in (nextafter(maximum, inf), maximum * 2):
            with self.subTest(value=value):
                form = await self.flow.async_step_parameters(
                    {
                        **self.values,
                        "manual_override_minutes": value,
                    }
                )
                self.assertEqual(
                    form["errors"], {"manual_override_minutes": "too_large"}
                )
                self.assertEqual(self.entries, [])
        for value in (maximum, 0.5):
            with self.subTest(value=value):
                result = await self.flow.async_step_parameters(
                    {
                        **self.values,
                        "manual_override_minutes": value,
                    }
                )
                self.assertEqual(result["type"], "create_entry")
                self.assertEqual(
                    result["options"]["parameters"]["manual_override_minutes"], value
                )

    async def test_either_single_measurement_pair_can_be_configured(self):
        for position in ("upper", "lower"):
            with self.subTest(position=position):
                inputs = {
                    key: value
                    for key, value in self.inputs.items()
                    if not key.startswith(("upper_", "lower_"))
                    or key.startswith(f"{position}_")
                }
                initial_form = await self.flow.async_step_user()
                initial_form["data_schema"]({"name": "Testsauna", **inputs})
                form = await self.flow.async_step_user({"name": "Testsauna", **inputs})
                self.assertEqual(form["step_id"], "parameters")
                result = await self.flow.async_step_parameters(self.values)
                self.assertEqual(result["options"]["bindings"], inputs)

    async def test_missing_or_incomplete_measurement_pair_stays_in_form(self):
        for omitted, error in (
            (
                {
                    "upper_temperature", "upper_humidity",
                    "lower_temperature", "lower_humidity",
                },
                {"base": "sensor_pair_required"},
            ),
            ({"lower_humidity"}, {"lower_humidity": "entity_required"}),
            ({"upper_temperature"}, {"upper_temperature": "entity_required"}),
        ):
            with self.subTest(omitted=omitted):
                inputs = {
                    key: value
                    for key, value in self.inputs.items()
                    if key not in omitted
                }
                form = await self.flow.async_step_user({"name": "Testsauna", **inputs})
                self.assertEqual(form["errors"], error)

    async def test_initial_button_uses_catalog_defaults(self):
        form = await self.flow.async_step_user({"name": "Testsauna", **self.inputs})
        values = form["data_schema"](self.values)
        self.assertEqual(values["button_program"], instance_default("button_program", setup=True))
        self.assertEqual(values["button_temperature_c"], instance_default("button_temperature_c"))

    async def test_duplicate_sensor_stays_in_form(self):
        inputs = {**self.inputs, "lower_temperature": self.inputs["upper_temperature"]}
        form = await self.flow.async_step_user({"name": "Testsauna", **inputs})
        self.assertEqual(form["errors"], {"lower_temperature": "duplicate_sensor"})

    async def test_wrong_unit_stays_in_form(self):
        from homeassistant.core import State
        entity = self.inputs["upper_temperature"]
        self.states[entity] = State(entity, "100", {"device_class": "temperature", "unit_of_measurement": "°F"})
        form = await self.flow.async_step_user({"name": "Testsauna", **self.inputs})
        self.assertEqual(form["errors"], {"upper_temperature": "wrong_unit"})

    async def test_parameter_error_is_field_specific(self):
        await self.flow.async_step_user({"name": "Testsauna", **self.inputs})
        form = await self.flow.async_step_parameters({**self.values, "session_gap_minutes": -1})
        self.assertEqual(form["errors"], {"session_gap_minutes": "positive"})

    async def test_initial_catalog_must_fit_the_selected_minimum_temperature(self):
        await self.flow.async_step_user({"name": "Testsauna", **self.inputs})
        form = await self.flow.async_step_parameters(
            {
                **self.values,
                "sauna_min_temperature_c": 80,
                "preset_start_c": 80,
                "target_temperature_c": 80,
                "final_temperature_c": 80,
            }
        )
        self.assertEqual(
            form["errors"],
            {"sauna_min_temperature_c": "program_catalog_invalid"},
        )

    async def test_existing_heater_cannot_be_claimed_twice(self):
        self.entries.append(self.entry)
        form = await self.flow.async_step_user({"name": "Testsauna", **self.inputs})
        self.assertEqual(form["errors"], {"heater": "heater_already_used"})

    async def test_parallel_flow_is_rechecked_on_submit(self):
        await self.flow.async_step_user({"name": "Testsauna", **self.inputs})
        self.entries.append(self.entry)
        result = await self.flow.async_step_parameters(self.values)
        self.assertEqual(result["reason"], "heater_already_used")

    async def test_options_preserve_normalized_button_settings_without_runtime(self):
        self.entry.options = {
            **self.entry.options,
            "button_program": "current",
            "button_temperature_c": 79,
            "selected_program_id": "gipfelstuermer",
            "temperature_programs": [
                program.as_dict() for program in DEFAULT_PROGRAMS
            ],
        }
        flow = self.module.SaunaOptionsFlow()
        flow.hass = self.hass
        flow.handler = self.entry.entry_id
        flow.context = {"source": "options"}
        with patch.object(type(flow), "config_entry", new_callable=PropertyMock, return_value=self.entry):
            form = await flow.async_step_parameters()
            self.assertNotIn(
                "button_program", {str(key) for key in form["data_schema"].schema}
            )
            self.assertNotIn(
                "button_temperature_c", {str(key) for key in form["data_schema"].schema}
            )
            form = await flow.async_step_parameters({**self.values, "session_gap_minutes": -1})
            self.assertEqual(form["errors"], {"session_gap_minutes": "positive"})
            result = await flow.async_step_parameters({**self.values, "session_gap_minutes": 7})
            self.assertEqual(result["data"]["parameters"]["session_gap_minutes"], 7)
            self.assertEqual(result["data"]["bindings"], self.inputs)
            self.assertEqual(result["data"]["button_program"], "gipfelstuermer")
            self.assertEqual(result["data"]["button_temperature_c"], 79)
            from custom_components.ha_sauna.runtime import Configuration

            self.assertEqual(
                Configuration.from_options(result["data"]).button_program,
                "gipfelstuermer",
            )
            self.assertFalse(hasattr(self.entry, "runtime_data"))

    async def test_options_can_remove_optional_binding(self):
        self.entry.options["bindings"] = {**self.inputs, "upper_status": "sensor.optional"}
        flow = self.module.SaunaOptionsFlow()
        flow.hass = self.hass
        with patch.object(type(flow), "config_entry", new_callable=PropertyMock, return_value=self.entry):
            result = await flow.async_step_bindings(self.inputs)
            self.assertNotIn("upper_status", result["data"]["bindings"])
            self.assertEqual(result["data"]["parameters"], self.values)

    async def test_offline_legacy_constant_survives_technical_options_save(self):
        from custom_components.ha_sauna.runtime import Configuration

        self.entry.options = {
            "bindings": self.inputs,
            "parameters": {
                "target_temperature_c": 80,
                "session_gap_minutes": 15,
                "night_brightness_percent": 37,
            },
        }
        flow = self.module.SaunaOptionsFlow()
        flow.hass = self.hass
        flow.handler = self.entry.entry_id
        flow.context = {"source": "options"}
        with patch.object(type(flow), "config_entry", new_callable=PropertyMock, return_value=self.entry):
            form = await flow.async_step_parameters()
            marker = next(key for key in form["data_schema"].schema if str(key) == "program_mode")
            self.assertEqual(marker.default(), "constant")
            self.assertEqual(marker.description["suggested_value"], "constant")
            submitted = form["data_schema"]({"session_gap_minutes": 16})
            result = await flow.async_step_parameters(submitted)
        loaded = Configuration.from_options(result["data"])
        self.assertEqual(loaded.program_mode, "constant")
        self.assertEqual(loaded.parameters.values["target_temperature_c"], 80)
        self.assertEqual(loaded.parameters.values["session_gap_minutes"], 16)
        self.assertEqual(loaded.parameters.values["night_brightness_percent"], 37)

    async def test_loaded_and_closed_options_share_effective_program_edit(self):
        from datetime import UTC, datetime, timedelta
        from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime
        from custom_components.ha_sauna.core.timeline import Event, Kind

        for loaded in (True, False):
            for end in (90, 95):
                with self.subTest(loaded=loaded, end=end):
                    initial = Configuration.from_options({
                        "bindings": self.inputs,
                        "parameters": {
                            **self.expected_values, "target_temperature_c": 80,
                            "final_temperature_c": 90, "temperature_gangs": 3,
                        },
                        "program_mode": "progressive",
                        "temperature_steps": [80, 85, 90],
                    })
                    self.entry.options = initial.as_options()
                    self.entry.runtime_data = SaunaRuntime(initial)
                    if not loaded:
                        await self.entry.runtime_data.close()
                    flow = self.module.SaunaOptionsFlow()
                    flow.hass = self.hass
                    with patch.object(
                        type(flow), "config_entry", new_callable=PropertyMock,
                        return_value=self.entry,
                    ):
                        result = await flow.async_step_parameters({
                            **initial.parameters.as_dict(), "final_temperature_c": end,
                            "program_mode": "progressive",
                        })
                    self.assertEqual(result["type"], "create_entry")
                    saved = Configuration.from_options(result["data"])
                    self.assertEqual(
                        saved.temperature_steps, (80, 85, 90) if end == 90 else None,
                    )
                    now = datetime(2030, 1, 1, tzinfo=UTC)
                    restored = SaunaRuntime(saved, clock=lambda: now)
                    await restored.set_operation(True)
                    restored.controller.set_temperature(
                        80, now, valid_until=now + timedelta(seconds=60)
                    )
                    await restored.receive(Event(
                        "closed", restored.session.session_id,
                        Kind.DOOR_CLOSE, now, now,
                    ))
                    targets = [restored.controller.target_temperature]
                    for index in range(2):
                        now += timedelta(seconds=1)
                        session_id = restored.session.session_id
                        await restored.receive(Event(
                            f"infusion-{index}", session_id, Kind.INFUSION, now, now,
                        ))
                        now += timedelta(seconds=1)
                        await restored.set_operation(False)
                        now += timedelta(seconds=1)
                        await restored.set_operation(True)
                        targets.append(restored.controller.target_temperature)
                    self.assertEqual(targets, [80, floor((80 + end) / 2 + .5), end])
                    await restored.close()
                    await self.entry.runtime_data.close()

    async def test_loaded_and_closed_options_adopt_only_saved_override_values(self):
        from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime

        definition = BY_KEY["manual_override_minutes"]
        maximum = definition.maximum
        for loaded in (True, False):
            with self.subTest(loaded=loaded):
                self.entry.options = {
                    "bindings": self.inputs,
                    "parameters": {
                        **self.expected_values,
                        "manual_override_minutes": maximum * 2,
                    },
                    "button_program": "genusszeit",
                    "selected_program_id": "genusszeit",
                }
                configuration = Configuration.from_options(self.entry.options)
                runtime = self.entry.runtime_data = SaunaRuntime(configuration)
                if not loaded:
                    await runtime.close()
                before = dict(self.entry.options)
                flow = self.module.SaunaOptionsFlow()
                flow.hass = self.hass
                flow.handler = self.entry.entry_id
                flow.context = {"source": "options"}
                with patch.object(
                    type(flow),
                    "config_entry",
                    new_callable=PropertyMock,
                    return_value=self.entry,
                ):
                    form = await flow.async_step_parameters()
                    field = next(
                        key
                        for key in form["data_schema"].schema
                        if str(key) == "manual_override_minutes"
                    )
                    self.assertEqual(field.description["suggested_value"], maximum)
                    selector = form["data_schema"].schema[field]
                    self.assertEqual(selector.config["max"], maximum)
                    self.assertEqual(selector.config["step"], definition.step)
                    for value in (nextafter(maximum, inf), maximum * 2):
                        rejected = await flow.async_step_parameters(
                            {
                                **self.values,
                                "manual_override_minutes": value,
                            }
                        )
                        self.assertEqual(
                            rejected["errors"], {"manual_override_minutes": "too_large"}
                        )
                        self.assertEqual(self.entry.options, before)
                        self.assertIs(runtime.configuration, configuration)
                        self.assertFalse(runtime.reconfiguring)
                    result = await flow.async_step_parameters(self.values)
                self.assertEqual(result["type"], "create_entry")
                saved = Configuration.from_options(result["data"])
                self.assertEqual(saved.parameters.values["manual_override_minutes"], self.values["manual_override_minutes"])
                self.assertEqual(saved.bindings, configuration.bindings)
                self.assertEqual(saved.button_program, "genusszeit")
                self.assertEqual(saved.selected_program_id, "genusszeit")
                await runtime.close()

    async def test_setup_unload_without_device_transport_never_starts_session(self):
        from custom_components.ha_sauna import async_setup_entry, async_unload_entry
        self.hass.services = MagicMock()
        with patch("custom_components.ha_sauna.device.HADevice.start", new_callable=AsyncMock), patch("homeassistant.helpers.event.async_track_time_interval", return_value=lambda: None):
            self.assertTrue(await async_setup_entry(self.hass, self.entry))
        self.assertIsNone(self.entry.runtime_data.session)
        with (
            patch("custom_components.ha_sauna.device.HADevice.close", new_callable=AsyncMock),
            patch("custom_components.ha_sauna.device.HADevice.apply", new_callable=AsyncMock),
            patch(
                "custom_components.ha_sauna.device.HADevice.prepare_heater_handoff",
                new_callable=AsyncMock, return_value=True,
            ) as handoff,
        ):
            self.assertTrue(await async_unload_entry(self.hass, self.entry))
            handoff.assert_awaited_once()
        self.assertIsNone(self.entry.runtime_data.session)
        self.assertTrue(self.entry.runtime_data.closed)
        self.assertEqual(self.hass.services.mock_calls, [])

    async def test_corrupt_configuration_rejected_on_setup(self):
        from homeassistant.exceptions import ConfigEntryError
        from custom_components.ha_sauna import async_setup_entry
        self.entry.options = {}
        with self.assertRaises(ConfigEntryError):
            await async_setup_entry(self.hass, self.entry)


if __name__ == "__main__":
    unittest.main()
