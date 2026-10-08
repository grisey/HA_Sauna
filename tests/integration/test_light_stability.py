"""Lichtkurve und ganzzahlige Ausgabe über die vorhandene echte HA-Fixture."""

import unittest
from datetime import timedelta

import test_device_path as device_fixture
from homeassistant.core import callback


class LightStabilityTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = device_fixture.DevicePathTests.asyncSetUp
    set_source = device_fixture.DevicePathTests.set_source
    set_light_externally = device_fixture.DevicePathTests.set_light_externally
    time = device_fixture.DevicePathTests.time

    def temperature_for_brightness(self, brightness):
        values = self.runtime.configuration.parameters.values
        cold = values["light_reference_temperature_c"]
        low = values["cooling_brightness_percent"]
        normal = values["operation_brightness_percent"]
        self.assertLess(low, brightness)
        self.assertLess(brightness, normal)
        return cold + (self.runtime.controller.target_temperature - cold) * (
            brightness - low
        ) / (normal - low)

    def curve_brightness(self, temperature):
        values = self.runtime.configuration.parameters.values
        cold = values["light_reference_temperature_c"]
        low = values["cooling_brightness_percent"]
        normal = values["operation_brightness_percent"]
        return low + (normal - low) * (temperature - cold) / (
            self.runtime.controller.target_temperature - cold
        )

    def rounding_boundary(self):
        hysteresis = self.runtime.configuration.parameters.values[
            "light_output_hysteresis_percent"
        ]
        self.assertGreater(hysteresis, 0)
        # The test boundary is fixed; its sensor inputs follow the current curve.
        boundary = 35.5
        margin = min(.01, hysteresis / 2)
        return tuple(self.temperature_for_brightness(value) for value in (
            boundary - margin, boundary + margin, boundary + hysteresis + margin,
        ))

    async def prepare_light(self, temperature):
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
        self.transition = self.runtime.configuration.parameters.values[
            "light_transition_seconds"
        ]
        self.assertGreater(self.transition, 0)
        await self.time(self.transition)
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
        below, above, outside = self.rounding_boundary()
        await self.prepare_light(below)
        for temperature in (above, below) * 5:
            await self.change_temperature(temperature)
        self.assertEqual(self.commands, [])
        await self.change_temperature(outside)
        self.assertEqual([row["brightness_pct"] for row in self.commands], [36])
        self.assertIsNone(self.runtime.device.light_output.manual_brightness)
        self.assert_integer_commands()

    async def test_ready_temperature_boundary_has_one_coherent_target(self):
        target = self.runtime.controller.target_temperature
        values = self.runtime.configuration.parameters.values
        boundary = target - values["readiness_hysteresis_c"]
        temperatures = (boundary - .1, boundary + .1)
        expected = round(self.curve_brightness(temperatures[0]))
        self.assertEqual(round(self.curve_brightness(temperatures[1])), expected)
        await self.prepare_light(target)
        for temperature in temperatures * 5:
            await self.change_temperature(temperature)
            self.assertEqual(self.runtime.controller.phase, "bereit")
        self.assertEqual([row["brightness_pct"] for row in self.commands], [expected])
        self.assertIsNone(self.runtime.device.light_output.manual_brightness)
        self.assert_integer_commands()

    async def test_return_uses_observed_light_and_original_transition_duration(self):
        values = self.runtime.configuration.parameters.values
        automatic = (
            values["cooling_brightness_percent"] + values["operation_brightness_percent"]
        ) / 2
        temperature = self.temperature_for_brightness(automatic)
        await self.prepare_light(temperature)
        manual = 80
        await self.runtime.set_light_override(manual)
        self.commands.clear()
        await self.runtime.set_light_override(None)
        self.assertEqual(self.commands, [])
        self.assertEqual(self.light.brightness, round(255 * manual / 100))
        await self.set_source("upper_temperature", temperature)
        await self.time(self.transition * 1.5)
        self.assertEqual(self.commands[-1]["brightness_pct"], round((manual + automatic) / 2))
        await self.set_source("upper_temperature", temperature)
        await self.time(self.transition * 2)
        self.assertEqual(self.commands[-1]["brightness_pct"], round(automatic))
        self.assertIsNone(self.runtime.device.light_output.manual_brightness)
        self.assert_integer_commands()

    async def test_manual_boundary_change_and_off_are_immediate(self):
        below, _, _ = self.rounding_boundary()
        await self.prepare_light(below)
        await self.runtime.set_light_override(35.55)
        self.assertEqual(self.commands[-1]["brightness_pct"], 36)
        await self.runtime.set_light_override(35.45)
        self.assertEqual(self.commands[-1]["brightness_pct"], 35)
        await self.runtime.set_light_override(0)
        self.assertEqual(self.commands[-1]["service"], "turn_off")
        self.assertFalse(self.light.is_on)
        self.assert_integer_commands()

    async def test_invalid_feedback_discards_previous_quantization_step(self):
        below, above, _ = self.rounding_boundary()
        await self.prepare_light(below)
        await self.change_temperature(above)
        self.assertEqual(self.commands, [])
        self.hass.states.async_set(self.light.entity_id, "unavailable")
        await self.hass.async_block_till_done()
        await self.runtime.tick()
        await self.hass.async_block_till_done()
        self.assertEqual(self.commands[-1]["brightness_pct"], 36)
        self.assertIsNone(self.runtime.device.light_output.manual_brightness)
        self.assert_integer_commands()
