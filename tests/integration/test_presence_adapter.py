"""Echte HA-State-Machine und Listener; keine Geräte- oder Aktordienste."""
import tempfile
import unittest
from datetime import UTC, datetime, timedelta

from homeassistant.core import HomeAssistant, State

from custom_components.ha_sauna.core.presence import PresenceProjection
from custom_components.ha_sauna.presence_adapter import HAPresenceAdapter


class PresenceAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.temp.name)
        self.reports = []
        self.projection = PresenceProjection()
        async def receive(report):
            self.reports.append(report)
            self.projection.accept(report)
        self.adapter = HAPresenceAdapter(self.hass, receive, "binary_sensor.presence")

    async def asyncTearDown(self):
        self.adapter.close()
        await self.hass.async_block_till_done()
        self.temp.cleanup()

    async def set_state(self, entity, state, attrs=None):
        self.hass.states.async_set(entity, state, attrs or {})
        await self.hass.async_block_till_done()

    async def test_initial_repeat_off_unavailable_return_and_reload(self):
        await self.set_state("binary_sensor.presence", "on")
        await self.adapter.start()
        first = self.reports[0]
        await self.set_state("binary_sensor.presence", "on", {"battery": 80})
        self.assertEqual(len(self.reports), 1)
        self.adapter.close()
        await self.adapter.start()
        self.assertEqual(len(self.reports), 1)
        for state in ("off", "unknown", "unavailable", "on"):
            await self.set_state("binary_sensor.presence", state)
        self.assertEqual([r.occupancy for r in self.reports],
                         ["present", "absent", "unknown", "unknown", "present"])
        self.assertIsNone(self.projection.current)
        self.assertEqual(first.source, "binary_sensor.presence")

    async def test_missing_rebind_removal_and_listener_cleanup(self):
        await self.adapter.start()
        self.assertEqual(self.reports[-1].reason, "missing")
        await self.set_state("binary_sensor.presence", "on")
        await self.set_state("binary_sensor.other", "off")
        await self.adapter.rebind("binary_sensor.other")
        self.assertEqual(self.reports[-2].reason, "source_unbound")
        self.assertEqual(self.reports[-1].occupancy, "absent")
        count = len(self.reports)
        await self.set_state("binary_sensor.presence", "off")
        self.assertEqual(len(self.reports), count)
        self.hass.states.async_remove("binary_sensor.other")
        await self.hass.async_block_till_done()
        self.assertEqual(self.reports[-1].reason, "missing")
        self.adapter.close()
        count = len(self.reports)
        await self.set_state("binary_sensor.other", "on")
        self.assertEqual(len(self.reports), count)

    async def test_long_valid_on_and_stable_reloaded_identity(self):
        old = datetime.now(UTC) - timedelta(days=30)
        state = State("binary_sensor.presence", "on", last_changed=old)
        now = datetime.now(UTC)
        await self.adapter._deliver(state, now, now)
        first = self.reports[-1]
        replacement = HAPresenceAdapter(self.hass, self.adapter.callback, "binary_sensor.presence")
        await replacement._deliver(state, now + timedelta(days=1), now)
        self.assertEqual(self.reports[-1].report_id, first.report_id)
        self.assertEqual(self.reports[-1].effective_at, old)
        self.assertTrue(self.reports[-1].available)
        self.assertEqual(len(self.projection.observations), 1)
