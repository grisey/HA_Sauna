"""Real HA service regressions for the F1/F2 temporary-door corrections."""

import unittest
from datetime import UTC, datetime, timedelta

from harness import create_sauna, start_hass
from homeassistant.components.switch import SwitchEntity
from homeassistant.setup import async_setup_component

from custom_components.ha_sauna.core.timeline import Event, Kind


class RecordedHeater(SwitchEntity):
    _attr_name = "Door heat relay"
    _attr_unique_id = "door-heat-relay"
    _attr_should_poll = False
    _attr_is_on = False
    entity_id = "switch.door_heat_relay"

    def __init__(self):
        self.calls = []
        self.accept = True

    async def async_turn_on(self, **kwargs):
        self.calls.append(True)
        if self.accept:
            self._attr_is_on = True
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self.calls.append(False)
        if self.accept:
            self._attr_is_on = False
            self.async_write_ha_state()


class DoorHeatRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass, self.temp = await start_hass()
        self.addCleanup(self.temp.cleanup)
        self.addAsyncCleanup(self.hass.async_stop, force=True)
        await async_setup_component(self.hass, "switch", {})
        self.heater = RecordedHeater()
        await self.hass.data["switch"].async_add_entities([self.heater])
        self.hass.states.async_set("binary_sensor.door_heat_actual", "off")
        self.entry = await create_sauna(
            self.hass,
            binding_overrides={
                "heater": self.heater.entity_id,
                "heater_feedback": "binary_sensor.door_heat_actual",
            },
            parameter_overrides={
                "target_temperature_c": 80,
                "minimum_heating_minutes": 10,
                "manual_override_minutes": 1,
                "sensor_timeout_seconds": 1200,
                "feedback_timeout_seconds": 2,
            },
        )
        self.runtime = self.entry.runtime_data
        self.now = datetime.now(UTC)
        self.runtime._clock = lambda: self.now
        for role in ("upper_temperature", "lower_temperature"):
            entity = self.entry.options["bindings"][role]
            old = self.hass.states.get(entity)
            self.hass.states.async_set(entity, "100", old.attributes)
        await self.hass.async_block_till_done()
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        self.heater.calls.clear()

    async def event(self, name, kind, seconds=1):
        self.now += timedelta(seconds=seconds)
        await self.runtime.receive(
            Event(name, self.runtime.session.session_id, kind, self.now, self.now)
        )
        await self.hass.async_block_till_done()

    async def feedback(self, value):
        self.hass.states.async_set("binary_sensor.door_heat_actual", value)
        await self.hass.async_block_till_done()

    async def test_contactor_on_without_actual_heat_requests_real_on(self):
        self.heater._attr_is_on = True
        self.heater.async_write_ha_state()
        await self.feedback("off")
        self.heater.calls.clear()
        await self.event("open", Kind.DOOR_OPEN)
        await self.event("close", Kind.DOOR_CLOSE)
        self.assertEqual(
            self.runtime.controller.last_decision.reason, "temporary_door_heat"
        )
        self.assertIn(True, self.heater.calls)

    async def test_confirmed_heat_is_not_extended_by_door_or_repeated_feedback(self):
        self.heater._attr_is_on = True
        self.heater.async_write_ha_state()
        await self.feedback("on")
        self.heater.calls.clear()
        await self.event("open", Kind.DOOR_OPEN)
        await self.event("close", Kind.DOOR_CLOSE)
        await self.feedback("on")
        self.assertFalse(self.runtime.controller.regulation_inputs.temporary_door_heat)
        self.assertNotIn(True, self.heater.calls)

    async def test_manual_off_discards_pending_despite_late_heat_feedback(self):
        self.heater.accept = False
        self.heater._attr_is_on = True
        self.heater.async_write_ha_state()
        await self.hass.async_block_till_done()
        await self.event("open", Kind.DOOR_OPEN)
        await self.event("close", Kind.DOOR_CLOSE)
        self.assertEqual(
            self.runtime.controller.last_decision.reason, "temporary_door_heat"
        )
        await self.runtime.set_heater_override(False)
        await self.feedback("on")  # late physical feedback cannot revive it
        self.heater.calls.clear()
        self.now += timedelta(seconds=61)
        await self.runtime.tick()
        await self.hass.async_block_till_done()
        self.assertNotIn(True, self.heater.calls)

        self.assertFalse(self.runtime.controller.regulation_inputs.temporary_door_heat)

    async def test_actual_feedback_starts_full_minimum_and_second_cycle_adds_no_time(
        self,
    ):
        origin = self.now
        await self.event("open", Kind.DOOR_OPEN)
        await self.event("close", Kind.DOOR_CLOSE)
        self.assertTrue(self.heater.calls[-1])
        self.assertTrue(self.runtime.controller.contactor)
        self.assertFalse(self.runtime.controller.feedback)
        self.now = origin + timedelta(seconds=10)
        await self.feedback("on")
        self.assertFalse(self.runtime.controller.regulation_inputs.temporary_door_heat)
        await self.event("second-open", Kind.DOOR_OPEN, seconds=290)
        await self.event("second-close", Kind.DOOR_CLOSE)
        self.now = origin + timedelta(seconds=610, microseconds=-1)
        await self.runtime.tick()
        await self.hass.async_block_till_done()
        self.assertEqual(
            self.runtime.controller.last_decision.reason, "minimum_heating"
        )
        self.assertTrue(self.heater.is_on)
        self.now = origin + timedelta(seconds=610)
        await self.runtime.tick()
        await self.hass.async_block_till_done()
        self.assertEqual(
            self.runtime.controller.last_decision.reason, "temperature_reached"
        )
        self.assertFalse(self.heater.calls[-1])
        self.assertFalse(self.heater.is_on)

    async def test_open_during_manual_off_is_inert_and_fresh_cycle_afterwards_works(
        self,
    ):
        await self.event("open-before-off", Kind.DOOR_OPEN)
        await self.runtime.set_heater_override(False)
        self.now += timedelta(seconds=61)
        await self.runtime.tick()
        await self.event("late-close", Kind.DOOR_CLOSE)
        self.assertFalse(self.runtime.controller.regulation_inputs.temporary_door_heat)

        await self.event("fresh-open", Kind.DOOR_OPEN)
        await self.event("fresh-close", Kind.DOOR_CLOSE)
        self.assertEqual(
            self.runtime.controller.last_decision.reason, "temporary_door_heat"
        )


if __name__ == "__main__":
    unittest.main()
