"""Präsenzwahl bleibt Konfiguration, optionale Ziele bleiben eigene Rollen."""
import importlib.util
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HA_AVAILABLE = importlib.util.find_spec("homeassistant") is not None


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant ist lokal nicht installiert")
class PresenceConfigTests(unittest.IsolatedAsyncioTestCase):
    async def test_source_is_not_a_binding_and_invalid_source_is_rejected(self):
        from custom_components.ha_sauna import config_flow as module
        from custom_components.ha_sauna.bindings import BindingError
        hass = SimpleNamespace(states=SimpleNamespace(get=lambda _: None))
        with patch.object(module, "Bindings") as bindings, patch.object(module, "validate_metadata"):
            bindings.return_value.values = {}
            module.checked_bindings(hass, {"presence_source": "ha_presence", "control_input_mode": "button", "button_event_type": "", "presence": "binary_sensor.room"})
            bindings.assert_called_once_with({"presence": "binary_sensor.room"})
        with self.assertRaises(BindingError):
            module.checked_bindings(hass, {"presence_source": "combined"})

    async def test_source_survives_setup_and_binding_options(self):
        from custom_components.ha_sauna import config_flow as module
        from homeassistant.config_entries import OptionsFlow
        bindings = SimpleNamespace(values={"heater": "switch.heater"}, as_dict=lambda: {"heater": "switch.heater"})
        hass = SimpleNamespace(config_entries=SimpleNamespace(async_entries=lambda _: []))
        flow = module.SaunaConfigFlow()
        flow.hass = hass
        with patch.object(module, "checked_bindings", return_value=bindings), patch.object(flow, "_async_current_entries", return_value=[]), patch.object(flow, "async_step_parameters", return_value={}):
            await flow.async_step_user({"name": "Test", "presence_source": "ha_presence"})
        self.assertEqual(flow._input_options["presence_source"], "ha_presence")
        from unittest.mock import PropertyMock
        entry = SimpleNamespace(entry_id="test", options={"presence_source": "ha_presence", "bindings": bindings.as_dict()})
        options = module.SaunaOptionsFlow()
        options.hass = hass
        with patch.object(OptionsFlow, "config_entry", new_callable=PropertyMock, return_value=entry), patch.object(module, "checked_bindings", return_value=bindings):
            result = await options.async_step_bindings({"heater": "switch.heater", "presence_source": "proxy"})
        self.assertEqual(result["data"]["presence_source"], "proxy")
        self.assertNotIn("presence_source", result["data"]["bindings"])
