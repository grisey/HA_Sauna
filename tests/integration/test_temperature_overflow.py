"""Real HA validation of overflowing program values from JSON requests."""

import json
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from aiohttp import ClientSession
from harness import create_sauna, start_hass
from homeassistant.auth.const import GROUP_ID_ADMIN
from homeassistant.exceptions import ConfigEntryError

from custom_components.ha_sauna import async_setup_entry
from custom_components.ha_sauna.core.timeline import Event, Kind


def _json_number(value):
    return json.loads(json.dumps(value))


class TemperatureOverflowIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass, self.temp = await start_hass()
        self.entry = await create_sauna(self.hass)
        self.base = (
            f"http://127.0.0.1:{self.hass.http.server_port}/api/ha_sauna/"
            f"{self.entry.entry_id}"
        )
        user = await self.hass.auth.async_create_user(
            "Temperature overflow", group_ids=[GROUP_ID_ADMIN]
        )
        token = await self.hass.auth.async_create_refresh_token(
            user, client_id="http://localhost/"
        )
        self.headers = {
            "Authorization": "Bearer " + self.hass.auth.async_create_access_token(token)
        }

    async def asyncTearDown(self):
        await self.hass.async_block_till_done()
        await self.hass.async_stop(force=True)
        self.temp.cleanup()

    async def test_overflow_requests_are_400_and_leave_the_runtime_unchanged(self):
        huge = _json_number(10**400)
        before_options = dict(self.entry.options)
        runtime = self.entry.runtime_data
        catalog = list(before_options["temperature_programs"])
        invalid_catalogs = []
        for field, value in (("start_c", huge), ("end_c", -huge)):
            changed = list(catalog)
            changed[0] = {**changed[0], field: value}
            invalid_catalogs.append(changed)

        async with ClientSession(headers=self.headers) as client:
            async with client.post(
                self.base + "/program", json={"temperature_steps": [huge]}
            ) as response:
                self.assertEqual(response.status, 400, await response.text())
                self.assertTrue((await response.json())["error"])
            for programs in invalid_catalogs:
                async with client.post(
                    self.base + "/programs", json={"programs": programs}
                ) as response:
                    self.assertEqual(response.status, 400, await response.text())

        self.assertEqual(dict(self.entry.options), before_options)
        self.assertIs(self.entry.runtime_data, runtime)
        self.assertEqual(runtime.configuration.as_options(), before_options)

    async def test_external_overflow_is_restored_then_a_valid_reload_starts(self):
        expected_options = dict(self.entry.options)
        runtime = self.entry.runtime_data
        huge = _json_number(10**400)
        catalog = list(expected_options["temperature_programs"])
        catalog[0] = {**catalog[0], "start_c": huge}
        for changed in (
            {"temperature_steps": [huge]},
            {"temperature_programs": catalog},
        ):
            with self.subTest(changed=tuple(changed)):
                self.hass.config_entries.async_update_entry(
                    self.entry, options={**expected_options, **changed}
                )
                await self.hass.async_block_till_done()

                self.assertEqual(dict(self.entry.options), expected_options)
                self.assertIs(self.entry.runtime_data, runtime)
                self.assertFalse(runtime.reconfiguring)

        self.hass.config_entries.async_update_entry(
            self.entry,
            options={
                **expected_options,
                "parameters": {
                    **expected_options["parameters"],
                    "target_temperature_c": 81,
                },
            },
        )
        await self.hass.async_block_till_done()
        await self.hass.config_entries.async_reload(self.entry.entry_id)
        await self.hass.async_block_till_done()
        await self.entry.runtime_data.set_operation(True)
        self.assertTrue(self.entry.runtime_data.session.operation_enabled)

    async def test_setup_without_a_valid_runtime_raises_config_entry_error(self):
        for value in (10**400, -(10**400)):
            for field in ("temperature_steps", "start_c", "end_c"):
                options = json.loads(json.dumps(dict(self.entry.options)))
                if field == "temperature_steps":
                    options[field] = [value]
                else:
                    options["temperature_programs"][0][field] = value
                with (
                    self.subTest(field=field, positive=value > 0),
                    self.assertRaises(ConfigEntryError),
                ):
                    await async_setup_entry(self.hass, SimpleNamespace(options=options))

    async def test_rejected_updates_preserve_active_gang_and_cooling_accounts(self):
        runtime = self.entry.runtime_data
        now = datetime.now(UTC)
        runtime._clock = lambda: now
        await runtime.set_operation(True)
        sid = runtime.session.session_id
        await runtime.receive(Event("close", sid, Kind.DOOR_CLOSE, now, now))
        await runtime.receive(Event("infusion", sid, Kind.INFUSION, now, now))
        runtime.controller.report_heating(True, now)
        original_options = dict(self.entry.options)
        for cooling in (False, True):
            if cooling:
                now += timedelta(seconds=10)
                await runtime.receive(Event("open", sid, Kind.DOOR_OPEN, now, now))
                now += timedelta(seconds=1)
                await runtime.receive(
                    Event("ventilation", sid, Kind.VENTILATION, now, now)
                )
                self.assertIsNotNone(runtime.session.after_run)
            else:
                self.assertIsNotNone(runtime.session.timeline.active)
            before = runtime.session
            async with ClientSession(headers=self.headers) as client:
                for value in (10**400, -(10**400)):
                    async with client.post(
                        self.base + "/program", json={"temperature_steps": [value]}
                    ) as response:
                        self.assertEqual(response.status, 400, await response.text())
                        self.assertTrue((await response.json())["error"])
                    self.assertEqual(runtime.session, before)
                    for field in ("temperature_steps", "start_c", "end_c"):
                        options = json.loads(json.dumps(original_options))
                        if field == "temperature_steps":
                            options[field] = [value]
                        else:
                            options["temperature_programs"][0][field] = value
                        self.hass.config_entries.async_update_entry(
                            self.entry, options=options
                        )
                        await self.hass.async_block_till_done()
                        self.assertIs(self.entry.runtime_data, runtime)
                        self.assertEqual(runtime.session, before)
                        self.assertEqual(dict(self.entry.options), original_options)
                        self.assertFalse(runtime.reconfiguring)
