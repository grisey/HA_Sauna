"""Source qualification and handover boundaries, without replaying sessions."""

import asyncio
import json
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_cooling import at, controller
from test_foundation import bindings, event, parameters

from custom_components.ha_sauna.bindings import Bindings
from custom_components.ha_sauna.archive import plain
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.detector import Detection
from custom_components.ha_sauna.core.presence import binary_presence
from custom_components.ha_sauna.core.timeline import Confirmation, Event, Kind
from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime


ENTITY = "binary_sensor.presence"
FALLBACK = "presence_fallback_delay_seconds"
RECOVERY = "presence_recovery_delay_seconds"


class PresenceRuntimeProbe:
    """Exercise selection in its runtime owner and delegate control observations."""

    def __init__(self, *, presence_source="ha_presence", **overrides):
        self.clock = [at(0)]
        self.runtime = SaunaRuntime(
            Configuration(
                Bindings({**bindings().values, "presence": ENTITY}),
                Parameters({**parameters().as_dict(), **overrides}),
                presence_source=presence_source,
            ),
            clock=lambda: self.clock[0],
        )
        self.runtime.controller.set_temperature(70, at(0))
        self.runtime.controller.set_operation(True, at(0), session_id="s")

    def __getattr__(self, name):
        return getattr(self.runtime.controller, name)

    @property
    def presence_source(self):
        return self.runtime.configuration_model.presence_source

    @property
    def configured_presence_source(self):
        return self.runtime.configuration.presence_source

    @property
    def direct_presence(self):
        return self.runtime.presence.external.get(ENTITY)

    def observe_direct_presence(self, value, at):
        previous = self.direct_presence
        self.clock[0] = at
        asyncio.run(self.runtime.accept_presence(value))
        return self.direct_presence is not previous

    def advance_presence_source(self, at):
        self.clock[0] = at
        changed = self.runtime._advance_presence_source(at)
        self.runtime.controller.advance(at)
        return changed

    def process(self, value, **kwargs):
        self.clock[0] = value.booking_at
        return self.runtime._process_event(value, **kwargs)


def direct_controller(**overrides):
    return PresenceRuntimeProbe(**overrides)


def report(c, state, second, **kwargs):
    value = binary_presence(ENTITY, state, at(second), at(second), **kwargs)
    c.observe_direct_presence(value, at(second))
    return value


def fallback(c, second=0):
    report(c, "unknown", second)
    due = second + c.parameters.values[FALLBACK]
    c.advance_presence_source(at(due))
    return due


def runtime_with_clock(clock):
    runtime = SaunaRuntime(
        Configuration(
            Bindings({**bindings().values, "presence": ENTITY}),
            parameters(),
            presence_source="ha_presence",
        ),
        clock=lambda: clock[0],
    )
    minimum = runtime.configuration.parameters.values["sauna_min_temperature_c"]
    runtime.controller.set_temperature(minimum, at(0))
    runtime.controller.set_operation(True, at(0), session_id="s")
    runtime._sync_detector()
    return runtime


