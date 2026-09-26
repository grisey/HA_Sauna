"""Real HA service path for delayed person signals at a replacement door cycle."""

import unittest
from datetime import UTC, datetime, timedelta

from harness import create_sauna, start_hass
from homeassistant.components.switch import SwitchEntity
from homeassistant.setup import async_setup_component

from custom_components.ha_sauna.core.timeline import Event, Kind


class RecordedHeater(SwitchEntity):
    _attr_name = "Entry-context relay"
    _attr_unique_id = "entry-context-relay"
    _attr_should_poll = False
    _attr_is_on = False
    entity_id = "switch.entry_context_relay"

    def __init__(self):
        self.calls = []

    async def async_turn_on(self, **kwargs):
        self.calls.append(True)
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self.calls.append(False)
        self._attr_is_on = False
        self.async_write_ha_state()


class EntryContextIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass, self.temp = await start_hass()
        self.addCleanup(self.temp.cleanup)
        self.addAsyncCleanup(self.hass.async_stop, force=True)
        await async_setup_component(self.hass, "switch", {})
        self.heater = RecordedHeater()
        await self.hass.data["switch"].async_add_entities([self.heater])
        # This is deliberately distinct from the relay state.  It proves the
        # decision reaches the real service path without treating a contactor
        # echo as heating feedback.
        self.hass.states.async_set("binary_sensor.entry_context_actual_heat", "off")
        self.entry = await create_sauna(
            self.hass,
            binding_overrides={
                "heater": self.heater.entity_id,
                "heater_feedback": "binary_sensor.entry_context_actual_heat",
            },
            parameter_overrides={
                "target_temperature_c": 80,
                "sensor_timeout_seconds": 1800,
                "feedback_timeout_seconds": 2,
            },
        )
        self.runtime = self.entry.runtime_data
        self.base = datetime.now(UTC)
        self.now = self.base
        self.runtime._clock = lambda: self.now
        for role in ("upper_temperature", "lower_temperature"):
            source = self.entry.options["bindings"][role]
            state = self.hass.states.get(source)
            self.hass.states.async_set(source, "99", state.attributes)
        await self.hass.async_block_till_done()
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        self.heater.calls.clear()

    async def event(self, event_id, kind, detected, *, effective=None):
        self.now = self.base + timedelta(seconds=detected)
        return await self.runtime.receive(
            Event(
                event_id,
                self.runtime.session.session_id,
                kind,
                self.base
                + timedelta(seconds=detected if effective is None else effective),
                self.now,
            )
        )

    async def test_replaced_close_rejects_old_signal_before_service_but_current_one_heats(
        self,
    ):
        await self.event("close-old", Kind.DOOR_CLOSE, 0)
        await self.event("open-new", Kind.DOOR_OPEN, 100)
        self.now = self.base + timedelta(seconds=105)
        await self.runtime.set_heater_override(False)
        await self.event("close-new", Kind.DOOR_CLOSE, 110)
        self.now = self.base + timedelta(seconds=120)
        await self.runtime.set_heater_override(None)
        self.assertEqual(
            self.runtime.controller.last_decision.reason, "temperature_reached"
        )
        self.assertEqual(
            self.runtime.device.observe_heating(self.now)["source"],
            "independent_feedback",
        )

        result = await self.event("old-strong", Kind.PERSON_STRONG, 200, effective=1)
        await self.hass.async_block_till_done()

        self.assertFalse(result.changed)
        self.assertEqual(result.reason, "entry_context_changed")
        self.assertIsNone(self.runtime.session.timeline.active)
        self.assertFalse(self.runtime.controller.regulation_inputs.gang_heat_demand)
        self.assertEqual(
            self.runtime.controller.last_decision.reason, "temperature_reached"
        )
        self.assertNotIn(True, self.heater.calls)

        result = await self.event(
            "current-strong", Kind.PERSON_STRONG, 201, effective=111
        )
        await self.hass.async_block_till_done()

        self.assertTrue(result.changed)
        self.assertTrue(self.runtime.controller.regulation_inputs.gang_heat_demand)
        self.assertEqual(
            self.runtime.controller.last_decision.reason, "gang_heat_demand"
        )
        self.assertIn(True, self.heater.calls)


if __name__ == "__main__":
    unittest.main()
