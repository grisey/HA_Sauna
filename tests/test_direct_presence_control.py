"""Direkte Präsenz benötigt vollständige Türvorgänge, keine Wartefristen."""
import unittest

from test_cooling import at, controller
from test_foundation import event

from custom_components.ha_sauna.archive import plain, session_has_gangs
from custom_components.ha_sauna.core.presence import binary_presence
from custom_components.ha_sauna.core.timeline import Confirmation, Kind


class DirectPresenceControlTests(unittest.TestCase):
    def setUp(self):
        self.c = controller()
        self.c.presence_source = "ha_presence"
        self.c.presence_entity = "binary_sensor.presence"

    def report(self, state, second, *, effective=None, source=None):
        return self.c.observe_direct_presence(binary_presence(
            source or "binary_sensor.presence", state,
            at(second if effective is None else effective), at(second),
        ), at(second))

    def door(self, kind, second):
        return self.c.process(event(f"{kind}:{second}", kind, second))

    def start(self):
        self.door(Kind.DOOR_OPEN, 1)
        self.report("on", 2)
        self.assertIsNone(self.c.session.timeline.active)
        self.door(Kind.DOOR_CLOSE, 3)
        self.assertEqual(self.c.session.timeline.active.confirmation, Confirmation.CONFIRMED)

    def test_presence_and_unpaired_closure_cannot_start(self):
        self.report("on", 1)
        self.door(Kind.DOOR_CLOSE, 2)
        self.c.advance(at(200))
        self.assertIsNone(self.c.session.timeline.active)
        self.assertFalse(self.c.regulation_inputs.temporary_door_heat)

    def test_complete_entry_starts_without_delay_or_infusion(self):
        self.start()
        gang = self.c.session.timeline.active
        self.assertEqual(gang.started_at, at(3))
        self.assertEqual(gang.detected_at, at(3))
        self.assertEqual(gang.infusion_events, ())
        self.assertTrue(session_has_gangs(plain(self.c.session)))
        self.c.advance(at(800))
        self.assertEqual(self.c.session.timeline.active.gang_id, gang.gang_id)
        self.assertEqual(sum(e.kind == "gang_confirmed" for e in self.c.consumer_events), 1)

    def test_absence_without_new_door_never_ends_round(self):
        self.start()
        self.report("off", 4)
        self.c.advance(at(1000))
        self.assertIsNotNone(self.c.session.timeline.active)
        self.assertEqual(self.c.session.timeline.gang_count, 0)
        self.assertTrue(self.c.regulation_inputs.gang_heat_demand)

    def test_exit_needs_close_and_is_counted_once(self):
        self.start()
        self.door(Kind.DOOR_OPEN, 5)
        self.report("off", 6)
        self.assertIsNotNone(self.c.session.timeline.active)
        self.door(Kind.VENTILATION, 7)
        self.assertIsNotNone(self.c.session.timeline.active)
        self.door(Kind.DOOR_CLOSE, 8)
        self.assertIsNone(self.c.session.timeline.active)
        self.assertEqual(self.c.session.timeline.gang_count, 1)
        self.assertIsNotNone(self.c.session.after_run)
        self.report("on", 9)
        self.assertIsNone(self.c.session.timeline.active)
        self.assertEqual(self.c.session.timeline.gang_count, 1)

    def test_delayed_absence_after_exit_close(self):
        self.start()
        self.door(Kind.DOOR_OPEN, 5)
        self.door(Kind.DOOR_CLOSE, 6)
        self.assertIsNotNone(self.c.session.timeline.active)
        self.report("off", 7)
        self.assertEqual(self.c.session.timeline.completed[0].ended_at, at(7))

    def test_unknown_never_finishes_and_cannot_heat_from_round(self):
        self.start()
        self.door(Kind.DOOR_OPEN, 5)
        self.report("unavailable", 6)
        self.door(Kind.DOOR_CLOSE, 7)
        self.assertIsNotNone(self.c.session.timeline.active)
        self.assertFalse(self.c.regulation_inputs.gang_heat_demand)
        self.assertEqual(self.c.session.timeline.gang_count, 0)

    def test_existing_presence_does_not_lend_itself_to_later_entry(self):
        self.report("on", 1)
        self.door(Kind.DOOR_OPEN, 2)
        self.door(Kind.DOOR_CLOSE, 3)
        self.assertIsNone(self.c.session.timeline.active)

    def test_minimum_temperature_blocks_entry_but_not_exit(self):
        self.c.set_temperature(25, at(0))
        self.door(Kind.DOOR_OPEN, 1)
        self.report("on", 2)
        self.door(Kind.DOOR_CLOSE, 3)
        self.c.set_temperature(70, at(4))
        self.assertIsNone(self.c.session.timeline.active)
        self.door(Kind.DOOR_OPEN, 5)
        self.report("on", 6)
        self.door(Kind.DOOR_CLOSE, 7)
        self.assertIsNotNone(self.c.session.timeline.active)
        self.c.set_temperature(25, at(8))
        self.door(Kind.DOOR_OPEN, 9)
        self.report("off", 10)
        self.door(Kind.DOOR_CLOSE, 11)
        self.assertEqual(self.c.session.timeline.gang_count, 1)

    def test_proxy_cannot_start_and_infusion_only_annotates(self):
        self.door(Kind.DOOR_CLOSE, 1)
        for i, kind in enumerate((Kind.PERSON_STRONG, Kind.INFUSION), 2):
            self.assertFalse(self.door(kind, i).changed)
        self.door(Kind.DOOR_OPEN, 5)
        self.report("on", 6)
        self.door(Kind.DOOR_CLOSE, 7)
        self.door(Kind.INFUSION, 8)
        self.assertEqual(len(self.c.session.timeline.active.infusion_events), 1)
        self.assertEqual(sum(e.kind == "gang_confirmed" for e in self.c.consumer_events), 1)

    def test_door_episode_across_operation_pause_cannot_start(self):
        self.door(Kind.DOOR_OPEN, 1)
        self.c.set_operation(False, at(2))
        self.report("on", 3)
        self.c.set_operation(True, at(4))
        self.door(Kind.DOOR_CLOSE, 5)
        self.assertIsNone(self.c.session.timeline.active)
        self.door(Kind.DOOR_OPEN, 6)
        self.report("on", 7)
        self.door(Kind.DOOR_CLOSE, 8)
        self.assertIsNotNone(self.c.session.timeline.active)

    def test_complete_door_episode_while_off_cannot_start_on_resume(self):
        self.c.set_operation(False, at(1))
        self.door(Kind.DOOR_OPEN, 2)
        self.report("on", 3)
        self.door(Kind.DOOR_CLOSE, 4)
        self.c.set_operation(True, at(5))
        self.assertIsNone(self.c.session.timeline.active)
        self.door(Kind.DOOR_OPEN, 6)
        self.report("off", 7)
        self.report("on", 8)
        self.door(Kind.DOOR_CLOSE, 9)
        self.assertIsNotNone(self.c.session.timeline.active)

    def test_wrong_entity_and_old_report_do_not_replace_current(self):
        self.start()
        self.assertFalse(self.report("off", 4, source="binary_sensor.other"))
        self.assertFalse(self.report("off", 5, effective=1))
        self.assertTrue(self.c.regulation_inputs.gang_heat_demand)


