"""Authenticated deletion after restoring a committed, interrupted snapshot."""
import shutil
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path

from aiohttp import ClientSession
from harness import create_sauna, credentials, retain_session, start_hass


class ArchiveRestartDeleteTests(unittest.IsolatedAsyncioTestCase):
    async def test_interrupted_confirmed_session_is_deletable_after_restart(self):
        hass, temp = await start_hass()
        try:
            entry = await create_sauna(hass, parameter_overrides={"sensor_timeout_seconds": 60})
            runtime = entry.runtime_data
            await runtime.set_operation(True)
            retain_session(runtime)
            identity = runtime.session.session_id
            await runtime.archive.flush()
            path = runtime.archive.path
            snapshot = Path(temp.name) / "interrupted.sqlite"
            with closing(sqlite3.connect(path)) as source, closing(sqlite3.connect(snapshot)) as target:
                source.backup(target)
            before = runtime.archive.read(identity)
            self.assertIsNone(before["session"]["ended_at"])
            token = await credentials(hass)
            base = f"http://127.0.0.1:{hass.http.server_port}/api/ha_sauna/{entry.entry_id}"
            async with ClientSession(headers={"Authorization": "Bearer " + token}) as client:
                for body in ({"session_id": identity}, {"reset": True}):
                    async with client.post(base + "/archive/erase", json=body) as response:
                        self.assertEqual(response.status, 409)
                self.assertTrue(await hass.config_entries.async_unload(entry.entry_id))
                shutil.copy2(snapshot, path)
                self.assertTrue(await hass.config_entries.async_setup(entry.entry_id))
                await hass.async_block_till_done()
                new_runtime = entry.runtime_data
                self.assertIsNone(new_runtime.session)
                self.assertEqual(new_runtime.archive.read(identity), before)
                async with client.post(base + "/archive/erase", json={"session_id": identity}) as response:
                    self.assertEqual(response.status, 200, await response.text())
                    self.assertEqual((await response.json())["deleted_sessions"], 1)
                self.assertIsNone(new_runtime.archive.read(identity))
                self.assertEqual(new_runtime.archive.read(), [])
        finally:
            await hass.async_stop(force=True)
            temp.cleanup()
