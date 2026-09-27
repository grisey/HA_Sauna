"""Real HA number service must enforce the technical/admin boundary."""

import unittest

from homeassistant.auth.const import GROUP_ID_ADMIN, GROUP_ID_USER
from homeassistant.core import Context
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import entity_registry as er

from harness import create_sauna, start_hass


class NumberPermissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass, self.temp = await start_hass()
        self.entry = await create_sauna(
            self.hass, parameter_overrides={"sensor_timeout_seconds": 180}
        )

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self.temp.cleanup()

    async def test_technical_numbers_require_admin_but_live_temperature_remains_controllable(
        self,
    ):
        admin = await self.hass.auth.async_create_user(
            "Sauna admin", group_ids=[GROUP_ID_ADMIN]
        )
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
        with self.assertRaises(Unauthorized):
            await self.hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": numbers["sensor_timeout_seconds"], "value": 3600},
                blocking=True,
                context=Context(user_id=user.id),
            )
        self.assertEqual(
            self.entry.options["parameters"]["sensor_timeout_seconds"], 180
        )
        await self.hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": numbers["target_temperature_c"], "value": 82},
            blocking=True,
            context=Context(user_id=user.id),
        )
        self.assertEqual(self.entry.options["parameters"]["target_temperature_c"], 82)
        await self.hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": numbers["sensor_timeout_seconds"], "value": 3600},
            blocking=True,
            context=Context(user_id=admin.id),
        )
        self.assertEqual(
            self.entry.options["parameters"]["sensor_timeout_seconds"], 3600
        )
        # The service persists options before HA's update listener finishes
        # reloading. Verify the applied value before tearing down its files.
        await self.hass.async_block_till_done()
        self.assertEqual(
            self.entry.runtime_data.configuration.parameters.values["sensor_timeout_seconds"],
            3600,
        )
