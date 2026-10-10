"""Partial settings writes through authenticated HA HTTP and the runtime lock."""
import asyncio
from copy import deepcopy
import unittest
from unittest.mock import patch

from aiohttp import ClientSession
from homeassistant.auth.const import GROUP_ID_USER

from custom_components.ha_sauna.settings import (
    _async_set_parameters_locked,
    async_set_parameters,
)
from harness import create_sauna, credentials, start_hass


class ParameterPatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass, self.temp = await start_hass()
        self.entry = await create_sauna(self.hass)
        self.base = (
            f"http://127.0.0.1:{self.hass.http.server_port}/api/ha_sauna/"
            f"{self.entry.entry_id}"
        )
        self.headers = {"Authorization": "Bearer " + await credentials(self.hass)}

    async def asyncTearDown(self):
        await self.hass.async_block_till_done()
        await self.hass.async_stop(force=True)
        self.temp.cleanup()

    async def test_control_permission_does_not_authorize_parameter_patch(self):
        user = await self.hass.auth.async_create_user(
            "Controls only", group_ids=[GROUP_ID_USER]
        )
        refresh = await self.hass.auth.async_create_refresh_token(
            user, client_id="http://localhost/"
        )
        headers = {
            "Authorization": "Bearer " + self.hass.auth.async_create_access_token(refresh)
        }
        before = deepcopy(dict(self.entry.options))
        async with ClientSession(headers=headers) as client:
            async with client.patch(
                self.base + "/parameters", json={"preset_count": 3}
            ) as response:
                self.assertEqual(response.status, 403, await response.text())
        self.assertEqual(self.entry.options, before)

    async def test_partial_display_edit_preserves_plant_and_program_choice(self):
        async with ClientSession(headers=self.headers) as client:
            profile = self.entry.runtime_data.configuration.temperature_programs[0].id
            async with client.post(
                self.base + "/program", json={"profile": profile}
            ) as response:
                self.assertEqual(response.status, 200, await response.text())
            await self.hass.async_block_till_done()
            await async_set_parameters(
                self.hass, self.entry, {"session_gap_minutes": 7}, partial=True
            )
            await self.hass.async_block_till_done()
            before = deepcopy(dict(self.entry.options))
            count = self.entry.runtime_data.configuration.parameters.values["preset_count"]
            changed_count = 2 if count != 2 else 3
            async with client.patch(
                self.base + "/parameters", json={"preset_count": changed_count}
            ) as response:
                self.assertEqual(response.status, 200, await response.text())
                result = await response.json()
                self.assertEqual(result["parameters"]["session_gap_minutes"], 7)
        await self.hass.async_block_till_done()
        expected = deepcopy(before)
        expected["parameters"]["preset_count"] = changed_count
        self.assertEqual(self.entry.options, expected)
        self.assertEqual(self.entry.options["selected_program_id"], profile)

    async def test_partial_merge_reads_parameters_after_waiting_for_runtime_lock(self):
        runtime = self.entry.runtime_data
        entered = asyncio.Event()

        async def observed_writer(*args, **kwargs):
            entered.set()
            return await async_set_parameters(*args, **kwargs)

        async with ClientSession(headers=self.headers) as client:
            with patch(
                "custom_components.ha_sauna.api.async_set_parameters",
                side_effect=observed_writer,
            ):
                async with runtime._lock:
                    pending = asyncio.create_task(client.patch(
                        self.base + "/parameters", json={"preset_count": 3}
                    ))
                    await asyncio.wait_for(entered.wait(), 5)
                    self.assertFalse(pending.done())
                    # This preceding serialized live edit completes before the
                    # waiting HTTP writer may read or merge the current values.
                    await _async_set_parameters_locked(
                        self.hass, self.entry,
                        {"target_temperature_c": 79}, partial=True,
                    )
                response = await asyncio.wait_for(pending, 5)
                async with response:
                    self.assertEqual(response.status, 200, await response.text())
                    result = await response.json()
                    self.assertEqual(result["parameters"]["target_temperature_c"], 79)
        await self.hass.async_block_till_done()
        self.assertEqual(self.entry.options["parameters"]["target_temperature_c"], 79)
        self.assertEqual(self.entry.options["parameters"]["preset_count"], 3)
        self.assertEqual(self.entry.options["program_mode"], "constant")

    async def test_session_blocks_display_patch_without_modifying_options(self):
        runtime = self.entry.runtime_data
        await runtime.set_operation(True)
        async with ClientSession(headers=self.headers) as client:
            for stopped in (False, True):
                if stopped:
                    await runtime.set_operation(False)
                before = deepcopy(dict(self.entry.options))
                count = runtime.configuration.parameters.values["preset_count"]
                changed_count = 2 if count != 2 else 3
                async with client.patch(
                    self.base + "/parameters", json={"preset_count": changed_count}
                ) as response:
                    self.assertEqual(response.status, 409, await response.text())
                self.assertEqual(self.entry.options, before)
                self.assertIsNotNone(runtime.session)

    async def test_patch_requires_nonempty_object_and_temperature_does_not_inherit_it(self):
        before = deepcopy(dict(self.entry.options))
        async with ClientSession(headers=self.headers) as client:
            for body in ({}, [], "invalid", {"unknown_parameter": 1}):
                with self.subTest(body=body):
                    async with client.patch(
                        self.base + "/parameters", json=body
                    ) as response:
                        self.assertEqual(response.status, 400, await response.text())
            async with client.patch(
                self.base + "/parameters", data="null",
                headers={"Content-Type": "application/json"},
            ) as response:
                self.assertEqual(response.status, 400, await response.text())
            async with client.patch(
                self.base + "/temperature", json={"session_gap_minutes": 7}
            ) as response:
                self.assertEqual(response.status, 405, await response.text())
        self.assertEqual(self.entry.options, before)
