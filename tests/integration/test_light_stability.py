"""Lichtkurve und ganzzahlige Ausgabe über die vorhandene echte HA-Fixture."""

import unittest
from datetime import timedelta

import test_device_path as device_fixture
from homeassistant.core import callback

from custom_components.ha_sauna.settings import async_set_parameters


class LightStabilityTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = device_fixture.DevicePathTests.asyncSetUp
    set_source = device_fixture.DevicePathTests.set_source
    set_light_externally = device_fixture.DevicePathTests.set_light_externally
    time = device_fixture.DevicePathTests.time

    async def prepare_light(self, temperature):
        # These temperatures straddle the 35.5-% rounding boundary of this
        # explicit 5-to-40-% curve; factory brightness edits must not move it.
        await async_set_parameters(self.hass, self.entry, {
            "target_temperature_c": 80,
            "light_reference_temperature_c": 30,
            "cooling_brightness_percent": 5,
            "operation_brightness_percent": 40,
            "light_transition_seconds": 30,
            "light_output_hysteresis_percent": .1,
        }, partial=True, explicit_target=True, program_mode="constant")
        await self.hass.async_block_till_done()
        self.runtime = self.entry.runtime_data
        self.base = self.now = self.runtime._clock()
        self.runtime._clock = lambda: self.now
        self.commands = []

        @callback
        def observe(event):
            if event.data.get("domain") == "light":
                self.commands.append({
                    "service": event.data["service"],
                    **event.data["service_data"],
                })

        self.addCleanup(self.hass.bus.async_listen("call_service", observe))
        self.light.percent_steps = True
        self.hass.states.async_set("sun.sun", "above_horizon", {"elevation": 10})
        await self.set_source("upper_temperature", temperature)
        await self.runtime.set_operation(True)
        await self.time(30)
        self.commands.clear()

    async def change_temperature(self, temperature):
        self.now += timedelta(seconds=1)
        await self.set_source("upper_temperature", temperature)
        await self.runtime.tick()
        await self.hass.async_block_till_done()

    def assert_integer_commands(self):
        self.assertTrue(all(
            isinstance(command["brightness_pct"], int)
            for command in self.commands if command["service"] == "turn_on"
        ))
        self.assertFalse(any("transition" in command for command in self.commands))

    async def test_rounding_boundary_noise_does_not_send_alternating_services(self):
        await self.prepare_light(73.56)
        for temperature in (73.58, 73.56) * 5:
            await self.change_temperature(temperature)
        self.assertEqual(self.commands, [])
        await self.change_temperature(73.8)
        self.assertEqual([row["brightness_pct"] for row in self.commands], [36])
        self.assertIsNone(self.runtime.device.light_output.manual_brightness)
        self.assert_integer_commands()

    async def test_ready_temperature_boundary_has_one_coherent_target(self):
        await self.prepare_light(80)
        for temperature in (76.9, 77.1) * 5:
            await self.change_temperature(temperature)
            self.assertEqual(self.runtime.controller.phase, "bereit")
        self.assertEqual([row["brightness_pct"] for row in self.commands], [38])
        self.assertIsNone(self.runtime.device.light_output.manual_brightness)
        self.assert_integer_commands()

    async def test_return_uses_observed_light_and_original_transition_duration(self):
        await self.prepare_light(70)
        await self.runtime.set_light_override(80)
        self.commands.clear()
        await self.runtime.set_light_override(None)
        self.assertEqual(self.commands, [])
        self.assertEqual(self.light.brightness, 204)
        await self.set_source("upper_temperature", 70)
        await self.time(45)
        self.assertEqual(self.commands[-1]["brightness_pct"], 56)
        await self.set_source("upper_temperature", 70)
        await self.time(60)
        self.assertEqual(self.commands[-1]["brightness_pct"], 33)
        self.assertIsNone(self.runtime.device.light_output.manual_brightness)
        self.assert_integer_commands()

    async def test_manual_boundary_change_and_off_are_immediate(self):
        await self.prepare_light(73.56)
        await self.runtime.set_light_override(35.55)
        self.assertEqual(self.commands[-1]["brightness_pct"], 36)
        await self.runtime.set_light_override(35.45)
        self.assertEqual(self.commands[-1]["brightness_pct"], 35)
        await self.runtime.set_light_override(0)
        self.assertEqual(self.commands[-1]["service"], "turn_off")
        self.assertFalse(self.light.is_on)
        self.assert_integer_commands()

    async def test_invalid_feedback_discards_previous_quantization_step(self):
        await self.prepare_light(73.56)
        await self.change_temperature(73.58)
        self.assertEqual(self.commands, [])
        self.hass.states.async_set(self.light.entity_id, "unavailable")
        await self.hass.async_block_till_done()
        await self.runtime.tick()
        await self.hass.async_block_till_done()
        self.assertEqual(self.commands[-1]["brightness_pct"], 36)
        self.assertIsNone(self.runtime.device.light_output.manual_brightness)
        self.assert_integer_commands()
