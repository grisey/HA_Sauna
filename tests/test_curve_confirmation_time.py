"""Physische Zuordnung und bisherige Bestätigung bleiben getrennte Zeiten."""

from datetime import UTC, timedelta, timezone
import unittest
from unittest.mock import Mock, patch

from test_cooling import at, controller
from test_foundation import event
from test_presence_handover import ENTITY, runtime_with_clock

from custom_components.ha_sauna.archive import plain
from custom_components.ha_sauna.core.detector import Detection
from custom_components.ha_sauna.core.presence import binary_presence
from custom_components.ha_sauna.core.timeline import Confirmation, DoorCloseEvidence, Event, Kind


class CurveConfirmationTimeTests(unittest.TestCase):
    def test_event_trace_is_normalized_bounded_and_optional(self):
        for trace_at in (at(1), at(11)):
            with self.subTest(trace_at=trace_at), self.assertRaises(ValueError):
                Event(
                    "close",
                    "s",
                    Kind.DOOR_CLOSE,
                    at(2),
                    at(12),
                    booking_at=at(10),
                    trace_at=trace_at,
                )
        normalized = Event(
            "close",
            "s",
            Kind.DOOR_CLOSE,
            at(2),
            at(12),
            booking_at=at(10),
            trace_at=at(9).astimezone(timezone(timedelta(hours=1))),
        )
        self.assertIs(normalized.trace_at.tzinfo, UTC)
        self.assertEqual(normalized.trace_at, at(9))
        self.assertEqual(normalized.confirmation_at, at(9))
        legacy = Event(
            "legacy-close", "s", Kind.DOOR_CLOSE, at(2), at(12), booking_at=at(10)
        )
        self.assertIsNone(legacy.trace_at)
        self.assertEqual(legacy.confirmation_at, at(2))

    def test_proxy_confirmation_deadline_uses_trace_with_legacy_effective_fallback(
        self,
    ):
        for trace_at, control_second in ((None, 2), (at(10), 10)):
            with self.subTest(trace_at=trace_at):
                c = controller()
                c.process(event("entry-open", Kind.DOOR_OPEN, 1))
                close = Event(
                    "entry-close",
                    "s",
                    Kind.DOOR_CLOSE,
                    at(2),
                    at(10),
                    booking_at=at(10),
                    trace_at=trace_at,
                )
                c.process(close)
                c.process(event("person", Kind.PERSON_STRONG, 11))
                gang = c.session.timeline.active
                self.assertIsNotNone(gang)
                self.assertEqual(gang.started_at, at(2))
                self.assertEqual(gang.confirmation, Confirmation.PROVISIONAL)
                due = at(control_second) + timedelta(
                    seconds=c.parameters.seconds("confirmation_minutes"),
                )
                deadline = next(
                    d for d in c.session.deadlines if d.purpose == "confirmation"
                )
                self.assertEqual(deadline.due_at, due)
                self.assertEqual(deadline.token, gang.gang_id)
                c.advance(due - timedelta.resolution)
                self.assertEqual(c.session.timeline.active.gang_id, gang.gang_id)
                c.advance(due)
                self.assertIsNone(c.session.timeline.active)
                self.assertEqual(c.session.timeline.retracted, (gang,))
                self.assertEqual(c.session.timeline.gang_count, 0)

    def test_conflicting_explicit_recognition_time_fails_before_state_changes(self):
        c = controller()
        c.process(event("entry-open", Kind.DOOR_OPEN, 1))
        close = Event(
            "entry-close",
            "s",
            Kind.DOOR_CLOSE,
            at(2),
            at(10),
            booking_at=at(10),
            trace_at=at(10),
        )
        session = c.session
        last_at = c._last_at
        decision = c.last_decision
        consumers = tuple(c.consumer_events)
        door_request = c.door_request
        with self.assertRaises(ValueError):
            c.process(close, recognition_at=at(9))
        self.assertIs(c.session, session)
        self.assertEqual(c._last_at, last_at)
        self.assertIs(c.last_decision, decision)
        self.assertEqual(tuple(c.consumer_events), consumers)
        self.assertEqual(c.door_request, door_request)

    def test_runtime_transports_the_same_canonical_event_and_archives_all_times(self):
        clock = [at(0)]
        runtime = runtime_with_clock(clock)
        owner = runtime.controller
        clock[0] = at(1)
        runtime._process_event(event("entry-open", Kind.DOOR_OPEN, 1))
        clock[0] = at(45)
        owner.advance(clock[0])
        clock[0] = at(50)
        runtime.archive = Mock()
        evidence = DoorCloseEvidence(at(30), at(40))
        detection = Detection(
            Kind.DOOR_CLOSE, at(20), at(50), ("upper",), at(40),
            door_close_evidence=evidence,
        )
        advance = runtime.detector.advance

        def deliver(through, **kwargs):
            # The real empty-input raster still executes. Only its one output
            # is controlled to exercise the transport without another replay.
            self.assertEqual(advance(through, **kwargs), [])
            kwargs["on_detection"](detection)
            return [detection]

        with (
            patch.object(runtime.detector, "advance", side_effect=deliver),
            patch.object(
                runtime, "_process_event", wraps=runtime._process_event
            ) as received,
            patch.object(owner, "process", wraps=owner.process) as processed,
        ):
            runtime._deliver_detection(at(40))

        received.assert_called_once()
        processed.assert_called_once()
        canonical = received.call_args.args[0]
        self.assertIs(runtime.controller, owner)
        self.assertIs(processed.call_args.args[0], canonical)
        self.assertIs(owner.session.timeline.processed[-1], canonical)
        self.assertIs(canonical.door_close_evidence, evidence)
        self.assertEqual(
            (
                canonical.effective_at,
                canonical.trace_at,
                canonical.booking_at,
                canonical.detected_at,
            ),
            (at(20), at(40), at(45), at(50)),
        )
        self.assertEqual(canonical.confirmation_at, at(40))
        self.assertEqual(
            sum(e.kind == Kind.DOOR_CLOSE for e in owner.session.timeline.processed), 1
        )
        records = [
            call
            for call in runtime.archive.append.call_args_list
            if call.args[0] == "detection"
        ]
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual((record.args[1], record.args[3]), (at(50), "s"))
        self.assertIs(record.args[2]["event"], canonical)
        saved = plain(record.args[2])
        self.assertEqual(saved["event"]["door_close_evidence"], plain(evidence))
        self.assertEqual(saved["trace_at"], at(40).isoformat())
        self.assertEqual(
            {
                key: saved["event"][key]
                for key in ("effective_at", "trace_at", "booking_at", "detected_at")
            },
            {
                "effective_at": at(20).isoformat(),
                "trace_at": at(40).isoformat(),
                "booking_at": at(45).isoformat(),
                "detected_at": at(50).isoformat(),
            },
        )


class DirectCurveConfirmationTimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_late_received_presence_before_confirmation_starts_at_physical_close(
        self,
    ):
        decisions = []
        for physical, trace_at in ((20, at(40)), (40, None)):
            with self.subTest(physical=physical, trace_at=trace_at):
                clock = [at(0)]
                runtime = runtime_with_clock(clock)
                await runtime.accept_presence(
                    binary_presence(ENTITY, "off", at(0), at(0))
                )
                clock[0] = at(1)
                await runtime.receive(event("entry-open", Kind.DOOR_OPEN, 1))
                clock[0] = at(40)
                await runtime.receive(
                    Event(
                        "entry-close",
                        "s",
                        Kind.DOOR_CLOSE,
                        at(physical),
                        at(40),
                        booking_at=at(40),
                        trace_at=trace_at,
                        door_close_evidence=DoorCloseEvidence(at(30), at(40)),
                    )
                )
                self.assertIsNone(runtime.session.timeline.active)
                clock[0] = at(45)
                await runtime.accept_presence(
                    binary_presence(ENTITY, "on", at(30), at(45))
                )
                gang = runtime.session.timeline.active
                self.assertIsNotNone(gang)
                self.assertEqual(gang.started_at, at(physical))
                self.assertEqual(gang.detected_at, at(45))
                self.assertEqual(gang.confirmation, Confirmation.CONFIRMED)
                self.assertEqual(gang.start_source_event_id, "entry-close")
                confirmed = [
                    e
                    for e in runtime.controller.consumer_events
                    if e.kind == "gang_confirmed"
                ]
                self.assertEqual(len(confirmed), 1)
                self.assertEqual(
                    (confirmed[0].effective_at, confirmed[0].received_at),
                    (at(physical), at(45)),
                )
                decisions.append(
                    (
                        gang.confirmation,
                        runtime.controller.regulation_inputs.gang_heat_demand,
                    )
                )
        self.assertEqual(decisions[0], decisions[1])
        self.assertTrue(decisions[0][1])

    async def test_presence_after_confirmation_cannot_reuse_a_close_booked_later(self):
        for physical, trace_at in ((40, None), (20, at(40))):
            with self.subTest(physical=physical, trace_at=trace_at):
                clock = [at(0)]
                runtime = runtime_with_clock(clock)
                await runtime.accept_presence(
                    binary_presence(ENTITY, "off", at(0), at(0))
                )
                clock[0] = at(1)
                await runtime.receive(event("entry-open", Kind.DOOR_OPEN, 1))
                clock[0] = at(50)
                await runtime.receive(
                    Event(
                        "entry-close",
                        "s",
                        Kind.DOOR_CLOSE,
                        at(physical),
                        at(50),
                        booking_at=at(50),
                        trace_at=trace_at,
                        door_close_evidence=DoorCloseEvidence(at(30), at(40)),
                    )
                )
                clock[0] = at(55)
                await runtime.accept_presence(
                    binary_presence(ENTITY, "on", at(45), at(55))
                )
                self.assertIsNone(runtime.session.timeline.active)
                self.assertFalse(runtime.controller.regulation_inputs.gang_heat_demand)
                self.assertFalse(
                    any(
                        e.kind == "gang_confirmed"
                        for e in runtime.controller.consumer_events
                    )
                )
