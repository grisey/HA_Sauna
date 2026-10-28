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
            ["control_input", "control_input_mode"],
            ["presence", "presence_illuminance", "presence_source"],
        ):
            with self.subTest(sequence=sequence):
                start = keys.index(sequence[0])
                self.assertEqual(keys[start : start + len(sequence)], sequence)
        self.assertNotIn("button_event_type", keys)

    async def test_event_name_field_matches_the_selected_input_capabilities(self):
        from homeassistant.core import State
        from custom_components.ha_sauna import config_flow as module

        native = State("event.button", "unavailable", {
            "device_class": "button",
            "event_types": ["btn_down", "btn_up", "single_push", "long_push"],
        })
        custom = State("event.custom", "unavailable", {
            "device_class": "button", "event_types": ["custom_press"],
        })
        states = {state.entity_id: state for state in (native, custom)}
        hass = SimpleNamespace(states=SimpleNamespace(
            async_all=lambda: list(states.values()), get=states.get,
        ))
        registry = SimpleNamespace(async_get=lambda entity: None)
        registry_patch = patch.object(module.er, "async_get", return_value=registry)
        registry_patch.start()
        self.addCleanup(registry_patch.stop)
        for entity, mode, expected in (
            (None, "button", False),
            ("binary_sensor.button", "button", False),
            ("event.button", "button", False),
            ("event.custom", "button", True),
            ("event.button", "switch", True),
        ):
            with self.subTest(entity=entity, mode=mode):
                schema = module.binding_schema(hass, saved={
                    "control_input": entity, "control_input_mode": mode,
                })
                keys = set()
                for marker, value in schema.schema.items():
                    fields = value.schema.schema if hasattr(value, "schema") else {marker: value}
                    keys.update(str(key) for key in fields)
                self.assertEqual("button_event_type" in keys, expected)

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
