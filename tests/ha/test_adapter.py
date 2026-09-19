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

from custom_components.ha_sauna.core.parameters import DEFINITIONS
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
        self.inputs = {r.key: f"{r.domains[0]}.test_{r.key}" for r in ROLES if not r.optional}
        self.values = {d.key: d.default if d.default is not None else 2.5 for d in DEFINITIONS}
        self.values.update(heating_minutes=2.5, heating_reduction_minutes=0.5)
        self.states = {
            self.inputs[r.key]: State(self.inputs[r.key], "unavailable", {
                "device_class": r.device_class, "unit_of_measurement": r.unit,
                "supported_color_modes": ["brightness"],
            })
            for r in ROLES if not r.optional
        }
        self.entries = []
        self.entry = SimpleNamespace(
            entry_id="example", options={"bindings": self.inputs, "parameters": self.values},
            async_on_unload=lambda callback: None,
            add_update_listener=lambda listener: lambda: None,
        )
        self.hass = SimpleNamespace(
            data={},
            http=SimpleNamespace(register_view=MagicMock()),
            bus=SimpleNamespace(async_listen_once=lambda *args: lambda: None),
            states=SimpleNamespace(get=self.states.get),
            config_entries=SimpleNamespace(
                async_entries=lambda *a, **kw: self.entries,
                async_get_entry=lambda *a: self.entry,
                async_reload=AsyncMock(),
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
        values = form["data_schema"](self.values)
        result = await self.flow.async_step_parameters(values)
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"], {})
        self.assertEqual(result["options"]["parameters"], self.values)
        self.assertEqual(result["options"]["bindings"], self.inputs)

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
        form = await self.flow.async_step_parameters({**self.values, "heating_minutes": -1})
        self.assertEqual(form["errors"], {"heating_minutes": "positive"})

    async def test_existing_heater_cannot_be_claimed_twice(self):
        self.entries.append(self.entry)
        form = await self.flow.async_step_user({"name": "Testsauna", **self.inputs})
        self.assertEqual(form["errors"], {"heater": "heater_already_used"})

    async def test_parallel_flow_is_rechecked_on_submit(self):
        await self.flow.async_step_user({"name": "Testsauna", **self.inputs})
        self.entries.append(self.entry)
        result = await self.flow.async_step_parameters(self.values)
        self.assertEqual(result["reason"], "heater_already_used")

    async def test_options_change_uses_same_parameter_validation(self):
        flow = self.module.SaunaOptionsFlow()
        flow.hass = self.hass
        flow.handler = self.entry.entry_id
        flow.context = {"source": "options"}
        with patch.object(type(flow), "config_entry", new_callable=PropertyMock, return_value=self.entry):
            form = await flow.async_step_parameters({**self.values, "heating_minutes": -1})
            self.assertEqual(form["errors"], {"heating_minutes": "positive"})
            result = await flow.async_step_parameters({**self.values, "heating_minutes": 7})
            self.assertEqual(result["data"]["parameters"]["heating_minutes"], 7)
            self.assertEqual(result["data"]["bindings"], self.inputs)

    async def test_options_can_remove_optional_binding(self):
        self.entry.options["bindings"] = {**self.inputs, "upper_status": "sensor.optional"}
        flow = self.module.SaunaOptionsFlow()
        flow.hass = self.hass
        with patch.object(type(flow), "config_entry", new_callable=PropertyMock, return_value=self.entry):
            result = await flow.async_step_bindings(self.inputs)
            self.assertNotIn("upper_status", result["data"]["bindings"])
            self.assertEqual(result["data"]["parameters"], self.values)

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
