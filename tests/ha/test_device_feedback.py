"""Concrete external-state and delayed-feedback counterexamples."""

import asyncio
import importlib.util
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from custom_components.ha_sauna.bindings import Bindings
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.timeline import Kind
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


def state(value, brightness=None, *, unit=None):
    attributes = {} if brightness is None else {"brightness": brightness}
    if unit is not None:
        attributes["unit_of_measurement"] = unit
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
            adapter.command, adapter.command_at = True, T0
            adapter.apply = AsyncMock()
            await runtime.tick()
            adapter.apply.reset_mock()
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
            outputs = adapter.apply.await_count
            await runtime.tick()
            return runtime.session.heating, outputs, runtime.detector.heating_since

        heating, outputs, heating_since = asyncio.run(exercise())
        self.assertEqual(heating.elapsed_seconds, 10)
        self.assertEqual(heating.intervals[0].ended_at, T0 + timedelta(seconds=1))
        self.assertEqual(heating.intervals[1].started_at, T0 + timedelta(seconds=2))
        self.assertEqual(outputs, 1)
        self.assertEqual(heating_since, T0 + timedelta(seconds=2))

    def test_queued_operation_pause_restarts_thermal_proof_without_physical_pause(self):
        async def exercise(queued):
            runtime, adapter, _ = self.device(target_temperature_c=100)
            options = runtime.configuration.as_options()
            options.update(button_program="constant", button_temperature_c=100)
            runtime.configuration = Configuration.from_options(options)
            clock = [T0]
            runtime._clock = lambda: clock[0]
            runtime.controller.begin_session("operation-gate", T0)
            adapter.ingest("heater", state("on"), T0)
            adapter.ingest("heater_feedback", state("on"), T0)
            adapter.command, adapter.command_at = True, T0
            adapter.apply = AsyncMock()  # Replace only the actuator boundary.
            tasks = []
            for second in range(61):
                clock[0] = T0 + timedelta(seconds=second)
                if second == 30 and queued:
                    runtime._lock.release()
                    await asyncio.gather(*tasks)
                if second not in (28, 29):
                    for position in ("upper", "lower"):
                        adapter.ingest(
                            f"{position}_temperature",
                            state(str(85 - max(0, second - 20) * .03), unit="°C"),
                            clock[0],
                        )
                        adapter.ingest(
                            f"{position}_humidity", state("30", unit="%"), clock[0],
                        )
                if second == 28 and queued:
                    await runtime._lock.acquire()
                if second in (28, 29):
                    event = SimpleNamespace(event_type="state_changed", data={
                        "entity_id": BINDINGS.values["control_input"],
                        "old_state": state("on" if second == 28 else "off"),
                        "new_state": state("off" if second == 28 else "on"),
                    })
                    if queued:
                        tasks.append(asyncio.create_task(runtime.device_input(event)))
                        await asyncio.sleep(0)
                    else:
                        await runtime.device_input(event)
                if not queued or second not in (28, 29):
                    await runtime.tick()
            return runtime, adapter

        for queued in (False, True):
            with self.subTest(queued=queued):
                runtime, adapter = asyncio.run(exercise(queued))
                self.assertEqual(adapter.start_errors(), [])
                self.assertNotIn("start_rejected", adapter.faults)
                self.assertTrue(runtime.session.operation_enabled)
                self.assertEqual(runtime.detector.heating_since, T0 + timedelta(seconds=29))
                self.assertEqual(
                    [(i.started_at, i.ended_at) for i in runtime.session.heating.intervals],
                    [(T0, None)],
                )
                opened = next(
                    event for event in runtime.session.timeline.processed
                    if event.kind == Kind.DOOR_OPEN
                )
                self.assertEqual(opened.effective_at, T0 + timedelta(seconds=38))
                self.assertEqual(opened.detected_at, T0 + timedelta(seconds=47))

    def test_waiting_tick_or_command_consumes_received_contactor_off_first(self):
        async def exercise(command):
            runtime, adapter, _ = self.device()
            clock = [T0]
            runtime._clock = lambda: clock[0]
            runtime.controller.begin_session("contactor", T0)
            adapter.ingest("heater", state("on"), T0)
            adapter.report_received_feedback(T0)
            adapter.apply = AsyncMock()
            await runtime._lock.acquire()
            clock[0] = T0 + timedelta(seconds=0.5)
            waiting = asyncio.create_task(
                runtime.set_light_override(40) if command else runtime.tick()
            )
            await asyncio.sleep(0)
            clock[0] = T0 + timedelta(seconds=1)
            edge = asyncio.create_task(runtime.device_input(SimpleNamespace(
                event_type="state_changed",
                data={
                    "entity_id": BINDINGS.values["heater"],
                    "old_state": state("on"),
                    "new_state": state("off"),
                },
            )))
            await asyncio.sleep(0)
            clock[0] = T0 + timedelta(seconds=11)
            runtime._lock.release()
            await asyncio.gather(waiting, edge)
            return runtime.session.heating

        for command in (False, True):
            with self.subTest(command=command):
                heating = asyncio.run(exercise(command))
                self.assertEqual(heating.elapsed_seconds, 1)
                self.assertEqual(heating.intervals[0].ended_at, T0 + timedelta(seconds=1))

    def test_binary_release_received_behind_tick_stays_a_short_press(self):
        async def exercise():
            runtime, adapter, _ = self.device(button_hold_seconds=2)
            runtime.configuration = replace(runtime.configuration, control_input_mode="button")
            clock = [T0]
            runtime._clock = lambda: clock[0]
            runtime.controller.begin_session("short", T0)
            adapter.ingest("upper_temperature", state("70"), T0)
            adapter.apply = AsyncMock()

            def edge(old, new):
                return SimpleNamespace(event_type="state_changed", data={
                    "entity_id": BINDINGS.values["control_input"],
                    "old_state": state(old), "new_state": state(new),
                })

            await runtime.device_input(edge("off", "on"))
            await runtime._lock.acquire()
            clock[0] = T0 + timedelta(seconds=0.5)
            tick = asyncio.create_task(runtime.tick())
            await asyncio.sleep(0)
            clock[0] = T0 + timedelta(seconds=1)
            release = asyncio.create_task(runtime.device_input(edge("on", "off")))
            await asyncio.sleep(0)
            clock[0] = T0 + timedelta(seconds=3)
            runtime._lock.release()
            await asyncio.gather(tick, release)
            return runtime

        runtime = asyncio.run(exercise())
        self.assertIsNotNone(runtime.session)
        self.assertTrue(runtime.session.operation_enabled)
        self.assertEqual(runtime.controller.completed_sessions, ())
        self.assertIsNone(runtime.controller.light_after_run)

    def test_waiting_off_enters_before_outputs_with_recurring_inputs(self):
        async def exercise():
            runtime, adapter, _ = self.device()
            clock = [T0]
            runtime._clock = lambda: clock[0]
            runtime.controller.begin_session("stop", T0)
            entered, release = asyncio.Queue(), asyncio.Queue()
            pending = []

            async def slow_output(now):
                entered.put_nowait(runtime.session.operation_enabled)
                await release.get()

            async def receive_temperature():
                clock[0] += timedelta(seconds=10)
                pending.append(asyncio.create_task(runtime.device_input(SimpleNamespace(
                    event_type="state_changed", data={
                        "entity_id": BINDINGS.values["upper_temperature"],
                        "old_state": state("70", unit="°C"),
                        "new_state": state("69", unit="°C"),
                    },
                ))))
                await asyncio.sleep(0)

            adapter.apply = slow_output
            await runtime._lock.acquire()
            stop = asyncio.create_task(runtime.set_operation(False))
            await asyncio.sleep(0)
            await receive_temperature()
            runtime._lock.release()
            enabled_during_outputs = []
            for _ in range(3):
                enabled_during_outputs.append(await asyncio.wait_for(entered.get(), 1))
                await receive_temperature()
                release.put_nowait(None)
            adapter.apply = AsyncMock()
            await asyncio.wait_for(asyncio.gather(stop, *pending), 1)
            return enabled_during_outputs

        self.assertEqual(asyncio.run(exercise()), [False, False, False])

    def test_consumed_inputs_get_output_after_configuration_or_rejected_command(self):
        async def exercise(reject, output_fails):
            runtime, adapter, _ = self.device()
            runtime.controller.begin_session("configuration", T0)
            adapter.apply = AsyncMock(
                side_effect=OSError("output") if output_fails else None
            )

            async def command():
                async with runtime.serialized():
                    if reject:
                        raise ValueError("command")
                    runtime.set_log_level("INFO")

            await runtime._lock.acquire()
            change = asyncio.create_task(command())
            await asyncio.sleep(0)
            received = asyncio.create_task(runtime.device_input(SimpleNamespace(
                event_type="state_changed", data={
                    "entity_id": BINDINGS.values["upper_temperature"],
                    "old_state": state("70", unit="°C"),
                    "new_state": state("69", unit="°C"),
                },
            )))
            await asyncio.sleep(0)
            runtime._lock.release()
            results = await asyncio.gather(change, received, return_exceptions=True)
            self.assertEqual(adapter.apply.await_count, 1)
            self.assertEqual(adapter.measurements["upper_temperature"].value, 69)
            return results[0]

        self.assertIsNone(asyncio.run(exercise(False, False)))
        self.assertIsInstance(asyncio.run(exercise(True, False)), ValueError)
        both = asyncio.run(exercise(True, True))
        self.assertIsInstance(both, ValueError)
        self.assertEqual(str(both), "command")
        self.assertIn("OSError('output')", both.__notes__[0])

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

    def test_queued_dimmer_before_session_light_deadline_keeps_due_off(self):
        async def exercise():
            runtime, adapter, _ = self.device(feedback_timeout_seconds=2)
            clock = [T0]
            runtime._clock = lambda: clock[0]
            phase = SimpleNamespace(
                session_id="ended", started_at=T0,
                ends_at=T0 + timedelta(seconds=2),
            )
            runtime.controller.light_after_run = phase
            adapter.send = AsyncMock()
            adapter.light_call = AsyncMock()
            event = SimpleNamespace(
                event_type="state_changed",
                data={
                    "entity_id": BINDINGS.values["light"],
                    "old_state": state("on", 180),
                    "new_state": state("on", 200),
                },
            )
            await runtime._lock.acquire()
            clock[0] = T0 + timedelta(seconds=1)
            queued = asyncio.create_task(runtime.device_input(event))
            await asyncio.sleep(0)  # The physical choice was received before OFF.
            clock[0] = T0 + timedelta(seconds=3)
            runtime._lock.release()
            await queued
            self.assertIsNone(adapter._light_session_off_superseded_key)
            self.assertIn(
                "turn_off", [call.args[0] for call in adapter.light_call.await_args_list]
            )
            self.assertEqual(
                adapter.light_output.manual_ends_at,
                T0 + timedelta(seconds=1, minutes=adapter.values["manual_override_minutes"]),
            )

        asyncio.run(exercise())
