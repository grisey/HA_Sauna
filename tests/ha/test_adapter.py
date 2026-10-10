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


def flat_fields(schema):
    """Inspect selectors inside real native sections without changing submitted data."""
    fields = {}
    for marker, value in schema.schema.items():
        if hasattr(value, "schema"):
            fields.update(value.schema.schema)
        else:
            fields[marker] = value
    return fields


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
        device_form = await self.flow.async_step_user()
        self.assertEqual(device_form["step_id"], "user")
        self.assertEqual(
            {str(key) for key in flat_fields(device_form["data_schema"])},
            {"name", "upper_device", "lower_device", "presence_device"},
        )
        form = await self.flow.async_step_user({"name": "Testsauna"})
        self.assertEqual(form["step_id"], "entities")
        validated = form["data_schema"](self.module.pack_binding_input({"name": "Testsauna", **self.inputs}))
        self.assertEqual(validated["heater"], self.inputs["heater"])

    def add_device_entity(self, entity_id, device_id, attributes):
        from homeassistant.core import State
        self.states[entity_id] = State(entity_id, "unavailable", attributes)
        self.registry_entries[entity_id] = SimpleNamespace(
            device_id=device_id, platform="test"
        )

    async def test_device_setup_suggests_unique_roles_and_records_lux_binding(self):
        self.add_device_entity("sensor.top_temperature", "top", {
            "device_class": "temperature", "unit_of_measurement": "°C",
        })
        self.add_device_entity("sensor.top_humidity", "top", {
            "device_class": "humidity", "unit_of_measurement": "%",
        })
        self.add_device_entity("binary_sensor.occupied", "presence", {
            "device_class": "occupancy",
        })
        self.add_device_entity("sensor.illuminance", "presence", {
            "device_class": "illuminance", "unit_of_measurement": "lx",
        })
        form = await self.flow.async_step_user({
            "name": "Testsauna", "upper_device": "top", "presence_device": "presence",
        })
        self.assertEqual(form["step_id"], "entities")
        self.assertFalse(form["errors"])
        suggested = {
            str(key): key.description.get("suggested_value")
            for key in flat_fields(form["data_schema"])
            if key.description
        }
        self.assertEqual(suggested["upper_temperature"], "sensor.top_temperature")
        self.assertEqual(suggested["upper_humidity"], "sensor.top_humidity")
        self.assertEqual(suggested["presence"], "binary_sensor.occupied")
        self.assertEqual(suggested["presence_illuminance"], "sensor.illuminance")
        submitted = {**self.inputs, **suggested}
        result = await self.flow.async_step_entities(self.module.pack_binding_input(submitted))
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["options"]["bindings"]["presence_illuminance"], "sensor.illuminance")
        self.assertNotIn("upper_device", result["options"]["bindings"])

    async def test_device_ambiguity_is_explicit_and_incompatible_lux_is_ignored(self):
        for entity, device_class in (("binary_sensor.pir", "motion"),
                                     ("binary_sensor.presence", "occupancy")):
            self.add_device_entity(entity, "presence", {"device_class": device_class})
        self.add_device_entity("sensor.lux_wrong_unit", "presence", {
            "device_class": "illuminance", "unit_of_measurement": "%",
        })
        self.add_device_entity("sensor.lux_wrong_class", "presence", {
            "device_class": "humidity", "unit_of_measurement": "lx",
        })
        values, errors = self.module.device_bindings(self.hass, {"presence_device": "presence"})
        self.assertEqual(errors, {"presence": "ambiguous_entity"})
        self.assertNotIn("presence", values)
        self.assertNotIn("presence_illuminance", values)
        from custom_components.ha_sauna.bindings import BindingError
        for entity in ("sensor.lux_wrong_unit", "sensor.lux_wrong_class"):
            with self.assertRaises(BindingError):
                self.module.checked_bindings(self.hass, {
                    **self.inputs, "presence_illuminance": entity,
                })
        form = await self.flow.async_step_user({"name": "Sauna", "presence_device": "presence"})
        self.assertEqual(form["errors"], self.module.binding_form_errors(errors))

    async def test_existing_presence_device_adds_missing_lux_and_preserves_manual_assignment(self):
        self.add_device_entity("binary_sensor.occupied", "presence", {
            "device_class": "occupancy",
        })
        self.add_device_entity("sensor.device_lux", "presence", {
            "device_class": "illuminance", "unit_of_measurement": "lx",
        })
        self.add_device_entity("sensor.external_lux", "external", {
            "device_class": "illuminance", "unit_of_measurement": "lx",
        })
        self.entry.options["bindings"] = {**self.inputs, "presence": "binary_sensor.occupied"}
        flow = self.module.SaunaOptionsFlow()
        flow.hass = self.hass
        with patch.object(type(flow), "config_entry", new_callable=PropertyMock, return_value=self.entry):
            form = await flow.async_step_bindings({"presence_device": "presence"})
            suggested = {
                str(key): key.description.get("suggested_value")
                for key in flat_fields(form["data_schema"])
                if key.description
            }
            self.assertEqual(suggested["presence"], "binary_sensor.occupied")
            self.assertEqual(suggested["presence_illuminance"], "sensor.device_lux")
            self.assertFalse(form["errors"])
            # The optional suggestion can still be removed before saving.
            submitted = self.module.pack_binding_input(suggested)
            submitted["presence_sensors"].pop("presence_illuminance")
            result = await flow.async_step_binding_entities(submitted)
            self.assertNotIn("presence_illuminance", self.entry.options["bindings"])
        saved = {**self.entry.options["bindings"], "presence_illuminance": "sensor.external_lux"}
        values, errors = self.module.device_bindings(
            self.hass, {"presence_device": "presence"}, saved=saved
        )
        self.assertEqual(values, saved)
        self.assertFalse(errors)
        self.add_device_entity("sensor.second_device_lux", "presence", {
            "device_class": "illuminance", "unit_of_measurement": "lx",
        })
        values, errors = self.module.device_bindings(
            self.hass, {"presence_device": "presence"}, saved=self.entry.options["bindings"]
        )
        self.assertEqual(values["presence"], "binary_sensor.occupied")
        self.assertNotIn("presence_illuminance", values)
        self.assertEqual(errors, {"presence_illuminance": "ambiguous_entity"})

    async def test_device_options_preserve_corrections_and_replace_only_changed_position(self):
        self.registry_entries[self.inputs["upper_temperature"]] = SimpleNamespace(
            device_id="original", platform="test"
        )
        # The manually assigned humidity intentionally comes from another device.
        self.registry_entries[self.inputs["upper_humidity"]] = SimpleNamespace(
            device_id="external", platform="test"
        )
        self.add_device_entity("sensor.new_temperature", "replacement", {
            "device_class": "temperature", "unit_of_measurement": "°C",
        })
        self.add_device_entity("sensor.new_humidity", "replacement", {
            "device_class": "humidity", "unit_of_measurement": "%",
        })
        unchanged, errors = self.module.device_bindings(
            self.hass, {"upper_device": "original"}, saved=self.inputs
        )
        self.assertEqual(unchanged, self.inputs)
        self.assertFalse(errors)
        flow = self.module.SaunaOptionsFlow()
        flow.hass = self.hass
        with patch.object(type(flow), "config_entry", new_callable=PropertyMock, return_value=self.entry):
            form = await flow.async_step_bindings({"upper_device": "replacement"})
            self.assertEqual(form["step_id"], "binding_entities")
            values = dict(flow._device_values)
            self.assertEqual(values["upper_temperature"], "sensor.new_temperature")
            self.assertEqual(values["upper_humidity"], "sensor.new_humidity")
            self.assertEqual(values["lower_temperature"], self.inputs["lower_temperature"])
            # Corrections in the entity step remain authoritative.
            values["upper_humidity"] = self.inputs["upper_humidity"]
            result = await flow.async_step_binding_entities(self.module.pack_binding_input(values))
        self.assertEqual(result["type"], "menu")
        self.assertEqual(self.entry.options["bindings"]["upper_humidity"], self.inputs["upper_humidity"])

    async def test_multiple_temperature_channels_require_explicit_position_selection(self):
        for entity in ("sensor.internal_temperature", "sensor.external_temperature"):
            self.add_device_entity(entity, "combined", {
                "device_class": "temperature", "unit_of_measurement": "°C",
            })
        self.add_device_entity("sensor.humidity", "combined", {
            "device_class": "humidity", "unit_of_measurement": "%",
        })
        values, errors = self.module.device_bindings(self.hass, {"upper_device": "combined"})
        self.assertEqual(errors, {"upper_temperature": "ambiguous_entity"})
        self.assertNotIn("upper_temperature", values)
        self.assertEqual(values["upper_humidity"], "sensor.humidity")
        saved = {**self.inputs, "upper_temperature": "sensor.external_temperature"}
        unchanged, errors = self.module.device_bindings(
            self.hass, {"upper_device": "combined"}, saved=saved
        )
        self.assertEqual(unchanged, saved)
        self.assertFalse(errors)
        del self.states["sensor.external_temperature"]
        unchanged, errors = self.module.device_bindings(
            self.hass, {"upper_device": "combined"}, saved=saved
        )
        self.assertEqual(unchanged, saved)
        self.assertFalse(errors)

    async def test_device_options_recheck_session_lock_between_steps(self):
        flow = self.module.SaunaOptionsFlow()
        flow.hass = self.hass
        with patch.object(type(flow), "config_entry", new_callable=PropertyMock, return_value=self.entry):
            form = await flow.async_step_bindings({})
            self.assertEqual(form["step_id"], "binding_entities")
            with patch.object(flow, "_has_session", return_value=True):
                result = await flow.async_step_binding_entities(self.module.pack_binding_input(self.inputs))
                self.assertEqual(result["reason"], "session_exists")
                result = await flow.async_step_bindings({})
                self.assertEqual(result["reason"], "session_exists")

    async def test_device_choices_use_compatible_measurements_not_names_or_status(self):
        self.add_device_entity("sensor.sauna_temperature", "wrong", {
            "device_class": "temperature", "unit_of_measurement": "°F",
        })
        self.add_device_entity("sensor.status", "status", {})
        self.add_device_entity("sensor.measurement", "valid", {
            "device_class": "temperature", "unit_of_measurement": "°C",
        })
        self.add_device_entity("sensor.lux", "lux", {
            "device_class": "illuminance", "unit_of_measurement": "lx",
        })
        device_registry = SimpleNamespace(async_get=lambda device_id: SimpleNamespace(
            name_by_user=None, name=f"Gerät {device_id}",
        ))
        with patch.object(self.module.dr, "async_get", return_value=device_registry):
            schema = self.module.device_schema(self.hass)
        selectors = {str(key): value for key, value in flat_fields(schema).items()}
        self.assertEqual(selectors["upper_device"].config["options"], [
            {"value": "valid", "label": "Gerät valid"},
        ])
        self.assertEqual(selectors["presence_device"].config["options"], [
            {"value": "lux", "label": "Gerät lux"},
        ])

    async def test_native_sections_preserve_nested_values_and_open_errors(self):
        from custom_components.ha_sauna.bindings import BindingError
        with self.assertRaises(BindingError):
            self.module.unpack_binding_input(self.inputs)
        flow = self.module.SaunaOptionsFlow()
        flow.hass = self.hass
        with patch.object(type(flow), "config_entry", new_callable=PropertyMock, return_value=self.entry):
            form = await flow.async_step_bindings({})
            schema = form["data_schema"]
            sections = {str(key): value for key, value in schema.schema.items()
                        if hasattr(value, "schema")}
            self.assertEqual(set(sections), set(self.module.BINDING_SECTIONS))
            self.assertTrue(sections["upper_sensors"].options["collapsed"])
            self.assertTrue(sections["lower_sensors"].options["collapsed"])
            self.assertTrue(sections["environment"].options["collapsed"])
            self.assertTrue(sections["additional_devices"].options["collapsed"])
            self.assertFalse(sections["presence_sensors"].options["collapsed"])
            submitted = schema(self.module.pack_binding_input(self.inputs))
            self.assertIn("upper_temperature", submitted["upper_sensors"])
            self.assertNotIn("upper_temperature", submitted)
            self.assertEqual(
                self.module.unpack_binding_input(submitted)["upper_temperature"],
                self.inputs["upper_temperature"],
            )
            submitted["upper_sensors"].pop("upper_humidity")
            rejected = await flow.async_step_binding_entities(submitted)
            self.assertEqual(rejected["errors"], {"upper_sensors": "entity_required"})
            sections = {str(key): value for key, value in rejected["data_schema"].schema.items()
                        if hasattr(value, "schema")}
            self.assertFalse(sections["upper_sensors"].options["collapsed"])
            self.assertTrue(sections["lower_sensors"].options["collapsed"])
            # Repair the nested value; persisted bindings stay flat.
            submitted["upper_sensors"]["upper_humidity"] = self.inputs["upper_humidity"]
            result = await flow.async_step_binding_entities(submitted)
        self.assertEqual(self.entry.options["bindings"], self.inputs)
        self.assertNotIn("upper_sensors", self.entry.options["bindings"])

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
        selectors = {str(key): value for key, value in flat_fields(schema).items()}
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
        selectors = {str(key): value for key, value in flat_fields(self.module.binding_schema(self.hass)).items()}
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
                     flat_fields(self.module.binding_schema(self.hass, saved=saved)).items()}
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

        form = await self.flow.async_step_user({"name": "  Testsauna  "})
        inputs = form["data_schema"](self.module.pack_binding_input({"name": "  Testsauna  ", **self.inputs}))
        result = await self.flow.async_step_entities(self.module.pack_binding_input(inputs))
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

    async def test_setup_and_binding_form_expose_only_hardware_configuration(self):
        expected = {role.key for role in ROLES} | {
            "control_input_mode", "button_event_type", "presence_source",
        }
        form = await self.flow.async_step_entities()
        self.assertEqual({str(key) for key in flat_fields(form["data_schema"])}, expected | {"name"})
        flow = self.module.SaunaOptionsFlow()
        flow.hass = self.hass
        with patch.object(type(flow), "config_entry", new_callable=PropertyMock, return_value=self.entry):
            form = await flow.async_step_binding_entities()
        self.assertEqual(form["step_id"], "binding_entities")
        self.assertEqual({str(key) for key in flat_fields(form["data_schema"])}, expected)
        for current in (self.flow, flow):
            self.assertFalse(hasattr(current, "async_step_parameters"))
            self.assertFalse(hasattr(current, "async_step_logging"))

    async def test_options_menu_uses_catalog_ownership(self):
        flow = self.module.SaunaOptionsFlow()
        flow.hass = self.hass
        with patch.object(type(flow), "config_entry", new_callable=PropertyMock, return_value=self.entry):
            result = await flow.async_step_init()
            self.assertEqual(result["type"], "menu")
            self.assertEqual(result["menu_options"], ["bindings", *self.module.integration_groups()])
            for area in self.module.integration_groups():
                menu = await getattr(flow, f"async_step_{area}")()
                self.assertIn("init", menu["menu_options"])
                for step in menu["menu_options"]:
                    form = await getattr(flow, f"async_step_{step}")()
                    if step == "init":
                        self.assertEqual(form["type"], "menu")
                        self.assertEqual(form["menu_options"], result["menu_options"])
                        continue
                    self.assertEqual(form["type"], "form")
                    self.assertTrue(flat_fields(form["data_schema"]))

    async def test_installation_save_preserves_concurrent_panel_options(self):
        flow = self.module.SaunaOptionsFlow()
        flow.hass = self.hass
        with patch.object(type(flow), "config_entry", new_callable=PropertyMock, return_value=self.entry):
            form = await flow.async_step_parameters_oven_cooling()
            definitions = flow._definitions("oven_cooling")
            submitted = {d.key: self.values[d.key] for d in definitions}
            self.entry.options = {
                **self.entry.options,
                "log_level": "DEBUG",
                "parameters": {**self.entry.options["parameters"], "target_temperature_c": 81},
            }
            before = self.entry.options
            submitted["after_run_minutes"] = 1
            result = await flow.async_step_parameters_oven_cooling(submitted)
        self.assertEqual(form["type"], "form")
        self.assertEqual(result["type"], "menu")
        self.assertIsNot(self.entry.options, before)
        self.assertEqual(self.entry.options["log_level"], "DEBUG")
        self.assertEqual(self.entry.options["parameters"]["target_temperature_c"], 81)
        self.assertEqual(self.entry.options["parameters"]["after_run_minutes"], 1)
        self.assertEqual(
            {key: value for key, value in self.entry.options.items() if key != "parameters"},
            {key: value for key, value in before.items() if key != "parameters"},
        )

    async def test_installation_forms_use_saved_values_and_preserve_unchanged_fields(self):
        flow = self.module.SaunaOptionsFlow()
        flow.hass = self.hass
        self.entry.options["parameters"]["after_run_minutes"] = 2
        with patch.object(type(flow), "config_entry", new_callable=PropertyMock, return_value=self.entry):
            form = await flow.async_step_parameters_oven_cooling()
            marker = next(key for key in flat_fields(form["data_schema"]) if key.schema == "after_run_minutes")
            self.assertEqual(marker.description["suggested_value"], 2)
            submitted = {d.key: self.entry.options["parameters"][d.key] for d in flow._definitions("oven_cooling")}
            self.entry.options["parameters"]["after_run_minutes"] = 3
            result = await flow.async_step_parameters_oven_cooling(submitted)
        self.assertEqual(self.entry.options["parameters"]["after_run_minutes"], 3)

    async def test_navigation_and_unchanged_parameters_do_not_write_options(self):
        flow = self.module.SaunaOptionsFlow()
        flow.hass = self.hass
        before = self.entry.options
        with (
            patch.object(type(flow), "config_entry", new_callable=PropertyMock, return_value=self.entry),
            patch.object(self.hass.config_entries, "async_update_entry") as update,
        ):
            await flow.async_step_init()
            await flow.async_step_operation()
            await flow.async_step_parameters_temperature_control()
            values = {d.key: self.values[d.key] for d in flow._definitions("temperature_control")}
            result = await flow.async_step_parameters_temperature_control(values)
            self.assertEqual(result["step_id"], "operation")
            await flow.async_step_init()
            update.assert_not_called()
        self.assertIs(self.entry.options, before)

    async def test_installation_save_retains_implicit_legacy_cooling_limit(self):
        from custom_components.ha_sauna.core.parameters import BY_KEY
        from custom_components.ha_sauna.runtime import Configuration

        old_duration = BY_KEY["oven_cooling_max_minutes"].default + 1
        raw = dict(self.entry.options["parameters"])
        raw["after_run_minutes"] = old_duration
        raw.pop("oven_cooling_max_minutes", None)
        self.entry.options = {**self.entry.options, "parameters": raw}
        current = Configuration.from_options(self.entry.options)
        self.assertEqual(current.parameters.values["oven_cooling_max_minutes"], old_duration)
        flow = self.module.SaunaOptionsFlow()
        flow.hass = self.hass
        with patch.object(type(flow), "config_entry", new_callable=PropertyMock, return_value=self.entry):
            await flow.async_step_parameters_oven_cooling()
            submitted = {d.key: current.parameters.values[d.key] for d in flow._definitions("oven_cooling")}
            submitted["after_run_minutes"] = BY_KEY["after_run_minutes"].default
            result = await flow.async_step_parameters_oven_cooling(submitted)
        self.assertEqual(result["type"], "menu")
        self.assertEqual(self.entry.options["parameters"]["oven_cooling_max_minutes"], old_duration)
        self.assertNotIn("oven_cooling_max_minutes", raw)

    async def test_installation_dependency_failure_does_not_write(self):
        flow = self.module.SaunaOptionsFlow()
        flow.hass = self.hass
        with patch.object(type(flow), "config_entry", new_callable=PropertyMock, return_value=self.entry):
            await flow.async_step_parameters_temperature_control()
            submitted = {d.key: self.values[d.key] for d in flow._definitions("temperature_control")}
            submitted["sauna_min_temperature_c"] = 100
            before = self.entry.options
            result = await flow.async_step_parameters_temperature_control(submitted)
        self.assertEqual(result["type"], "form")
        self.assertTrue(result["errors"])
        self.assertIs(self.entry.options, before)
        if result["errors"].get("base") == "parameter_dependency":
            self.assertIn("parameter", result["description_placeholders"])
            self.assertIn("reason", result["description_placeholders"])

    async def test_reload_allows_navigation_but_preserves_pending_form_input(self):
        from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime

        runtime = self.entry.runtime_data = SaunaRuntime(Configuration.from_options(self.entry.options))
        runtime.reconfiguring = True
        flow = self.module.SaunaOptionsFlow()
        flow.hass = self.hass
        before = self.entry.options
        with patch.object(type(flow), "config_entry", new_callable=PropertyMock, return_value=self.entry):
            self.assertEqual((await flow.async_step_init())["type"], "menu")
            self.assertEqual((await flow.async_step_operation())["type"], "menu")
            await flow.async_step_parameters_temperature_control()
            values = {d.key: self.values[d.key] for d in flow._definitions("temperature_control")}
            definition = next(d for d in flow._definitions("temperature_control") if d.key == "readiness_offset_c")
            values[definition.key] += definition.step
            form = await flow.async_step_parameters_temperature_control(values)
            self.assertEqual(form["type"], "form")
            self.assertEqual(form["errors"], {"base": "configuration_busy"})
            marker = next(key for key in flat_fields(form["data_schema"]) if key.schema == definition.key)
            self.assertEqual(marker.description["suggested_value"], values[definition.key])
            form = await flow.async_step_bindings({})
            self.assertEqual(form["step_id"], "binding_entities")
            form = await flow.async_step_binding_entities(self.module.pack_binding_input(self.inputs))
            self.assertEqual(form["errors"], {"base": "configuration_busy"})
        self.assertIs(self.entry.options, before)

    async def test_installation_rechecks_session_before_save(self):
        from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime

        runtime = self.entry.runtime_data = SaunaRuntime(Configuration.from_options(self.entry.options))
        flow = self.module.SaunaOptionsFlow()
        flow.hass = self.hass
        with patch.object(type(flow), "config_entry", new_callable=PropertyMock, return_value=self.entry):
            await flow.async_step_parameters_oven_cooling()
            values = {d.key: self.values[d.key] for d in flow._definitions("oven_cooling")}
            with patch.object(type(runtime), "session", new_callable=PropertyMock, return_value=object()):
                result = await flow.async_step_parameters_oven_cooling(values)
        self.assertEqual(result["reason"], "session_exists")
        self.assertEqual(self.entry.options["parameters"], self.values)

    async def test_presence_method_hides_inapplicable_installation_groups(self):
        flow = self.module.SaunaOptionsFlow()
        flow.hass = self.hass
        self.entry.options["presence_source"] = "ha_presence"
        with patch.object(type(flow), "config_entry", new_callable=PropertyMock, return_value=self.entry):
            menu = await flow.async_step_sensors()
            self.assertNotIn("parameters_presence_strong", menu["menu_options"])
            self.assertIn("parameters_door", menu["menu_options"])
            rejected = await flow.async_step_parameters_presence_strong()
        self.assertEqual(rejected["reason"], "settings_unavailable")

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
                result = await self.flow.async_step_entities(self.module.pack_binding_input({"name": "Testsauna", **inputs}))
                self.assertEqual(result["errors"], {"base": "unknown_binding"})
                with patch.object(type(flow), "config_entry", new_callable=PropertyMock, return_value=self.entry):
                    result = await flow.async_step_binding_entities(self.module.pack_binding_input(inputs))
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
                initial_form = await self.flow.async_step_entities()
                initial_form["data_schema"](self.module.pack_binding_input({"name": "Testsauna", **inputs}))
                result = await self.flow.async_step_entities(self.module.pack_binding_input({"name": "Testsauna", **inputs}))
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
                form = await self.flow.async_step_entities(self.module.pack_binding_input({"name": "Testsauna", **inputs}))
                self.assertEqual(form["errors"], self.module.binding_form_errors(error))

    async def test_duplicate_sensor_stays_in_form(self):
        inputs = {**self.inputs, "lower_temperature": self.inputs["upper_temperature"]}
        form = await self.flow.async_step_entities(self.module.pack_binding_input({"name": "Testsauna", **inputs}))
        self.assertEqual(form["errors"], {"lower_sensors": "duplicate_sensor"})

    async def test_wrong_unit_stays_in_form(self):
        from homeassistant.core import State
        entity = self.inputs["upper_temperature"]
        self.states[entity] = State(entity, "100", {"device_class": "temperature", "unit_of_measurement": "°F"})
        form = await self.flow.async_step_entities(self.module.pack_binding_input({"name": "Testsauna", **self.inputs}))
        self.assertEqual(form["errors"], {"upper_sensors": "wrong_unit"})

    async def test_existing_heater_cannot_be_claimed_twice(self):
        self.entries.append(self.entry)
        form = await self.flow.async_step_entities(self.module.pack_binding_input({"name": "Testsauna", **self.inputs}))
        self.assertEqual(form["errors"], {"heater": "heater_already_used"})

    async def test_parallel_flow_is_rechecked_on_submit(self):
        await self.flow.async_step_entities()
        self.entries.append(self.entry)
        result = await self.flow.async_step_entities(self.module.pack_binding_input({"name": "Testsauna", **self.inputs}))
        self.assertEqual(result["errors"], {"heater": "heater_already_used"})

    async def test_options_can_remove_optional_binding(self):
        self.entry.options["bindings"] = {**self.inputs, "upper_status": "sensor.optional"}
        flow = self.module.SaunaOptionsFlow()
        flow.hass = self.hass
        with patch.object(type(flow), "config_entry", new_callable=PropertyMock, return_value=self.entry):
            result = await flow.async_step_binding_entities(self.module.pack_binding_input(self.inputs))
            self.assertNotIn("upper_status", self.entry.options["bindings"])
            self.assertEqual(self.entry.options["parameters"], self.values)

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
                    result = await flow.async_step_binding_entities(self.module.pack_binding_input(inputs))
                self.assertEqual(result["type"], "menu")
                self.assertEqual(self.entry.options, {**original, "bindings": inputs})
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
            form = await flow.async_step_binding_entities()
            submitted = form["data_schema"](self.module.pack_binding_input(bindings))
            result = await flow.async_step_binding_entities(self.module.pack_binding_input(submitted))
            self.assertEqual(self.entry.options, before)
            # An error redisplay must also retain newly entered basic values.
            rejected = await flow.async_step_binding_entities(self.module.pack_binding_input({
                **bindings, "presence_source": "proxy", "control_input_mode": "switch",
                "button_event_type": "changed_event", "heater": "switch.missing",
            }))
            self.assertTrue(rejected["errors"])
            submitted = rejected["data_schema"](self.module.pack_binding_input(bindings))
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
                    result = await flow.async_step_binding_entities(self.module.pack_binding_input({
                        **self.inputs, "control_input": "binary_sensor.button",
                    }))
                self.assertEqual(result["errors"], {"control_input": "button_gesture_incompatible"})
                self.assertEqual(self.entry.options, before)

    async def test_options_recheck_session_lock_when_submitted(self):
        from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime

        runtime = self.entry.runtime_data = SaunaRuntime(Configuration.from_options(self.entry.options))
        flow = self.module.SaunaOptionsFlow()
        flow.hass = self.hass
        with patch.object(type(flow), "config_entry", new_callable=PropertyMock, return_value=self.entry):
            self.assertEqual((await flow.async_step_init())["type"], "menu")
            # A session can start while the configuration form is open.
            with patch.object(type(runtime), "session", new_callable=PropertyMock, return_value=object()):
                for inputs in (None, self.inputs):
                    submitted = None if inputs is None else self.module.pack_binding_input(inputs)
                    result = await flow.async_step_binding_entities(submitted)
                    self.assertEqual(result["reason"], "session_exists")
        self.assertEqual(self.entry.options["bindings"], self.inputs)

    async def test_options_reject_heater_used_by_another_entry(self):
        self.entries.append(SimpleNamespace(entry_id="other", options=self.entry.options))
        flow = self.module.SaunaOptionsFlow()
        flow.hass = self.hass
        with patch.object(type(flow), "config_entry", new_callable=PropertyMock, return_value=self.entry):
            result = await flow.async_step_binding_entities(self.module.pack_binding_input(self.inputs))
        self.assertEqual(result["errors"], {"heater": "heater_already_used"})

    async def test_presence_entity_required_only_for_ha_source(self):
        from homeassistant.core import State

        self.states["binary_sensor.room"] = State("binary_sensor.room", "off", {"device_class": "occupancy"})
        result = await self.flow.async_step_entities(self.module.pack_binding_input({
            "name": "Testsauna", **self.inputs, "presence_source": "ha_presence",
        }))
        self.assertEqual(result["errors"], {"presence_sensors": "entity_required"})
        result = await self.flow.async_step_entities(self.module.pack_binding_input({
            "name": "Testsauna", **self.inputs, "presence_source": "ha_presence",
            "presence": "binary_sensor.room",
        }))
        self.assertEqual(result["type"], "create_entry")
        self.entry.options = result["options"]
        flow = self.module.SaunaOptionsFlow()
        flow.hass = self.hass
        with patch.object(type(flow), "config_entry", new_callable=PropertyMock, return_value=self.entry):
            rejected = await flow.async_step_binding_entities(self.module.pack_binding_input(self.inputs))
            self.assertEqual(rejected["errors"], {"presence_sensors": "entity_required"})
            result = await flow.async_step_binding_entities(self.module.pack_binding_input({**self.inputs, "presence_source": "proxy"}))
        self.assertEqual(result["type"], "menu")
        self.assertNotIn("presence", self.entry.options["bindings"])
        self.assertEqual(self.entry.options["presence_source"], "proxy")

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
