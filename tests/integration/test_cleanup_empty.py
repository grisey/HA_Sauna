"""Empty registry cleanup remains navigable through HA's options manager."""

import unittest
from threading import get_ident
from unittest.mock import patch

from homeassistant.block_async_io import _check_import_call_allowed
from homeassistant.helpers import entity_registry as er
from homeassistant.util.loop import protect_loop

from custom_components.ha_sauna import maintenance

from harness import create_sauna, start_hass


class EmptyCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass, self.temp = await start_hass()
        self.addCleanup(self.temp.cleanup)
        self.addAsyncCleanup(self.hass.async_stop, force=True)

    async def test_cleanup_uses_imports_allowed_by_ha_event_loop_guard(self):
        entry = await create_sauna(self.hass)
        # HA skips its import guard under unittest. Exercise the real guard
        # explicitly and promote its production warning to an exception.
        guarded_import = protect_loop(
            maintenance.import_module,
            loop_thread_id=get_ident(),
            check_allowed=_check_import_call_allowed,
            strict=True,
        )
        with patch.object(maintenance, "import_module", guarded_import):
            self.assertEqual(maintenance.cleanup_candidates(self.hass, entry), [])

    async def test_empty_cleanup_menu_returns_to_options_without_removing_entities(self):
        entry = await create_sauna(self.hass)
        registry = er.async_get(self.hass)
        before = {
            entity.entity_id: entity.id
            for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
        }
        self.assertTrue(before)

        flow = await self.hass.config_entries.options.async_init(entry.entry_id)
        empty = await self.hass.config_entries.options.async_configure(
            flow["flow_id"], {"next_step_id": "cleanup_entities"}
        )

        self.assertEqual(empty["type"], "menu")
        self.assertEqual(empty["step_id"], "cleanup_empty")
        self.assertEqual(empty["menu_options"], ["init"])
        result = await self.hass.config_entries.options.async_configure(
            flow["flow_id"], {"next_step_id": "init"}
        )
        self.assertEqual(result["step_id"], "init")
        self.assertEqual(
            {
                entity.entity_id: entity.id
                for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
            },
            before,
        )
