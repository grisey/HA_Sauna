"""Direkte Präsenz über HA-Listener, Türzuordnung, Quellwechsel und Reload."""
import asyncio
import json
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from harness import create_sauna, start_hass
from test_settings import async_timeout

from custom_components.ha_sauna.archive import plain
from custom_components.ha_sauna.core.timeline import Confirmation, DoorCloseEvidence, Event, Kind
from custom_components.ha_sauna.core.presence import ProxyPresenceSource, binary_presence
from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime


class PresenceRuntimeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_selection_recovers_original_report_at_real_first_usability(self):
        configured = (await self.configure()).configuration
        now = datetime(2030, 1, 1, tzinfo=UTC)
        runtime = SaunaRuntime(configured, clock=lambda: now)
        gang_before = runtime.session.timeline.active if runtime.session else None
        entity = configured.bindings.values["presence"]
        await runtime.accept_presence(binary_presence(entity, "unknown", now, now))
        now += timedelta(seconds=configured.parameters.values["presence_fallback_delay_seconds"])
        await runtime.tick()
        self.assertEqual(runtime.configuration_model.presence_source, "proxy")
        self.assertFalse(runtime.controller.direct_presence.qualified_presence)
        fallback_observed = {
            "source": runtime.presence_source,
            "switched_at": runtime._presence_source_selection.last_changed_at,
            "available_since": runtime._presence_source_selection.availability_since,
            "producer_report": runtime.presence.current,
            "input": runtime.controller.direct_presence,
        }
        now += timedelta(seconds=1)
        report = binary_presence(entity, "on", now, now)
        await runtime.accept_presence(report)
        self.assertEqual(runtime.configuration_model.presence_source, "proxy")
        self.assertFalse(runtime.controller.direct_presence.qualified_presence)
        now += timedelta(seconds=configured.parameters.values["presence_recovery_delay_seconds"])
        await runtime.tick()
        self.assertEqual(runtime.configuration_model.presence_source, "ha_presence")
        selected = runtime.controller.direct_presence
        self.assertTrue(selected.qualified_presence)
        self.assertEqual(selected.report_id, report.report_id)
        self.assertEqual(selected.timing.effective_at, report.effective_at)
        self.assertEqual(selected.timing.received_at, report.received_at)
        self.assertEqual(selected.timing.first_usable_at, now)
        self.assertIsNone(runtime.session)
        evidence = {
            "delays_seconds": {
                "fallback": configured.parameters.values["presence_fallback_delay_seconds"],
                "recovery": configured.parameters.values["presence_recovery_delay_seconds"],
            },
            "fallback": fallback_observed,
            "recovery": {
                "source": runtime.presence_source,
                "switched_at": runtime._presence_source_selection.last_changed_at,
                "available_since": runtime._presence_source_selection.availability_since,
                "producer_report": runtime.presence.external[entity],
                "input": selected,
            },
            "gang_before": gang_before,
            "gang_after": runtime.session.timeline.active if runtime.session else None,
            "last_decision": runtime.controller.last_decision,
            "regulation_inputs": runtime.controller.regulation_inputs,
            "cooling": runtime.session.after_run if runtime.session else None,
        }
        print("SOURCE_HANDOVER_EVIDENCE=" + json.dumps(
            plain(evidence), sort_keys=True, separators=(",", ":"),
        ))
        await runtime.close()

    async def test_selected_proxy_reports_preserve_identity_without_durable_history(self):
        runtime = self.entry.runtime_data
        self.assertEqual(runtime.configuration.presence_source, "proxy")
        now = runtime._clock()
        report = ProxyPresenceSource.present(Event(
            "original-person", "projection-only", Kind.PERSON_STRONG, now, now,
        ))
        history = tuple(runtime.controller._direct_presence_history)
        controller_at = runtime.controller._last_at
        decisions = tuple(runtime.controller.decisions)
        runtime._record_presence(report)
        occupancy = runtime.controller.direct_presence
        self.assertEqual((occupancy.occupancy, occupancy.available, occupancy.qualified_presence),
                         ("present", True, False))
        self.assertEqual(occupancy.report_id, report.report_id)
        self.assertEqual(occupancy.timing.effective_at, report.effective_at)
        self.assertEqual(occupancy.timing.received_at, report.received_at)
        self.assertEqual(tuple(runtime.controller._direct_presence_history), history)
        self.assertEqual(runtime.controller._last_at, controller_at)
        self.assertEqual(tuple(runtime.controller.decisions), decisions)
        self.assertIsNone(runtime.session)
        runtime._record_presence(report)
        self.assertIs(runtime.controller.direct_presence, occupancy)

    async def test_selected_sources_share_door_heat_during_thermostat_pause(self):
        configured = (await self.configure()).configuration
        observations = []
        for source in ("proxy", "ha_presence"):
            now = datetime(2030, 1, 1, tzinfo=UTC)
            options = configured.as_options()
            options["presence_source"] = source
            options["program_mode"] = "constant"
            options["parameters"] = {**options["parameters"], "target_temperature_c": 80}
            runtime = SaunaRuntime(Configuration.from_options(options), clock=lambda: now)
            await runtime.begin_session("source-neutral-door")
            entity = configured.bindings.values["presence"]
            await runtime.accept_presence(binary_presence(entity, "off", now, now))
            target = runtime.controller.target_temperature
            runtime.controller.set_temperature(target - 2, now)
            await runtime.set_operation(True)
            self.assertTrue(runtime.controller.last_decision.heat)
            now += timedelta(seconds=runtime.configuration.parameters.seconds(
                "minimum_heating_minutes") + 1)
            runtime.controller.set_temperature(target + runtime.configuration.parameters.values[
                "readiness_offset_c"], now)
            await runtime.tick()
            self.assertFalse(runtime.controller.last_decision.heat)
            now += timedelta(seconds=1)
            runtime.controller.set_temperature(target, now)
            await runtime.tick()
            before = runtime.controller.last_decision
            self.assertEqual(before.reason, "thermostat_cooldown")
            for name, kind in (("entry-open", Kind.DOOR_OPEN), ("entry-close", Kind.DOOR_CLOSE)):
                now += timedelta(seconds=1)
                await runtime.receive(Event(name, runtime.session.session_id, kind, now, now))
            inputs = runtime.controller.regulation_inputs
            decision = runtime.controller.last_decision
            observed = {
                "source": runtime.configuration_model.presence_source,
                "temperature": target,
                "before": {"heat": before.heat, "reason": before.reason},
                "inputs": {"temporary_door_heat": inputs.temporary_door_heat,
                           "gang_heat_demand": inputs.gang_heat_demand,
                           "cooling": inputs.cooling},
                "last_decision": {"at": decision.at.isoformat(), "heat": decision.heat,
                                  "reason": decision.reason},
            }
            self.assertIsNone(runtime.session.timeline.active)
            self.assertTrue(inputs.temporary_door_heat)
            self.assertEqual((decision.heat, decision.reason), (True, "temporary_door_heat"))
            observations.append(observed)
            await runtime.close()
        self.assertEqual({k: v for k, v in observations[0].items() if k != "source"},
                         {k: v for k, v in observations[1].items() if k != "source"})
        print("SOURCE_NEUTRAL_DOOR=" + json.dumps(observations, sort_keys=True, separators=(",", ":")))

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

    async def test_initial_changes_unknown_and_audio_do_not_start_a_session(self):
        runtime = await self.configure()
        self.assertEqual(runtime.configuration.presence_source, "ha_presence")
        self.assertEqual(runtime.configuration_model.presence_source, "ha_presence")
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
                self.assertEqual(runtime.presence_status["current"], report)
                selected = runtime.controller.direct_presence
                self.assertTrue(selected.qualified_presence)
                self.assertEqual((selected.occupancy, selected.available), (occupancy, available))
            self.assertEqual(tuple(runtime.controller.decisions), decisions)
            self.assertEqual(audio_calls, [])
            self.assertIsNone(runtime.session)
            self.assertTrue(all(e.kind == "occupancy" for e in runtime.consumer_events))
            await runtime.archive.flush()
            self.assertEqual(len(runtime.consumer_events), len({e.event_id for e in runtime.consumer_events}))
        finally:
            unsub()

    async def test_live_presence_needs_entry_cycle_and_can_end_with_door_open(self):
        runtime = await self.configure()
        source = runtime.configuration.bindings.values["presence"]
        for role in ("upper_temperature", "lower_temperature"):
            entity_id = runtime.configuration.bindings.values[role]
            state = self.hass.states.get(entity_id)
            self.hass.states.async_set(entity_id, "80", state.attributes)
        await self.hass.async_block_till_done()
        await runtime.set_operation(True)

        async def presence(state):
            self.hass.states.async_set(source, state)
            await self.hass.async_block_till_done()

        async def door(name, kind):
            await self.semantic_door(runtime, name, kind)

        await presence("off")
        await presence("on")
        self.assertIsNone(runtime.session.timeline.active)
        await door("entry-open", Kind.DOOR_OPEN)
        await presence("off")
        await presence("on")
        self.assertIsNone(runtime.session.timeline.active)
        await door("entry-close", Kind.DOOR_CLOSE)
        gang = runtime.session.timeline.active
        self.assertIsNotNone(gang)
        self.assertEqual(gang.confirmation, Confirmation.CONFIRMED)
        self.assertEqual(gang.infusion_events, ())
        self.assertTrue(runtime.controller.regulation_inputs.gang_heat_demand)

        await presence("off")
        self.assertEqual(runtime.session.timeline.active.gang_id, gang.gang_id)
        await presence("unavailable")
        self.assertEqual(runtime.session.timeline.active.gang_id, gang.gang_id)
        self.assertFalse(runtime.controller.regulation_inputs.gang_heat_demand)
        await presence("on")
        self.assertTrue(runtime.controller.regulation_inputs.gang_heat_demand)
        await door("exit-open", Kind.DOOR_OPEN)
        await presence("off")
        self.assertEqual(runtime.session.timeline.active.gang_id, gang.gang_id)
        self.assertIsNone(runtime.session.after_run)
        await door("exit-ventilation", Kind.VENTILATION)
        self.assertIsNone(runtime.session.timeline.active)
        ended = runtime.session.timeline.completed[-1]
        self.assertEqual(runtime.session.timeline.door, "open")
        await door("exit-close", Kind.DOOR_CLOSE)
        self.assertIsNone(runtime.session.timeline.active)
        self.assertEqual(runtime.session.timeline.completed[-1], ended)
        self.assertEqual(runtime.session.timeline.gang_count, 1)
        self.assertEqual(sum(e.kind == "gang_confirmed" for e in runtime.consumer_events), 1)
        self.assertEqual(sum(e.kind == "gang_ended" for e in runtime.consumer_events), 1)

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

    async def start_absent_direct_session(self):
        runtime = await self.configure()
        source = runtime.configuration.bindings.values["presence"]
        minimum = runtime.configuration.parameters.values["sauna_min_temperature_c"]
        for role in ("upper_temperature", "lower_temperature"):
            entity_id = runtime.configuration.bindings.values[role]
            state = self.hass.states.get(entity_id)
            self.hass.states.async_set(entity_id, str(minimum + 1), state.attributes)
        self.hass.states.async_set(source, "off")
        await self.hass.async_block_till_done()
        await runtime.set_operation(True)
        return runtime, source

    async def semantic_door(self, runtime, name, kind, *, effective_at=None):
        # This seam tests semantic door handoff, not thermal door detection.
        candidate_at = None
        if kind == Kind.DOOR_CLOSE:
            candidate_at = runtime._clock()
            await asyncio.sleep(0)
        now = runtime._clock()
        await runtime.receive(Event(
            name, runtime.session.session_id, kind, effective_at or now, now,
            door_close_evidence=(DoorCloseEvidence(candidate_at, now)
                                 if candidate_at is not None else None),
        ))
        await self.hass.async_block_till_done()
        return now

    async def test_presence_before_closure_waiting_for_runtime_lock_starts_once(self):
        runtime, source = await self.start_absent_direct_session()
        await self.semantic_door(runtime, "delayed-entry-open", Kind.DOOR_OPEN)
        entered = asyncio.Event()
        callback = runtime.presence_adapter.callback

        async def received(report):
            entered.set()
            await callback(report)

        with patch.object(runtime.presence_adapter, "callback", received):
            async with runtime._lock:
                self.hass.states.async_set(source, "on")
                state = self.hass.states.get(source)
                await asyncio.wait_for(entered.wait(), async_timeout("service"))
                self.assertEqual(runtime.controller.direct_presence.occupancy, "absent")
                candidate_at = runtime._clock()
                await asyncio.sleep(0)
                closed_at = runtime._clock()
                self.assertLess(state.last_changed, closed_at)
                self.assertLessEqual(state.last_changed, candidate_at)
                # The lock owner processes the recognized closure while the real
                # HA presence callback is waiting to enter the same runtime.
                runtime._process_event(Event(
                    "delayed-entry-close", runtime.session.session_id,
                    Kind.DOOR_CLOSE, closed_at, closed_at,
                    door_close_evidence=DoorCloseEvidence(candidate_at, closed_at),
                ))
                await runtime._cycle()
                self.assertIsNone(runtime.session.timeline.active)
            await self.hass.async_block_till_done()

        gang = runtime.session.timeline.active
        self.assertIsNotNone(gang)
        self.assertEqual(gang.started_at, closed_at)
        self.assertEqual(gang.confirmation, Confirmation.CONFIRMED)
        self.assertTrue(runtime.controller.regulation_inputs.gang_heat_demand)
        self.assertEqual(runtime.controller.direct_presence.timing.effective_at, state.last_changed)
        await runtime.tick()
        self.assertEqual(runtime.session.timeline.active.gang_id, gang.gang_id)
        self.assertEqual(sum(e.kind == "gang_confirmed" for e in runtime.consumer_events), 1)

        # The binary absence can precede a slow thermal exit indication.
        exit_opened_at = runtime._clock()
        self.hass.states.async_set(source, "off")
        await self.hass.async_block_till_done()
        absent_at = runtime.controller.direct_presence.timing.effective_at
        self.assertEqual(runtime.session.timeline.active.gang_id, gang.gang_id)
        opened_at = await self.semantic_door(
            runtime, "slow-exit-open", Kind.DOOR_OPEN, effective_at=exit_opened_at,
        )
        self.assertLessEqual(exit_opened_at, absent_at)
        self.assertLess(absent_at, opened_at)
        await self.semantic_door(runtime, "slow-exit-ventilation", Kind.VENTILATION)
        self.assertIsNone(runtime.session.timeline.active)
        self.assertEqual(runtime.session.timeline.gang_count, 1)
        self.assertEqual(sum(e.kind == "gang_ended" for e in runtime.consumer_events), 1)

    async def test_new_presence_after_empty_closure_requires_another_door_cycle(self):
        runtime, source = await self.start_absent_direct_session()
        await self.semantic_door(runtime, "empty-open", Kind.DOOR_OPEN)
        closed_at = await self.semantic_door(runtime, "empty-close", Kind.DOOR_CLOSE)
        self.hass.states.async_set(source, "on")
        await self.hass.async_block_till_done()
        self.assertGreater(runtime.controller.direct_presence.timing.effective_at, closed_at)
        self.assertIsNone(runtime.session.timeline.active)
        self.assertFalse(any(e.kind == "gang_confirmed" for e in runtime.consumer_events))

        await self.semantic_door(runtime, "new-entry-open", Kind.DOOR_OPEN)
        new_closed_at = await self.semantic_door(runtime, "new-entry-close", Kind.DOOR_CLOSE)
        self.assertEqual(runtime.session.timeline.active.started_at, new_closed_at)
        self.assertEqual(sum(e.kind == "gang_confirmed" for e in runtime.consumer_events), 1)

    async def test_temperature_wait_and_return_during_cooling_through_ha_inputs(self):
        runtime = await self.configure()
        source = runtime.configuration.bindings.values["presence"]
        minimum = runtime.configuration.parameters.values["sauna_min_temperature_c"]

        async def temperature(value):
            for role in ("upper_temperature", "lower_temperature"):
                entity_id = runtime.configuration.bindings.values[role]
                state = self.hass.states.get(entity_id)
                self.hass.states.async_set(entity_id, str(value), state.attributes)
            await self.hass.async_block_till_done()

        async def presence(value):
            self.hass.states.async_set(source, value)
            await self.hass.async_block_till_done()

        async def door(name, kind):
            await self.semantic_door(runtime, name, kind)

        await temperature(minimum - 1)
        await runtime.set_operation(True)
        await door("entry-open", Kind.DOOR_OPEN)
        await door("entry-close", Kind.DOOR_CLOSE)
        self.assertIsNone(runtime.session.timeline.active)
        threshold_before = runtime._clock()
        await temperature(minimum)
        first = runtime.session.timeline.active
        self.assertIsNotNone(first)
        self.assertGreaterEqual(first.started_at, threshold_before)
        self.assertLessEqual(first.started_at, runtime._clock())

        await door("first-exit", Kind.DOOR_OPEN)
        await presence("off")
        await temperature(minimum - 2)
        await door("first-ventilation", Kind.VENTILATION)
        self.assertEqual(runtime.session.timeline.gang_count, 1)
        self.assertIsNotNone(runtime.session.after_run)

        await presence("on")
        await door("return-close", Kind.DOOR_CLOSE)
        self.assertIsNone(runtime.session.timeline.active)
        self.assertIsNotNone(runtime.session.after_run)
        await temperature(minimum)
        second = runtime.session.timeline.active
        self.assertIsNotNone(second)
        self.assertNotEqual(second.gang_id, first.gang_id)
        self.assertIsNone(runtime.session.after_run)

        await door("second-exit", Kind.DOOR_OPEN)
        await presence("off")
        await door("second-ventilation", Kind.VENTILATION)
        self.assertEqual(runtime.session.timeline.gang_count, 2)
        await runtime.archive.flush()
        records = runtime.archive.read(runtime.session.session_id)["records"]
        events = [item["payload"]["kind"] for item in records
                  if item["kind"] == "consumer_event"]
        self.assertEqual(events.count("gang_started"), 2)
        self.assertEqual(events.count("gang_ended"), 2)

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
