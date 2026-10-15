"""Regressions for proxy-presence delivery at runtime boundaries."""

import asyncio
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from custom_components.ha_sauna.archive import plain
from custom_components.ha_sauna.bindings import Bindings
from custom_components.ha_sauna.core.button import END_HOLD
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.presence import binary_presence
from custom_components.ha_sauna.core.presence import ProxyPresenceSource
from custom_components.ha_sauna.core.timeline import Event, Kind
from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime


START = datetime(2026, 9, 26, 12, tzinfo=UTC)
BINDINGS = Bindings(
    {
        "upper_temperature": "sensor.top_t",
        "upper_humidity": "sensor.top_h",
        "lower_temperature": "sensor.bottom_t",
        "lower_humidity": "sensor.bottom_h",
        "heater": "switch.heater",
        "light": "light.sauna",
        "control_input": "event.button",
    }
)


def _runtime(clock):
    return SaunaRuntime(
        Configuration(BINDINGS, Parameters({"target_temperature_c": 80})),
        clock=lambda: clock[0],
    )


def _event(runtime, kind, seconds, event_id):
    at = START + timedelta(seconds=seconds)
    return Event(event_id, runtime.session.session_id, kind, at, at)


class PresenceRegressionTests(unittest.TestCase):
    def test_proxy_person_uses_original_event_for_gang_and_presence_projection(self):
        clock = [START]
        runtime = _runtime(clock)
        runtime.controller.begin_session("original-person", START)
        runtime.controller.set_temperature(75, START)
        runtime._process_event(_event(runtime, Kind.DOOR_OPEN, 1, "entry-open"))
        runtime._process_event(_event(runtime, Kind.DOOR_CLOSE, 2, "entry-close"))
        clock[0] = START + timedelta(seconds=5)
        person = Event(
            "original-person", runtime.session.session_id, Kind.PERSON_STRONG,
            START + timedelta(seconds=3), clock[0], START + timedelta(seconds=4),
        )
        with patch.object(runtime.controller, "process", wraps=runtime.controller.process) as process:
            result = runtime._process_event(person)
        self.assertTrue(result.changed)
        self.assertIs(process.call_args.args[0], person)
        self.assertIs(runtime.session.timeline.processed[-1], person)
        self.assertEqual(runtime.session.timeline.active.recognition_event_id, person.event_id)
        self.assertEqual(runtime.session.timeline.active.started_at, START + timedelta(seconds=2))
        self.assertEqual(runtime.presence.current.source_ref, person.event_id)
        self.assertEqual(runtime.presence.current.received_at, person.detected_at)
        self.assertEqual(runtime.presence.current.effective_at, person.effective_at)

    def test_selected_occupancy_has_same_gang_effect_for_different_entities(self):
        async def exercise(source):
            clock = [START]
            configuration = replace(
                _runtime(clock).configuration,
                presence_source="ha_presence",
                bindings=Bindings({**BINDINGS.values, "presence": source}),
            )
            runtime = SaunaRuntime(configuration, clock=lambda: clock[0])
            runtime.controller.begin_session("normalized-occupancy", START)
            runtime.controller.set_temperature(75, START)

            async def report(state, second, report_id, *, entity=None, assertion=None,
                             effective=None):
                clock[0] = START + timedelta(seconds=second)
                value = binary_presence(
                    entity or source, state,
                    START + timedelta(seconds=second if effective is None else effective),
                    clock[0],
                )
                value = replace(value, report_id=report_id)
                if assertion is not None:
                    value = replace(value, assertion=assertion)
                await runtime.accept_presence(value)
                return value

            def door(kind, second, event_id):
                clock[0] = START + timedelta(seconds=second)
                runtime._process_event(_event(runtime, kind, second, event_id))

            with patch.object(
                runtime.controller, "consume_occupancy_input",
                wraps=runtime.controller.consume_occupancy_input,
            ) as consume:
                door(Kind.DOOR_OPEN, 1, "entry-open")
                selected = await report("on", 2, "selected-present")
                normalized = consume.call_args.args[0]
                door(Kind.DOOR_CLOSE, 3, "entry-close")
                gang = runtime.session.timeline.active
                self.assertIsNotNone(gang)
                self.assertTrue(runtime.controller.regulation_inputs.gang_heat_demand)

                await report("off", 4, "wrong-entity", entity="binary_sensor.unselected")
                await report("off", 5, "wrong-assertion", assertion="provisional_proxy")
                self.assertEqual(consume.call_count, 1)
                self.assertIs(runtime.presence_status["current"], selected)
                self.assertTrue(runtime.controller.regulation_inputs.gang_heat_demand)
                self.assertEqual(runtime.session.timeline.active.gang_id, gang.gang_id)

                await report("unknown", 6, "selected-unknown")
                self.assertEqual(runtime.controller.direct_presence.occupancy, "unknown")
                self.assertFalse(runtime.controller.direct_presence.qualified_presence)
                self.assertFalse(runtime.controller.regulation_inputs.gang_heat_demand)
                self.assertEqual(runtime.session.timeline.active.gang_id, gang.gang_id)

                current = await report("on", 7, "selected-return")
                await report("off", 8, "stale-absence", effective=1)
                self.assertIs(runtime.presence_status["current"], current)
                self.assertTrue(runtime.controller.regulation_inputs.gang_heat_demand)
                door(Kind.DOOR_OPEN, 9, "exit-open")
                await report("off", 10, "selected-absent")
                self.assertEqual(runtime.session.timeline.active.gang_id, gang.gang_id)
                door(Kind.VENTILATION, 11, "exit-ventilation")
                self.assertIsNone(runtime.session.timeline.active)
                self.assertEqual(runtime.session.timeline.gang_count, 1)
                self.assertEqual(runtime.session.timeline.completed[0].ended_at, clock[0])
                self.assertIsNone(normalized.timing.first_usable_at)
                self.assertEqual(normalized.timing.received_at, selected.received_at)
                self.assertEqual(normalized.timing.effective_at, selected.effective_at)
                return normalized, tuple(
                    (item.kind, item.effective_at) for item in runtime.controller.consumer_events
                )

        first = asyncio.run(exercise("binary_sensor.first_presence"))
        second = asyncio.run(exercise("binary_sensor.second_presence"))
        self.assertEqual(first, second)

    def test_unqualified_person_keeps_booking_and_infusion_annotates_occupancy_gang(self):
        async def exercise():
            clock = [START]
            configuration = replace(
                _runtime(clock).configuration,
                presence_source="ha_presence",
                bindings=Bindings({**BINDINGS.values, "presence": "binary_sensor.occupancy"}),
            )
            runtime = SaunaRuntime(configuration, clock=lambda: clock[0])
            runtime.controller.begin_session("qualified-events", START)
            runtime.controller.set_temperature(75, START)
            clock[0] = START + timedelta(seconds=1)
            person = _event(runtime, Kind.PERSON_STRONG, 1, "unqualified-person")
            result = runtime._process_event(person)
            self.assertFalse(result.changed)
            self.assertIsNone(runtime.session.timeline.active)
            self.assertIs(runtime.session.timeline.processed[-1], person)
            self.assertEqual(runtime.controller._last_at, person.booking_at)
            self.assertIsNone(runtime.presence.current)

            clock[0] = START + timedelta(seconds=2)
            runtime._process_event(_event(runtime, Kind.DOOR_OPEN, 2, "entry-open"))
            clock[0] = START + timedelta(seconds=3)
            await runtime.accept_presence(binary_presence(
                "binary_sensor.occupancy", "on", clock[0], clock[0],
            ))
            clock[0] = START + timedelta(seconds=4)
            runtime._process_event(_event(runtime, Kind.DOOR_CLOSE, 4, "entry-close"))
            gang_id = runtime.session.timeline.active.gang_id
            clock[0] = START + timedelta(seconds=5)
            repeated = _event(runtime, Kind.PERSON_WEAK, 5, "unqualified-repeat")
            self.assertFalse(runtime._process_event(repeated).changed)
            self.assertIs(runtime.session.timeline.processed[-1], repeated)
            self.assertEqual(runtime.session.timeline.active.gang_id, gang_id)

            clock[0] = START + timedelta(seconds=6)
            infusion = _event(runtime, Kind.INFUSION, 6, "annotating-infusion")
            self.assertTrue(runtime._process_event(infusion).changed)
            self.assertEqual(runtime.session.timeline.active.gang_id, gang_id)
            self.assertEqual(runtime.session.timeline.active.infusion_events, (infusion,))
            self.assertEqual(sum(item.kind == "gang_confirmed"
                                 for item in runtime.controller.consumer_events), 1)

        asyncio.run(exercise())

    def test_current_status_stays_bounded_as_external_history_grows(self):
        runtime = SaunaRuntime(Configuration(
            Bindings({**BINDINGS.values, "presence": "binary_sensor.occupancy"}),
            Parameters({}), presence_source="proxy",
        ), clock=lambda: START)
        for second in range(1000):
            at = START + timedelta(seconds=second)
            runtime.presence.accept(binary_presence(
                "binary_sensor.occupancy", "on" if second % 2 else "off",
                at, at,
            ))
        status = runtime.presence_status
        self.assertNotIn("observations", status)
        self.assertEqual(status["external"]["binary_sensor.occupancy"].occupancy, "present")
        self.assertEqual(len(runtime.presence.observations), 500)

    def test_stopped_recording_retracts_evidence_and_completes_at_deadline(self):
        async def exercise():
            clock = [START]
            runtime = _runtime(clock)
            runtime.controller.begin_session("explicit-end", START)
            runtime.controller.set_temperature(75, START)
            runtime._sync_detector()
            runtime._process_event(_event(runtime, Kind.DOOR_OPEN, 1, "open"))
            runtime._process_event(_event(runtime, Kind.DOOR_CLOSE, 5, "close"))
            clock[0] = START + timedelta(seconds=10)
            runtime._process_event(_event(runtime, Kind.PERSON_STRONG, 10, "person"))
            runtime.notify()
            clock[0] = START + timedelta(seconds=20)
            runtime._process_event(_event(runtime, Kind.INFUSION, 20, "infusion"))
            runtime.notify()
            clock[0] = START + timedelta(seconds=30)
            await runtime._apply_button_action(END_HOLD, clock[0])
            await runtime._cycle()
            self.assertEqual(runtime.session.session_id, "explicit-end")
            self.assertFalse(runtime.session.operation_enabled)
            self.assertEqual(runtime.presence.current.occupancy, "unknown")
            self.assertEqual(runtime.presence.current.source_ref, "person")
            deadline = next(d for d in runtime.session.deadlines if d.purpose == "session_gap")
            clock[0] = deadline.due_at
            await runtime._cycle()
            self.assertEqual(runtime.controller.completed_sessions[-1].ended_at, deadline.due_at)
            return runtime

        runtime = asyncio.run(exercise())
        self.assertIsNone(runtime.session)
        self.assertEqual(len(runtime.controller.completed_sessions), 1)
        self.assertEqual(runtime.presence.current.occupancy, "unknown")
        self.assertEqual(runtime.presence.current.source_ref, "person")
        self.assertEqual(
            sum(event.kind == "gang_ended" for event in runtime.consumer_events), 1
        )

    def test_archive_failure_during_end_delivery_still_closes_heater_and_archive(self):
        class FailingArchive:
            closed = False

            def append(self, *args, **kwargs):
                raise OSError("synthetic archive failure")

            async def close(self):
                self.closed = True

        class ClosingDevice:
            closed = False

            async def close(self):
                self.closed = True

            async def prepare_light_handoff(self):
                return True

        async def exercise():
            clock = [START]
            runtime = _runtime(clock)
            runtime.controller.begin_session("failed-end", START)
            runtime.controller.set_temperature(75, START)
            runtime._process_event(_event(runtime, Kind.DOOR_OPEN, 1, "open"))
            runtime._process_event(_event(runtime, Kind.DOOR_CLOSE, 5, "close"))
            clock[0] = START + timedelta(seconds=10)
            runtime._process_event(_event(runtime, Kind.PERSON_STRONG, 10, "person"))
            runtime.notify()
            runtime.archive = FailingArchive()
            runtime.device = ClosingDevice()
            clock[0] = START + timedelta(seconds=30)
            with self.assertRaises(ExceptionGroup):
                await runtime.close()
            return runtime

        runtime = asyncio.run(exercise())
        self.assertTrue(runtime.device.closed)
        self.assertTrue(runtime.archive.closed)
        self.assertFalse(runtime.controller.last_decision.heat)

    def test_old_retraction_does_not_replace_newer_proxy_presence(self):
        async def exercise():
            clock = [START]
            runtime = _runtime(clock)
            runtime.controller.begin_session("batch", START)
            runtime.controller.set_temperature(75, START)
            runtime._sync_detector()
            runtime._process_event(_event(runtime, Kind.DOOR_OPEN, 1, "old-open"))
            runtime._process_event(_event(runtime, Kind.DOOR_CLOSE, 5, "old-close"))
            runtime._process_event(_event(runtime, Kind.PERSON_STRONG, 10, "old-person"))
            clock[0] = START + timedelta(seconds=10)
            runtime.notify()

            # A delayed detector sample retracts the old candidate before it reports
            # a new candidate in this same serialized runtime cycle.
            received = START + timedelta(seconds=800)
            clock[0] = received
            for kind, effective, event_id in (
                (Kind.DOOR_OPEN, 740, "new-open"),
                (Kind.DOOR_CLOSE, 745, "new-close"),
                (Kind.PERSON_STRONG, 800, "new-person"),
            ):
                runtime._process_event(
                    Event(
                        event_id,
                        "batch",
                        kind,
                        START + timedelta(seconds=effective),
                        received,
                    )
                )
            runtime.notify()
            return runtime

        runtime = asyncio.run(exercise())

        assert runtime.session.timeline.active.recognition_event_id == "new-person"
        assert runtime.controller.regulation_inputs.gang_veto
        assert runtime.presence.current.occupancy == "present"
        assert runtime.presence.current.source_ref == "new-person"
        assert not any(
            event.kind == "occupancy"
            and event.presence is not None
            and event.presence.assertion == "proxy_retraction"
            and event.presence.source_ref == "old-person"
            for event in runtime.consumer_events
        )

    def test_unload_saves_final_session_before_at_and_after_gap_deadline(self):
        async def exercise(path, seconds):
            clock = [START]
            runtime = SaunaRuntime(
                Configuration(BINDINGS, Parameters({"target_temperature_c": 80})),
                clock=lambda: clock[0],
            )
            await runtime.start_archive(path, "entry")
            runtime.controller.begin_session("shutdown-gap", START)
            runtime.controller.set_temperature(80, START)
            for kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE, Kind.INFUSION):
                runtime.controller.process(Event(kind.value, "shutdown-gap", kind, START, START))
            clock[0] = START + timedelta(seconds=1)
            runtime.controller.set_operation(False, clock[0])
            runtime.notify()

            class TeardownDevice:
                command = False
                command_error = False

                async def close(self):
                    pass

                async def prepare_light_handoff(self):
                    return True

                def invalidate_historical_warmup(self):
                    pass

            runtime.device = TeardownDevice()
            clock[0] = START + timedelta(seconds=seconds)
            await runtime.close()
            return runtime.archive.read("shutdown-gap")

        with TemporaryDirectory() as directory:
            gap = Parameters({}).seconds("session_gap_minutes")
            for seconds in (gap, gap + 1, gap + 2):
                with self.subTest(seconds=seconds):
                    archived = asyncio.run(exercise(
                        Path(directory) / f"{seconds}.sqlite", seconds
                    ))
                    ended_at = (START + timedelta(seconds=min(seconds, gap + 1))).isoformat()
                    self.assertEqual(archived["session"]["ended_at"], ended_at)
                    finals = [record for record in archived["records"]
                              if record["kind"] == "session"
                              and record["payload"].get("ended_at")]
                    self.assertEqual(len(finals), 1)
                    self.assertEqual(finals[0]["payload"]["ended_at"], ended_at)

    def test_close_publishes_gang_end_before_archive_close(self):
        tmp_path = Path(self.enterContext(TemporaryDirectory()))
        async def exercise():
            clock = [START]
            runtime = _runtime(clock)
            await runtime.start_archive(tmp_path / "synthetic.sqlite", "entry")
            runtime.controller.begin_session("shutdown", START)
            runtime.controller.set_temperature(75, START)
            runtime._sync_detector()
            runtime._process_event(_event(runtime, Kind.DOOR_OPEN, 1, "open"))
            runtime._process_event(_event(runtime, Kind.DOOR_CLOSE, 5, "close"))
            runtime._process_event(_event(runtime, Kind.PERSON_STRONG, 10, "person"))
            clock[0] = START + timedelta(seconds=10)
            runtime.notify()
            runtime._process_event(_event(runtime, Kind.INFUSION, 20, "infusion"))
            clock[0] = START + timedelta(seconds=20)
            runtime.notify()
            before = len(runtime.consumer_events)

            class TeardownDevice:
                command = False
                command_error = False

                async def close(self):
                    return None

                async def prepare_light_handoff(self):
                    return True

            runtime.device = TeardownDevice()
            clock[0] = START + timedelta(seconds=30)
            await runtime.close()
            return runtime, before, runtime.archive.read("shutdown")

        runtime, before, archive = asyncio.run(exercise())

        assert len(runtime.consumer_events) == before + 2
        assert runtime.consumer_events[-2].kind == "gang_ended"
        assert runtime.consumer_events[-2].event_id in runtime._consumer_ids
        assert runtime.consumer_events[-1].kind == "occupancy"
        assert runtime.consumer_events[-1].presence.occupancy == "unknown"
        assert any(
            record["kind"] == "consumer_event"
            and record["payload"]["kind"] == "gang_ended"
            for record in archive["records"]
        )
        assert any(
            record["kind"] == "presence"
            and record["payload"]["occupancy"] == "unknown"
            and record["payload"]["source_ref"] == "person"
            for record in archive["records"]
        )
        assert plain(runtime.presence.current)["occupancy"] == "unknown"
        assert runtime.presence.current.source_ref == "person"

    def test_old_gang_end_does_not_retract_new_proxy_evidence(self):
        async def exercise():
            clock = [START]
            runtime = _runtime(clock)
            runtime.controller.begin_session("end-batch", START)
            runtime.controller.set_temperature(75, START)
            runtime._sync_detector()
            runtime._process_event(_event(runtime, Kind.DOOR_OPEN, 1, "old-open"))
            runtime._process_event(_event(runtime, Kind.DOOR_CLOSE, 5, "old-close"))
            runtime._process_event(_event(runtime, Kind.PERSON_STRONG, 10, "old-person"))
            clock[0] = START + timedelta(seconds=10)
            runtime.notify()
            runtime._process_event(_event(runtime, Kind.INFUSION, 20, "infusion"))
            clock[0] = START + timedelta(seconds=20)
            runtime.notify()

            clock[0] = START + timedelta(seconds=30)
            runtime._process_event(_event(runtime, Kind.OPERATION_OFF, 30, "operation-off"))
            # Delivery is deliberately delayed.  A later proxy observation must
            # not be invalidated by the old gang end.
            runtime._record_presence(
                ProxyPresenceSource.present(
                    _event(runtime, Kind.PERSON_STRONG, 31, "new-person")
                )
            )
            runtime.notify()
            return runtime

        runtime = asyncio.run(exercise())

        assert any(event.kind == "gang_ended" for event in runtime.consumer_events)
        assert runtime.presence.current.occupancy == "present"
        assert runtime.presence.current.source_ref == "new-person"
