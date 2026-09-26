"""Regressions for proxy-presence delivery at runtime boundaries."""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from dataclasses import replace
import unittest
from datetime import UTC, datetime, timedelta

from custom_components.ha_sauna.archive import plain
from custom_components.ha_sauna.bindings import Bindings
from custom_components.ha_sauna.core.button import END_HOLD
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.presence import ProxyPresenceSource
from custom_components.ha_sauna.core.timeline import Door, Event, Kind
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
    def test_explicit_session_end_retracts_evidence_from_completed_session(self):
        async def exercise():
            clock = [START]
            runtime = _runtime(clock)
            runtime.controller.begin_session("explicit-end", START)
            runtime.controller.set_temperature(75, START)
            runtime._sync_detector()
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

        async def exercise():
            clock = [START]
            runtime = _runtime(clock)
            runtime.controller.begin_session("failed-end", START)
            runtime.controller.set_temperature(75, START)
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
            runtime.controller._session = replace(
                runtime.session,
                timeline=replace(runtime.session.timeline, door=Door.CLOSED),
            )
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

    def test_close_publishes_gang_end_before_archive_close(self):
        tmp_path = Path(self.enterContext(TemporaryDirectory()))
        async def exercise():
            clock = [START]
            runtime = _runtime(clock)
            await runtime.start_archive(tmp_path / "synthetic.sqlite", "entry")
            runtime.controller.begin_session("shutdown", START)
            runtime.controller.set_temperature(75, START)
            runtime._sync_detector()
            runtime.controller._session = replace(
                runtime.session,
                timeline=replace(runtime.session.timeline, door=Door.CLOSED),
            )
            runtime._process_event(_event(runtime, Kind.PERSON_STRONG, 10, "person"))
            clock[0] = START + timedelta(seconds=10)
            runtime.notify()
            runtime._process_event(_event(runtime, Kind.INFUSION, 20, "infusion"))
            clock[0] = START + timedelta(seconds=20)
            runtime.notify()
            before = len(runtime.consumer_events)

            class TeardownDevice:
                async def close(self):
                    return None

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
            runtime.controller._session = replace(
                runtime.session,
                timeline=replace(runtime.session.timeline, door=Door.CLOSED),
            )
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
