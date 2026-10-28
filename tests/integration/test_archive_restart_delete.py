"""Persisted HA-stop interruptions and authenticated deletion after restart."""
import json
import unittest
from datetime import timedelta

from aiohttp import ClientSession
from harness import create_sauna, credentials, retain_session, start_hass


class ArchiveRestartDeleteTests(unittest.IsolatedAsyncioTestCase):
    async def test_interrupted_confirmed_session_is_deletable_after_restart(self):
        await self._assert_stop_restart(completed=False)

    async def test_completed_session_stays_completed_after_stop_restart(self):
        await self._assert_stop_restart(completed=True)

    async def _assert_stop_restart(self, *, completed):
        hass, temp = await start_hass()
        http_restarted = False
        try:
            entry = await create_sauna(hass)
            runtime = entry.runtime_data
            await runtime.set_operation(True)
            retain_session(runtime)
            identity = runtime.session.session_id
            original = runtime.session
            if completed:
                now = runtime._clock()
                runtime._clock = lambda: now
                await runtime.set_operation(False)
                now += timedelta(minutes=runtime.configuration.parameters.values["session_gap_minutes"])
                await runtime.tick()
                self.assertIsNone(runtime.session)
            await runtime.archive.flush()
            observed_at = runtime._clock()
            before = runtime.archive.read(identity, now=observed_at)
            if completed:
                self.assertIsNotNone(before["session"]["ended_at"])
            else:
                self.assertIsNone(before["session"]["ended_at"])
            token = await credentials(hass)
            base = f"http://127.0.0.1:{hass.http.server_port}/api/ha_sauna/{entry.entry_id}"
            async with ClientSession(headers={"Authorization": "Bearer " + token}) as client:
                if not completed:
                    for body in ({"session_id": identity}, {"reset": True}):
                        async with client.post(base + "/archive/erase", json=body) as response:
                            self.assertEqual(response.status, 409)
                # Exercise the registered production listener, not a manually
                # restored pre-shutdown database or direct runtime.close().
                hass.bus.async_fire("homeassistant_stop")
                await hass.async_block_till_done()
                self.assertTrue(runtime.closed)
                self.assertTrue(runtime.archive.closed)
                self.assertIs(runtime.device.command, False)
                stopped = runtime.archive.read(identity, now=observed_at)
                print(json.dumps({
                    "evidence": "archive_stop",
                    "completed_before_stop": completed,
                    "session_id": stopped["session"]["timeline"]["session_id"],
                    "ended_at": stopped["session"]["ended_at"],
                    "heater_commanded": runtime.device.command,
                }, ensure_ascii=False, sort_keys=True), flush=True)
                self.assertEqual(stopped["session"]["ended_at"], before["session"]["ended_at"])
                self.assertEqual(stopped["session"]["timeline"]["session_id"], identity)
                self.assertEqual(stopped["session"]["timeline"]["session_started_at"], original.timeline.session_started_at.isoformat())
                self.assertEqual(stopped["session"]["timeline"]["completed"], before["session"]["timeline"]["completed"])
                if not completed:
                    self.assertTrue(any(record["kind"] == "interruption" and record["session_id"] == identity for record in stopped["records"]))
                self.assertTrue(await hass.config_entries.async_unload(entry.entry_id))
                self.assertTrue(await hass.config_entries.async_setup(entry.entry_id))
                await hass.async_block_till_done()
                new_runtime = entry.runtime_data
                self.assertIsNot(new_runtime, runtime)
                self.assertIsNone(new_runtime.session)
                self.assertFalse(new_runtime.controller.automatic_operation_enabled)
                self.assertIs(new_runtime.device.command, False)
                restarted = new_runtime.archive.read(identity, now=observed_at)
                self.assertEqual(restarted["session"], stopped["session"])
                print(json.dumps({
                    "evidence": "archive_stop_restart",
                    "completed_before_stop": completed,
                    "session_id": identity,
                    "before": before,
                    "after_stop": stopped,
                    "after_restart": restarted,
                    "commanded_outputs": {
                        "after_stop": {
                            "heater": runtime.device.command,
                            "archived_commands": [record for record in stopped["records"] if record["kind"] in {"command", "light_command"}],
                        },
                        "after_restart": {
                            "heater": new_runtime.device.command,
                            "archived_commands": [record for record in restarted["records"] if record["kind"] in {"command", "light_command"}],
                        },
                    },
                    "active_session_after_restart": new_runtime.session,
                    "automatic_operation_after_restart": new_runtime.controller.automatic_operation_enabled,
                }, ensure_ascii=False, sort_keys=True), flush=True)
                # The global stop also closed HA's HTTP listener. Rebind and
                # start the existing authenticated application before deletion.
                await client.close()
                await hass.http.async_bind()
                http_restarted = True
                await hass.http.start()
                base = f"http://127.0.0.1:{hass.http.server_port}/api/ha_sauna/{entry.entry_id}"
                async with ClientSession(headers={"Authorization": "Bearer " + token}) as restarted_client:
                    async with restarted_client.post(base + "/archive/erase", json={"session_id": identity}) as response:
                        self.assertEqual(response.status, 200, await response.text())
                        self.assertEqual((await response.json())["deleted_sessions"], 1)
                self.assertIsNone(new_runtime.archive.read(identity))
                self.assertEqual(new_runtime.archive.read(), [])
        finally:
            try:
                await hass.async_stop(force=True)
            finally:
                try:
                    # HA's original HTTP stop listener was registered once and
                    # consumed by the explicit stop event above.
                    if http_restarted:
                        await hass.http.stop()
                finally:
                    temp.cleanup()
