"""LZ2: Nach dem HA-Start werden die Kennzahlen im Hintergrund vorgerechnet."""

import unittest
from unittest.mock import call, patch

from harness import create_sauna, start_hass

from custom_components.ha_sauna.archive import Archive


class KeyFigurePreparationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass, self.temp = await start_hass()
        self.addCleanup(self.temp.cleanup)
        self.addAsyncCleanup(self.hass.async_stop, force=True)

    async def test_started_instance_prepares_with_its_settings_after_setup(self):
        with patch.object(Archive, "prepare_key_figures", autospec=True) as prepare:
            entry = await create_sauna(self.hass)
            await self.hass.async_block_till_done()
        runtime = entry.runtime_data
        self.assertTrue(prepare.call_args_list)
        self.assertEqual(
            prepare.call_args_list[-1],
            call(runtime.archive, **runtime.key_figure_settings()),
        )
        # Die Vorbereitung füllt nur den Zwischenspeicher; Anfragen rechnen wie bisher.
        self.assertEqual(
            runtime.archive.key_figures(**runtime.key_figure_settings()), []
        )
