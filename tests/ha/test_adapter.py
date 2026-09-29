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
        self.values = {d.key: d.default if d.default is not None else 2.5
                       for d in EDITABLE_DEFINITIONS}
        self.values.update(session_gap_minutes=2.5)
        self.expected_values = Parameters(self.values).as_dict()
        self.states = {
            self.inputs[r.key]: State(self.inputs[r.key], "unavailable", {
                "device_class": r.device_class, "unit_of_measurement": r.unit,
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
            states=SimpleNamespace(get=self.states.get),
            config_entries=SimpleNamespace(
                async_entries=lambda *a, **kw: self.entries,
                async_get_entry=lambda *a: self.entry,
                async_reload=AsyncMock(),
                async_update_entry=lambda entry, **kwargs: setattr(entry, "options", kwargs["options"]),
                async_forward_entry_setups=AsyncMock(),
                async_unload_platforms=AsyncMock(return_value=True),
            ),
        )
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
        self.assertEqual(result["options"]["program_mode"], "progressive")
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
        for value in (10.000000000000002, 20):
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
        for value in (10, 0.5):
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

    async def test_initial_button_default_is_constant(self):
        form = await self.flow.async_step_user({"name": "Testsauna", **self.inputs})
        values = form["data_schema"](self.values)
        self.assertEqual(values["button_program"], "constant")

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
                    self.assertEqual(targets, [80, (80 + end) / 2, end])
                    await restored.close()
                    await self.entry.runtime_data.close()

    async def test_loaded_and_closed_options_adopt_only_saved_override_values(self):
        from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime

        for loaded in (True, False):
            with self.subTest(loaded=loaded):
                self.entry.options = {
                    "bindings": self.inputs,
                    "parameters": {
                        **self.expected_values,
                        "manual_override_minutes": 20,
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
                    self.assertEqual(field.description["suggested_value"], 10)
                    selector = form["data_schema"].schema[field]
                    self.assertEqual(selector.config["max"], 10)
                    self.assertEqual(selector.config["step"], "any")
                    for value in (10.000000000000002, 20):
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
                self.assertEqual(saved.parameters.values["manual_override_minutes"], 10)
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
        with patch("custom_components.ha_sauna.device.HADevice.close", new_callable=AsyncMock):
            self.assertTrue(await async_unload_entry(self.hass, self.entry))
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
