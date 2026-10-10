"""Präsenzwahl bleibt Konfiguration, optionale Ziele bleiben eigene Rollen."""
import importlib.util
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HA_AVAILABLE = importlib.util.find_spec("homeassistant") is not None


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant ist lokal nicht installiert")
class PresenceConfigTests(unittest.IsolatedAsyncioTestCase):
    async def test_configuration_fields_keep_related_settings_together(self):
        from custom_components.ha_sauna import config_flow as module

        hass = SimpleNamespace(states=SimpleNamespace(async_all=lambda: []))
        schema = module.binding_schema(hass)
        keys = []
        for marker, value in schema.schema.items():
            if hasattr(value, "schema"):
                keys.extend(str(key) for key in value.schema.schema)
            else:
                keys.append(str(marker))
        for sequence in (
            ["upper_temperature", "upper_humidity", "upper_status"],
            ["lower_temperature", "lower_humidity", "lower_status"],
            ["heater_feedback", "heater_power", "audio_output"],
            ["control_input", "control_input_mode", "button_event_type"],
            ["presence", "presence_illuminance", "presence_source"],
        ):
            with self.subTest(sequence=sequence):
                start = keys.index(sequence[0])
                self.assertEqual(keys[start : start + len(sequence)], sequence)
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
