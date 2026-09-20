"""Real HA API coverage for restoring the integration's software defaults."""
import unittest

from aiohttp import ClientSession

from custom_components.ha_sauna.core.parameters import Parameters
from harness import create_sauna, credentials, start_hass


class ResetSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass, self.temp = await start_hass()
        self.entry = await create_sauna(self.hass)
        self.url = (f"http://127.0.0.1:{self.hass.http.server_port}/api/ha_sauna/"
                    f"{self.entry.entry_id}/parameters/reset")
        self.headers = {"Authorization": "Bearer " + await credentials(self.hass)}

    async def asyncTearDown(self):
        await self.hass.async_block_till_done()
        await self.hass.async_stop(force=True)
        self.temp.cleanup()

    async def test_reset_restores_defaults_and_preserves_hardware_associations(self):
        original_bindings = dict(self.entry.options["bindings"])
        self.hass.config_entries.async_update_entry(self.entry, options={
            **self.entry.options,
            "control_input_mode": "button",
            "button_event_type": "custom_press",
            "program_mode": "progressive",
            "button_program": "program_2",
            "log_level": "DEBUG",
        })
        await self.hass.async_block_till_done()

        async with ClientSession(headers=self.headers) as client:
            async with client.post(self.url) as response:
                self.assertEqual(response.status, 200, await response.text())
                result = await response.json()

        defaults = Parameters({}).as_dict()
        self.assertTrue(result["success"])
        self.assertEqual(result["parameters"], defaults)
        await self.hass.async_block_till_done()
        self.assertEqual(self.entry.options["parameters"], defaults)
        self.assertEqual(self.entry.options["bindings"], original_bindings)
        self.assertEqual(self.entry.options["control_input_mode"], "button")
        self.assertEqual(self.entry.options["button_event_type"], "custom_press")
        self.assertEqual(self.entry.options["program_mode"], "constant")
        self.assertEqual(self.entry.options["button_program"], "current")
        self.assertEqual(self.entry.options["log_level"], "INFO")

    async def test_active_or_stopped_session_rejects_reset_without_changes(self):
        async with ClientSession(headers=self.headers) as client:
            # Establish central defaults, then make only a normally live value
            # differ. Reset must still reject every extant session.
            async with client.post(self.url) as response:
                self.assertEqual(response.status, 200, await response.text())
            await self.hass.async_block_till_done()
            temperature_url = self.url.removesuffix("/parameters/reset") + "/temperature"
            async with client.post(temperature_url, json={"target_temperature_c": 81}) as response:
                self.assertEqual(response.status, 200, await response.text())

            runtime = self.entry.runtime_data
            await runtime.set_operation(True)
            before = dict(self.entry.options)
            before_parameters = runtime.configuration.parameters.as_dict()
            async with client.post(self.url) as response:
                self.assertEqual(response.status, 409, await response.text())
            self.assertEqual(self.entry.options, before)
            self.assertEqual(runtime.configuration.parameters.as_dict(), before_parameters)
            self.assertTrue(runtime.session.operation_enabled)

            await runtime.set_operation(False)
            async with client.post(self.url) as response:
                self.assertEqual(response.status, 409, await response.text())
        self.assertEqual(self.entry.options, before)
        self.assertEqual(runtime.configuration.parameters.as_dict(), before_parameters)
        self.assertIsNotNone(runtime.session)
        self.assertFalse(runtime.session.operation_enabled)

    async def test_ordinary_user_cannot_reset_settings(self):
        user = await self.hass.auth.async_create_user("Ordinary user", group_ids=[])
        token = await self.hass.auth.async_create_refresh_token(user, client_id="http://localhost/")
        headers = {"Authorization": "Bearer " + self.hass.auth.async_create_access_token(token)}
        before = dict(self.entry.options)

        async with ClientSession(headers=headers) as client:
            async with client.post(self.url) as response:
                self.assertEqual(response.status, 403, await response.text())

        self.assertEqual(self.entry.options, before)
