"""Abnahme der abgeleiteten Feuchtesensoren im isolierten HA-Kern."""
from datetime import UTC, datetime, timedelta
import unittest

from homeassistant.helpers import entity_registry as er

from harness import create_sauna, start_hass


class DerivedHumiditySensorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass, self.temp = await start_hass()
        self.entry = await create_sauna(
            self.hass, parameter_overrides={"sensor_timeout_seconds": 30}
        )
        self.runtime = self.entry.runtime_data
        self.now = datetime.now(UTC)
        self.runtime._clock = lambda: self.now

        registry = er.async_get(self.hass)
        entries = er.async_entries_for_config_entry(registry, self.entry.entry_id)
        self.absolute_humidity = {
            entry.unique_id.rsplit("_", 1)[-1]: entry
            for entry in entries
            if entry.unique_id.endswith(("_absolute_humidity_upper", "_absolute_humidity_lower"))
        }

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self.temp.cleanup()

    async def set_source(self, role, value):
        entity_id = self.entry.options["bindings"][role]
        state = self.hass.states.get(entity_id)
        self.hass.states.async_set(entity_id, str(value), state.attributes)
        await self.hass.async_block_till_done()

    def state(self, position):
        return self.hass.states.get(self.absolute_humidity[position].entity_id)

    async def test_absolute_humidity_entities_are_fresh_positioned_and_diagnostic(self):
        # Independently hand-checked with the ASHRAE saturation-vapour-pressure
        # equation: 80 °C / 12 % = 34.91 g/m³; 70 °C / 15 % = 29.55 g/m³.
        await self.set_source("upper_temperature", 80)
        await self.set_source("upper_humidity", 12)
        await self.set_source("lower_temperature", 70)
        await self.set_source("lower_humidity", 15)

        self.assertEqual(set(self.absolute_humidity), {"upper", "lower"})
        self.assertEqual(len(self.absolute_humidity), 2)
        upper, lower = self.state("upper"), self.state("lower")
        self.assertEqual(upper.attributes["unit_of_measurement"], "g/m³")
        self.assertEqual(upper.attributes["state_class"], "measurement")
        self.assertEqual(self.absolute_humidity["upper"].entity_category, "diagnostic")
        self.assertEqual(float(upper.state), 34.91)
        self.assertEqual(float(lower.state), 29.55)
        self.assertEqual(upper.attributes["temperature_source"], self.entry.options["bindings"]["upper_temperature"])
        self.assertEqual(lower.attributes["humidity_source"], self.entry.options["bindings"]["lower_humidity"])

        await self.set_source("lower_humidity", "unavailable")
        self.assertEqual(self.state("lower").state, "unavailable")
        await self.set_source("lower_humidity", 15)
        self.assertEqual(float(self.state("lower").state), 29.55)

        # Keep the lower pair current while the upper pair passes its expiry.
        self.now += timedelta(seconds=31)
        await self.set_source("lower_temperature", 70)
        await self.set_source("lower_humidity", 15)
        await self.runtime.tick()
        await self.hass.async_block_till_done()
        self.assertEqual(self.state("upper").state, "unavailable")
        self.assertEqual(float(self.state("lower").state), 29.55)
        await self.set_source("upper_temperature", 80)
        await self.set_source("upper_humidity", 12)
        self.assertEqual(float(self.state("upper").state), 34.91)

    async def test_rebinding_keeps_derived_sensor_identity_and_ignores_old_source(self):
        await self.set_source("upper_temperature", 80)
        await self.set_source("upper_humidity", 12)
        derived = self.absolute_humidity["upper"]
        original_temperature = self.entry.options["bindings"]["upper_temperature"]
        original_state = self.hass.states.get(original_temperature)
        replacement = "sensor.replacement_upper_temperature"
        self.hass.states.async_set(replacement, "60", original_state.attributes)

        # A binding update outside a session reloads the adapter but must not
        # create a new derived entity or retain the former source's value.
        self.now = datetime.now(UTC)
        bindings = {**self.entry.options["bindings"], "upper_temperature": replacement}
        self.hass.config_entries.async_update_entry(
            self.entry, options={**self.entry.options, "bindings": bindings}
        )
        await self.hass.async_block_till_done()
        self.runtime = self.entry.runtime_data
        self.now = datetime.now(UTC)
        self.runtime._clock = lambda: self.now

        entries = er.async_entries_for_config_entry(er.async_get(self.hass), self.entry.entry_id)
        current = next(entry for entry in entries if entry.unique_id == derived.unique_id)
        self.assertEqual(current.entity_id, derived.entity_id)
        self.assertEqual(float(self.hass.states.get(current.entity_id).state), 15.57)
        self.assertEqual(self.hass.states.get(current.entity_id).attributes["temperature_source"], replacement)

        self.hass.states.async_set(original_temperature, "90", original_state.attributes)
        await self.hass.async_block_till_done()
        self.assertEqual(float(self.hass.states.get(current.entity_id).state), 15.57)