class DirectPresenceRuntimeTests(unittest.TestCase):
    def test_selected_source_drives_controller_archive_and_status(self):
        import asyncio
        from dataclasses import replace
        from datetime import timedelta
        from test_presence_regressions import START, _runtime
        from custom_components.ha_sauna.bindings import Bindings
        from custom_components.ha_sauna.runtime import SaunaRuntime
        from custom_components.ha_sauna.core.timeline import Event

        async def exercise():
            clock = [START]
            configuration = _runtime(clock).configuration
            configuration = replace(configuration, presence_source="ha_presence",
                bindings=Bindings({**configuration.bindings.values,
                                   "presence":"binary_sensor.presence"}))
            runtime = SaunaRuntime(configuration, clock=lambda:clock[0])
            runtime.controller.set_temperature(70, START)
            await runtime.set_operation(True)
            sid = runtime.session.session_id
            clock[0] = START + timedelta(seconds=1)
            runtime._process_event(Event("entry-open", sid, Kind.DOOR_OPEN, clock[0], clock[0]))
            clock[0] += timedelta(seconds=1)
            await runtime.accept_presence(binary_presence("binary_sensor.presence", "on", clock[0], clock[0]))
            self.assertIsNone(runtime.session.timeline.active)
            clock[0] += timedelta(seconds=1)
            runtime._process_event(Event("entry-close", sid, Kind.DOOR_CLOSE, clock[0], clock[0]))
            runtime.persist()
            self.assertEqual(runtime.session.timeline.active.confirmation, Confirmation.CONFIRMED)
            self.assertEqual(runtime.presence_status["effective_source"], "ha_presence")
            self.assertEqual(runtime.presence_status["current"].occupancy, "present")
            self.assertEqual(sum(e.kind == "gang_confirmed" for e in runtime.consumer_events), 1)
        asyncio.run(exercise())
