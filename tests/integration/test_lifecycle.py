import unittest

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from custom_components.ha_sauna.core.parameters import DEFINITIONS

from harness import create_sauna, start_hass


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass, self.temp = await start_hass()

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self.temp.cleanup()

    async def test_flow_entities_options_and_repeated_reload(self):
        entry = await create_sauna(self.hass)
        self.assertEqual(entry.state, ConfigEntryState.LOADED)
        entities = er.async_entries_for_config_entry(er.async_get(self.hass), entry.entry_id)
        self.assertEqual(len(entities), len(DEFINITIONS) + 2)
        self.assertTrue(all(self.hass.states.get(e.entity_id) is not None for e in entities))
        self.assertEqual(entry.data, {})
        self.assertIsNone(entry.runtime_data.session)
        for _ in range(3):
            old = entry.runtime_data
            self.assertTrue(await self.hass.config_entries.async_unload(entry.entry_id))
            self.assertTrue(old.closed)
            self.assertTrue(await self.hass.config_entries.async_setup(entry.entry_id))
            await self.hass.async_block_till_done()
            self.assertEqual(len(er.async_entries_for_config_entry(er.async_get(self.hass), entry.entry_id)), len(DEFINITIONS) + 2)
        flow = await self.hass.config_entries.options.async_init(entry.entry_id)
        form = await self.hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "parameters"})
        values = {**entry.options["parameters"], "heating_minutes": 7}
        await self.hass.config_entries.options.async_configure(form["flow_id"], values)
        await self.hass.async_block_till_done()
        self.assertEqual(entry.runtime_data.configuration.parameters.values["heating_minutes"], 7)
        number = next(e.entity_id for e in entities if e.unique_id.endswith("_heating_minutes"))
        await self.hass.services.async_call("number", "set_value", {"entity_id": number, "value": 9}, blocking=True)
        await self.hass.async_block_till_done()
        self.assertEqual(entry.options["parameters"]["heating_minutes"], 9)
        self.assertEqual(entry.runtime_data.configuration.parameters.values["heating_minutes"], 9)
        self.assertEqual(float(self.hass.states.get(number).state), 9)

    async def test_operation_switch_session_expiry_and_configuration_lock(self):
        from datetime import UTC, datetime, timedelta
        entry = await create_sauna(self.hass)
        runtime = entry.runtime_data
        now = datetime.now(UTC)
        runtime._clock = lambda: now
        entities = er.async_entries_for_config_entry(er.async_get(self.hass), entry.entry_id)
        switch = next(e.entity_id for e in entities if e.unique_id.endswith("_operation"))
        number = next(e.entity_id for e in entities if e.unique_id.endswith("_heating_minutes"))
        await self.hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
        session_id = runtime.session.session_id
        self.assertTrue(runtime.session.operation_enabled)
        self.assertEqual(self.hass.states.get(switch).state, "on")
        flow = await self.hass.config_entries.options.async_init(entry.entry_id)
        self.assertEqual(flow["type"], "abort")
        self.assertEqual(flow["reason"], "session_exists")
        with self.assertRaises(ValueError):
            await self.hass.services.async_call("number", "set_value", {"entity_id": number, "value": 9}, blocking=True)
        self.assertEqual(entry.options["parameters"]["heating_minutes"], 2.5)
        await self.hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
        now += timedelta(seconds=149)
        await runtime.tick()
        self.assertEqual(runtime.session.session_id, session_id)
        self.assertFalse(runtime.session.operation_enabled)
        with self.assertRaises(ValueError):
            runtime.check_configuration_change()
        now += timedelta(seconds=1)
        await runtime.tick()
        self.assertIsNone(runtime.session)
        runtime.check_configuration_change()
        await self.hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
        self.assertNotEqual(runtime.session.session_id, session_id)

    async def test_binding_change_survives_new_homeassistant_instance(self):
        entry = await create_sauna(self.hass)
        original = entry.options["bindings"]["upper_temperature"]
        self.hass.states.async_set("sensor.replacement", "31", self.hass.states.get(original).attributes)
        flow = await self.hass.config_entries.options.async_init(entry.entry_id)
        form = await self.hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "bindings"})
        bindings = {**entry.options["bindings"], "upper_temperature": "sensor.replacement"}
        await self.hass.config_entries.options.async_configure(form["flow_id"], bindings)
        await self.hass.async_block_till_done()
        saved_id = entry.entry_id
        await self.hass.async_stop(force=True)
        self.hass, _ = await start_hass(self.temp.name)
        restored = self.hass.config_entries.async_get_entry(saved_id)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.options["bindings"], bindings)
        self.assertTrue(await self.hass.config_entries.async_setup(saved_id))
        await self.hass.async_block_till_done()
        self.assertEqual(restored.runtime_data.configuration.bindings.as_dict(), bindings)
        self.assertIsNone(restored.runtime_data.session)

    async def test_invalid_selection_and_values_create_no_entry(self):
        from harness import seed_sources
        bindings = seed_sources(self.hass)
        flow = await self.hass.config_entries.flow.async_init("ha_sauna", context={"source": "user"})
        bad = {**bindings, "lower_temperature": bindings["upper_temperature"]}
        result = await self.hass.config_entries.flow.async_configure(flow["flow_id"], {"name": "Test", **bad})
        self.assertEqual(result["errors"], {"lower_temperature": "duplicate_sensor"})
        self.assertEqual(self.hass.config_entries.async_entries("ha_sauna"), [])
        await self.hass.config_entries.flow.async_configure(flow["flow_id"], {"name": "Test", **bindings})
        from homeassistant.data_entry_flow import InvalidData
        from custom_components.ha_sauna.core.parameters import DEFINITIONS
        values = {d.key: d.default if d.default is not None else 2.5 for d in DEFINITIONS}
        values.update(heating_minutes=2.5, heating_reduction_minutes=0.5)
        with self.assertRaises(InvalidData):
            await self.hass.config_entries.flow.async_configure(flow["flow_id"], {**values, "heating_minutes": -1})
        self.assertEqual(self.hass.config_entries.async_entries("ha_sauna"), [])
        result = await self.hass.config_entries.flow.async_configure(flow["flow_id"], {**values, "heating_minutes": 0})
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["errors"], {"heating_minutes": "positive"})
        self.assertEqual(self.hass.config_entries.async_entries("ha_sauna"), [])
