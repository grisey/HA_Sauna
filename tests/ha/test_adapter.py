"""API-Smoketests gegen Home Assistant; keine laufende Nutzerinstallation.

Der HA-Manager und Entitätsbestand sind isolierte Testdoubles. Schema-, Flow-
und Selektorklassen stammen aus dem tatsächlich installierten Home Assistant.
"""
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
import tempfile
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

from custom_components.ha_sauna.core.parameters import EDITABLE_DEFINITIONS, Parameters
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

    async def test_complete_flow_creates_entry_with_configuration_defaults(self):
        from custom_components.ha_sauna.bindings import Bindings
        from custom_components.ha_sauna.runtime import Configuration

        form = await self.flow.async_step_user()
        inputs = form["data_schema"]({"name": "  Testsauna  ", **self.inputs})
        result = await self.flow.async_step_user(inputs)
        expected = Configuration(
            bindings=Bindings(self.inputs),
            parameters=Parameters({}),
            control_input_mode=instance_default("control_input_mode", setup=True),
            program_mode=instance_default("program_mode", setup=True),
        )
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["title"], "Testsauna")
        self.assertEqual(result["data"], {})
        self.assertEqual(result["options"], expected.as_options())
        self.assertEqual(
            Configuration.from_options(result["options"]).as_options(),
            expected.as_options(),
        )

    async def test_setup_and_options_expose_only_basic_configuration(self):
        expected = {role.key for role in ROLES} | {
            "control_input_mode", "button_event_type", "presence_source",
        }
        form = await self.flow.async_step_user()
        self.assertEqual({str(key) for key in form["data_schema"].schema}, expected | {"name"})
        flow = self.module.SaunaOptionsFlow()
        flow.hass = self.hass
        with patch.object(type(flow), "config_entry", new_callable=PropertyMock, return_value=self.entry):
            form = await flow.async_step_init()
        self.assertEqual(form["step_id"], "bindings")
        self.assertEqual({str(key) for key in form["data_schema"].schema}, expected)
        for current in (self.flow, flow):
            self.assertFalse(hasattr(current, "async_step_parameters"))
            self.assertFalse(hasattr(current, "async_step_logging"))

    async def test_panel_fields_cannot_be_submitted_as_basic_configuration(self):
        flow = self.module.SaunaOptionsFlow()
        flow.hass = self.hass
        for key, value in (
            ("session_gap_minutes", 7),
            ("program_mode", "constant"),
            ("button_program", "constant"),
            ("log_level", "DEBUG"),
        ):
            with self.subTest(key=key):
                inputs = {**self.inputs, key: value}
                result = await self.flow.async_step_user({"name": "Testsauna", **inputs})
                self.assertEqual(result["errors"], {"base": "unknown_binding"})
                with patch.object(type(flow), "config_entry", new_callable=PropertyMock, return_value=self.entry):
                    result = await flow.async_step_init(inputs)
                self.assertEqual(result["errors"], {"base": "unknown_binding"})
                self.assertEqual(self.entry.options["parameters"], self.values)

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
                result = await self.flow.async_step_user({"name": "Testsauna", **inputs})
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

    async def test_existing_heater_cannot_be_claimed_twice(self):
        self.entries.append(self.entry)
        form = await self.flow.async_step_user({"name": "Testsauna", **self.inputs})
        self.assertEqual(form["errors"], {"heater": "heater_already_used"})

    async def test_parallel_flow_is_rechecked_on_submit(self):
        await self.flow.async_step_user()
        self.entries.append(self.entry)
        result = await self.flow.async_step_user({"name": "Testsauna", **self.inputs})
        self.assertEqual(result["errors"], {"heater": "heater_already_used"})

    async def test_options_can_remove_optional_binding(self):
        self.entry.options["bindings"] = {**self.inputs, "upper_status": "sensor.optional"}
        flow = self.module.SaunaOptionsFlow()
        flow.hass = self.hass
        with patch.object(type(flow), "config_entry", new_callable=PropertyMock, return_value=self.entry):
            result = await flow.async_step_bindings(self.inputs)
            self.assertNotIn("upper_status", result["data"]["bindings"])
            self.assertEqual(result["data"]["parameters"], self.values)

    async def test_binding_options_preserve_all_other_saved_values(self):
        from homeassistant.core import State
        from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime

        for loaded in (True, False):
            with self.subTest(loaded=loaded):
                self.entry.options = {
                    **Configuration.from_options(self.entry.options).as_options(),
                    "program_mode": "progressive",
                    "selected_program_id": DEFAULT_PROGRAMS[0].id,
                    "temperature_steps": [80, 83, 90],
                    "button_program": "current",
                    "button_temperature_c": 79,
                    "log_level": "DEBUG",
                    "control_input_mode": "button",
                    "button_event_type": "single_push",
                    "presence_source": "proxy",
                }
                original = dict(self.entry.options)
                configuration = Configuration.from_options(original)
                runtime = self.entry.runtime_data = SaunaRuntime(configuration)
                if not loaded:
                    await runtime.close()
                self.states["switch.replacement"] = State("switch.replacement", "off")
                inputs = {**self.inputs, "heater": "switch.replacement"}
                flow = self.module.SaunaOptionsFlow()
                flow.hass = self.hass
                with patch.object(type(flow), "config_entry", new_callable=PropertyMock, return_value=self.entry):
                    result = await flow.async_step_init(inputs)
                self.assertEqual(result["type"], "create_entry")
                self.assertEqual(result["data"], {**original, "bindings": inputs})
                self.assertEqual(self.entry.options, original)
                self.assertIs(runtime.configuration, configuration)
                await runtime.close()

    async def test_options_schema_roundtrip_preserves_saved_basic_values(self):
        from homeassistant.core import State

        self.states["binary_sensor.room"] = State(
            "binary_sensor.room", "off", {"device_class": "occupancy"}
        )
        bindings = {**self.inputs, "presence": "binary_sensor.room"}
        self.entry.options.update({
            "bindings": bindings,
            "presence_source": "ha_presence",
            "control_input_mode": "button",
            "button_event_type": "single_push",
        })
        before = dict(self.entry.options)
        flow = self.module.SaunaOptionsFlow()
        flow.hass = self.hass
        with patch.object(type(flow), "config_entry", new_callable=PropertyMock, return_value=self.entry):
            form = await flow.async_step_init()
            submitted = form["data_schema"](bindings)
            result = await flow.async_step_bindings(submitted)
            self.assertEqual(result["data"], before)
            # An error redisplay must also retain newly entered basic values.
            rejected = await flow.async_step_bindings({
                **bindings, "presence_source": "proxy", "control_input_mode": "switch",
                "button_event_type": "changed_event", "heater": "switch.missing",
            })
            self.assertTrue(rejected["errors"])
            submitted = rejected["data_schema"](bindings)
            self.assertEqual(submitted["presence_source"], "proxy")
            self.assertEqual(submitted["control_input_mode"], "switch")
            self.assertEqual(submitted["button_event_type"], "changed_event")

    async def test_options_reject_device_incompatible_with_saved_button_gesture(self):
        from homeassistant.core import State
        from custom_components.ha_sauna.runtime import Configuration

        self.states["binary_sensor.button"] = State("binary_sensor.button", "off")
        flow = self.module.SaunaOptionsFlow()
        flow.hass = self.hass
        for gesture in ("double", "triple"):
            with self.subTest(gesture=gesture):
                self.entry.options.update({
                    "control_input_mode": "button", "button_session_gesture": gesture,
                })
                before = dict(self.entry.options)
                Configuration.from_options(before)
                with patch.object(type(flow), "config_entry", new_callable=PropertyMock, return_value=self.entry):
                    result = await flow.async_step_bindings({
                        **self.inputs, "control_input": "binary_sensor.button",
                    })
                self.assertEqual(result["errors"], {"control_input": "button_gesture_incompatible"})
                self.assertEqual(self.entry.options, before)

    async def test_options_recheck_session_lock_when_submitted(self):
        from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime

        runtime = self.entry.runtime_data = SaunaRuntime(Configuration.from_options(self.entry.options))
        flow = self.module.SaunaOptionsFlow()
        flow.hass = self.hass
        with patch.object(type(flow), "config_entry", new_callable=PropertyMock, return_value=self.entry):
            self.assertEqual((await flow.async_step_init())["type"], "form")
            # A session can start while the configuration form is open.
            with patch.object(type(runtime), "session", new_callable=PropertyMock, return_value=object()):
                for inputs in (None, self.inputs):
                    result = await flow.async_step_init(inputs)
                    self.assertEqual(result["reason"], "session_exists")
        self.assertEqual(self.entry.options["bindings"], self.inputs)

    async def test_options_reject_heater_used_by_another_entry(self):
        self.entries.append(SimpleNamespace(entry_id="other", options=self.entry.options))
        flow = self.module.SaunaOptionsFlow()
        flow.hass = self.hass
        with patch.object(type(flow), "config_entry", new_callable=PropertyMock, return_value=self.entry):
            result = await flow.async_step_init(self.inputs)
        self.assertEqual(result["errors"], {"heater": "heater_already_used"})

    async def test_presence_entity_required_only_for_ha_source(self):
        from homeassistant.core import State

        self.states["binary_sensor.room"] = State("binary_sensor.room", "off", {"device_class": "occupancy"})
        result = await self.flow.async_step_user({
            "name": "Testsauna", **self.inputs, "presence_source": "ha_presence",
        })
        self.assertEqual(result["errors"], {"presence": "entity_required"})
        result = await self.flow.async_step_user({
            "name": "Testsauna", **self.inputs, "presence_source": "ha_presence",
            "presence": "binary_sensor.room",
        })
        self.assertEqual(result["type"], "create_entry")
        self.entry.options = result["options"]
        flow = self.module.SaunaOptionsFlow()
        flow.hass = self.hass
        with patch.object(type(flow), "config_entry", new_callable=PropertyMock, return_value=self.entry):
            rejected = await flow.async_step_init(self.inputs)
            self.assertEqual(rejected["errors"], {"presence": "entity_required"})
            result = await flow.async_step_init({**self.inputs, "presence_source": "proxy"})
        self.assertEqual(result["type"], "create_entry")
        self.assertNotIn("presence", result["data"]["bindings"])
        self.assertEqual(result["data"]["presence_source"], "proxy")

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
