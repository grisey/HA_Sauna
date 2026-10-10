"""The panel route is shared by concurrently configured saunas."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from homeassistant.components import frontend

from custom_components.ha_sauna.const import DOMAIN
from custom_components.ha_sauna.config_flow import pack_binding_input
from custom_components.ha_sauna.frontend import register
from harness import create_sauna, seed_sources, start_hass


class PanelRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_sidebar_uses_current_entry_title_without_replacing_static_route(self):
        hass, temp = await start_hass()
        try:
            entry = await create_sauna(hass)
            original_panel = hass.data[frontend.DATA_PANELS]["ha-sauna"]
            self.assertEqual(original_panel.sidebar_title, entry.title)
            runtime = entry.runtime_data
            hass.config_entries.async_update_entry(entry, title="Gartensauna")
            await hass.async_block_till_done()
            await register(hass)
            panel = hass.data[frontend.DATA_PANELS]["ha-sauna"]
            self.assertEqual(panel.sidebar_title, "Gartensauna")
            self.assertEqual(panel.config, original_panel.config)
            self.assertIs(entry.runtime_data, runtime)
            await register(hass)
            self.assertIs(hass.data[frontend.DATA_PANELS]["ha-sauna"], panel)
            routes = [
                route
                for route in hass.http.app.router.routes()
                if route.method == "GET"
                and route.resource.canonical == "/ha_sauna/panel.js"
            ]
            self.assertEqual(len(routes), 1)
            second = await create_sauna(
                hass, binding_overrides={"heater": "switch.second_heater"}
            )
            self.assertEqual(
                hass.data[frontend.DATA_PANELS]["ha-sauna"].sidebar_title, "Sauna"
            )
            await hass.config_entries.async_remove(second.entry_id)
            await hass.async_block_till_done()
            self.assertEqual(
                hass.data[frontend.DATA_PANELS]["ha-sauna"].sidebar_title,
                "Gartensauna",
            )
            await hass.config_entries.async_remove(entry.entry_id)
            await hass.async_block_till_done()
            self.assertNotIn("ha-sauna", hass.data[frontend.DATA_PANELS])
            self.assertFalse(hass.data[DOMAIN]["panel_registered"])
            self.assertTrue(hass.data[DOMAIN]["panel_static_registered"])
            replacement = await create_sauna(hass)
            self.assertEqual(
                hass.data[frontend.DATA_PANELS]["ha-sauna"].sidebar_title,
                replacement.title,
            )
            routes = [
                route
                for route in hass.http.app.router.routes()
                if route.method == "GET"
                and route.resource.canonical == "/ha_sauna/panel.js"
            ]
            self.assertEqual(len(routes), 1)
        finally:
            await hass.async_stop(force=True)
            temp.cleanup()

    async def test_concurrent_registration_adds_one_static_route(self):
        hass, temp = await start_hass()
        try:
            registrations = 0
            original_register_static_paths = hass.http.async_register_static_paths

            async def read_panel_bytes(read_bytes):
                await asyncio.sleep(0)
                return read_bytes()

            async def register_static_paths(paths):
                nonlocal registrations
                registrations += 1
                await asyncio.sleep(0)
                await original_register_static_paths(paths)

            with (
                patch(
                    "custom_components.ha_sauna.frontend.asyncio",
                    SimpleNamespace(Lock=asyncio.Lock, to_thread=read_panel_bytes),
                ),
                patch.object(hass.http, "async_register_static_paths", register_static_paths),
            ):
                await asyncio.gather(register(hass), register(hass))

            self.assertEqual(registrations, 1)
            self.assertTrue(hass.data[DOMAIN]["panel_registered"])
            self.assertEqual(
                hass.data[frontend.DATA_PANELS]["ha-sauna"].sidebar_title, "Sauna"
            )
            routes = [
                route
                for route in hass.http.app.router.routes()
                if route.method == "GET"
                and route.resource.canonical == "/ha_sauna/panel.js"
            ]
            self.assertEqual(len(routes), 1)
        finally:
            await hass.async_stop(force=True)
            temp.cleanup()

    async def test_failed_setup_closes_started_archive(self):
        hass, temp = await start_hass()
        try:
            bindings = seed_sources(hass)
            flow = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": "user"})
            self.assertEqual(flow["step_id"], "user")
            flow = await hass.config_entries.flow.async_configure(
                flow["flow_id"], {"name": "Testsauna"})
            self.assertEqual(flow["step_id"], "entities")
            with patch(
                "custom_components.ha_sauna.frontend.register",
                side_effect=RuntimeError("synthetic panel registration failure"),
            ):
                result = await hass.config_entries.flow.async_configure(
                    flow["flow_id"],
                    pack_binding_input({"name": "Testsauna", "control_input_mode": "switch", **bindings}),
                )
                self.assertEqual(result["type"], "create_entry")
                await hass.async_block_till_done()
            entry = result["result"]
            runtime = entry.runtime_data
            self.assertTrue(runtime.closed)
            self.assertTrue(runtime.archive.closed)
            self.assertTrue(runtime.archive.worker.done())
        finally:
            await hass.async_stop(force=True)
            temp.cleanup()
