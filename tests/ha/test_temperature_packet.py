"""One selected temperature raster for same-time recognition packets."""

import asyncio
import unittest
from datetime import timedelta
from itertools import permutations

import test_device_feedback as fixtures


@unittest.skipUnless(fixtures.HA_AVAILABLE, "Home Assistant ist lokal nicht installiert")
class TemperaturePacketTests(unittest.TestCase):
    async def packet(self, order, temperature):
        fixture = fixtures.DeviceFeedbackTests()
        runtime, adapter, clock = fixture.detection_device(
            median_seconds=1, infusion_window_seconds=1, infusion_hold_seconds=1,
        )
        adapter.ingest("upper_temperature", fixtures.state("60.1", unit="°C"), fixtures.T0)
        adapter.refresh(fixtures.T0)
        runtime.controller.begin_session("packet", fixtures.T0)
        await runtime.tick()
        clock[0] = fixtures.T0 + timedelta(seconds=20)
        await runtime.device_input(fixture.detection_edge(runtime, "upper_humidity", 22))
        clock[0] = fixtures.T0 + timedelta(seconds=21)
        inputs = {
            "humidity": ("upper_humidity", 24, "22"),
            "temperature": ("upper_temperature", temperature, "60.1"),
            "off": ("control_input", "off", "on"),
        }
        tasks = []
        await runtime._lock.acquire()
        try:
            for key in order:
                role, value, old = inputs[key]
                tasks.append(asyncio.create_task(runtime.device_input(
                    fixture.detection_edge(runtime, role, value, old)
                )))
                await asyncio.sleep(0)
        finally:
            runtime._lock.release()
        await asyncio.gather(*tasks)
        self.assertIsNone(runtime.controller._recognition_temperature_raster)
        self.assertFalse(runtime.session.operation_enabled)
        self.assertEqual(runtime.controller.temperature, temperature)
        return runtime

    def test_same_time_cold_temperature_blocks_every_control_edge_order(self):
        for order in permutations(("humidity", "temperature", "off")):
            with self.subTest(order=order):
                runtime = asyncio.run(self.packet(order, 59.9))
                self.assertEqual(runtime.session.timeline.gang_count, 0)
                self.assertIsNone(runtime.session.timeline.active)
                self.assertIsNone(runtime.session.timeline.anchor)

    def test_same_time_warm_temperature_allows_one_round_in_every_order(self):
        for temperature in (60, 60.1):
            for order in permutations(("humidity", "temperature", "off")):
                with self.subTest(order=order, temperature=temperature):
                    runtime = asyncio.run(self.packet(order, temperature))
                    self.assertEqual(runtime.session.timeline.gang_count, 1)

    def test_packet_context_is_released_after_a_processing_error(self):
        async def exercise():
            fixture = fixtures.DeviceFeedbackTests()
            runtime, _adapter, clock = fixture.detection_device()
            clock[0] = fixtures.T0 + timedelta(seconds=1)
            previous = runtime.controller.set_temperature

            def failed(*_args, **_kwargs):
                self.assertIsNotNone(runtime.controller._recognition_temperature_raster)
                raise ValueError("synthetic packet failure")

            runtime.controller.set_temperature = failed
            try:
                with self.assertRaisesRegex(ValueError, "synthetic packet failure"):
                    await runtime.device_input(fixture.detection_edge(
                        runtime, "upper_temperature", 25,
                    ))
            finally:
                runtime.controller.set_temperature = previous
            self.assertIsNone(runtime.controller._recognition_temperature_raster)
            clock[0] += timedelta(seconds=1)
            await runtime.device_input(fixture.detection_edge(runtime, "upper_temperature", 80))
            self.assertIsNone(runtime.controller._gang_temperature_blocked())

        asyncio.run(exercise())
