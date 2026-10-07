"""Ausgewählte HA-Temperatur und ihre Frist führen die Gangstartfreigabe."""
import asyncio
import unittest
from datetime import timedelta

import test_device_feedback as fixture_module
from test_device_feedback import HA_AVAILABLE, T0, state

from custom_components.ha_sauna.core.timeline import Event, Kind


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant ist lokal nicht installiert")
class GangTemperatureDeviceTests(unittest.TestCase):
    def test_upper_priority_and_lower_fallback_keep_the_same_start_rule(self):
        fixture = fixture_module.DeviceFeedbackTests()
        runtime, adapter, clock = fixture.detection_device(
            both_positions=True, sensor_timeout_seconds=10,
        )
        adapter.ingest("upper_temperature", state("25", unit="°C"), T0)
        adapter.ingest("lower_temperature", state("80", unit="°C"), T0)
        adapter.refresh(T0)
        runtime.controller.set_operation(True, T0, session_id="s")
        self.assertEqual(runtime.controller.temperature, 25)
        self.assertFalse(runtime.controller.recognition_allowed(Kind.INFUSION))

        clock[0] = T0 + timedelta(seconds=5)
        adapter.ingest("lower_temperature", state("60", unit="°C"), clock[0])
        clock[0] = T0 + timedelta(seconds=11)
        adapter.refresh(clock[0])
        self.assertEqual(runtime.controller.temperature, 60)
        self.assertTrue(runtime.controller.recognition_allowed(Kind.INFUSION))
        self.assertEqual(runtime.controller._temperature_valid_until,
                         T0 + timedelta(seconds=15))

    def test_received_selection_expires_before_next_refresh_and_new_warm_input_recovers(self):
        async def exercise():
            fixture = fixture_module.DeviceFeedbackTests()
            runtime, adapter, clock = fixture.detection_device(
                both_positions=True, sensor_timeout_seconds=10,
            )
            runtime.controller.set_operation(True, T0, session_id="s")
            runtime._process_event(Event("close", "s", Kind.DOOR_CLOSE, T0, T0))
            clock[0] = T0 + timedelta(seconds=5)
            await runtime.device_input(fixture.detection_edge(runtime, "lower_temperature", 25))
            self.assertEqual(runtime.controller.temperature, 90)
            self.assertEqual(runtime.controller._temperature_valid_until,
                             T0 + timedelta(seconds=10))

            # The runtime delivers detector events before its next refresh.
            clock[0] = T0 + timedelta(seconds=11)
            runtime._process_event(Event("expired", "s", Kind.INFUSION, clock[0], clock[0]))
            self.assertIsNone(runtime.session.timeline.active)
            adapter.refresh(clock[0])
            self.assertEqual(runtime.controller.temperature, 25)
            self.assertFalse(runtime.controller.recognition_allowed(Kind.INFUSION))

            clock[0] = T0 + timedelta(seconds=12)
            await runtime.device_input(fixture.detection_edge(runtime, "lower_temperature", 60))
            runtime._process_event(Event("warm", "s", Kind.INFUSION, clock[0], clock[0]))
            self.assertEqual(runtime.session.timeline.active.started_at, clock[0])
            self.assertEqual(runtime.session.timeline.active.start_basis, "recognition_only")

        asyncio.run(exercise())
