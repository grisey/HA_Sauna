import asyncio
from contextlib import closing
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from custom_components.ha_sauna import backup
from custom_components.ha_sauna.archive import Archive
from custom_components.ha_sauna.const import DOMAIN


class Hook:
    def __init__(self, entry_id, *, pre_error=None, flush_error=None):
        self.entry_id = entry_id
        self.pre_error = pre_error
        self.flush_error = flush_error
        self.prepared = False
        self.released = False
        self.flush_calls = 0

    async def pre_backup(self):
        self.prepared = True
        if self.pre_error:
            raise self.pre_error

    def release_backup(self):
        self.released = True

    async def flush(self):
        self.flush_calls += 1
        if callable(self.flush_error):
            await self.flush_error()
        elif self.flush_error:
            raise self.flush_error


def hass_for(*archives):
    entries = [
        SimpleNamespace(runtime_data=SimpleNamespace(closed=False, archive=archive))
        for archive in archives
    ]
    return SimpleNamespace(
        data={DOMAIN: {"archives": dict.fromkeys(archives)}},
        config_entries=SimpleNamespace(async_entries=lambda domain: entries)
    )


def count_records(path):
    with closing(sqlite3.connect(path)) as db:
        return db.execute("SELECT COUNT(*) FROM records").fetchone()[0]


class BackupReleaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_archive_already_closing_finishes_before_backup_returns(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(Path(directory) / "closing.sqlite", "entry")
            await archive.start()
            hass = hass_for(archive)
            entered, release = asyncio.Event(), asyncio.Event()
            original = archive._drain_failed_records

            async def wait_for_write():
                entered.set()
                await release.wait()
                return await original()

            with patch.object(archive, "_drain_failed_records", wait_for_write):
                archive.append("diagnostic", datetime.now(UTC), {"last": True})
                closing_task = asyncio.create_task(archive.close())
                await entered.wait()
                preparation = asyncio.create_task(backup.async_pre_backup(hass))
                await asyncio.sleep(0)
                self.assertFalse(preparation.done())
                release.set()
                await asyncio.wait_for(preparation, 1)
                await closing_task
                self.assertEqual(await asyncio.to_thread(count_records, archive.path), 1)
                await backup.async_post_backup(hass)

    async def test_unload_preserves_pending_and_active_backup_pause(self):
        for pending in (True, False):
            with self.subTest(pending=pending), tempfile.TemporaryDirectory() as directory:
                archive = Archive(Path(directory) / "archive.sqlite", "entry")
                await archive.start()
                hass = hass_for(archive)
                gate = asyncio.Event()
                entered = asyncio.Event()
                original = archive._drain_failed_records

                async def wait_before_pause():
                    entered.set()
                    await gate.wait()
                    return await original()

                try:
                    with patch.object(archive, "_drain_failed_records", wait_before_pause):
                        preparation = asyncio.create_task(backup.async_pre_backup(hass))
                        await entered.wait()
                        if not pending:
                            gate.set()
                            await preparation
                        archive.append("diagnostic", datetime.now(UTC), {})
                        closing = asyncio.create_task(archive.close())
                        await asyncio.sleep(0)
                        gate.set()
                        await preparation
                        self.assertFalse(closing.done())
                        self.assertEqual(await asyncio.to_thread(count_records, archive.path), 0)
                        # HA may have removed the entry before its post-hook.
                        hass.config_entries.async_entries = lambda domain: []
                        await asyncio.wait_for(backup.async_post_backup(hass), 1)
                        await asyncio.wait_for(closing, 1)
                        self.assertEqual(await asyncio.to_thread(count_records, archive.path), 1)
                finally:
                    gate.set()
                    archive.release_backup()
                    await archive.close()

    async def test_releases_healthy_real_archive_when_first_flush_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Archive(Path(directory) / "first.sqlite", "first")
            second = Archive(Path(directory) / "second.sqlite", "second")
            await first.start()
            await second.start()
            try:
                hass = hass_for(first, second)
                await backup.async_pre_backup(hass)
                now = datetime.now(UTC)
                first.append("diagnostic", now, {"archive": "first"})
                second.append("diagnostic", now, {"archive": "second"})
                original_write = first._write

                def fail(record):
                    raise sqlite3.DatabaseError("first flush failed")

                first._write = fail
                with self.assertRaisesRegex(
                    sqlite3.DatabaseError, "first flush failed"
                ):
                    await backup.async_post_backup(hass)
                self.assertTrue(second.resume.is_set())
                self.assertEqual(await asyncio.to_thread(count_records, second.path), 1)

                first._write = original_write
                await first.flush()
                first.append("diagnostic", now, {"archive": "recovered"})
                await first.flush()
                self.assertEqual(await asyncio.to_thread(count_records, first.path), 2)
                export = await first.export()
                try:
                    self.assertTrue(export.exists())
                finally:
                    export.unlink()
                await first.close()
                reopened = Archive(first.path, "first")
                await reopened.start()
                try:
                    self.assertEqual(
                        await asyncio.to_thread(count_records, reopened.path), 2
                    )
                finally:
                    await reopened.close()
            finally:
                if not first.closed:
                    await first.close()
                if not second.closed:
                    await second.close()

    async def test_flush_failure_at_each_position_releases_every_archive(self):
        for failed_index in range(3):
            with self.subTest(failed_index=failed_index):
                hooks = [Hook(f"archive-{index}") for index in range(3)]
                hooks[failed_index].flush_error = RuntimeError(
                    f"failure {failed_index}"
                )
                with self.assertRaisesRegex(RuntimeError, f"failure {failed_index}"):
                    await backup.async_post_backup(hass_for(*hooks))
                self.assertTrue(all(hook.released for hook in hooks))
                self.assertEqual([hook.flush_calls for hook in hooks], [1, 1, 1])

    async def test_releases_all_archives_before_delayed_flush_completes(self):
        gate = asyncio.get_running_loop().create_future()

        async def wait_for_gate():
            await gate

        first = Hook("first", flush_error=wait_for_gate)
        second = Hook("second")
        task = asyncio.create_task(backup.async_post_backup(hass_for(first, second)))
        await asyncio.sleep(0)
        self.assertTrue(first.released)
        self.assertTrue(second.released)
        self.assertEqual(second.flush_calls, 0)
        gate.set_result(None)
        await task
        self.assertEqual(second.flush_calls, 1)

    async def test_pre_backup_preserves_original_failure_and_notes_cleanup_failure(
        self,
    ):
        cleanup_failure = RuntimeError("cleanup flush failed")
        original = ValueError("preparation failed")
        first = Hook("first", flush_error=cleanup_failure)
        second = Hook("second", pre_error=original)
        with self.assertRaises(ValueError) as caught:
            await backup.async_pre_backup(hass_for(first, second))
        self.assertIs(caught.exception, original)
        self.assertTrue(first.released)
        self.assertTrue(second.released)
        self.assertIn("cleanup flush failed", "\n".join(caught.exception.__notes__))

    async def test_cancelled_preparation_and_cleanup_failure_preserve_cancellation(
        self,
    ):
        cleanup_failure = RuntimeError("cleanup flush failed")
        cancelled = asyncio.CancelledError("pause cancelled")
        first = Hook("first", flush_error=cleanup_failure)
        second = Hook("second", pre_error=cancelled)
        with self.assertRaises(asyncio.CancelledError) as caught:
            await backup.async_pre_backup(hass_for(first, second))
        self.assertIs(caught.exception, cancelled)
        self.assertTrue(first.released)
        self.assertTrue(second.released)
        self.assertIn("cleanup flush failed", "\n".join(caught.exception.__notes__))

    async def test_pre_backup_keeps_prior_flush_error_when_cleanup_is_cancelled(self):
        original = ValueError("preparation failed")
        first = Hook("first", flush_error=RuntimeError("first flush failed"))
        second = Hook("second", flush_error=asyncio.CancelledError("cleanup cancelled"))
        third = Hook("third", pre_error=original)

        with self.assertRaises(ValueError) as caught:
            await backup.async_pre_backup(hass_for(first, second, third))

        self.assertIs(caught.exception, original)
        self.assertTrue(all(hook.released for hook in (first, second, third)))
        notes = "\n".join(caught.exception.__notes__)
        self.assertIn("CancelledError: cleanup cancelled", notes)
        self.assertIn("first: RuntimeError: first flush failed", notes)

    async def test_cancelled_flush_after_release_is_not_masked(self):
        flush_started = asyncio.Event()
        gate = asyncio.get_running_loop().create_future()

        async def wait_for_gate():
            flush_started.set()
            await gate

        first = Hook("first", flush_error=wait_for_gate)
        second = Hook("second")
        task = asyncio.create_task(backup.async_post_backup(hass_for(first, second)))
        await flush_started.wait()
        self.assertTrue(first.released)
        self.assertTrue(second.released)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(second.flush_calls, 0)
