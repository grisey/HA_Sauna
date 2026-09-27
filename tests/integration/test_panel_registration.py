"""The panel route is shared by concurrently configured saunas."""

import asyncio
import unittest
from unittest.mock import patch

from homeassistant.setup import async_setup_component

from custom_components.ha_sauna.const import DOMAIN
from custom_components.ha_sauna.frontend import register
from harness import create_sauna, start_hass


class PanelRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_registration_adds_one_static_route(self):
        hass, temp = await start_hass()
        try:
            self.assertTrue(await async_setup_component(hass, "frontend", {}))
            await asyncio.gather(register(hass), register(hass))
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
            with patch(
                "custom_components.ha_sauna.frontend.register",
                side_effect=RuntimeError("synthetic panel registration failure"),
            ):
                entry = await create_sauna(hass)
            runtime = entry.runtime_data
            self.assertTrue(runtime.closed)
            self.assertTrue(runtime.archive.closed)
            self.assertTrue(runtime.archive.worker.done())
        finally:
            await hass.async_stop(force=True)
            temp.cleanup()
