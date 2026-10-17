"""One selected temperature raster for same-time recognition packets."""

import asyncio
import unittest
from datetime import timedelta

import test_device_feedback as fixtures


@unittest.skipUnless(fixtures.HA_AVAILABLE, "Home Assistant ist lokal nicht installiert")
class TemperaturePacketTests(unittest.TestCase):
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
