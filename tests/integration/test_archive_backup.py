"""Echter HA-HTTP-Abruf und Core-Backup mit Restore in separater Installation."""
import asyncio
from contextlib import closing
from datetime import UTC, datetime, timedelta
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from aiohttp import ClientPayloadError, ClientSession
from homeassistant.setup import async_setup_component
from homeassistant.components.backup import async_get_manager
from homeassistant.components.backup.manager import CoreBackupReaderWriter
from homeassistant.backup_restore import restore_backup
from custom_components.ha_sauna.core.models import Measurement, Position, Quantity
from harness import create_sauna, credentials, start_hass, retain_session
from test_settings import async_timeout
from custom_components.ha_sauna import backup
from custom_components.ha_sauna.archive import ArchiveExport, ExportUnavailable
from custom_components.ha_sauna.const import DOMAIN

API_LOGGER = "custom_components.ha_sauna.api"


class ArchiveIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass, self.temp = await start_hass()
        self.entry = await create_sauna(self.hass)
        self.runtime = self.entry.runtime_data
        self.now = datetime.now(UTC)
        self.runtime._clock = lambda: self.now
        await self.runtime.set_operation(True)
        retain_session(self.runtime)
        self.session_id = self.runtime.session.session_id
        for n in range(20):
            m = Measurement(Position.UPPER, Quantity.TEMPERATURE, 70 + n / 100,
                str(70 + n / 100), "sensor.synthetic", self.now + timedelta(microseconds=n * 100))
            self.runtime.archive.append("measurement", m.received_at, m, self.session_id)
        await self.runtime.archive.flush()

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self.temp.cleanup()

    async def test_new_entry_cannot_initialize_database_during_backup(self):
        await backup.async_pre_backup(self.hass)
        entered = asyncio.Event()
        paths = []
        original = backup.async_start_archive

        async def observe_start(hass, runtime, path, entry_id):
            paths.append(Path(path))
            entered.set()
            await original(hass, runtime, path, entry_id)

        with patch.object(backup, "async_start_archive", observe_start):
            creating = asyncio.create_task(create_sauna(
                self.hass, binding_overrides={"heater": "switch.second_heater"}
            ))
            try:
                await asyncio.wait_for(entered.wait(), async_timeout("background"))
                self.assertFalse(paths[0].exists())
                self.assertFalse(creating.done())
            finally:
                await backup.async_post_backup(self.hass)
            entry = await asyncio.wait_for(creating, async_timeout("reload"))
        self.assertTrue(paths[0].exists())
        self.assertIn(entry.runtime_data.archive, self.hass.data[DOMAIN]["archives"])

    async def test_closing_runtime_stays_in_backup_until_writer_stops(self):
        entered, release, device_done = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original = self.runtime.device.close

        async def delayed_close():
            entered.set()
            await release.wait()
            await original()
            device_done.set()

        with patch.object(self.runtime.device, "close", delayed_close):
            closing = asyncio.create_task(self.runtime.close())
            await asyncio.wait_for(entered.wait(), async_timeout("background"))
            self.assertFalse(self.runtime.closed)
            await backup.async_pre_backup(self.hass)
            archive = self.runtime.archive
            try:
                before = await asyncio.to_thread(archive.read, self.session_id, now=self.now)
                release.set()
                await asyncio.wait_for(device_done.wait(), async_timeout("background"))
                self.assertFalse(closing.done())
                self.assertEqual(await asyncio.to_thread(archive.read, self.session_id, now=self.now), before)
            finally:
                release.set()
                await backup.async_post_backup(self.hass)
            await closing
        self.assertTrue(self.runtime.closed)
        self.assertTrue(archive.worker.done())
        self.assertNotIn(archive, backup.archives(self.hass))

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
                self.assertEqual(session["session_id"], self.session_id)
                self.assertEqual(session["timeline"]["session_id"], self.session_id)
                self.assertEqual(session["configuration"], self.runtime.configuration.as_snapshot())

    async def export_access(self):
        token = await credentials(self.hass)
        url = (f"http://127.0.0.1:{self.hass.http.server_port}"
               f"/api/ha_sauna/{self.entry.entry_id}/export")
        return url, {"Authorization": "Bearer " + token}

    def assert_no_reader(self):
        archive = self.runtime.archive
        self.assertIsNone(archive._running_export)
        # Waits for short runtime reads or writes; a remaining export reader keeps busy=1.
        with closing(sqlite3.connect(archive.path)) as db:
            self.assertEqual(db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0], 0)

    def held_first_block(self, aborted, stopped):
        """The worker keeps its first block until the export was stopped."""
        original_put, original_abort = ArchiveExport._put, ArchiveExport.abort
        first = []

        def put(export, block):
            if not first:
                first.append(block)
                export._stop.wait(async_timeout("background"))
            original_put(export, block)

        def abort(export, reason):
            aborted.append((export, reason))
            stopped.set()
            original_abort(export, reason)

        return (patch.object(ArchiveExport, "_put", put),
                patch.object(ArchiveExport, "abort", abort))

    async def test_export_check_and_failures_before_the_first_byte_are_json(self):
        url, headers = await self.export_access()
        archive = self.runtime.archive
        async with ClientSession(headers=headers) as client:
            async with client.get(url, params={"check": "1"}) as response:
                self.assertEqual(response.status, 200, await response.text())
                self.assertEqual(await response.json(), {"success": True})
            async with archive.export():
                for params in ({"check": "1"}, {}):
                    async with client.get(url, params=params) as response:
                        self.assertEqual(response.status, 409)
                        self.assertEqual(response.content_type, "application/json")
                        self.assertEqual(await response.json(),
                                         {"error": "Ein Archivexport läuft bereits."})
            self.assert_no_reader()
            original = archive._write

            def fail(record):
                raise sqlite3.OperationalError("database or disk is full")

            archive._write = fail
            try:
                archive.append("diagnostic", self.now, {"before_export": True}, self.session_id)
                with self.assertLogs(API_LOGGER, "ERROR"):
                    async with client.get(url) as response:
                        self.assertEqual(response.status, 500)
                        self.assertEqual(response.content_type, "application/json")
                        self.assertIn("database or disk is full", (await response.json())["error"])
            finally:
                archive._write = original
            await archive.flush()
        self.assert_no_reader()

    async def test_failure_after_the_first_byte_ends_without_chunked_terminator(self):
        url, headers = await self.export_access()

        def failing(db, row, *, cache=None):
            raise ValueError("Datensatz nicht lesbar")

        with patch.object(self.runtime.archive, "_decode_record", failing), \
                self.assertLogs(API_LOGGER, "ERROR") as logs:
            async with ClientSession(headers=headers) as client, client.get(url) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.content_type, "application/zip")
                with self.assertRaises(ClientPayloadError):
                    await response.read()
        self.assertIn("nach Beginn der Übertragung", logs.records[0].getMessage())
        self.assertIsInstance(logs.records[0].exc_info[1], ValueError)
        self.assert_no_reader()

    async def test_client_disconnect_ends_the_export_read(self):
        url, headers = await self.export_access()
        aborted, stopped = [], asyncio.Event()
        put, abort = self.held_first_block(aborted, stopped)
        with put, abort:
            async with ClientSession(headers=headers) as client:
                response = await client.get(url)
                self.assertEqual(response.status, 200)
                response.close()
            # The server notices the closed connection and cancels the handler.
            await asyncio.wait_for(stopped.wait(), async_timeout("background"))
            export, reason = aborted[0]
            await asyncio.wait_for(export.close(), async_timeout("background"))
        self.assertEqual(reason, "Der Archivexport wurde beendet.")
        self.assert_no_reader()

    async def test_ha_backup_stops_a_running_export(self):
        url, headers = await self.export_access()
        aborted = []
        put, abort = self.held_first_block(aborted, asyncio.Event())
        with put, abort, self.assertLogs(API_LOGGER, "ERROR") as logs:
            async with ClientSession(headers=headers) as client, client.get(url) as response:
                self.assertEqual(response.status, 200)
                await asyncio.wait_for(backup.async_pre_backup(self.hass),
                                       async_timeout("background"))
                try:
                    with self.assertRaises(ClientPayloadError):
                        await response.read()
                finally:
                    await backup.async_post_backup(self.hass)
        self.assertIn("Home-Assistant-Backup", aborted[0][1])
        self.assertIsInstance(logs.records[0].exc_info[1], ExportUnavailable)
        self.assert_no_reader()

    async def test_actual_ha_backup_and_restore_to_new_installation(self):
        assert await async_setup_component(self.hass, "backup", {})
        await self.hass.async_block_till_done()
        manager = async_get_manager(self.hass)
        self.assertIn("ha_sauna", manager.platforms)
        # Flush real ConfigEntry storage through HA's Store API before taking the backup.
        await self.hass.config_entries._store.async_save(self.hass.config_entries._data_to_save())
        expected = await asyncio.to_thread(self.runtime.archive.read, self.session_id, now=self.now)
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
            # Die wiederhergestellte HTTP-Konfiguration enthält denselben Port.
            # Die Quellinstallation gibt ihn vor Start der getrennten Instanz frei.
            await self.hass.async_stop(force=True)
            restored_hass, _ = await start_hass(separate)
            try:
                entry = restored_hass.config_entries.async_get_entry(self.entry.entry_id)
                self.assertIsNotNone(entry)
                self.assertEqual(entry.options, self.entry.options)
                self.assertTrue(await restored_hass.config_entries.async_setup(entry.entry_id))
                await restored_hass.async_block_till_done()
                self.assertIsNone(entry.runtime_data.session)
                actual = await asyncio.to_thread(entry.runtime_data.archive.read, self.session_id, now=self.now)
                self.assertEqual(actual, expected)
            finally:
                await restored_hass.async_stop(force=True)
