"""Echter HA-HTTP-Abruf und Core-Backup mit Restore in separater Installation."""
import asyncio
from datetime import UTC, datetime, timedelta
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from aiohttp import ClientSession
from homeassistant.setup import async_setup_component
from homeassistant.components.backup import async_get_manager
from homeassistant.components.backup.manager import CoreBackupReaderWriter
from homeassistant.backup_restore import restore_backup
from custom_components.ha_sauna.core.models import Measurement, Position, Quantity
from harness import create_sauna, credentials, start_hass


class ArchiveIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass, self.temp = await start_hass()
        self.entry = await create_sauna(self.hass)
        self.runtime = self.entry.runtime_data
        self.now = datetime.now(UTC)
        self.runtime._clock = lambda: self.now
        await self.runtime.set_operation(True)
        self.session_id = self.runtime.session.session_id
        for n in range(20):
            m = Measurement(Position.UPPER, Quantity.TEMPERATURE, 70 + n / 100,
                str(70 + n / 100), "sensor.synthetic", self.now + timedelta(microseconds=n * 100))
            self.runtime.archive.append("measurement", m.received_at, m, self.session_id)
        await self.runtime.archive.flush()

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self.temp.cleanup()

    async def test_authenticated_download_during_capture(self):
        token = await credentials(self.hass)
        url = f"http://127.0.0.1:{self.hass.http.server_port}/api/ha_sauna/{self.entry.entry_id}"
        async with ClientSession() as client:
            async with client.get(url + "/export") as response:
                self.assertEqual(response.status, 401)
            async with client.get(url + "/archive", headers={"Authorization": "Bearer " + token}) as response:
                self.assertEqual(response.status, 200, await response.text())
                listing = await response.json()
                self.assertEqual(listing[0]["session_id"], self.session_id)
            task = asyncio.create_task(client.get(url + "/export", headers={"Authorization": "Bearer " + token}))
            self.runtime.archive.append("diagnostic", self.now, {"during_export": True}, self.session_id)
            response = await task
            async with response:
                self.assertEqual(response.status, 200, await response.text() if response.status != 200 else "")
                data = await response.read()
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                rows = archive.read("measurements.csv").decode().splitlines()
                self.assertEqual(len(rows), 21)
                session = json.loads(archive.read("sessions.jsonl").splitlines()[0])
                self.assertEqual(session["timeline"]["session_id"], self.session_id)
                self.assertEqual(session["configuration"], self.runtime.configuration.as_options())

    async def test_actual_ha_backup_and_restore_to_new_installation(self):
        assert await async_setup_component(self.hass, "backup", {})
        await self.hass.async_block_till_done()
        manager = async_get_manager(self.hass)
        self.assertIn("ha_sauna", manager.platforms)
        # Flush real ConfigEntry storage through HA's Store API before taking the backup.
        await self.hass.config_entries._store.async_save(self.hass.config_entries._data_to_save())
        expected = await asyncio.to_thread(self.runtime.archive.read, self.session_id)
        writer = CoreBackupReaderWriter(self.hass)
        _, task = await writer.async_create_backup(agent_ids=[], backup_name="Sauna integration test",
            extra_metadata={}, include_addons=None, include_all_addons=False,
            include_database=False, include_folders=None, include_homeassistant=True,
            on_progress=lambda event: None, password=None)
        written = await task
        self.assertGreater(written.backup.size, 0)
        with tempfile.TemporaryDirectory(prefix="sauna-restore-") as separate:
            tar_path = Path(separate) / "ha-backup.tar"
            stream = await written.open_stream()
            with tar_path.open("wb") as output:
                async for part in stream:
                    output.write(part)
            await written.release_stream()
            # Offizielle HA-Startanweisung und echte Restore-Routine, kein
            # Entpacken nur unserer Datenbank und kein kopiertes Archivmock.
            instruction = {"path": str(tar_path), "password": None,
                "remove_after_restore": True, "restore_database": False,
                "restore_homeassistant": True}
            (Path(separate) / ".HA_RESTORE").write_text(json.dumps(instruction))
            self.assertTrue(await asyncio.to_thread(restore_backup, separate))
            restored_hass, _ = await start_hass(separate)
            try:
                entry = restored_hass.config_entries.async_get_entry(self.entry.entry_id)
                self.assertIsNotNone(entry)
                self.assertEqual(entry.options, self.entry.options)
                self.assertTrue(await restored_hass.config_entries.async_setup(entry.entry_id))
                await restored_hass.async_block_till_done()
                self.assertIsNone(entry.runtime_data.session)
                actual = await asyncio.to_thread(entry.runtime_data.archive.read, self.session_id)
                self.assertEqual(actual, expected)
            finally:
                await restored_hass.async_stop(force=True)
