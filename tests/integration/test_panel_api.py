"""Real authenticated panel endpoints, session guard and HA options reload."""
import unittest
from aiohttp import ClientSession
from homeassistant.auth.const import GROUP_ID_ADMIN
from harness import create_sauna, start_hass


class PanelAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass, self.temp = await start_hass()
        self.entry = await create_sauna(self.hass, parameter_overrides={"sensor_timeout_seconds": 60})
        self.base = f"http://127.0.0.1:{self.hass.http.server_port}/api/ha_sauna"
        user = await self.hass.auth.async_create_user("Panel test", group_ids=[GROUP_ID_ADMIN])
        token = await self.hass.auth.async_create_refresh_token(user, client_id="http://localhost/")
        self.headers = {"Authorization": "Bearer " + self.hass.auth.async_create_access_token(token)}

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self.temp.cleanup()

    async def test_panel_control_parameters_and_server_side_session_lock(self):
        async with ClientSession(headers=self.headers) as client:
            async with client.get(self.base) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual((await response.json())[0]["entry_id"], self.entry.entry_id)
            url = self.base + "/" + self.entry.entry_id
            values = {**self.entry.options["parameters"], "target_temperature_c": 82}
            async with client.post(url + "/parameters", json=values) as response:
                self.assertEqual(response.status, 200, await response.text())
            await self.hass.async_block_till_done()
            self.assertEqual(self.entry.runtime_data.configuration.parameters.values["target_temperature_c"], 82)
            async with client.post(url + "/control", json={"enabled": True}) as response:
                self.assertEqual(response.status, 200, await response.text())
            identity = self.entry.runtime_data.session.session_id
            async with client.get(url + "/state") as response:
                self.assertEqual(response.status, 200, await response.text())
                state = await response.json()
                self.assertEqual(state["session"]["timeline"]["session_id"], identity)
                self.assertTrue(state["configuration_locked"])
            async with client.post(url + "/parameters", json=values) as response:
                self.assertEqual(response.status, 409)
            async with client.post(url + "/control", json={"enabled": False}) as response:
                self.assertEqual(response.status, 200)
            async with client.post(url + "/parameters", json=values) as response:
                self.assertEqual(response.status, 409)

    async def test_unauthenticated_and_non_admin_writes_are_rejected(self):
        url = self.base + "/" + self.entry.entry_id
        async with ClientSession() as client:
            async with client.get(url + "/state") as response:
                self.assertEqual(response.status, 401)
        user = await self.hass.auth.async_create_user("Read only", group_ids=[])
        token = await self.hass.auth.async_create_refresh_token(user, client_id="http://localhost/")
        headers = {"Authorization": "Bearer " + self.hass.auth.async_create_access_token(token)}
        async with ClientSession(headers=headers) as client:
            async with client.post(url + "/control", json={"enabled": True}) as response:
                self.assertEqual(response.status, 403)
        self.assertIsNone(self.entry.runtime_data.session)

    async def test_logging_is_live_persistent_and_does_not_reload_or_end_session(self):
        import logging
        runtime = self.entry.runtime_data
        await runtime.set_operation(True)
        identity = runtime.session.session_id
        url = self.base + "/" + self.entry.entry_id
        async with ClientSession(headers=self.headers) as client:
            for level in ("DEBUG", "ERROR", "INFO"):
                async with client.post(url + "/logging", json={"level": level}) as response:
                    self.assertEqual(response.status, 200, await response.text())
                await self.hass.async_block_till_done()
                self.assertIs(self.entry.runtime_data, runtime)
                self.assertEqual(runtime.session.session_id, identity)
                self.assertTrue(runtime.session.operation_enabled)
                self.assertEqual(self.entry.options["log_level"], level)
                self.assertEqual(runtime.log.logger.level, getattr(logging, level))
            async with client.post(url + "/logging", json={"level": "VERBOSE"}) as response:
                self.assertEqual(response.status, 400)
            async with client.post(url + "/logging", json={"level": []}) as response:
                self.assertEqual(response.status, 400)

    async def test_missing_configuration_blocks_start_without_locking_settings(self):
        url = self.base + "/" + self.entry.entry_id
        values = dict(self.entry.options["parameters"])
        for key in ("sensor_timeout_seconds", "feedback_timeout_seconds", "fault_confirmation_seconds"):
            values.pop(key)
        self.hass.config_entries.async_update_entry(self.entry, options={**self.entry.options,"parameters":values})
        await self.hass.async_block_till_done()
        async with ClientSession(headers=self.headers) as client:
            async with client.post(url + "/control", json={"enabled": True}) as response:
                self.assertEqual(response.status, 409)
                message = (await response.json())["error"]
                self.assertIn("Höchstalter eines Messwerts", message)
                self.assertNotIn("sensor_timeout_seconds", message)
            async with client.get(url + "/state") as response:
                state = await response.json()
                self.assertIsNone(state["session"])
                self.assertFalse(state["configuration_locked"])
                self.assertTrue(all(v["state"]=="validity_unconfigured" for v in state["measurement_status"].values()))
                self.assertEqual([i["key"] for i in state["issues"]], ["configuration"])
            async with client.post(url + "/parameters", json={**values,"sensor_timeout_seconds":60,"feedback_timeout_seconds":2,"fault_confirmation_seconds":5}) as response:
                self.assertEqual(response.status, 200)
