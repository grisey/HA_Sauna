"""Echte HA-Einbindung der beobachtenden Quelle, Konfigurationssperre und Reload."""
import asyncio
import unittest

from harness import create_sauna, start_hass


class PresenceRuntimeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass, self.temp = await start_hass()
        self.entry = await create_sauna(self.hass)

    async def asyncTearDown(self):
        await self.hass.async_stop()
        if self.temp:
            self.temp.cleanup()

    async def configure(self, entity="binary_sensor.observed_presence"):
        self.hass.states.async_set(entity, "on", {"device_class": "occupancy"})
        self.hass.states.async_set("media_player.prepared_audio", "idle", {})
        options = dict(self.entry.options)
        options["presence_source"] = "ha_presence"
        options["bindings"] = {**options["bindings"], "presence": entity,
                               "audio_output": "media_player.prepared_audio"}
        self.hass.config_entries.async_update_entry(self.entry, options=options)
        await self.hass.async_block_till_done()
        return self.entry.runtime_data

    async def test_preview_initial_changes_unknown_and_audio_have_no_control_effect(self):
        runtime = await self.configure()
        self.assertEqual(runtime.configuration.presence_source, "ha_presence")
        self.assertEqual(runtime.presence_status["effective_source"], "proxy")
        source = runtime.configuration.bindings.values["presence"]
        self.assertEqual(runtime.presence.external[source].occupancy, "present")
        self.assertIsNone(runtime.session)
        decisions = tuple(runtime.controller.decisions)
        audio_calls = []
        unsub = self.hass.bus.async_listen("call_service", lambda e: audio_calls.append(e.data)
                                          if e.data.get("domain") == "media_player" else None)
        try:
            for state, occupancy, available in (("on", "present", True),
                                                ("off", "absent", True),
                                                ("unknown", "unknown", False),
                                                ("unavailable", "unknown", False),
                                                ("on", "present", True)):
                self.hass.states.async_set(source, state)
                await self.hass.async_block_till_done()
                report = runtime.presence.external[source]
                self.assertEqual((report.occupancy, report.available), (occupancy, available))
            self.assertEqual(tuple(runtime.controller.decisions), decisions)
            self.assertEqual(audio_calls, [])
            self.assertIsNone(runtime.session)
            self.assertTrue(all(e.kind == "occupancy" for e in runtime.consumer_events))
            await runtime.archive.flush()
            self.assertEqual(len(runtime.consumer_events), len({e.event_id for e in runtime.consumer_events}))
        finally:
            unsub()

    async def test_entity_change_detaches_old_listener_and_active_session_locks_options(self):
        old = await self.configure()
        runtime = await self.configure("binary_sensor.replacement_presence")
        self.assertTrue(old.closed)
        old_count = len(old.consumer_events)
        self.hass.states.async_set("binary_sensor.observed_presence", "off")
        await self.hass.async_block_till_done()
        self.assertEqual(len(old.consumer_events), old_count)
        self.assertNotIn("binary_sensor.observed_presence", runtime.presence.external)
        await runtime.begin_session("presence-lock")
        original = dict(self.entry.options)
        self.hass.config_entries.async_update_entry(self.entry, options={
            **original, "presence_source": "proxy", "bindings": {
                **original["bindings"], "presence": "binary_sensor.observed_presence"}})
        await self.hass.async_block_till_done()
        self.assertIs(self.entry.runtime_data, runtime)
        self.assertEqual(dict(self.entry.options), original)
        self.assertIsNone(runtime.session.timeline.active)
        self.assertEqual(runtime.session.timeline.gang_count, 0)

    async def test_reload_same_on_updates_status_but_does_not_redeliver(self):
        runtime = await self.configure()
        source = runtime.configuration.bindings.values["presence"]
        report_id = runtime.presence.external[source].report_id
        await runtime.archive.flush()
        await self.hass.config_entries.async_reload(self.entry.entry_id)
        await self.hass.async_block_till_done()
        current = self.entry.runtime_data
        self.assertTrue(runtime.closed)
        self.assertEqual(current.presence.external[source].report_id, report_id)
        self.assertFalse(any(e.event_id == report_id for e in current.consumer_events))
        self.assertIsNone(current.session)
