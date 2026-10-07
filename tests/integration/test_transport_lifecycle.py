"""Owned HA services, bounded scheduling and observable entity updates."""

import asyncio
import threading
import unittest
from datetime import timedelta
from unittest.mock import patch

import test_device_path as device_path
from homeassistant.components.switch import SwitchEntity
from homeassistant.core import Event, State
from homeassistant.exceptions import HomeAssistantError

from custom_components.ha_sauna import async_unload_entry
from custom_components.ha_sauna.entity import SaunaEntity


class TransportLifecycleTests(unittest.IsolatedAsyncioTestCase):
    # Reuse the real HA platforms, actors and cleanup from the device-path suite.
    asyncSetUp = device_path.DevicePathTests.asyncSetUp

    async def test_executor_timeout_retains_on_and_final_off_until_close(self):
        adapter = self.runtime.device
        adapter.values = {**adapter.values, "feedback_timeout_seconds": .05}
        release, entered = threading.Event(), asyncio.Event()
        trace = []
        original_off = self.heater.async_turn_off

        def sync_on(**_kwargs):
            trace.append("on_started")
            self.hass.loop.call_soon_threadsafe(entered.set)
            if not release.wait(5):
                raise TimeoutError("test did not release executor")
            self.heater._attr_is_on = True
            trace.append("on_finished")
            self.heater.schedule_update_ha_state()

        async def off(**kwargs):
            trace.append("off_finished")
            await original_off(**kwargs)

        with (
            patch.object(device_path.TestHeater, "async_turn_on", SwitchEntity.async_turn_on),
            patch.object(self.heater, "turn_on", side_effect=sync_on, create=True),
            patch.object(self.heater, "async_turn_off", side_effect=off),
        ):
            try:
                await self.runtime.set_operation(True)
                self.assertTrue(entered.is_set())
                service = adapter._heater_service_task
                self.assertFalse(service.done())
                self.assertTrue(adapter.command_error)
                for _ in range(5):
                    await self.runtime.tick()
                self.assertIs(adapter._heater_service_task, service)
                self.assertEqual(trace, ["on_started"])
                cleanup = tuple(self.runtime._cleanup)
                with self.assertRaises(RuntimeError):
                    await self.runtime.close()
                self.assertFalse(self.runtime.closed)
                self.assertFalse(self.runtime.archive.closed)
                self.assertEqual(tuple(self.runtime._cleanup), cleanup)
                final_off = adapter._heater_service_task
                self.assertIsNot(final_off, service)
                self.assertFalse(final_off.done())
            finally:
                release.set()
            self.assertTrue(await asyncio.wait_for(final_off, 3))
            await self.hass.async_block_till_done()
            self.assertEqual(trace, ["on_started", "on_finished", "off_finished"])
            self.assertFalse(self.heater.is_on)
            await self.runtime.close()
            self.assertTrue(self.runtime.closed)

    async def test_cancelled_caller_and_failed_on_still_require_ordered_off(self):
        adapter = self.runtime.device
        adapter.values = {**adapter.values, "feedback_timeout_seconds": .05}
        entered, release = asyncio.Event(), asyncio.Event()

        async def failing_on(**_kwargs):
            entered.set()
            await release.wait()
            raise HomeAssistantError("late synthetic ON failure")

        with patch.object(self.heater, "async_turn_on", side_effect=failing_on):
            starting = asyncio.create_task(self.runtime.set_operation(True))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                starting.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await starting
                old_service = adapter._heater_service_task
                self.assertFalse(old_service.done())
                with patch.object(self.hass.config_entries, "async_unload_platforms") as unload:
                    self.assertFalse(await async_unload_entry(self.hass, self.entry))
                    unload.assert_not_called()
                self.assertFalse(self.runtime.closed)
                self.assertFalse(self.runtime.session.operation_enabled)
                self.assertTrue(adapter._heater_owned)
                final_off = adapter._heater_service_task
                self.assertIsNot(final_off, old_service)
            finally:
                release.set()
            self.assertTrue(await asyncio.wait_for(final_off, 3))
            self.assertFalse(await old_service)
            await self.runtime.tick()
            await self.hass.async_block_till_done()
        self.assertFalse(self.heater.is_on)
        self.assertFalse(adapter.command_error)

    async def test_failed_final_off_keeps_runtime_open_for_retry(self):
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()

        async def failing_off(**_kwargs):
            raise HomeAssistantError("synthetic OFF failure")

        with patch.object(self.heater, "async_turn_off", side_effect=failing_off):
            with self.assertRaises(RuntimeError):
                await self.runtime.close()
            self.assertFalse(self.runtime.closed)
            self.assertFalse(self.runtime.archive.closed)
            self.assertTrue(self.runtime.device.command_error)
        await self.runtime.close()
        self.assertTrue(self.runtime.closed)
        self.assertFalse(self.heater.is_on)

    async def test_cancelled_on_service_still_runs_owned_final_off_without_a_tick(self):
        adapter = self.runtime.device
        adapter.values = {**adapter.values, "feedback_timeout_seconds": .05}
        release = asyncio.Event()
        original_on = self.heater.async_turn_on
        self.heater.calls.clear()

        async def cancelled_on(**kwargs):
            await release.wait()
            await original_on(**kwargs)
            raise asyncio.CancelledError("driver cancelled after applying ON")

        with (
            patch.object(self.heater, "async_turn_on", side_effect=cancelled_on),
            patch.object(self.runtime.archive, "append", wraps=self.runtime.archive.append) as append,
        ):
            try:
                await self.runtime.set_operation(True)
                session_id = self.runtime.session.session_id
                previous = adapter._heater_service_task
                with self.assertRaises(RuntimeError):
                    await self.runtime.close()
                final_off = adapter._heater_service_task
                async with self.runtime._lock:
                    # No periodic/feedback cycle can rescue a lost final OFF.
                    release.set()
                    self.assertTrue(await asyncio.wait_for(final_off, 3))
                    self.assertTrue(previous.cancelled())
                    self.assertEqual(self.heater.calls, [True, False])
                    self.assertFalse(self.heater.is_on)
                    self.assertFalse(self.runtime.closed)
                    self.assertFalse(self.runtime.archive.closed)
                commands = [call.args for call in append.call_args_list
                            if call.args[0] == "command"]
                self.assertEqual(
                    [(args[2]["heat"], args[2]["service_error"], args[3]) for args in commands],
                    [(True, "CancelledError", session_id), (False, None, session_id)],
                )
            finally:
                release.set()
            await self.runtime.close()

    async def test_cancelling_queued_off_keeps_its_running_on_owned_for_retry(self):
        adapter = self.runtime.device
        adapter.values = {**adapter.values, "feedback_timeout_seconds": .05}
        release = asyncio.Event()
        original_on = self.heater.async_turn_on
        self.heater.calls.clear()

        async def paused_on(**kwargs):
            await release.wait()
            await original_on(**kwargs)

        with patch.object(self.heater, "async_turn_on", side_effect=paused_on):
            try:
                await self.runtime.set_operation(True)
                previous = adapter._heater_service_task
                with self.assertRaises(RuntimeError):
                    await self.runtime.close()
                final_off = adapter._heater_service_task
                async with self.runtime._lock:
                    final_off.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await final_off
                    self.assertFalse(previous.done())
                    self.assertIs(adapter._heater_service_task, previous)
                    self.assertFalse(await adapter.prepare_heater_handoff())
                    retry = adapter._heater_service_task
                    self.assertIsNot(retry, final_off)
                    self.assertFalse(retry.done())
                    self.assertEqual(self.heater.calls, [])
                    release.set()
                    self.assertTrue(await asyncio.wait_for(retry, 3))
                    self.assertEqual(self.heater.calls, [True, False])
            finally:
                release.set()
            await self.runtime.close()

    async def test_rejected_unload_stays_off_after_old_on_finishes(self):
        adapter = self.runtime.device
        adapter.values = {**adapter.values, "feedback_timeout_seconds": .05}
        release = asyncio.Event()
        original_on = self.heater.async_turn_on

        async def paused_on(**kwargs):
            await release.wait()
            await original_on(**kwargs)

        self.heater.calls.clear()
        with patch.object(self.heater, "async_turn_on", side_effect=paused_on):
            try:
                await self.runtime.set_operation(True)
                self.assertFalse(adapter._heater_service_task.done())
                self.assertFalse(await async_unload_entry(self.hass, self.entry))
                self.assertFalse(self.runtime.closed)
                self.assertFalse(self.runtime.session.operation_enabled)
                final_off = adapter._heater_service_task
            finally:
                release.set()
            self.assertTrue(await asyncio.wait_for(final_off, 3))
            await self.hass.async_block_till_done()
            for _ in range(5):
                await self.runtime.tick()
            self.assertFalse(self.heater.is_on)
            self.assertEqual(self.heater.calls, [True, False])

    async def test_concurrent_handoffs_and_close_share_the_final_off(self):
        adapter = self.runtime.device
        entered, release = asyncio.Event(), asyncio.Event()
        original_off = self.heater.async_turn_off

        async def paused_off(**kwargs):
            entered.set()
            await release.wait()
            await original_off(**kwargs)

        self.heater.calls.clear()
        with patch.object(self.heater, "async_turn_off", side_effect=paused_off):
            first = asyncio.create_task(adapter.prepare_heater_handoff())
            try:
                await asyncio.wait_for(entered.wait(), 3)
                service = adapter._heater_service_task
                second = asyncio.create_task(adapter.prepare_heater_handoff())
                await asyncio.sleep(0)
                self.assertIs(adapter._heater_service_task, service)
            finally:
                release.set()
            self.assertEqual(await asyncio.gather(first, second), [True, True])
            self.assertTrue(await adapter.prepare_heater_handoff())
            await self.runtime.close()
        self.assertTrue(self.runtime.closed)
        self.assertEqual(self.heater.calls, [False])

    async def test_pending_heater_blocks_rebinding_before_platform_unload(self):
        adapter = self.runtime.device
        adapter.values = {**adapter.values, "feedback_timeout_seconds": .05}
        entered, release, rolled_back = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original_on = self.heater.async_turn_on
        from custom_components.ha_sauna import _restore_failed_options

        async def paused_on(**kwargs):
            entered.set()
            await release.wait()
            await original_on(**kwargs)

        async def restore(*args):
            await _restore_failed_options(*args)
            rolled_back.set()

        old_options = dict(self.entry.options)
        with (
            patch.object(self.heater, "async_turn_on", side_effect=paused_on),
            patch("custom_components.ha_sauna._restore_failed_options", side_effect=restore),
            patch.object(self.hass.config_entries, "async_reload") as reload_entry,
        ):
            try:
                await self.runtime.set_operation(True)
                self.assertTrue(entered.is_set())
                await self.runtime.set_operation(False)
                await self.runtime.finish_session_gap(next(
                    deadline.token for deadline in self.runtime.session.deadlines
                    if deadline.purpose == "session_gap"
                ))
                self.assertIsNone(self.runtime.session)
                self.hass.config_entries.async_update_entry(self.entry, options={
                    **old_options,
                    "bindings": {**old_options["bindings"], "heater": "switch.replacement"},
                })
                await asyncio.wait_for(rolled_back.wait(), 3)
                reload_entry.assert_not_called()
                self.assertEqual(dict(self.entry.options), old_options)
                self.assertIs(self.entry.runtime_data, self.runtime)
                self.assertFalse(self.runtime.closed)
                self.assertTrue(adapter._heater_owned)
            finally:
                release.set()
            self.assertTrue(await asyncio.wait_for(adapter._heater_service_task, 3))
            await self.hass.async_block_till_done()
        self.assertFalse(self.heater.is_on)

    async def test_ticks_coalesce_before_lock_and_recover_after_cancellation(self):
        await self.runtime._lock.acquire()
        ticks = [asyncio.create_task(self.runtime.tick()) for _ in range(100)]
        try:
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.assertEqual(sum(not task.done() for task in ticks), 1)
            self.assertTrue(self.runtime._tick_pending)
            ticks[0].cancel()
            with self.assertRaises(asyncio.CancelledError):
                await ticks[0]
            self.assertFalse(self.runtime._tick_pending)
        finally:
            self.runtime._lock.release()
            await asyncio.gather(*ticks, return_exceptions=True)
        await self.runtime.tick()
        self.assertFalse(self.runtime._tick_pending)

    async def test_pending_light_does_not_delay_later_ticks_or_operation_off(self):
        adapter = self.runtime.device
        adapter.values = {**adapter.values, "feedback_timeout_seconds": .5}
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        entered, release = asyncio.Event(), asyncio.Event()
        original_on = self.light.async_turn_on

        async def paused_on(**kwargs):
            entered.set()
            await release.wait()
            await original_on(**kwargs)

        with patch.object(self.light, "async_turn_on", side_effect=paused_on):
            try:
                await self.runtime.set_light_override(80)
                self.assertTrue(entered.is_set())
                service = adapter._light_service_task
                self.assertFalse(service.done())
                # All follow-up ticks together take less than one service budget.
                await asyncio.wait_for(
                    asyncio.gather(*(self.runtime.tick() for _ in range(100))), .2
                )
                await asyncio.wait_for(self.runtime.set_operation(False), .2)
                self.assertFalse(self.heater.is_on)
                self.assertFalse(service.done())
            finally:
                release.set()
            await service
            await self.hass.async_block_till_done()

    async def test_actual_ha_timer_does_not_accumulate_pending_light_waiters(self):
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        entered, release = asyncio.Event(), asyncio.Event()
        original_on = self.light.async_turn_on

        async def paused_on(**kwargs):
            entered.set()
            await release.wait()
            await original_on(**kwargs)

        with patch.object(self.light, "async_turn_on", side_effect=paused_on):
            selecting = asyncio.create_task(self.runtime.set_light_override(80))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                for _ in range(4):
                    await asyncio.sleep(1.05)
                    ticks = [
                        task for task in asyncio.all_tasks()
                        if not task.done()
                        and "SaunaRuntime.tick" in getattr(task.get_coro(), "__qualname__", "")
                    ]
                    self.assertLessEqual(len(ticks), 1)
                self.assertTrue(selecting.done())
                self.assertFalse(self.runtime.device._light_service_task.done())
                await asyncio.wait_for(self.runtime.set_operation(False), .5)
                self.assertFalse(self.heater.is_on)
            finally:
                release.set()
                await selecting
            await self.runtime.device._light_service_task
            await self.hass.async_block_till_done()

    async def test_continuous_inputs_with_slow_light_failures_do_not_queue_before_off(self):
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        self.runtime.device.values = {
            **self.runtime.device.values, "feedback_timeout_seconds": .05,
        }
        source = self.runtime.configuration.bindings.values["upper_temperature"]
        attributes = self.hass.states.get(source).attributes
        entered = asyncio.Event()
        produced = []

        async def failing_on(**_kwargs):
            entered.set()
            await asyncio.sleep(.035)
            raise HomeAssistantError("slow repeatable light failure")

        async def produce():
            while True:
                value = 25 + (len(produced) + 1) / 100000
                produced.append(value)
                self.hass.states.async_set(source, str(value), attributes)
                await asyncio.sleep(.003)

        with patch.object(self.light, "async_turn_on", side_effect=failing_on):
            selecting = asyncio.create_task(self.runtime.set_light_override(81))
            await asyncio.wait_for(entered.wait(), 3)
            producer = asyncio.create_task(produce())
            try:
                await asyncio.sleep(.1)
                stopping = asyncio.create_task(self.runtime.set_operation(False))
                done, _ = await asyncio.wait({stopping}, timeout=.25)
                completed = stopping in done
                heater_off = not self.heater.is_on
            finally:
                producer.cancel()
                await asyncio.gather(producer, return_exceptions=True)
                await asyncio.wait_for(stopping, 3)
                await selecting
                await self.hass.async_block_till_done()
        self.assertTrue(completed, "old sensor callbacks repeated slow light cycles before OFF")
        self.assertTrue(heater_off)
        await self.runtime.archive.flush()
        archived = self.runtime.archive.read(self.runtime.session.session_id, limit=10000)
        values = [record["payload"]["value"] for record in archived["records"]
                  if record["kind"] == "measurement" and record["payload"]["source"] == source]
        self.assertEqual(values, produced)

    async def test_cancelled_input_worker_leaves_all_new_inputs_for_next_tick(self):
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        adapter = self.runtime.device
        adapter.set_light_override(81)
        source = self.runtime.configuration.bindings.values["upper_temperature"]
        attributes = self.hass.states.get(source).attributes
        entered, release = asyncio.Event(), asyncio.Event()
        original_on = self.light.async_turn_on

        def event(value):
            return Event("state_changed", {
                "entity_id": source,
                "old_state": State(source, "25", attributes),
                "new_state": State(source, str(value), attributes),
            })

        async def paused_on(**kwargs):
            entered.set()
            await release.wait()
            await original_on(**kwargs)

        with patch.object(self.light, "async_turn_on", side_effect=paused_on):
            worker = asyncio.create_task(self.runtime.device_input(event(26)))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                await self.runtime.device_input(event(27))
                await self.runtime.device_input(event(28))
                worker.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await worker
                self.assertFalse(self.runtime._device_input_pending)
                self.assertEqual(len(self.runtime._pending_device_inputs), 2)
                await asyncio.wait_for(self.runtime.tick(), .5)
                self.assertFalse(self.runtime._pending_device_inputs)
                self.assertEqual(adapter.measurements["upper_temperature"].value, 28)
            finally:
                release.set()
            await adapter._light_service_task
            await self.hass.async_block_till_done()

    async def test_light_output_lock_does_not_block_control_and_handoff_is_bounded(self):
        adapter = self.runtime.device
        adapter.values = {**adapter.values, "feedback_timeout_seconds": .1}
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        await adapter._light_output_lock.acquire()
        try:
            await asyncio.wait_for(self.runtime.set_operation(False), .5)
            self.assertFalse(self.heater.is_on)
            self.assertFalse(await asyncio.wait_for(adapter.finish_session_light(self.now, None), .5))
        finally:
            adapter._light_output_lock.release()
            adapter.restore_light_ownership()

    async def _assert_stale_off_cannot_discharge_late_on(self, *, handoff):
        if not handoff:
            await self.runtime.set_operation(True)
            await self.runtime.set_operation(False)
            await self.hass.async_block_till_done()
        self.assertFalse(self.light.is_on)
        adapter = self.runtime.device
        adapter.values = {**adapter.values, "feedback_timeout_seconds": .1}
        entered, release = asyncio.Event(), asyncio.Event()
        original_on = self.light.async_turn_on

        async def delayed_on(**kwargs):
            entered.set()
            await release.wait()
            await original_on(**kwargs)

        self.light.defer_state_writes = True
        self.light.calls.clear()
        with patch.object(self.light, "async_turn_on", side_effect=delayed_on):
            selecting = asyncio.create_task(self.runtime.set_light_override(80))
            finishing = None
            try:
                await asyncio.wait_for(entered.wait(), 3)
                selecting.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await selecting
                actual_on = adapter._light_service_task
                if handoff:
                    finishing = asyncio.create_task(adapter.finish_session_light(self.now, None))
                    await asyncio.sleep(0)
                    self.assertFalse(adapter._light_owned)
                else:
                    token = next(deadline.token for deadline in self.runtime.session.deadlines
                                 if deadline.purpose == "session_gap")
                    await self.runtime.finish_session_gap(token)
                release.set()
                await actual_on
                if finishing is not None:
                    self.assertTrue(await finishing)
                await self.hass.async_block_till_done()
                # No new state report or periodic tick can rescue the old OFF.
                self.assertEqual(self.hass.states.get(self.light.entity_id).state, "off")
                self.assertEqual([call[0] for call in self.light.calls], ["on", "off"])
                self.assertFalse(self.light.is_on)
            finally:
                release.set()
                await asyncio.gather(selecting, return_exceptions=True)
                if finishing is not None:
                    await asyncio.gather(finishing, return_exceptions=True)
                self.light.defer_state_writes = False
                self.light.async_write_ha_state()
                adapter.restore_light_ownership()
                await self.hass.async_block_till_done()

    async def test_completion_sends_due_off_despite_pre_on_off_state(self):
        await self._assert_stale_off_cannot_discharge_late_on(handoff=False)

    async def test_handoff_sends_off_despite_pre_on_off_state(self):
        await self._assert_stale_off_cannot_discharge_late_on(handoff=True)

    async def test_entity_updates_skip_identical_values_but_keep_dynamic_attributes(self):
        await self.runtime.tick()
        await self.hass.async_block_till_done()
        writes = []
        original_write = SaunaEntity.async_write_ha_state

        def write(entity):
            writes.append(entity.entity_id)
            original_write(entity)

        with patch.object(SaunaEntity, "async_write_ha_state", write):
            for _ in range(100):
                await self.runtime.tick()
            self.assertEqual(writes, [])
            await self.runtime.set_operation(True)
            await self.hass.async_block_till_done()
            self.assertIn(self.operation, writes)
            self.assertIn(self.climate, writes)
            writes.clear()
            # Heating elapsed time/energy are attributes of otherwise stable
            # states and must still reach HA on a later clock tick.
            self.now += timedelta(seconds=1)
            await self.runtime.tick()
            await self.hass.async_block_till_done()
            self.assertTrue(any("betriebszustand" in entity for entity in writes))
            await self.hass.services.async_call("climate", "set_temperature", {
                "entity_id": self.climate, "temperature": 85,
            }, blocking=True)
            self.assertEqual(self.hass.states.get(self.climate).attributes["temperature"], 85)
            entities = [callback.__self__ for callback in self.runtime._subscribers]
            target = next(entity for entity in entities
                          if entity.unique_id.endswith("_target_temperature_c"))
            humidity = next(entity for entity in entities
                            if entity.unique_id.endswith("_absolute_humidity_upper"))
            self.assertEqual(float(self.hass.states.get(target.entity_id).state), 85)
            self.assertNotEqual(self.hass.states.get(humidity.entity_id).state, "unavailable")
            self.now += timedelta(seconds=31)
            await self.runtime.tick()
            self.assertEqual(self.hass.states.get(humidity.entity_id).state, "unavailable")
