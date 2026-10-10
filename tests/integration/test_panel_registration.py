"""The panel route is shared by concurrently configured saunas."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from custom_components.ha_sauna.const import DOMAIN
from custom_components.ha_sauna.frontend import register
from harness import seed_sources, start_hass


class PanelRegistrationTests(unittest.IsolatedAsyncioTestCase):
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
            with patch(
                "custom_components.ha_sauna.frontend.register",
                side_effect=RuntimeError("synthetic panel registration failure"),
            ):
                result = await hass.config_entries.flow.async_configure(
                    flow["flow_id"],
                    {"name": "Testsauna", "control_input_mode": "switch", **bindings},
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
