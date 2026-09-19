"""Echter HA-Pfad: Entity -> Listener -> Detektor/Kern -> Service -> Aktorfeedback."""
from datetime import UTC, datetime, timedelta
import unittest
from homeassistant.components.switch import SwitchEntity
from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from harness import create_sauna, start_hass


class TestHeater(SwitchEntity):
    _attr_name = "Test heater"
    _attr_unique_id = "isolated-heater"
    _attr_should_poll = False
    _attr_is_on = False
    entity_id = "switch.test_heater"

    def __init__(self):
        self.calls = []
        self.respond = True
        self.powered = True

    async def async_turn_on(self, **kwargs):
        self.calls.append(True)
        self._attr_is_on = True
        self.async_write_ha_state()
        if self.respond:
            self.hass.states.async_set("binary_sensor.actual_heating", "on" if self.powered else "off")

    async def async_turn_off(self, **kwargs):
        self.calls.append(False)
        self._attr_is_on = False
        self.async_write_ha_state()
        if self.respond:
            self.hass.states.async_set("binary_sensor.actual_heating", "off")


class TestLight(LightEntity):
    _attr_name = "Test light"
    _attr_unique_id = "isolated-light"
    _attr_should_poll = False
    _attr_is_on = True
    _attr_brightness = 180
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_color_mode = ColorMode.BRIGHTNESS
    entity_id = "light.test_light"

    async def async_turn_on(self, **kwargs):
        self._attr_is_on = True
        self._attr_brightness = kwargs.get("brightness", self._attr_brightness)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self._attr_is_on = False
        self.async_write_ha_state()


class DevicePathTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass, self.temp = await start_hass()
        assert await async_setup_component(self.hass, "switch", {})
        assert await async_setup_component(self.hass, "light", {})
        self.heater, self.light = TestHeater(), TestLight()
        await self.hass.data["switch"].async_add_entities([self.heater])
        await self.hass.data["light"].async_add_entities([self.light])
        self.hass.states.async_set("binary_sensor.actual_heating", "off")
        self.entry = await create_sauna(self.hass, binding_overrides={
            "heater_feedback": "binary_sensor.actual_heating", "control_input": "binary_sensor.operator"},
            parameter_overrides={"target_temperature_c": 80, "safety_temperature_c": 105,
                "sensor_timeout_seconds": 30, "feedback_timeout_seconds": 2,
                "fault_confirmation_seconds": 5, "minimum_heating_minutes": 0,
                "thermostat_cooldown_minutes": 0, "heating_minutes": 4,
                "heating_reduction_minutes": 1, "forced_cooling_minutes": 1,
                "after_run_minutes": .5, "confirmation_minutes": 13,
                "cooling_brightness_percent": 5, "mechanical_timer_minutes": 240})
        self.runtime = self.entry.runtime_data
        self.base = datetime.now(UTC)
        self.now = self.base
        self.runtime._clock = lambda: self.now
        self.light.async_write_ha_state()
        await self.hass.async_block_till_done()
        entities = er.async_entries_for_config_entry(er.async_get(self.hass), self.entry.entry_id)
        self.operation = next(e.entity_id for e in entities if e.unique_id.endswith("_operation"))
        self.climate = next(e.entity_id for e in entities if e.unique_id.endswith("_thermostat"))

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self.assertFalse(self.heater.calls[-1])
        self.temp.cleanup()

    async def set_source(self, role, value):
        entity = self.entry.options["bindings"][role]
        old = self.hass.states.get(entity)
        self.hass.states.async_set(entity, str(value), old.attributes if old else {})
        await self.hass.async_block_till_done()

    async def time(self, seconds):
        self.now = self.base + timedelta(seconds=seconds)
        await self.runtime.tick()
        await self.hass.async_block_till_done()

    async def test_full_measured_gang_and_cooling_chain_through_ha(self):
        self.assertTrue(self.heater.calls)
        self.assertFalse(any(self.heater.calls))
        self.assertIsNone(self.runtime.session)
        await self.set_source("control_input", "on")
        session_id = self.runtime.session.session_id
        provisional = None
        confirmed = None
        for second in range(401):
            self.now = self.base + timedelta(seconds=second)
            if second < 70:
                temperature, humidity = 70, 40
            elif second < 85:
                temperature, humidity = 70 - .3 * (second - 70), 40 - .08 * (second - 70)
            elif second < 150:
                temperature, humidity = 65.5, 38.8
            elif second < 260:
                temperature = 65.5 + .025 * (second - 150)
                humidity = 38.8 + .02 * (second - 150) + (3 if second >= 240 else 0)
            elif second < 280:
                temperature, humidity = 68.25, 44
            else:
                temperature, humidity = 68.25 - .05 * (second - 280), 44 - .06 * (second - 280)
            for position in ("upper", "lower"):
                await self.set_source(f"{position}_temperature", temperature)
                await self.set_source(f"{position}_humidity", humidity)
            await self.runtime.tick()
            await self.hass.async_block_till_done()
            gang = self.runtime.session.timeline.active
            if gang and not gang.infusion_events and provisional is None:
                provisional = gang
            if gang and gang.infusion_events and confirmed is None:
                confirmed = gang
                self.assertEqual(gang.gang_id, provisional.gang_id)
                self.assertEqual(gang.started_at, provisional.started_at)
                self.assertTrue(self.heater.is_on)
        self.assertIsNotNone(provisional)
        self.assertIsNotNone(confirmed)
        self.assertEqual(self.runtime.session.timeline.gang_count, 1)
        self.assertEqual(self.runtime.session.session_id, session_id)
        self.assertFalse(self.heater.is_on)
        self.assertIsNotNone(self.runtime.session.cooling)
        self.assertLess(self.light.brightness, 20)
        end = self.runtime.session.cooling.ends_at
        self.assertEqual(self.runtime.session.cooling.credited_seconds, 30)
        self.now = end
        await self.runtime.tick()
        await self.hass.async_block_till_done()
        self.assertIsNone(self.runtime.session.cooling)
        self.assertEqual(self.light.brightness, 180)
        self.assertTrue(self.heater.is_on)
        self.assertEqual(self.runtime.session.heating.elapsed_seconds, 0)
        await self.runtime.archive.flush()
        import asyncio
        archived = await asyncio.to_thread(self.runtime.archive.read, session_id, limit=10000)
        originals = [r for r in archived["records"] if r["kind"] == "measurement"]
        self.assertGreaterEqual(len(originals), 4 * 401)
        self.assertTrue(any(r["kind"] == "command" and r["payload"]["heat"] for r in archived["records"]))
        self.assertTrue(any(r["kind"] == "detection" for r in archived["records"]))
        self.assertGreater(self.runtime.session.heating.intervals[0].ended_at.timestamp() - self.base.timestamp(), 240)

    async def test_normal_idle_keeps_operation_and_ui_physical_input_share_control(self):
        await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": self.climate, "hvac_mode": "heat"}, blocking=True)
        await self.hass.async_block_till_done()
        self.assertEqual(self.hass.states.get(self.operation).state, "on")
        identity = self.runtime.session.session_id
        await self.set_source("upper_temperature", 85)
        self.assertFalse(self.heater.is_on)
        self.assertTrue(self.runtime.session.operation_enabled)
        self.assertEqual(self.runtime.session.session_id, identity)
        self.assertEqual(self.hass.states.get(self.climate).attributes["hvac_action"], "idle")
        await self.set_source("control_input", "on")
        await self.set_source("control_input", "off")
        self.assertFalse(self.runtime.session.operation_enabled)
        await self.hass.services.async_call("switch", "turn_on", {"entity_id": self.operation}, blocking=True)
        self.assertEqual(self.runtime.session.session_id, identity)

    async def test_successful_command_without_feedback_does_not_count_heating(self):
        self.heater.respond = False
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        self.assertTrue(self.heater.is_on)
        await self.time(2)
        self.assertNotIn("heater_feedback_mismatch", self.runtime.controller.protection)
        await self.time(7)
        self.assertEqual(self.runtime.session.heating.elapsed_seconds, 0)
        self.assertIn("heater_feedback_mismatch", self.runtime.controller.protection)
        self.assertFalse(self.heater.is_on)
        await self.time(8)
        self.assertFalse(self.heater.is_on)
        self.assertTrue(self.runtime.session.operation_enabled)

    async def test_transient_sensor_failure_is_visible_without_latched_shutdown(self):
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        await self.set_source("upper_temperature", "unavailable")
        await self.time(1)
        self.assertIn("upper_temperature", self.runtime.device.faults)
        self.assertEqual(self.runtime.controller.protection, set())
        self.assertTrue(self.heater.is_on)
        self.assertEqual(tuple(p.value for p in self.runtime.detector.active_positions), ("lower",))
        await self.set_source("upper_temperature", 70)
        await self.time(2)
        self.assertNotIn("upper_temperature", self.runtime.device.faults)
        self.assertEqual(len(self.runtime.detector.active_positions), 2)