class PresenceHandoverTests(unittest.TestCase):
    def test_qualification_is_continuous_and_consumes_both_parameter_values(self):
        defaults = parameters()
        longer = Parameters(
            {
                FALLBACK: defaults.values[FALLBACK] * 2,
                RECOVERY: defaults.values[RECOVERY] * 2,
            }
        )
        for selected, valid_states in (
            (defaults, ("off", "on")),
            (longer, ("on", "off")),
        ):
            with self.subTest(
                fallback=selected.values[FALLBACK],
                recovery=selected.values[RECOVERY],
            ):
                c = direct_controller(**selected.as_dict())
                invalid_delay = selected.values[FALLBACK]
                valid_delay = selected.values[RECOVERY]
                start = invalid_delay + valid_delay
                # There is no invalid interval until the first observation.
                self.assertFalse(c.advance_presence_source(at(start)))
                report(c, "unknown", start)
                report(c, "on", start + invalid_delay / 2)
                invalid_since = start + invalid_delay
                report(c, "unavailable", invalid_since)
                report(c, "unknown", invalid_since + invalid_delay / 2)
                due = invalid_since + invalid_delay
                self.assertFalse(
                    c.advance_presence_source(
                        at(due) - timedelta.resolution,
                    )
                )
                self.assertEqual(c.presence_source, "ha_presence")
                self.assertTrue(c.advance_presence_source(at(due)))
                self.assertEqual(c.presence_source, "proxy")

                report(c, "on", due)
                report(c, "unknown", due + valid_delay / 2)
                valid_since = due + valid_delay
                report(c, valid_states[0], valid_since)
                # ON/OFF changes occupancy, but availability remains continuous.
                report(c, valid_states[1], valid_since + valid_delay / 2)
                recovered = valid_since + valid_delay
                self.assertFalse(
                    c.advance_presence_source(
                        at(recovered) - timedelta.resolution,
                    )
                )
                self.assertEqual(c.presence_source, "proxy")
                self.assertTrue(c.advance_presence_source(at(recovered)))
                self.assertEqual(c.presence_source, "ha_presence")
                self.assertEqual(c.configured_presence_source, "ha_presence")
                self.assertFalse(c.advance_presence_source(at(recovered)))

    def test_unchanged_valid_on_and_off_never_expire_or_override_configured_proxy(self):
        for source in ("ha_presence", "proxy"):
            for state in ("on", "off"):
                with self.subTest(source=source, state=state):
                    c = PresenceRuntimeProbe(presence_source=source)
                    original = report(c, state, 0)
                    later = sum(
                        c.parameters.values[key] for key in (FALLBACK, RECOVERY)
                    )
                    later += c.parameters.seconds("session_gap_minutes")
                    self.assertFalse(c.advance_presence_source(at(later)))
                    self.assertEqual(c.presence_source, source)
                    self.assertEqual(c.direct_presence, original)
                    self.assertFalse(c.advance_presence_source(at(later * 2)))
                    self.assertEqual(c.presence_source, source)
                    if source == "proxy":
                        report(c, "unknown", later * 2)
                        self.assertFalse(c.advance_presence_source(at(later * 3)))
                        self.assertEqual(c.presence_source, "proxy")

    def test_foreign_stale_and_duplicate_reports_cannot_change_health_intervals(self):
        c = direct_controller()
        delay = c.parameters.values[FALLBACK]
        first = report(c, "unknown", 0)
        current = report(c, "unavailable", delay / 2)
        rejected = (
            binary_presence("binary_sensor.other", "on", at(delay / 2), at(delay / 2)),
            binary_presence(ENTITY, "on", at(0), at(delay / 2)),
            binary_presence(ENTITY, "on", at(delay / 2), at(0)),
            replace(
                current,
                assertion="provisional_proxy",
                report_id="wrong-assertion",
            ),
            replace(first, received_at=at(delay / 2)),
        )
        for value in rejected:
            self.assertFalse(c.observe_direct_presence(value, at(delay / 2)))
            self.assertEqual(c.direct_presence, current)
        self.assertTrue(c.advance_presence_source(at(delay)))
        self.assertEqual(c.presence_source, "proxy")
        valid = report(c, "on", delay)
        recovery = c.parameters.values[RECOVERY]
        self.assertFalse(c.observe_direct_presence(current, at(delay + recovery / 2)))
        self.assertFalse(
            c.observe_direct_presence(
                replace(valid, received_at=at(delay + recovery / 2)),
                at(delay + recovery / 2),
            )
        )
        self.assertTrue(c.advance_presence_source(at(delay + recovery)))
        self.assertEqual(c.presence_source, "ha_presence")
        self.assertEqual(c.direct_presence, valid)

    def test_running_gang_and_consumer_identity_survive_both_handovers(self):
        c = direct_controller()
        c.process(event("entry-open", Kind.DOOR_OPEN, 1))
        report(c, "on", 2)
        c.process(event("entry-close", Kind.DOOR_CLOSE, 3))
        c.process(event("first-infusion", Kind.INFUSION, 4))
        original = c.session.timeline.active
        consumers = tuple(e for e in c.consumer_events if e.kind.startswith("gang_"))
        before_invalid = tuple(c.consumer_events)
        invalid_delay = c.parameters.values[FALLBACK]
        invalid = binary_presence(ENTITY, "unknown", at(5), at(6))
        processed_invalid = 6 + invalid_delay
        self.assertTrue(c.observe_direct_presence(invalid, at(processed_invalid)))
        self.assertEqual(c.direct_presence, invalid)
        self.assertEqual(c.session.timeline.active, original)
        self.assertEqual(tuple(c.consumer_events), before_invalid)
        self.assertFalse(c.advance_presence_source(at(processed_invalid)))
        switched = processed_invalid + invalid_delay
        self.assertFalse(c.advance_presence_source(at(switched) - timedelta.resolution))
        self.assertEqual(c.presence_source, "ha_presence")
        self.assertEqual(tuple(c.consumer_events), before_invalid)
        self.assertTrue(c.advance_presence_source(at(switched)))
        self.assertEqual(c.presence_source, "proxy")
        self.assertEqual(c.session.timeline.active, original)
        self.assertEqual(c.session.timeline.active.confirmation, Confirmation.CONFIRMED)
        # A late one-time report cannot replace the running state-confirmed gang.
        old_person = Event("old-person", "s", Kind.PERSON_STRONG, at(4), at(switched))
        c.process(old_person, recognition_at=at(4))
        self.assertEqual(c.session.timeline.active, original)
        self.assertIsNone(c.session.after_run)
        # An infusion already belonging to this gang keeps its original time.
        old_infusion = Event("late-infusion", "s", Kind.INFUSION, at(4.5), at(switched))
        self.assertTrue(c.process(old_infusion, recognition_at=at(4.5)).changed)
        before_valid = tuple(c.consumer_events)
        active_before_valid = c.session.timeline.active
        valid_delay = c.parameters.values[RECOVERY]
        valid = binary_presence(ENTITY, "on", at(switched + 1), at(switched + 2))
        processed_valid = switched + 2 + valid_delay
        self.assertTrue(c.observe_direct_presence(valid, at(processed_valid)))
        self.assertEqual(c.direct_presence, valid)
        self.assertEqual(c.session.timeline.active, active_before_valid)
        self.assertEqual(tuple(c.consumer_events), before_valid)
        self.assertFalse(c.advance_presence_source(at(processed_valid)))
        returned = processed_valid + valid_delay
        self.assertFalse(c.advance_presence_source(at(returned) - timedelta.resolution))
        self.assertEqual(c.presence_source, "proxy")
        self.assertEqual(tuple(c.consumer_events), before_valid)
        self.assertTrue(c.advance_presence_source(at(returned)))
        self.assertEqual(c.presence_source, "ha_presence")
        self.assertEqual(c.direct_presence, valid)
        active = c.session.timeline.active
        self.assertEqual(
            (
                active.gang_id,
                active.started_at,
                active.recognition_kind,
                active.confirmation,
            ),
            (
                original.gang_id,
                original.started_at,
                original.recognition_kind,
                original.confirmation,
            ),
        )
        self.assertEqual(
            active.infusion_events,
            (*original.infusion_events, old_infusion),
        )
        self.assertEqual(
            tuple(e for e in c.consumer_events if e.kind.startswith("gang_")),
            consumers,
        )
        self.assertEqual(
            len({e.event_id for e in c.consumer_events}),
            len(c.consumer_events),
        )

    def test_old_person_evidence_cannot_reuse_a_new_entry_cycle(self):
        c = direct_controller()
        first = fallback(c)
        report(c, "on", first)
        returned = first + c.parameters.values[RECOVERY]
        c.advance_presence_source(at(returned))
        second = fallback(c, returned)
        c.process(event("new-open", Kind.DOOR_OPEN, second + 1))
        c.process(event("new-close", Kind.DOOR_CLOSE, second + 2))
        for kind in (Kind.PERSON_STRONG, Kind.INFUSION):
            with self.subTest(kind=kind):
                value = Event(
                    f"historical:{kind}", "s", kind, at(first), at(second + 2)
                )
                result = c.process(value, recognition_at=at(first))
                self.assertFalse(result.changed)
                self.assertEqual(
                    result.reason,
                    (
                        "person_evidence_unqualified"
                        if kind == Kind.INFUSION
                        else "entry_context_changed"
                    ),
                )
                self.assertIsNone(c.session.timeline.active)
                self.assertIsNone(c.session.after_run)
        self.assertFalse(any(e.kind.startswith("gang_") for e in c.consumer_events))
        current = event("current-person", Kind.PERSON_STRONG, second + 2)
        self.assertTrue(c.process(current, recognition_at=at(second + 2)).changed)
        self.assertEqual(
            c.session.timeline.active.recognition_event_id, current.event_id
        )

    def test_proxy_provisional_deadline_is_not_restarted_or_confirmed_on_recovery(self):
        c = direct_controller()
        switched = fallback(c)
        c.process(event("entry-open", Kind.DOOR_OPEN, switched + 1))
        c.process(event("entry-close", Kind.DOOR_CLOSE, switched + 2))
        c.process(event("person", Kind.PERSON_STRONG, switched + 3))
        original = c.session.timeline.active
        deadline = next(d for d in c.session.deadlines if d.purpose == "confirmation")
        report(c, "on", switched + 4)
        returned = switched + 4 + c.parameters.values[RECOVERY]
        c.advance_presence_source(at(returned))
        self.assertEqual(c.session.timeline.active, original)
        self.assertEqual(
            next(d for d in c.session.deadlines if d.purpose == "confirmation"),
            deadline,
        )
        self.assertFalse(any(e.kind == "gang_confirmed" for e in c.consumer_events))
        c.advance(deadline.due_at)
        self.assertIsNone(c.session.timeline.active)
        self.assertEqual(c.session.timeline.retracted[-1].gang_id, original.gang_id)
        self.assertEqual(c.session.timeline.gang_count, 0)

    def test_open_proxy_exit_can_finish_the_same_gang_after_sensor_recovery(self):
        c = direct_controller()
        switched = fallback(c)
        for key, kind, offset in (
            ("entry-open", Kind.DOOR_OPEN, 1),
            ("entry-close", Kind.DOOR_CLOSE, 2),
            ("person", Kind.PERSON_STRONG, 3),
            ("infusion", Kind.INFUSION, 4),
            ("exit-open", Kind.DOOR_OPEN, 5),
        ):
            c.process(event(key, kind, switched + offset))
        original = c.session.timeline.active
        report(c, "on", switched + 6)
        returned = switched + 6 + c.parameters.values[RECOVERY]
        c.advance_presence_source(at(returned))
        self.assertEqual(c.session.timeline.active, original)
        report(c, "off", returned + 1)
        c.process(event("exit-ventilation", Kind.VENTILATION, returned + 2))
        self.assertIsNone(c.session.timeline.active)
        completed = c.session.timeline.completed[-1]
        self.assertEqual(completed.gang_id, original.gang_id)
        self.assertEqual(completed.infusion_events, original.infusion_events)
        self.assertEqual(completed.ended_at, at(returned + 2))
        self.assertEqual(completed.end_reason, "presence_exit")
        self.assertIn(
            event("exit-ventilation", Kind.VENTILATION, returned + 2),
            c.session.timeline.processed,
        )
        self.assertEqual(sum(e.kind == "gang_ended" for e in c.consumer_events), 1)
        self.assertFalse(c.advance_presence_source(at(returned + 2)))
        self.assertEqual(c.session.timeline.completed, (completed,))

    def test_recovery_distinguishes_closure_booking_at_the_same_time(self):
        for closure_order in ("before", "after_same_time", "after_later"):
            with self.subTest(closure_order=closure_order):
                c = direct_controller()
                switched = fallback(c)
                c.process(event("entry-open", Kind.DOOR_OPEN, switched + 1))
                report(c, "on", switched + 2)
                returned = switched + 2 + c.parameters.values[RECOVERY]
                if closure_order == "before":
                    c.process(event("entry-close", Kind.DOOR_CLOSE, returned))
                c.advance_presence_source(at(returned))
                self.assertIsNone(c.session.timeline.active)
                self.assertFalse(
                    any(e.kind == "gang_started" for e in c.consumer_events)
                )
                if closure_order == "before":
                    c.advance(at(returned + 1))
                    self.assertIsNone(c.session.timeline.active)
                    c.process(event("next-open", Kind.DOOR_OPEN, returned + 1))
                    closed = returned + 2
                    c.process(event("next-close", Kind.DOOR_CLOSE, closed))
                else:
                    closed = returned + (closure_order == "after_later")
                    c.process(event("entry-close", Kind.DOOR_CLOSE, closed))
                self.assertEqual(c.session.timeline.active.started_at, at(closed))
                self.assertEqual(
                    c.session.timeline.active.confirmation,
                    Confirmation.CONFIRMED,
                )
                self.assertEqual(
                    sum(e.kind == "gang_started" for e in c.consumer_events),
                    1,
                )
                self.assertEqual(
                    sum(e.kind == "gang_confirmed" for e in c.consumer_events),
                    1,
                )


class PresenceHandoverRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_pre_admission_infusion_keeps_onset_and_only_annotates_existing_gang(
        self,
    ):
        observations = []
        for existing_gang in (False, True):
            with self.subTest(existing_gang=existing_gang):
                clock = [at(0)]
                runtime = runtime_with_clock(clock)
                await runtime.accept_presence(
                    binary_presence(
                        ENTITY,
                        "on" if existing_gang else "off",
                        clock[0],
                        clock[0],
                    )
                )
                if existing_gang:
                    for key, kind, second in (
                        ("entry-open", Kind.DOOR_OPEN, 1),
                        ("entry-close", Kind.DOOR_CLOSE, 2),
                    ):
                        clock[0] = at(second)
                        await runtime.receive(event(key, kind, second))
                gang = runtime.session.timeline.active
                self.assertEqual(gang is not None, existing_gang)
                clock[0] = at(3)
                await runtime.accept_presence(
                    binary_presence(
                        ENTITY,
                        "unknown",
                        clock[0],
                        clock[0],
                    )
                )
                switched = 3 + runtime.configuration.parameters.values[FALLBACK]
                if not existing_gang:
                    # This complete eligible cycle is still unused at fallback.
                    for key, kind, second in (
                        ("waiting-open", Kind.DOOR_OPEN, switched - 3),
                        ("waiting-close", Kind.DOOR_CLOSE, switched - 2),
                    ):
                        clock[0] = at(second)
                        await runtime.receive(event(key, kind, second))
                clock[0] = at(switched)
                await runtime.tick()
                self.assertEqual(runtime.presence_source, "proxy")
                gang_before_detection = runtime.session.timeline.active
                clock[0] = at(switched + 1)
                detection = Detection(
                    Kind.INFUSION,
                    at(switched - 1),
                    clock[0],
                    ("upper",),
                    trace_at=clock[0],
                )

                def deliver(*args, **kwargs):
                    kwargs["on_detection"](detection)

                # Supply the detector's canonical output to the actual runtime
                # callback; selection, event times and controller are untouched.
                with patch.object(runtime.detector, "advance", side_effect=deliver):
                    runtime._deliver_detection(clock[0])
                delivered = runtime.session.timeline.processed[-1]
                self.assertEqual(delivered.kind, Kind.INFUSION)
                self.assertEqual(delivered.effective_at, detection.effective_at)
                self.assertEqual(delivered.trace_at, detection.trace_at)
                self.assertEqual(delivered.detected_at, detection.detected_at)
                self.assertEqual(delivered.booking_at, clock[0])
                active = runtime.session.timeline.active
                if existing_gang:
                    self.assertEqual(active.gang_id, gang.gang_id)
                    self.assertEqual(active.infusion_events, (delivered,))
                else:
                    self.assertIsNone(active)
                    self.assertEqual(runtime.session.timeline.gang_count, 0)
                observations.append(
                    plain(
                        {
                            "source": runtime.presence_source,
                            "fallback_delay_seconds": runtime.configuration.parameters.values[
                                FALLBACK
                            ],
                            "available_since": runtime._presence_source_selection.availability_since,
                            "switched_at": runtime._presence_source_selection.last_changed_at,
                            "producer_report": runtime.presence.external[ENTITY],
                            "occupancy_input": runtime.controller.direct_presence,
                            "detection": detection,
                            "event": delivered,
                            "gang_before": gang_before_detection,
                            "gang_after": active,
                            "last_decision": runtime.controller.last_decision,
                            "regulation_inputs": runtime.controller.regulation_inputs,
                            "cooling": runtime.session.after_run,
                        }
                    )
                )
        print(
            "INFUSION_ADMISSION_EVIDENCE="
            + json.dumps(
                observations,
                sort_keys=True,
                separators=(",", ":"),
            )
        )

    async def test_tick_qualifies_once_after_queued_inputs_and_detector_catchup(self):
        clock = [at(0)]
        runtime = runtime_with_clock(clock)
        await runtime.accept_presence(
            binary_presence(
                ENTITY,
                "unknown",
                clock[0],
                clock[0],
            )
        )
        delay = runtime.configuration.parameters.values[FALLBACK]
        device = Mock(
            bindings={
                "control_input": runtime.configuration.bindings.values["control_input"],
            },
            measurements={},
            faults={},
            command=False,
            command_error=False,
        )
        device.regulation_measurement.return_value = None
        device.regulation_valid_until.return_value = None
        device.external_light_selection.return_value = None
        device.physical_action.return_value = None
        device.apply = AsyncMock()
        runtime.device = device
        runtime.archive = Mock()
        received = at(delay / 2)
        runtime._pending_device_inputs.append(
            (
                received,
                SimpleNamespace(
                    data={
                        "entity_id": device.bindings["control_input"],
                        "new_state": None,
                    }
                ),
            )
        )
        order = Mock()
        order.attach_mock(device.ingest, "input")
        clock[0] = at(delay)
        with (
            patch.object(
                runtime.detector,
                "advance",
                wraps=runtime.detector.advance,
            ) as detect,
            patch.object(
                runtime,
                "_advance_presence_source",
                wraps=runtime._advance_presence_source,
            ) as select,
        ):
            order.attach_mock(detect, "detect")
            order.attach_mock(select, "select")
            await runtime.tick()
            await runtime.tick()
        self.assertFalse(runtime._pending_device_inputs)
        first_select = next(
            i for i, call in enumerate(order.mock_calls) if call[0] == "select"
        )
        before = order.mock_calls[:first_select]
        self.assertEqual(sum(call[0] == "input" for call in before), 1)
        self.assertTrue(
            any(call[0] == "detect" and call.args[0] == clock[0] for call in before)
        )
        self.assertEqual(runtime.presence_source, "proxy")
        markers = [
            call
            for call in runtime.archive.append.call_args_list
            if call.args[0] == "presence_source"
        ]
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0].args[1], clock[0])
        self.assertEqual(markers[0].args[2]["previous_source"], "ha_presence")
        self.assertEqual(markers[0].args[2]["effective_source"], "proxy")

    async def test_second_fallback_resets_proxy_hint_and_keeps_raw_evidence(self):
        clock = [at(0)]
        runtime = runtime_with_clock(clock)
        runtime.archive = Mock()
        runtime.controller.report_heating(True, clock[0])
        # Sensor transport and thermal availability are separate contracts.
        with patch.object(runtime, "_detector_presence_available", return_value=True):
            await runtime.accept_presence(
                binary_presence(
                    ENTITY,
                    "on",
                    clock[0],
                    clock[0],
                )
            )
            for key, kind, second in (
                ("entry-open", Kind.DOOR_OPEN, 1),
                ("entry-close", Kind.DOOR_CLOSE, 2),
                ("infusion", Kind.INFUSION, 3),
            ):
                clock[0] = at(second)
                await runtime.receive(event(key, kind, second))
            clock[0] = at(4)
            await runtime.accept_presence(
                binary_presence(
                    ENTITY,
                    "off",
                    clock[0],
                    clock[0],
                )
            )
            for key, kind, second in (
                ("exit-open", Kind.DOOR_OPEN, 5),
                ("exit-ventilation", Kind.VENTILATION, 6),
            ):
                clock[0] = at(second)
                item = (Event(key, "s", kind, at(3.5), clock[0])
                        if kind == Kind.DOOR_OPEN else event(key, kind, second))
                await runtime.receive(item)
            self.assertIsNone(runtime.session.timeline.active)
            clock[0] = at(7)
            await runtime.accept_presence(
                binary_presence(
                    ENTITY,
                    "unknown",
                    clock[0],
                    clock[0],
                )
            )
            first = 7 + runtime.configuration.parameters.values[FALLBACK]
            clock[0] = at(first)
            await runtime.tick()
            runtime.controller.report_contactor(False, clock[0])
            runtime.controller.report_heating(False, clock[0])
            self.assertIsNone(runtime.session.timeline.active)
            clock[0] = at(first + 1)
            result = await runtime.receive(
                event(
                    "cooling-person",
                    Kind.PERSON_STRONG,
                    first + 1,
                )
            )
            self.assertTrue(result.changed)
            self.assertEqual(result.reason, "entry_context_missing")
            old_hint = runtime.presence_status["current"]
            self.assertEqual(old_hint.occupancy, "present")
            self.assertEqual(old_hint.source_ref, "cooling-person")

            clock[0] = at(first + 2)
            await runtime.accept_presence(
                binary_presence(
                    ENTITY,
                    "on",
                    clock[0],
                    clock[0],
                )
            )
            returned = first + 2 + runtime.configuration.parameters.values[RECOVERY]
            clock[0] = at(returned)
            await runtime.tick()
            self.assertEqual(runtime.presence_source, "ha_presence")
            clock[0] = at(returned + 1)
            await runtime.accept_presence(
                binary_presence(
                    ENTITY,
                    "unavailable",
                    clock[0],
                    clock[0],
                )
            )
            second = returned + 1 + runtime.configuration.parameters.values[FALLBACK]
            clock[0] = at(second)
            await runtime.tick()
            current = runtime.presence_status["current"]
            self.assertEqual(runtime.presence_source, "proxy")
            self.assertEqual(
                (current.occupancy, current.reason),
                ("unknown", "no_proxy_evidence"),
            )
            self.assertEqual(
                (current.effective_at, current.received_at),
                (clock[0], clock[0]),
            )
            self.assertNotEqual(current.report_id, old_hint.report_id)
            self.assertEqual(runtime.presence.reports[old_hint.report_id], old_hint)
            self.assertIsNone(runtime.session.timeline.active)
            self.assertEqual(
                sum(e.kind == "gang_ended" for e in runtime.consumer_events),
                1,
            )
            await runtime.tick()
        markers = [
            call
            for call in runtime.archive.append.call_args_list
            if call.args[0] == "presence_source"
        ]
        self.assertEqual(
            [call.args[2]["effective_source"] for call in markers],
            ["proxy", "ha_presence", "proxy"],
        )
        self.assertEqual(
            sum(e.event_id == current.report_id for e in runtime.consumer_events),
            1,
        )
        self.assertTrue(
            any(
                call.args[0] == "presence" and call.args[2] == old_hint
                for call in runtime.archive.append.call_args_list
            )
        )
