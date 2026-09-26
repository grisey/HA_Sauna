"""A pending oven-cooling phase remains safely finishable through the API."""

import asyncio
import unittest
from datetime import UTC, datetime, timedelta

from aiohttp import ClientSession
from harness import create_sauna, start_hass
from homeassistant.auth.const import GROUP_ID_ADMIN

from custom_components.ha_sauna.core.timeline import Event, Kind


class PendingCoolingAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass, self.temp = await start_hass()
        self.entry = await create_sauna(self.hass)
        user = await self.hass.auth.async_create_user(
            "Pending cooling", group_ids=[GROUP_ID_ADMIN]
        )
        token = await self.hass.auth.async_create_refresh_token(
            user, client_id="http://localhost/"
        )
        self.headers = {
            "Authorization": "Bearer " + self.hass.auth.async_create_access_token(token)
        }
        self.base = f"http://127.0.0.1:{self.hass.http.server_port}/api/ha_sauna/{self.entry.entry_id}"

    async def asyncTearDown(self):
        await self.hass.async_block_till_done()
        await self.hass.async_stop(force=True)
        self.temp.cleanup()

    async def _pending_phase(self, end_contactor_state, prefix):
        runtime = self.entry.runtime_data
        base = datetime.now(UTC)
        now = [base]
        runtime._clock = lambda: now[0]
        heater = self.entry.options["bindings"]["heater"]
        state = self.hass.states.get(heater)
        self.hass.states.async_set(heater, "on", state.attributes, force_update=True)
        await self.hass.async_block_till_done()
        await runtime.set_operation(True)
        session_id = runtime.session.session_id
        for second, kind in (
            (1, Kind.DOOR_CLOSE),
            (2, Kind.INFUSION),
            (3, Kind.DOOR_OPEN),
            (4, Kind.VENTILATION),
        ):
            now[0] = base + timedelta(seconds=second)
            if kind is Kind.VENTILATION and end_contactor_state != "on":
                self.hass.states.async_set(
                    heater, end_contactor_state, state.attributes, force_update=True
                )
                await self.hass.async_block_till_done()
            await runtime.receive(
                Event(f"{prefix}:{second}", session_id, kind, now[0], now[0])
            )

        return runtime, heater, state, now

    async def test_pending_cooling_can_be_finished_once_without_restarting_on_late_off(
        self,
    ):
        runtime, heater, state, now = await self._pending_phase("on", "pending")

        phase = runtime.session.after_run
        self.assertTrue(phase.pending_start)
        self.assertIsNone(phase.ends_at)
        self.assertEqual(phase.active_intervals, ())
        token = phase.phase_id
        reference = runtime.session.last_completed_oven_cooling_at
        runtime.controller.protection.add("test_protection")

        async with ClientSession(headers=self.headers) as client:
            async with client.post(
                self.base + "/finish_phase",
                json={"purpose": "after_run", "token": "stale"},
            ) as response:
                self.assertEqual(response.status, 409, await response.text())
            now[0] += timedelta(seconds=1)
            async with client.post(
                self.base + "/finish_phase",
                json={"purpose": "after_run", "token": token},
            ) as response:
                self.assertEqual(response.status, 200, await response.text())
            self.assertIsNone(runtime.session.after_run)
            self.assertEqual(runtime.session.last_completed_oven_cooling_at, reference)
            archived_phase = runtime.session.after_run_history[-1]
            self.assertTrue(archived_phase.pending_start)
            self.assertEqual(archived_phase.active_intervals, ())
            self.assertIsNone(archived_phase.cooling_calculation)
            self.assertFalse(runtime.controller.last_decision.heat)
            async with client.post(
                self.base + "/finish_phase",
                json={"purpose": "after_run", "token": token},
            ) as response:
                self.assertEqual(response.status, 409, await response.text())

        await runtime.archive.flush()
        archive = await asyncio.to_thread(
            runtime.archive.read, runtime.session.session_id, limit=10000
        )
        record = next(
            row for row in archive["records"] if row["kind"] == "manual_phase_end"
        )
        self.assertEqual(
            record["payload"],
            {
                "purpose": "after_run",
                "token": token,
                "planned_ends_at": None,
                "ended_at": now[0].isoformat(),
            },
        )

        self.hass.states.async_set(heater, "off", state.attributes, force_update=True)
        await self.hass.async_block_till_done()
        self.assertIsNone(runtime.session.after_run)
        await runtime.tick()
        self.assertFalse(runtime.controller.last_decision.heat)
        await runtime.set_operation(False)
        self.assertFalse(runtime.controller.last_decision.heat)

    async def test_unknown_contactor_pending_phase_is_also_finishable(self):
        runtime, _heater, _state, now = await self._pending_phase("unknown", "unknown")
        phase = runtime.session.after_run
        self.assertTrue(phase.pending_start)
        self.assertIsNone(phase.ends_at)
        token = phase.phase_id
        now[0] += timedelta(seconds=1)
        async with (
            ClientSession(headers=self.headers) as client,
            client.post(
                self.base + "/finish_phase",
                json={"purpose": "after_run", "token": token},
            ) as response,
        ):
            self.assertEqual(response.status, 200, await response.text())
        self.assertIsNone(runtime.session.after_run)
        self.assertTrue(runtime.session.after_run_history[-1].pending_start)


if __name__ == "__main__":
    unittest.main()
