"""The registered number services expose the normal live temperature controls."""

import unittest

from homeassistant.auth.const import GROUP_ID_USER
from homeassistant.core import Context
from homeassistant.helpers import entity_registry as er

from custom_components.ha_sauna.core.parameters import BY_KEY
from harness import create_sauna, start_hass


class NumberPermissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass, self.temp = await start_hass()
        self.entry = await create_sauna(self.hass)

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self.temp.cleanup()

    async def test_only_live_numbers_are_registered_and_normal_user_can_set_target(self):
        user = await self.hass.auth.async_create_user(
            "Normal sauna user", group_ids=[GROUP_ID_USER]
        )
        entities = er.async_entries_for_config_entry(
            er.async_get(self.hass), self.entry.entry_id
        )
        numbers = {
            entity.unique_id.removeprefix(f"{self.entry.entry_id}_"): entity.entity_id
            for entity in entities
            if entity.entity_id.startswith("number.")
        }
        self.assertEqual(set(numbers), {
            "target_temperature_c", "final_temperature_c", "temperature_gangs",
        })
        definition = BY_KEY["target_temperature_c"]
        selected = definition.default + definition.number_step
        await self.hass.services.async_call(
            "number", "set_value",
            {"entity_id": numbers[definition.key], "value": selected},
            blocking=True, context=Context(user_id=user.id),
        )
        await self.hass.async_block_till_done()
        self.assertEqual(self.entry.options["parameters"][definition.key], selected)
        self.assertEqual(
            self.entry.runtime_data.configuration.parameters.values[definition.key], selected
        )
