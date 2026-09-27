"""Concrete external-state and delayed-feedback counterexamples."""

import asyncio
import importlib.util
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from custom_components.ha_sauna.bindings import Bindings
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime

HA_AVAILABLE = importlib.util.find_spec("homeassistant") is not None
if os.environ.get("HA_TEST_REQUIRED") and not HA_AVAILABLE:
    raise RuntimeError("Für diesen Testlauf muss Home Assistant installiert sein")


T0 = datetime(2026, 9, 27, tzinfo=UTC)
BINDINGS = Bindings(
    {
        "upper_temperature": "sensor.top_t",
        "upper_humidity": "sensor.top_h",
        "lower_temperature": "sensor.bottom_t",
        "lower_humidity": "sensor.bottom_h",
        "heater": "switch.heater",
        "heater_feedback": "binary_sensor.actual_heating",
        "light": "light.sauna",
        "control_input": "binary_sensor.operator",
    }
)


def state(value, brightness=None):
    attributes = {} if brightness is None else {"brightness": brightness}
    return SimpleNamespace(state=value, attributes=attributes, domain="binary_sensor")


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant ist lokal nicht installiert")
class DeviceFeedbackTests(unittest.TestCase):
    def device(self, **parameters):
        from custom_components.ha_sauna.device import HADevice

        configuration = Configuration(BINDINGS, Parameters(parameters))
        runtime = SaunaRuntime(configuration, lambda: T0)
        light = [state("on", 180)]
        hass = SimpleNamespace(states=SimpleNamespace(get=lambda entity: light[0]))
        adapter = HADevice(hass, runtime)
        runtime.device = adapter
        return runtime, adapter, light

    def test_independent_heat_while_off_latches_after_confirmation(self):
        runtime, adapter, _ = self.device(fault_confirmation_seconds=2)
        adapter.ingest("heater", state("off"), T0)
        adapter.ingest("heater_feedback", state("on"), T0)
        adapter.command, adapter.command_at = False, T0
        adapter.refresh(T0 + timedelta(seconds=10))
        self.assertEqual(adapter.faults["heater_still_heating"], "pending")
        adapter.refresh(T0 + timedelta(seconds=12))
        self.assertIn("heater_still_heating", runtime.controller.protection)

    def test_switch_recovery_off_stops_but_recovery_on_does_not_start(self):
        _, adapter, _ = self.device()

        def event(new):
            return SimpleNamespace(
                data={
                    "entity_id": BINDINGS.values["control_input"],
                    "old_state": state("unavailable"),
                    "new_state": state(new),
                }
            )

        self.assertIs(adapter.physical_action(event("off")), False)
        self.assertIsNone(adapter.physical_action(event("on")))

    def test_received_heating_edge_is_booked_before_waiting_cycle(self):
        async def exercise():
            runtime, adapter, _ = self.device()
            clock = [T0]
            runtime._clock = lambda: clock[0]
            runtime.controller.begin_session("delayed", T0)
            adapter.ingest("heater", state("on"), T0)
            adapter.ingest("heater_feedback", state("on"), T0)
            adapter.report_received_feedback(T0)
            adapter.apply = AsyncMock()
            event = SimpleNamespace(
                event_type="state_changed",
                data={
                    "entity_id": BINDINGS.values["heater_feedback"],
                    "old_state": state("on"),
                    "new_state": state("off"),
                },
            )
            await runtime._lock.acquire()
            clock[0] = T0 + timedelta(seconds=1)
            queued = asyncio.create_task(runtime.device_input(event))
            await asyncio.sleep(0)  # The input has captured t=1 and waits on the lock.
            clock[0] = T0 + timedelta(seconds=2)
            returned = SimpleNamespace(
                event_type="state_changed",
                data={
                    "entity_id": BINDINGS.values["heater_feedback"],
                    "old_state": state("off"),
                    "new_state": state("on"),
                },
            )
            queued_return = asyncio.create_task(runtime.device_input(returned))
            await asyncio.sleep(0)
            clock[0] = T0 + timedelta(seconds=11)
            runtime._lock.release()
            await asyncio.gather(queued, queued_return)
            return runtime.session.heating, adapter.apply.await_count

        heating, outputs = asyncio.run(exercise())
        self.assertEqual(heating.elapsed_seconds, 10)
        self.assertEqual(heating.intervals[0].ended_at, T0 + timedelta(seconds=1))
        self.assertEqual(heating.intervals[1].started_at, T0 + timedelta(seconds=2))
        self.assertEqual(outputs, 1)

    def test_waiting_switch_action_keeps_feedback_edges_in_order(self):
        async def exercise():
            runtime, adapter, _ = self.device()
            clock = [T0]
            runtime._clock = lambda: clock[0]
            runtime.controller.begin_session("mixed", T0)
            adapter.ingest("heater", state("on"), T0)
            adapter.ingest("heater_feedback", state("on"), T0)
            adapter.report_received_feedback(T0)
            adapter.apply = AsyncMock()
            events = (
                (1, BINDINGS.values["heater_feedback"], "on", "off"),
                (1.5, BINDINGS.values["control_input"], "on", "off"),
                (2, BINDINGS.values["heater_feedback"], "off", "on"),
            )
            await runtime._lock.acquire()
            waiting = []
            for second, entity_id, old, new in events:
                clock[0] = T0 + timedelta(seconds=second)
                event = SimpleNamespace(
                    event_type="state_changed",
                    data={
                        "entity_id": entity_id,
                        "old_state": state(old),
                        "new_state": state(new),
                    },
                )
                waiting.append(asyncio.create_task(runtime.device_input(event)))
                await asyncio.sleep(0)
            clock[0] = T0 + timedelta(seconds=11)
            runtime._lock.release()
            await asyncio.gather(*waiting)
            return runtime.session, adapter.apply.await_count

        session, outputs = asyncio.run(exercise())
        self.assertFalse(session.operation_enabled)
        self.assertEqual(session.heating.elapsed_seconds, 10)
        self.assertEqual(
            session.heating.intervals[0].ended_at, T0 + timedelta(seconds=1)
        )
        self.assertEqual(
            session.heating.intervals[1].started_at, T0 + timedelta(seconds=2)
        )
        self.assertEqual(outputs, 1)

    def test_cooling_start_uses_received_off_time_after_wait(self):
        async def exercise():
            runtime, adapter, _ = self.device()
            clock = [T0]
            runtime._clock = lambda: clock[0]
            controller = runtime.controller
            controller.set_temperature(90, T0)
            adapter.ingest("heater", state("on"), T0)
            adapter.report_received_feedback(T0)
            controller.begin_session("delayed", T0)
            controller._begin_after_run("g", T0)
            self.assertTrue(controller.session.after_run.pending_start)
            adapter.apply = AsyncMock()
            event = SimpleNamespace(
                event_type="state_changed",
                data={
                    "entity_id": BINDINGS.values["heater"],
                    "old_state": state("on"),
                    "new_state": state("off"),
                },
            )
            await runtime._lock.acquire()
            clock[0] = T0 + timedelta(seconds=1)
            queued = asyncio.create_task(runtime.device_input(event))
            await asyncio.sleep(0)
            clock[0] = T0 + timedelta(seconds=11)
            runtime._lock.release()
            await queued
            return controller.session.after_run.started_at

        self.assertEqual(asyncio.run(exercise()), T0 + timedelta(seconds=1))

    def test_configured_native_light_scale_matches_only_its_exact_echo(self):
        _, adapter, _ = self.device(light_brightness_scale=10)
        adapter._expect_light_change(T0, "turn_on", 39)
        event = SimpleNamespace(
            event_type="state_changed",
            data={
                "entity_id": BINDINGS.values["light"],
                "old_state": state("off"),
                "new_state": state("on", 102),
            },
        )
        self.assertIsNone(adapter.external_light_selection(event, T0))
        adapter._expect_light_change(T0, "turn_on", 39)
        event.data["new_state"] = state("on", 128)
        self.assertAlmostEqual(
            adapter.external_light_selection(event, T0), 128 * 100 / 255
        )

    def test_session_light_off_waits_for_feedback_then_retries(self):
        async def exercise():
            runtime, adapter, light = self.device(feedback_timeout_seconds=2)
            phase = SimpleNamespace(session_id="ended", started_at=T0, ends_at=T0)
            runtime.controller.light_after_run = phase
            adapter.light_call = AsyncMock()
            self.assertFalse(await adapter._finish_expired_session_light(T0))
            self.assertNotIn("session_light", adapter.faults)
            self.assertFalse(
                await adapter._finish_expired_session_light(T0 + timedelta(seconds=1))
            )
            self.assertEqual(adapter.light_call.await_count, 1)
            self.assertFalse(
                await adapter._finish_expired_session_light(T0 + timedelta(seconds=2))
            )
            self.assertEqual(adapter.light_call.await_count, 2)
            self.assertEqual(adapter.faults["session_light"], "feedback_missing")
            light[0] = state("off")
            self.assertTrue(
                await adapter._finish_expired_session_light(T0 + timedelta(seconds=3))
            )
            self.assertNotIn("session_light", adapter.faults)

        asyncio.run(exercise())
