import unittest
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from custom_components.ha_sauna.core.parameters import EDITABLE_DEFINITIONS

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
        self.assertEqual(len(entities), len(EDITABLE_DEFINITIONS) + 7)
        self.assertTrue(all(self.hass.states.get(e.entity_id) is not None for e in entities))
        self.assertEqual(entry.data, {})
        self.assertIsNone(entry.runtime_data.session)
        for _ in range(3):
            old = entry.runtime_data
            self.assertTrue(await self.hass.config_entries.async_unload(entry.entry_id))
            self.assertTrue(old.closed)
            self.assertTrue(await self.hass.config_entries.async_setup(entry.entry_id))
            await self.hass.async_block_till_done()
            self.assertEqual(len(er.async_entries_for_config_entry(er.async_get(self.hass), entry.entry_id)), len(EDITABLE_DEFINITIONS) + 7)
        flow = await self.hass.config_entries.options.async_init(entry.entry_id)
        form = await self.hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "parameters"})
        editable_keys = {definition.key for definition in EDITABLE_DEFINITIONS}
        values = {
            key: value
            for key, value in entry.options["parameters"].items()
            if key in editable_keys
        }
        values["session_gap_minutes"] = 7
        await self.hass.config_entries.options.async_configure(form["flow_id"], values)
        await self.hass.async_block_till_done()
        self.assertEqual(entry.runtime_data.configuration.parameters.values["session_gap_minutes"], 7)
        number = next(e.entity_id for e in entities if e.unique_id.endswith("_session_gap_minutes"))
        await self.hass.services.async_call("number", "set_value", {"entity_id": number, "value": 9}, blocking=True)
        await self.hass.async_block_till_done()
        self.assertEqual(entry.options["parameters"]["session_gap_minutes"], 9)
        self.assertEqual(entry.runtime_data.configuration.parameters.values["session_gap_minutes"], 9)
        self.assertEqual(float(self.hass.states.get(number).state), 9)

    async def test_operation_switch_session_expiry_and_configuration_lock(self):
        from datetime import UTC, datetime, timedelta
        entry = await create_sauna(self.hass)
        runtime = entry.runtime_data
        now = datetime.now(UTC)
        runtime._clock = lambda: now
        entities = er.async_entries_for_config_entry(er.async_get(self.hass), entry.entry_id)
        switch = next(e.entity_id for e in entities if e.unique_id.endswith("_operation"))
        number = next(e.entity_id for e in entities if e.unique_id.endswith("_session_gap_minutes"))
        await self.hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
        session_id = runtime.session.session_id
        self.assertTrue(runtime.session.operation_enabled)
        self.assertEqual(self.hass.states.get(switch).state, "on")
        flow = await self.hass.config_entries.options.async_init(entry.entry_id)
        self.assertEqual(flow["type"], "menu")
        flow = await self.hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "parameters"})
        self.assertEqual(flow["type"], "form")
        self.assertEqual({str(key) for key in flow["data_schema"].schema}, {
            "target_temperature_c", "final_temperature_c", "temperature_gangs",
            "program_mode",
        })
        from homeassistant.data_entry_flow import InvalidData
        with self.assertRaises(InvalidData):
            await self.hass.config_entries.options.async_configure(flow["flow_id"], {
                "target_temperature_c": 80, "final_temperature_c": 95,
                "temperature_gangs": 4, "program_mode": "progressive",
                "button_program": "gipfelstuermer",
            })
        self.assertEqual(runtime.configuration.button_program, "constant")
        flow = await self.hass.config_entries.options.async_configure(flow["flow_id"], {
            "target_temperature_c": 80, "final_temperature_c": 95,
            "temperature_gangs": 4, "program_mode": "progressive",
        })
        await self.hass.async_block_till_done()
        self.assertEqual(flow["type"], "create_entry")
        self.assertIs(entry.runtime_data,runtime)
        self.assertEqual(runtime.session.session_id,session_id)
        self.assertEqual(runtime.controller.target_temperature,80)
        with self.assertRaises(ValueError):
            await self.hass.services.async_call("number", "set_value", {"entity_id": number, "value": 9}, blocking=True)
        self.assertEqual(entry.options["parameters"]["session_gap_minutes"], 2.5)
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
        with self.assertRaisesRegex(ValueError, "Temperaturwert"):
            await self.hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
        self.assertIsNone(runtime.session)
        source = entry.options["bindings"]["upper_temperature"]
        self.hass.states.async_set(source, "25", self.hass.states.get(source).attributes, force_update=True)
        await self.hass.async_block_till_done()
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

    async def _assert_failed_reload_preserves_runtime(self, *, raises):
        from datetime import UTC, datetime

        entry = await create_sauna(self.hass)
        runtime = entry.runtime_data
        now = datetime.now(UTC)
        runtime._clock = lambda: now
        old_options = dict(entry.options)
        old_light = old_options["bindings"]["light"]
        self.hass.states.async_set(
            "light.replacement", "off", {"supported_color_modes": ["brightness"]}
        )
        calls = []

        async def light_service(call):
            calls.append((call.service, call.data["entity_id"]))
            self.hass.states.async_set(
                call.data["entity_id"], "on" if call.service == "turn_on" else "off",
                {"supported_color_modes": ["brightness"], "brightness": 94},
            )

        async def switch_service(call):
            self.hass.states.async_set(
                call.data["entity_id"], "on" if call.service == "turn_on" else "off"
            )

        for service in ("turn_on", "turn_off"):
            self.hass.services.async_register("light", service, light_service)
            self.hass.services.async_register("switch", service, switch_service)
        await runtime.set_operation(True)
        async with runtime._lock:
            runtime.controller.finish_session(now)
            await runtime._cycle()
        await self.hass.async_block_till_done()
        phase = runtime.controller.light_after_run
        self.assertIsNotNone(phase)
        # False goes through HA's real reload and the original integration
        # unload. The exceptional case exercises the caller's cleanup boundary.
        boundary = "async_reload" if raises else "async_unload_platforms"
        with patch.object(
            self.hass.config_entries, boundary,
            **({"side_effect": RuntimeError("reload failed")} if raises
               else {"return_value": False}),
        ):
            self.hass.config_entries.async_update_entry(
                entry, options={
                    **old_options,
                    "bindings": {**old_options["bindings"], "light": "light.replacement"},
                },
            )
            await self.hass.async_block_till_done()
        self.assertIs(entry.runtime_data, runtime)
        self.assertFalse(runtime.closed)
        self.assertFalse(runtime.reconfiguring)
        self.assertEqual(dict(entry.options), old_options)
        self.assertEqual(runtime.configuration.as_options(), old_options)
        self.assertEqual(runtime.controller.light_after_run, phase)
        await runtime.set_light_override(37)
        self.assertIn(("turn_on", old_light), calls)
        self.assertFalse(any(entity == "light.replacement" for _, entity in calls))
        await runtime.set_operation(True)
        self.assertTrue(runtime.session.operation_enabled)

    async def test_rejected_platform_unload_restores_old_configuration_and_light(self):
        await self._assert_failed_reload_preserves_runtime(raises=False)

    async def test_reload_exception_restores_old_configuration_and_light(self):
        await self._assert_failed_reload_preserves_runtime(raises=True)

    async def test_direct_unload_keeps_pending_light_owner_before_platform_unload(self):
        import asyncio

        from custom_components.ha_sauna import async_unload_entry

        entry = await create_sauna(self.hass)
        runtime = entry.runtime_data
        entered, release = asyncio.Event(), asyncio.Event()

        async def light_service(call):
            entered.set()
            await release.wait()
            self.hass.states.async_set(
                call.data["entity_id"], "on",
                {"supported_color_modes": ["brightness"], "brightness": 204},
                context=call.context,
            )

        self.hass.services.async_register("light", "turn_on", light_service)
        selecting = asyncio.create_task(runtime.set_light_override(80))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            selecting.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await selecting
            service = runtime.device._light_service_task
            with patch.object(self.hass.config_entries, "async_unload_platforms") as unload:
                self.assertFalse(await async_unload_entry(self.hass, entry))
                unload.assert_not_called()
            self.assertIs(entry.runtime_data, runtime)
            self.assertFalse(runtime.closed)
            self.assertFalse(runtime.archive.closed)
            self.assertTrue(runtime.device._light_owned)
            self.assertFalse(service.done())
        finally:
            release.set()
        await service
        await self.hass.async_block_till_done()
        self.assertTrue(await self.hass.config_entries.async_unload(entry.entry_id))
        self.assertTrue(runtime.closed)

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
        values = {d.key: d.default if d.default is not None else 2.5 for d in EDITABLE_DEFINITIONS}
        values.update(session_gap_minutes=2.5)
        with self.assertRaises(InvalidData):
            await self.hass.config_entries.flow.async_configure(flow["flow_id"], {**values, "session_gap_minutes": -1})
        self.assertEqual(self.hass.config_entries.async_entries("ha_sauna"), [])
        result = await self.hass.config_entries.flow.async_configure(flow["flow_id"], {**values, "session_gap_minutes": 0})
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["errors"], {"session_gap_minutes": "positive"})
        self.assertEqual(self.hass.config_entries.async_entries("ha_sauna"), [])
