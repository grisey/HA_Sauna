"""Source qualification and handover boundaries, without replaying sessions."""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_cooling import at, controller
from test_foundation import bindings, event, parameters

from custom_components.ha_sauna.bindings import Bindings
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.presence import binary_presence
from custom_components.ha_sauna.core.timeline import Confirmation, Event, Kind
from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime


ENTITY = "binary_sensor.presence"
FALLBACK = "presence_fallback_delay_seconds"
RECOVERY = "presence_recovery_delay_seconds"


def direct_controller(**overrides):
    return controller(
        presence_source="ha_presence", presence_entity=ENTITY, **overrides,
    )


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
    runtime = SaunaRuntime(Configuration(
        Bindings({**bindings().values, "presence": ENTITY}), parameters(),
        presence_source="ha_presence",
    ), clock=lambda: clock[0])
    minimum = runtime.configuration.parameters.values["sauna_min_temperature_c"]
    runtime.controller.set_temperature(minimum, at(0))
    runtime.controller.set_operation(True, at(0), session_id="s")
    runtime._sync_detector()
    return runtime


class PresenceHandoverTests(unittest.TestCase):
    def test_qualification_is_continuous_and_consumes_both_parameter_values(self):
        defaults = parameters()
        longer = Parameters({
            FALLBACK: defaults.values[FALLBACK] * 2,
            RECOVERY: defaults.values[RECOVERY] * 2,
        })
        for selected, valid_states in (
            (defaults, ("off", "on")), (longer, ("on", "off")),
        ):
            with self.subTest(
                fallback=selected.values[FALLBACK], recovery=selected.values[RECOVERY],
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
                self.assertFalse(c.advance_presence_source(
                    at(due) - timedelta.resolution,
                ))
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
                self.assertFalse(c.advance_presence_source(
                    at(recovered) - timedelta.resolution,
                ))
                self.assertEqual(c.presence_source, "proxy")
                self.assertTrue(c.advance_presence_source(at(recovered)))
                self.assertEqual(c.presence_source, "ha_presence")
                self.assertEqual(c.configured_presence_source, "ha_presence")
                self.assertFalse(c.advance_presence_source(at(recovered)))

    def test_unchanged_valid_on_and_off_never_expire_or_override_configured_proxy(self):
        for source in ("ha_presence", "proxy"):
            for state in ("on", "off"):
                with self.subTest(source=source, state=state):
                    c = controller(presence_source=source, presence_entity=ENTITY)
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
                current, assertion="provisional_proxy", report_id="wrong-assertion",
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
        self.assertFalse(c.observe_direct_presence(
            replace(valid, received_at=at(delay + recovery / 2)),
            at(delay + recovery / 2),
        ))
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
        # Catch-up from the direct section cannot become a proxy person proof.
        old_person = Event("old-person", "s", Kind.PERSON_STRONG, at(4), at(switched))
        self.assertFalse(c.process(old_person, recognition_at=at(4)).changed)
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
            (active.gang_id, active.started_at,
             active.recognition_kind, active.confirmation),
            (original.gang_id, original.started_at,
             original.recognition_kind, original.confirmation),
        )
        self.assertEqual(
            active.infusion_events, (*original.infusion_events, old_infusion),
        )
        self.assertEqual(
            tuple(e for e in c.consumer_events if e.kind.startswith("gang_")),
            consumers,
        )
        self.assertEqual(
            len({e.event_id for e in c.consumer_events}), len(c.consumer_events),
        )

    def test_old_proxy_sections_cannot_start_a_gang_in_a_later_fallback(self):
        c = direct_controller()
        first = fallback(c)
        report(c, "on", first)
        returned = first + c.parameters.values[RECOVERY]
        c.advance_presence_source(at(returned))
        second = fallback(c, returned)
        c.process(event("new-open", Kind.DOOR_OPEN, second + 1))
        c.process(event("new-close", Kind.DOOR_CLOSE, second + 2))
        for kind in (Kind.PERSON_STRONG, Kind.INFUSION):
            for old_effective, old_recognition in (
                (True, False), (False, True), (True, True),
            ):
                with self.subTest(
                    kind=kind, old_effective=old_effective,
                    old_recognition=old_recognition,
                ):
                    value = Event(
                        f"historical:{kind}:{old_effective}:{old_recognition}",
                        "s", kind,
                        at(first if old_effective else second + 2), at(second + 2),
                    )
                    result = c.process(value, recognition_at=at(
                        first if old_recognition else second + 2,
                    ))
                    self.assertFalse(result.changed)
                    self.assertEqual(result.reason, "presence_source_context_changed")
                    self.assertIsNone(c.session.timeline.active)
                    self.assertIsNone(c.session.after_run)
        self.assertFalse(any(e.kind.startswith("gang_") for e in c.consumer_events))
        current = event("current-person", Kind.PERSON_STRONG, second + 2)
        self.assertTrue(c.process(current, recognition_at=at(second + 2)).changed)
        self.assertEqual(
            c.session.timeline.active.recognition_event_id, current.event_id,
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
            ("entry-open", Kind.DOOR_OPEN, 1), ("entry-close", Kind.DOOR_CLOSE, 2),
            ("person", Kind.PERSON_STRONG, 3), ("infusion", Kind.INFUSION, 4),
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
        self.assertIn(event("exit-ventilation", Kind.VENTILATION, returned + 2),
                      c.session.timeline.processed)
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
                self.assertFalse(any(
                    e.kind == "gang_started" for e in c.consumer_events
                ))
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
                    c.session.timeline.active.confirmation, Confirmation.CONFIRMED,
                )
                self.assertEqual(
                    sum(e.kind == "gang_started" for e in c.consumer_events), 1,
                )
                self.assertEqual(
                    sum(e.kind == "gang_confirmed" for e in c.consumer_events), 1,
                )


class PresenceHandoverRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_tick_qualifies_once_after_queued_inputs_and_detector_catchup(self):
        clock = [at(0)]
        runtime = runtime_with_clock(clock)
        await runtime.accept_presence(binary_presence(
            ENTITY, "unknown", clock[0], clock[0],
        ))
        delay = runtime.configuration.parameters.values[FALLBACK]
        device = Mock(
            bindings={
                "control_input": runtime.configuration.bindings.values["control_input"],
            },
            measurements={}, faults={}, command=False, command_error=False,
        )
        device.regulation_measurement.return_value = None
        device.regulation_valid_until.return_value = None
        device.external_light_selection.return_value = None
        device.physical_action.return_value = None
        device.apply = AsyncMock()
        runtime.device = device
        runtime.archive = Mock()
        received = at(delay / 2)
        runtime._pending_device_inputs.append((received, SimpleNamespace(data={
            "entity_id": device.bindings["control_input"], "new_state": None,
        })))
        order = Mock()
        order.attach_mock(device.ingest, "input")
        clock[0] = at(delay)
        with (
            patch.object(
                runtime.detector, "advance", wraps=runtime.detector.advance,
            ) as detect,
            patch.object(runtime.controller, "advance_presence_source",
                         wraps=runtime.controller.advance_presence_source) as select,
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
        self.assertTrue(any(
            call[0] == "detect" and call.args[0] == clock[0] for call in before
        ))
        self.assertEqual(runtime.controller.presence_source, "proxy")
        markers = [call for call in runtime.archive.append.call_args_list
                   if call.args[0] == "presence_source"]
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
            await runtime.accept_presence(binary_presence(
                ENTITY, "on", clock[0], clock[0],
            ))
            for key, kind, second in (
                ("entry-open", Kind.DOOR_OPEN, 1), ("entry-close", Kind.DOOR_CLOSE, 2),
                ("infusion", Kind.INFUSION, 3),
            ):
                clock[0] = at(second)
                await runtime.receive(event(key, kind, second))
            clock[0] = at(4)
            await runtime.accept_presence(binary_presence(
                ENTITY, "unknown", clock[0], clock[0],
            ))
            for key, kind, second in (
                ("exit-open", Kind.DOOR_OPEN, 5),
                ("exit-ventilation", Kind.VENTILATION, 6),
            ):
                clock[0] = at(second)
                await runtime.receive(event(key, kind, second))
            first = 4 + runtime.configuration.parameters.values[FALLBACK]
            clock[0] = at(first)
            await runtime.tick()
            runtime.controller.report_contactor(False, clock[0])
            runtime.controller.report_heating(False, clock[0])
            self.assertIsNone(runtime.session.timeline.active)
            clock[0] = at(first + 1)
            result = await runtime.receive(event(
                "cooling-person", Kind.PERSON_STRONG, first + 1,
            ))
            self.assertTrue(result.changed)
            self.assertEqual(result.reason, "entry_context_missing")
            old_hint = runtime.presence_status["current"]
            self.assertEqual(old_hint.occupancy, "present")
            self.assertEqual(old_hint.source_ref, "cooling-person")

            clock[0] = at(first + 2)
            await runtime.accept_presence(binary_presence(
                ENTITY, "on", clock[0], clock[0],
            ))
            returned = first + 2 + runtime.configuration.parameters.values[RECOVERY]
            clock[0] = at(returned)
            await runtime.tick()
            self.assertEqual(runtime.controller.presence_source, "ha_presence")
            clock[0] = at(returned + 1)
            await runtime.accept_presence(binary_presence(
                ENTITY, "unavailable", clock[0], clock[0],
            ))
            second = returned + 1 + runtime.configuration.parameters.values[FALLBACK]
            clock[0] = at(second)
            await runtime.tick()
            current = runtime.presence_status["current"]
            self.assertEqual(runtime.controller.presence_source, "proxy")
            self.assertEqual(
                (current.occupancy, current.reason), ("unknown", "no_proxy_evidence"),
            )
            self.assertEqual(
                (current.effective_at, current.received_at), (clock[0], clock[0]),
            )
            self.assertNotEqual(current.report_id, old_hint.report_id)
            self.assertEqual(runtime.presence.reports[old_hint.report_id], old_hint)
            self.assertIsNone(runtime.session.timeline.active)
            self.assertEqual(
                sum(e.kind == "gang_ended" for e in runtime.consumer_events), 1,
            )
            await runtime.tick()
        markers = [call for call in runtime.archive.append.call_args_list
                   if call.args[0] == "presence_source"]
        self.assertEqual([call.args[2]["effective_source"] for call in markers],
                         ["proxy", "ha_presence", "proxy"])
        self.assertEqual(
            sum(e.event_id == current.report_id for e in runtime.consumer_events), 1,
        )
        self.assertTrue(any(call.args[0] == "presence" and call.args[2] == old_hint
                            for call in runtime.archive.append.call_args_list))
