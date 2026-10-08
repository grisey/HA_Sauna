"""Concrete external-state and delayed-feedback counterexamples."""

import asyncio
import importlib.util
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from custom_components.ha_sauna.bindings import Bindings
from custom_components.ha_sauna.core.button import END_HOLD
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.timeline import Event, Kind
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
    def test_readonly_light_observation_distinguishes_unknown_from_off(self):
        from custom_components.ha_sauna.api import manual_controls

        runtime, adapter, light = self.device()
        for value in (None, state("unknown"), state("unavailable"), state("on"),
                      state("on", float("nan"))):
            with self.subTest(state=value):
                light[0] = value
                self.assertEqual(manual_controls(runtime)["light"]["observation"],
                                 {"available": False, "brightness_percent": None})
        light[0] = state("off")
        self.assertEqual(adapter.light_observation,
                         {"available": True, "brightness_percent": 0.0})
        light[0] = state("on", 153)
        self.assertEqual(manual_controls(runtime)["light"]["observation"],
                         {"available": True, "brightness_percent": 60.0})
        runtime.device = None
        self.assertEqual(manual_controls(runtime)["light"]["observation"],
                         {"available": False, "brightness_percent": None})

    def device(self, **parameters):
        from custom_components.ha_sauna.device import HADevice

        configuration = Configuration(BINDINGS, Parameters(parameters))
        runtime = SaunaRuntime(configuration, lambda: T0)
        light = [state("on", 180)]
        hass = SimpleNamespace(states=SimpleNamespace(get=lambda entity: light[0]))
        adapter = HADevice(hass, runtime)
        runtime.device = adapter
        return runtime, adapter, light

    def detection_device(self, *, both_positions=False, **parameters):
        from custom_components.ha_sauna.device import HADevice

        clock = [T0]
        roles = {key: value for key, value in BINDINGS.values.items()
                 if key != "heater_feedback"
                 and (both_positions or key not in {"lower_temperature", "lower_humidity"})}
        runtime = SaunaRuntime(Configuration(Bindings(roles), Parameters({
            "target_temperature_c": 80, "feedback_timeout_seconds": 60, **parameters,
        })), lambda: clock[0])
        hass = SimpleNamespace(states=SimpleNamespace(get=lambda _entity: None),
                               services=SimpleNamespace(async_call=AsyncMock()))
        adapter = HADevice(hass, runtime)
        runtime.device = adapter
        adapter.apply_light = AsyncMock()
        adapter.command, adapter.command_at, adapter.last_sent_at = False, T0, T0
        adapter.ingest("heater", state("off"), T0, initial=True)
        adapter.ingest("upper_temperature", state("90", unit="°C"), T0, initial=True)
        adapter.ingest("upper_humidity", state("20", unit="%"), T0, initial=True)
        if both_positions:
            adapter.ingest("lower_temperature", state("90", unit="°C"), T0, initial=True)
            adapter.ingest("lower_humidity", state("20", unit="%"), T0, initial=True)
        adapter.refresh(T0)
        return runtime, adapter, clock

    @staticmethod
    def detection_edge(runtime, role, value, old="off"):
        return SimpleNamespace(event_type="state_changed", data={
            "entity_id": runtime.configuration.bindings.values[role],
            "old_state": state(old),
            "new_state": state(str(value), unit=("°C" if role.endswith("temperature")
                                                else "%" if role.endswith("humidity") else None)),
        })

    def test_received_readiness_releases_override_before_current_output(self):
        async def exercise(queued, same_time, path):
            runtime, adapter, clock = self.detection_device(feedback_timeout_seconds=10)
            adapter.ingest("upper_temperature", state("79.99", unit="°C"), T0, initial=True)
            adapter.refresh(T0)
            runtime.controller.begin_session("readiness", T0)
            await runtime.start_archive(path, "readiness-entry")
            clock[0] = T0 + timedelta(seconds=100)
            await runtime.set_heater_override(False)
            self.assertIsNone(runtime.session.ready_at)
            self.assertFalse(runtime.controller.last_decision.heat)
            del adapter.apply_light  # Use the original adapter's light service boundary.
            calls, entered, release = [], asyncio.Event(), asyncio.Event()

            async def service(domain, service, data, **_kwargs):
                calls.append((domain, service, clock[0]))
                if queued and domain == "light" and not entered.is_set():
                    entered.set()
                    await release.wait()

            adapter.hass.services.async_call = service
            clock[0] = T0 + timedelta(seconds=101)
            light = asyncio.create_task(runtime.set_light_override(80))
            if queued:
                await entered.wait()
            else:
                await light
            pending = []
            for second, value in ((102, 80.01), (102 if same_time else 103, 79.99)):
                clock[0] = T0 + timedelta(seconds=second)
                edge = asyncio.create_task(runtime.device_input(
                    self.detection_edge(runtime, "upper_temperature", value)))
                if queued:
                    pending.append(edge)
                    await asyncio.sleep(0)
                else:
                    await edge
            clock[0] = T0 + timedelta(seconds=104)
            if queued:
                release.set()
                await asyncio.gather(light, *pending)
            await runtime.archive.flush()
            stored = await asyncio.to_thread(runtime.archive.read, "readiness")
            await runtime.archive.close()
            return runtime, calls, stored

        with TemporaryDirectory() as directory:
            for queued in (False, True):
                for same_time in (False, True):
                    with self.subTest(queued=queued, same_time=same_time):
                        runtime, calls, stored = asyncio.run(exercise(
                            queued, same_time, Path(directory) / f"{queued}-{same_time}.sqlite"))
                        self.assertEqual(runtime.session.ready_at, T0 + timedelta(seconds=102))
                        self.assertEqual(runtime.controller.phase, "bereit")
                        self.assertIsNone(runtime.controller.heater_override)
                        self.assertTrue(runtime.controller.last_decision.heat)
                        temperatures = [r["payload"]["value"] for r in stored["records"]
                                        if r["kind"] == "measurement"
                                        and r["payload"]["quantity"] == "temperature"]
                        self.assertEqual(temperatures, [80.01, 79.99])
                        heat = next(r for r in stored["records"]
                                    if r["kind"] == "decision"
                                    and r["payload"]["at"] == (T0 + timedelta(seconds=102)).isoformat()
                                    and r["payload"]["heat"])
                        created_at = T0 + timedelta(seconds=104 if queued else 102)
                        self.assertEqual(heat["received_at"], created_at.isoformat())
                        self.assertEqual(heat["payload"]["created_at"], created_at.isoformat())
                        if queued:
                            self.assertEqual([at for domain, service, at in calls
                                              if domain == "switch" and service == "turn_on"],
                                             [created_at])

    def test_same_time_start_does_not_take_a_later_temperature_before_its_input(self):
        async def exercise():
            runtime, adapter, clock = self.detection_device()
            target = runtime.configuration.button_temperature_c
            below, above = target - .01, target + .01
            adapter.ingest("upper_temperature", state(str(below), unit="°C"), T0, initial=True)
            adapter.refresh(T0)
            await runtime._lock.acquire()
            clock[0] = T0 + timedelta(seconds=1)
            pending = []
            for role, value in (("upper_temperature", below), ("control_input", "on"),
                                ("upper_temperature", above), ("upper_temperature", below)):
                pending.append(asyncio.create_task(runtime.device_input(
                    self.detection_edge(runtime, role, value))))
                await asyncio.sleep(0)
            at_start = []
            start = runtime.controller.set_operation

            def operation(enabled, at, **kwargs):
                self.assertEqual(runtime.controller.target_temperature, target)
                result = start(enabled, at, **kwargs)
                at_start.append((runtime.controller.temperature, runtime.session.ready_at))
                return result

            runtime.controller.set_operation = operation
            runtime._lock.release()
            await asyncio.gather(*pending)
            return runtime, at_start

        runtime, at_start = asyncio.run(exercise())
        below = runtime.controller.target_temperature - .01
        self.assertEqual(at_start, [(below, None)])
        self.assertEqual(runtime.session.ready_at, T0 + timedelta(seconds=1))
        self.assertEqual(runtime.controller.temperature, below)

    def test_blocked_humidity_rise_cannot_be_revived_after_waiting_off_service(self):
        async def exercise(queued, resumes_at, off_at=2, fresh_after_resume=False):
            runtime, adapter, clock = self.detection_device()
            # Regular heat must stay off both before and after a physical
            # restart; only an admitted infusion may request heat in this test.
            cutoff = max(
                runtime.controller.thermostat_target,
                runtime.configuration.button_temperature_c
                + runtime.configuration.parameters.values["readiness_offset_c"],
            )
            adapter.ingest("upper_temperature", state(str(cutoff), unit="°C"), T0, initial=True)
            adapter.refresh(T0)
            runtime.controller.begin_session("admission", T0)
            calls, entered, release = [], asyncio.Event(), asyncio.Event()

            async def service(domain, service, data, **_kwargs):
                calls.append(service)
                if queued and len(calls) == 1:
                    entered.set()
                    await release.wait()

            adapter.hass.services.async_call = service
            adapter.command = True  # Original adapter must send the required OFF.
            initial = asyncio.create_task(runtime.tick())
            if queued:
                await entered.wait()
            else:
                await initial
            pending = []
            for second in range(1, 51):
                clock[0] = T0 + timedelta(seconds=second)
                values = []
                if resumes_at is not None and second == off_at:
                    values.append(("control_input", "off", "on"))
                if second == 10:
                    values.append(("upper_humidity", 23, "20"))
                if second == resumes_at:
                    values.append(("control_input", "on", "off"))
                if fresh_after_resume and second == 40:
                    values.append(("upper_humidity", 26, "23"))
                for role, value, old in values:
                    task = asyncio.create_task(runtime.device_input(
                        self.detection_edge(runtime, role, value, old)))
                    if queued:
                        pending.append(task)
                        await asyncio.sleep(0)
                    else:
                        await task
                if not queued:
                    await runtime.tick()
            if queued:
                release.set()
                await asyncio.gather(initial, *pending)
                await runtime.tick()
            return runtime, calls

        for off_at, resumes_at in ((2, 14), (2, 30), (28, 30)):
            for queued in (False, True):
                with self.subTest(queued=queued, resumes_at=resumes_at):
                    runtime, calls = asyncio.run(exercise(queued, resumes_at, off_at))
                    self.assertIsNone(runtime.session.timeline.active)
                    if queued or off_at == 2:
                        self.assertNotIn("turn_on", calls)
                    else:
                        self.assertIn("turn_on", calls)
                    self.assertEqual(runtime.session.timeline.gang_count, int(off_at == 28))
                    if off_at == 28:
                        gang = runtime.session.timeline.completed[0]
                        self.assertEqual(gang.started_at, T0 + timedelta(seconds=17))
                        self.assertEqual(gang.ended_at, T0 + timedelta(seconds=28))
                        self.assertEqual(gang.detected_at,
                                         T0 + timedelta(seconds=50 if queued else 17))
                    self.assertFalse(runtime.controller.last_decision.heat)
        runtime, calls = asyncio.run(exercise(False, None))
        self.assertTrue(runtime.session.timeline.active.infusion_events)
        self.assertIn("turn_on", calls)
        runtime, calls = asyncio.run(exercise(True, 30, 28, fresh_after_resume=True))
        self.assertTrue(runtime.session.timeline.active.infusion_events)
        self.assertIn("turn_on", calls)

    def test_waiting_api_off_counts_received_infusion_before_current_output(self):
        async def exercise(path):
            runtime, adapter, clock = self.detection_device(
                final_temperature_c=90, temperature_gangs=3)
            runtime.configuration = replace(runtime.configuration, program_mode="progressive")
            runtime.controller.program_mode = "progressive"
            runtime.controller.begin_session("api-admission", T0)
            await runtime.start_archive(path, "api-admission-entry")
            calls, entered, release = [], asyncio.Event(), asyncio.Event()

            async def service(domain, service, data, **_kwargs):
                calls.append(service)
                if len(calls) == 1:
                    entered.set()
                    await release.wait()

            adapter.hass.services.async_call = service
            adapter.command = True
            initial = asyncio.create_task(runtime.tick())
            await entered.wait()
            clock[0] = T0 + timedelta(seconds=5)
            off = asyncio.create_task(runtime.set_operation(False))
            await asyncio.sleep(0)
            clock[0] = T0 + timedelta(seconds=10)
            humidity = asyncio.create_task(runtime.device_input(
                self.detection_edge(runtime, "upper_humidity", 23, "20")))
            await asyncio.sleep(0)
            clock[0] = T0 + timedelta(seconds=50)
            release.set()
            await asyncio.gather(initial, humidity, off)
            await runtime.archive.flush()
            stored = await asyncio.to_thread(runtime.archive.read, "api-admission")
            await runtime.archive.close()
            return runtime, calls, stored

        with TemporaryDirectory() as directory:
            runtime, calls, stored = asyncio.run(exercise(Path(directory) / "api.sqlite"))
        self.assertFalse(runtime.session.operation_enabled)
        self.assertIsNone(runtime.session.timeline.active)
        self.assertEqual(runtime.session.timeline.gang_count, 1)
        self.assertEqual(runtime.controller.target_temperature, 85)
        self.assertNotIn("turn_on", calls)
        gang = runtime.session.timeline.completed[0]
        self.assertEqual((gang.started_at, gang.ended_at),
                         (T0 + timedelta(seconds=17), T0 + timedelta(seconds=50)))
        self.assertEqual(gang.detected_at, T0 + timedelta(seconds=50))
        detection = next(record["payload"] for record in stored["records"]
                         if record["kind"] == "detection"
                         and record["payload"]["event"]["kind"] == Kind.INFUSION.value)
        self.assertEqual(detection["event"]["booking_at"], detection["trace_at"])
        self.assertNotEqual(detection["event"]["booking_at"], detection["event"]["detected_at"])
        decision = next(record for record in stored["records"]
                        if record["kind"] == "decision"
                        and record["payload"]["reason"] == "gang_heat_demand")
        self.assertEqual(decision["payload"]["at"], (T0 + timedelta(seconds=17)).isoformat())
        self.assertEqual(decision["payload"]["created_at"], gang.detected_at.isoformat())
        self.assertEqual(decision["received_at"], gang.detected_at.isoformat())
        ended = next(e for e in runtime.consumer_events if e.kind == "gang_ended")
        self.assertEqual(ended.received_at, gang.detected_at)

    def test_sampling_stops_at_session_gap_before_old_detector_callback(self):
        async def exercise(path, resumes):
            runtime, adapter, clock = self.detection_device(session_gap_minutes=1)
            runtime.controller.begin_session("gap", T0)
            await runtime.start_archive(path, "gap-entry")
            await runtime.tick()
            clock[0] = T0 + timedelta(seconds=1)
            await runtime.receive(Event("person-gap", "gap", Kind.PERSON_STRONG,
                                        clock[0], clock[0]))
            if resumes:
                await runtime.receive(Event("water-gap", "gap", Kind.INFUSION,
                                            clock[0], clock[0]))
            await runtime._lock.acquire()
            clock[0] = T0 + timedelta(seconds=2)
            off = asyncio.create_task(runtime.device_input(
                self.detection_edge(runtime, "control_input", "off", "on")))
            await asyncio.sleep(0)
            pending = [off]
            if resumes:
                clock[0] = T0 + timedelta(seconds=65)
                pending.append(asyncio.create_task(runtime.device_input(
                    self.detection_edge(runtime, "control_input", "on", "off"))))
                await asyncio.sleep(0)
            clock[0] = T0 + timedelta(seconds=70)
            runtime._lock.release()
            await asyncio.gather(*pending)
            await runtime.archive.flush()
            stored = await asyncio.to_thread(runtime.archive.read, "gap")
            await runtime.archive.close()
            return runtime, stored

        with TemporaryDirectory() as directory:
            for resumes in (False, True):
                with self.subTest(resumes=resumes):
                    runtime, stored = asyncio.run(exercise(
                        Path(directory) / f"gap-{resumes}.sqlite", resumes))
                    old = runtime.controller.completed_sessions[-1]
                    self.assertEqual(old.ended_at,
                                     T0 + timedelta(seconds=62))
                    self.assertEqual(old.timeline.gang_count, int(resumes))
                    self.assertEqual(len(old.timeline.completed), 1)
                    self.assertEqual(old.timeline.completed[0].end_reason, "ausgeschaltet")
                    self.assertEqual(old.timeline.completed[0].ended_at,
                                     T0 + timedelta(seconds=2))
                    self.assertEqual(old.timeline.retracted, ())
                    self.assertEqual(runtime.presence.current.occupancy, "unknown")
                    ended = next(e for e in runtime.consumer_events if e.kind == "gang_ended")
                    self.assertEqual(ended.received_at, T0 + timedelta(seconds=70))
                    withdrawal = next(e for e in runtime.consumer_events if e.kind == "occupancy"
                                      and e.presence.assertion == "proxy_retraction")
                    self.assertEqual(withdrawal.session_id, "gap")
                    if resumes:
                        self.assertTrue(any(r["kind"] == "presence"
                                            and r["payload"]["assertion"] == "proxy_retraction"
                                            for r in stored["records"]))
                    else:
                        self.assertIsNone(stored)  # Unconfirmed attempts are discarded.
                    if resumes:
                        self.assertNotEqual(runtime.session.session_id, "gap")
                        self.assertEqual(runtime._detector_session, runtime.session.session_id)
                        self.assertEqual(runtime.detector.origin, runtime.session.started_at)
                        self.assertEqual(runtime.presence.current.source_ref, runtime.session.session_id)
                    else:
                        self.assertIsNone(runtime.session)
                        self.assertIsNone(runtime.detector)
                        self.assertEqual(runtime.presence.current.source_ref, "person-gap")

    def test_physical_session_start_delivers_all_following_measurements_in_one_batch(self):
        async def exercise(queued, path):
            runtime, adapter, clock = self.detection_device()
            await runtime.start_archive(path, "start-entry")
            pending = []
            if queued:
                await runtime._lock.acquire()
            start = asyncio.create_task(runtime.device_input(
                self.detection_edge(runtime, "control_input", "on")))
            if queued:
                pending.append(start)
                await asyncio.sleep(0)
            else:
                await start
            for role, value in (("upper_temperature", 90), ("upper_humidity", 20),
                                ("upper_temperature", 89.99), ("upper_humidity", 20.01)):
                task = asyncio.create_task(runtime.device_input(
                    self.detection_edge(runtime, role, value)))
                if queued:
                    pending.append(task)
                    await asyncio.sleep(0)
                else:
                    await task
            for second in range(1, 39):
                clock[0] = T0 + timedelta(seconds=second)
                fall = max(0, second - 15)
                for role, value in (("upper_temperature", 90 - .08 * fall),
                                    ("upper_humidity", 20 - .06 * fall)):
                    task = asyncio.create_task(runtime.device_input(
                        self.detection_edge(runtime, role, value)))
                    if queued:
                        pending.append(task)
                        await asyncio.sleep(0)
                    else:
                        await task
                if not queued:
                    await runtime.tick()
            if queued:
                runtime._lock.release()
                await asyncio.gather(*pending)
            await runtime.tick()
            await runtime.archive.flush()
            stored = await asyncio.to_thread(runtime.archive.read, runtime.session.session_id)
            await runtime.archive.close()
            return runtime, stored

        with TemporaryDirectory() as directory:
            for queued in (False, True):
                with self.subTest(queued=queued):
                    runtime, stored = asyncio.run(exercise(
                        queued, Path(directory) / f"start-{queued}.sqlite"))
                    self.assertIn(Kind.DOOR_OPEN,
                                  [event.kind for event in runtime.session.timeline.processed])
                    self.assertEqual(sum(record["kind"] == "measurement"
                                         for record in stored["records"]), 80)
                    initial_originals = [record["payload"]["value"] for record in stored["records"]
                                         if record["kind"] == "measurement"
                                         and record["payload"]["received_at"] == T0.isoformat()]
                    self.assertEqual(initial_originals, [90, 20, 89.99, 20.01])
                    traces = [record["payload"] for record in stored["records"]
                              if record["kind"] == "detector_trace"]
                    self.assertTrue(any(trace["at"] == (T0 + timedelta(seconds=17)).isoformat()
                                        and trace["metrics"]["upper"]["door_temperature_slope"] is not None
                                        for trace in traces))

    def test_measurement_and_tick_consume_equal_confirmation_only_after_sampling(self):
        async def exercise(tick_first, confirmation_at):
            runtime, adapter, clock = self.detection_device(
                both_positions=tick_first in ("split", "control_split", "control_last"),
                confirmation_minutes=1, median_seconds=1,
                infusion_window_seconds=1, infusion_hold_seconds=1,
            )
            runtime.controller.begin_session("confirmation", T0)
            await runtime.tick()
            clock[0] = T0 + timedelta(seconds=11)
            await runtime.receive(Event("person", "confirmation", Kind.PERSON_STRONG,
                                        clock[0], clock[0]))
            old = runtime.session.timeline.active
            clock[0] = T0 + timedelta(seconds=confirmation_at - 1)
            roles = (("upper_humidity", "lower_humidity")
                     if tick_first in ("split", "control_split", "control_last")
                     else ("upper_humidity",))
            await asyncio.gather(*(runtime.device_input(self.detection_edge(runtime, role, 22))
                                   for role in roles))
            clock[0] = T0 + timedelta(seconds=confirmation_at)
            if tick_first in ("control_split", "control_last"):
                upper = runtime.device_input(self.detection_edge(runtime, "upper_humidity", 24))
                lower = runtime.device_input(self.detection_edge(runtime, "lower_humidity", 24))
                off = runtime.device_input(self.detection_edge(runtime, "control_input", "off", "on"))
                if tick_first == "control_split":
                    await asyncio.gather(upper, off, lower)
                else:
                    await asyncio.gather(upper, lower, off)
                return runtime, old
            if tick_first == "split":
                # HA dispatch order: upper callback, due tick, lower callback.
                await asyncio.gather(
                    runtime.device_input(self.detection_edge(runtime, "upper_humidity", 24)),
                    runtime.tick(),
                    runtime.device_input(self.detection_edge(runtime, "lower_humidity", 24)),
                )
                return runtime, old
            if tick_first is None:
                # Normal concurrently dispatched callbacks, without a test-held lock.
                await asyncio.gather(
                    runtime.device_input(self.detection_edge(runtime, "upper_temperature", 90)),
                    runtime.device_input(self.detection_edge(runtime, "upper_humidity", 24)),
                )
                return runtime, old
            await runtime._lock.acquire()
            async def humidity():
                await runtime.device_input(self.detection_edge(runtime, "upper_humidity", 24))
            first = asyncio.create_task(runtime.tick() if tick_first else humidity())
            await asyncio.sleep(0)
            second = asyncio.create_task(humidity() if tick_first else runtime.tick())
            await asyncio.sleep(0)
            runtime._lock.release()
            await asyncio.gather(first, second)
            return runtime, old

        for confirmation_at in (70, 71, 72):
            for tick_first in (False, True, None, "split", "control_split", "control_last"):
                with self.subTest(tick_first=tick_first, confirmation_at=confirmation_at):
                    runtime, old = asyncio.run(exercise(tick_first, confirmation_at))
                    ended = tick_first in ("control_split", "control_last")
                    gang = (runtime.session.timeline.completed[-1] if ended
                            else runtime.session.timeline.active)
                    self.assertTrue(gang.infusion_events)
                    if ended:
                        self.assertFalse(runtime.session.operation_enabled)
                        self.assertEqual(runtime.session.timeline.gang_count, 1)
                        self.assertEqual(runtime.presence.current.occupancy, "unknown")
                    if confirmation_at <= 71:
                        self.assertEqual((gang.gang_id, gang.started_at),
                                         (old.gang_id, old.started_at))
                        if not ended:
                            self.assertEqual(runtime.presence.current.occupancy, "present")
                    else:
                        self.assertNotEqual(gang.gang_id, old.gang_id)
                        self.assertEqual(runtime.presence.current.occupancy, "unknown")

    def test_fifo_native_relay_off_preserves_earlier_thermal_detection(self):
        from custom_components.ha_sauna.device import HADevice

        async def exercise(path):
            roles = {key: value for key, value in BINDINGS.values.items()
                     if key not in ("heater_feedback", "lower_temperature", "lower_humidity")}
            runtime = SaunaRuntime(
                Configuration(Bindings(roles), Parameters({"target_temperature_c": 100})),
                lambda: clock[0],
            )
            hass = SimpleNamespace(states=SimpleNamespace(get=lambda _entity: None))
            adapter = HADevice(hass, runtime)
            runtime.device = adapter
            adapter.command, adapter.command_at = True, T0
            adapter.ingest("heater", state("on"), T0)
            adapter.report_received_feedback(T0)
            adapter.apply = AsyncMock()
            runtime.controller.begin_session("fifo", T0)
            await runtime.start_archive(path, "fifo-entry")
            await runtime.tick()

            def edge(role, value):
                return SimpleNamespace(event_type="state_changed", data={
                    "entity_id": roles[role],
                    "old_state": state("unknown"),
                    "new_state": state(str(value), unit=("°C" if role.endswith("temperature")
                                                  else "%" if role.endswith("humidity")
                                                  else None)),
                })

            pending = []
            await runtime._lock.acquire()
            for second in range(1, 61):
                clock[0] = T0 + timedelta(seconds=second)
                for role, value in (
                    ("upper_temperature", 50 - .06 * second),
                    ("upper_humidity", 30),
                ):
                    pending.append(asyncio.create_task(runtime.device_input(edge(role, value))))
                    await asyncio.sleep(0)
                if second == 40:
                    pending.append(asyncio.create_task(runtime.device_input(edge("heater", "off"))))
                    await asyncio.sleep(0)
            runtime._lock.release()
            await asyncio.gather(*pending)
            await runtime.tick()
            await runtime.archive.flush()
            stored = await asyncio.to_thread(runtime.archive.read, "fifo")
            await runtime.archive.close()
            return runtime, stored

        clock = [T0]
        with TemporaryDirectory() as directory:
            runtime, stored = asyncio.run(exercise(Path(directory) / "sessions.sqlite"))
        self.assertEqual(runtime.session.heating.intervals[0].ended_at,
                         T0 + timedelta(seconds=40))
        self.assertIn(Kind.DOOR_OPEN,
                      [event.kind for event in runtime.session.timeline.processed])
        detections = [record["payload"] for record in stored["records"]
                      if record["kind"] == "detection"]
        opening = next(payload for payload in detections
                       if payload["event"]["kind"] == Kind.DOOR_OPEN.value)
        traces = [record["payload"] for record in stored["records"]
                  if record["kind"] == "detector_trace"]
        self.assertTrue(any(trace["at"] == opening["trace_at"]
                            and Kind.DOOR_OPEN.value in trace["signals"]
                            for trace in traces))

    def test_long_hold_off_decision_and_command_keep_old_archive_session(self):
        async def exercise(path):
            runtime, adapter, _ = self.device(target_temperature_c=100)
            runtime._clock = lambda: clock[0]
            adapter.hass.services = SimpleNamespace(async_call=AsyncMock())
            adapter.command, adapter.command_at = True, T0
            adapter.ingest("heater", state("on"), T0)
            adapter.report_received_feedback(T0)
            runtime.controller.begin_session("old", T0)
            runtime.controller.set_temperature(80, T0)
            for kind in (Kind.DOOR_CLOSE, Kind.INFUSION):
                runtime.controller.process(Event(kind.value, "old", kind, T0, T0))
            await runtime.start_archive(path, "archive-entry")
            clock[0] = T0 + timedelta(seconds=3)
            await runtime._apply_button_action(END_HOLD, clock[0])
            await runtime._cycle()
            self.assertIsNone(runtime.session)
            self.assertEqual(runtime.controller.last_decision.session_id, "old")
            clock[0] += timedelta(seconds=1)
            runtime.controller.begin_session("new", clock[0])
            runtime.persist()
            await runtime.archive.flush()
            stored = await asyncio.to_thread(runtime.archive.read, "old")
            await runtime.archive.close()
            return stored

        clock = [T0]
        with TemporaryDirectory() as directory:
            stored = asyncio.run(exercise(Path(directory) / "sessions.sqlite"))
        decisions = [record for record in stored["records"]
                     if record["kind"] == "decision"]
        commands = [record for record in stored["records"]
                    if record["kind"] == "command"]
        self.assertTrue(any(record["payload"]["reason"] == "operation_off"
                            and record["session_id"] == "old" for record in decisions))
        self.assertTrue(any(record["payload"]["heat"] is False
                            and record["session_id"] == "old" for record in commands))

    def test_held_button_retries_unconfirmed_off_and_keeps_fault_visible(self):
        async def exercise():
            runtime, adapter, light = self.device(feedback_timeout_seconds=0.2)
            clock = [T0]
            runtime._clock = lambda: clock[0]
            adapter.light_call = AsyncMock()
            await adapter.show_button_hold_light(T0, "held")
            clock[0] += timedelta(seconds=0.3)
            await adapter.show_button_hold_light(clock[0], "held")
            self.assertEqual(adapter.light_call.await_count, 2)
            self.assertEqual(adapter.faults["operation_light"], "feedback_missing")
            light[0] = state("off")
            adapter.external_light_selection(SimpleNamespace(
                event_type="state_changed", data={
                    "entity_id": BINDINGS.values["light"],
                    "old_state": state("on", 180), "new_state": light[0],
                },
            ), clock[0])
            await adapter.show_button_hold_light(clock[0], "held")
            self.assertNotIn("operation_light", adapter.faults)
            light[0] = state("on", 180)
            await adapter.show_button_hold_light(clock[0], "held")
            self.assertEqual(adapter.light_call.await_count, 3)

        asyncio.run(exercise())

    def test_light_echo_window_starts_when_command_is_sent(self):
        async def exercise():
            runtime, adapter, _ = self.device(feedback_timeout_seconds=0.2)
            clock = [T0 + timedelta(seconds=0.15)]
            runtime._clock = lambda: clock[0]
            adapter.light_call = AsyncMock()
            await adapter._send_light_command(
                T0, key=("heat", "turn_off", None), phase="aufheizen",
                service="turn_off", brightness=None, session_id=None,
            )
            event = SimpleNamespace(event_type="state_changed", data={
                "entity_id": BINDINGS.values["light"],
                "old_state": state("on", 180), "new_state": state("off"),
            })
            self.assertIsNone(adapter.external_light_selection(
                event, T0 + timedelta(seconds=0.21)
            ))

        asyncio.run(exercise())

    def test_light_completion_window_without_archive_is_finite_and_context_specific(self):
        from homeassistant.core import Context

        async def exercise(delay, own_context):
            runtime, adapter, _ = self.device(feedback_timeout_seconds=.2)
            clock = [T0]
            runtime._clock = lambda: clock[0]
            contexts = []

            async def complete(service, data, *, context):
                contexts.append(context)
                clock[0] = T0 + timedelta(seconds=1)

            adapter.light_call = complete
            self.assertIsNone(runtime.archive)
            self.assertTrue(await adapter._send_light_command(
                T0, key=("heat", "turn_on", 80), phase="aufheizen",
                service="turn_on", brightness=80, session_id=None,
            ))
            self.assertEqual(adapter._expected_light_changes[0]["sent_at"], T0)
            self.assertEqual(
                adapter._expected_light_changes[0]["completed_at"], clock[0]
            )
            new = state("on", 204)
            new.context = contexts[0] if own_context else Context()
            event = SimpleNamespace(event_type="state_changed", data={
                "entity_id": BINDINGS.values["light"],
                "old_state": state("on", 180), "new_state": new,
            })
            return adapter.external_light_selection(
                event, clock[0] + timedelta(seconds=delay)
            )

        for delay, own_context, expected in ((.199, True, None), (.2, True, 80),
                                             (.199, False, 80)):
            with self.subTest(delay=delay, own_context=own_context):
                self.assertEqual(asyncio.run(exercise(delay, own_context)), expected)

    def test_large_brightness_integer_is_a_controlled_input_error(self):
        _, adapter, _ = self.device()
        adapter.set_light_override(37, at=T0)
        deadline = adapter.light_output.manual_ends_at
        for invalid in (10**309, -(10**309)):
            with self.subTest(invalid=invalid > 0), self.assertRaises(ValueError):
                adapter.set_light_override(invalid, at=T0)
        self.assertEqual(adapter.light_output.manual_brightness, 37)
        self.assertEqual(adapter.light_output.manual_ends_at, deadline)


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
                            state(str(85 - max(0, second - 20) * .06), unit="°C"),
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
                self.assertEqual(opened.effective_at, T0 + timedelta(seconds=37))
                self.assertGreaterEqual(opened.detected_at, T0 + timedelta(seconds=47))

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
            clock = [T0]
            runtime._clock = lambda: clock[0]
            phase = SimpleNamespace(session_id="ended", started_at=T0, ends_at=T0)
            runtime.controller.light_after_run = phase
            adapter.light_call = AsyncMock()
            self.assertFalse(await adapter._finish_expired_session_light(T0))
            self.assertNotIn("session_light", adapter.faults)
            clock[0] = T0 + timedelta(seconds=1)
            self.assertFalse(
                await adapter._finish_expired_session_light(clock[0])
            )
            self.assertEqual(adapter.light_call.await_count, 1)
            clock[0] = T0 + timedelta(seconds=2)
            self.assertFalse(
                await adapter._finish_expired_session_light(clock[0])
            )
            self.assertEqual(adapter.light_call.await_count, 2)
            self.assertEqual(adapter.faults["session_light"], "feedback_missing")
            light[0] = state("off")
            clock[0] = T0 + timedelta(seconds=3)
            self.assertTrue(
                await adapter._finish_expired_session_light(clock[0])
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
