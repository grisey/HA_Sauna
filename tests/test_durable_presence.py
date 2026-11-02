"""Präsenz beim Türschluss: vorläufiger Gang, Bestätigung durch Aufguss, Ende durch Lüften."""

from dataclasses import replace
import unittest
from unittest.mock import patch

from test_cooling import at, controller
from test_foundation import event, selected_occupancy
from test_presence_handover import (
    ENTITY,
    RECOVERY,
    PresenceRuntimeProbe,
    direct_controller,
    fallback,
    report,
    runtime_with_clock,
)

from custom_components.ha_sauna.core.recognition import Detection
from custom_components.ha_sauna.core.presence import binary_presence
from custom_components.ha_sauna.core.timeline import (
    Confirmation,
    DoorCloseEvidence,
    Event,
    Kind,
)


def close_event(
    *, candidate=3, confirmed=6, received=7, effective=None, key="close",
):
    """Keep the observed candidate separate from retrospective door timing."""
    return Event(
        key, "s", Kind.DOOR_CLOSE,
        at(candidate if effective is None else effective), at(received),
        trace_at=at(confirmed),
        door_close_evidence=DoorCloseEvidence(at(candidate), at(confirmed)),
    )


class DurablePresenceControllerTests(unittest.TestCase):
    def setUp(self):
        self.c = controller()
        self.c.set_temperature(
            self.c.parameters.values["sauna_min_temperature_c"], at(0),
        )
        self.c.process(event("open", Kind.DOOR_OPEN, 1))

    def presence(self, state, effective, *, received=None):
        received = effective if received is None else received
        value = binary_presence(ENTITY, state, at(effective), at(received))
        self.assertTrue(self.c.consume_occupancy_input(
            selected_occupancy(value, at(received)),
        ))
        return value

    def assert_no_entry(self):
        self.assertIsNone(self.c.session.timeline.active)
        self.assertEqual(self.c.session.timeline.completed, ())
        self.assertFalse(any(
            item.kind == "gang_confirmed" for item in self.c.consumer_events
        ))

    def test_unchanged_on_covers_the_interval_without_another_report(self):
        for onset in (2, 3):
            with self.subTest(onset=onset):
                self.setUp()
                original = self.presence("on", onset)
                closure = close_event()
                self.c.process(closure)
                gang = self.c.session.timeline.active
                self.assertIsNotNone(gang)
                self.assertEqual(gang.confirmation, Confirmation.PROVISIONAL)
                self.assertEqual(gang.recognition_kind, Kind.PRESENCE_ENTRY)
                self.assertEqual(gang.start_source_event_id, closure.event_id)
                # Begins at the closing, recognized only once it is confirmed.
                self.assertEqual(gang.started_at, closure.effective_at)
                self.assertEqual(gang.detected_at, closure.booking_at)
                self.assertGreaterEqual(
                    gang.detected_at, closure.door_close_evidence.confirmed_at,
                )
                self.assertEqual(self.c.direct_presence.report_id, original.report_id)
                self.c.advance(at(8))
                self.assertEqual(self.c.session.timeline.active.gang_id, gang.gang_id)
                self.assertFalse(any(
                    item.kind == "gang_confirmed" for item in self.c.consumer_events
                ))

    def test_presence_starting_inside_the_candidate_does_not_cover_it(self):
        for onset in (4, 6):
            with self.subTest(onset=onset):
                self.setUp()
                self.presence("off", 2)
                self.presence("on", onset)
                self.c.process(close_event())
                self.c.advance(at(8))
                self.assert_no_entry()

    def test_retrospective_door_time_does_not_replace_candidate_beginning(self):
        self.presence("on", 2.5)
        self.c.process(close_event(effective=2))
        self.assertIsNotNone(self.c.session.timeline.active)
        self.assertEqual(
            self.c.session.timeline.active.recognition_kind, Kind.PRESENCE_ENTRY,
        )

    def test_interrupted_report_before_the_closing_is_no_presence(self):
        proxy = selected_occupancy(binary_presence(ENTITY, "on", at(2), at(2.5)), at(2.5))
        self.presence("on", 1.5)
        self.c.consume_occupancy_input(replace(proxy, qualified_presence=False, report_id="proxy"))
        self.c.process(close_event())
        self.c.advance(at(600))
        self.assert_no_entry()
        self.presence("on", 650)
        self.c.process(event("new-open", Kind.DOOR_OPEN, 700))
        self.c.process(close_event(candidate=701, confirmed=702, received=703, key="new-close"))
        self.assertIsNotNone(self.c.session.timeline.active)
        self.assertEqual(self.c.session.timeline.active.start_source_event_id, "new-close")

    def test_open_door_history_trimming_keeps_the_presence_at_the_closing(self):
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted):
                self.setUp()
                self.presence("on", 2)
                if interrupted:
                    self.presence("off", 4)
                    self.presence("on", 5)
                self.c.discard_recognition_context_before(at(5.5))
                self.c.process(close_event())
                gang = self.c.session.timeline.active
                self.assertIsNotNone(gang)
                self.assertEqual(gang.start_source_event_id, "close")

    def test_late_received_on_uses_its_original_state_time(self):
        self.c.process(close_event())
        self.assert_no_entry()
        self.presence("on", 2, received=8)
        gang = self.c.session.timeline.active
        self.assertIsNotNone(gang)
        self.assertEqual(gang.start_source_event_id, "close")
        self.assertEqual(gang.detected_at, at(8))

    def test_late_close_keeps_presence_at_the_candidate_before_a_later_off(self):
        for absent_at in (5, 8):
            with self.subTest(absent_at=absent_at):
                self.setUp()
                self.presence("on", 2)
                self.presence("off", absent_at)
                self.c.process(close_event(received=10))
                gang = self.c.session.timeline.active
                self.assertIsNotNone(gang)
                self.assertEqual(gang.confirmation, Confirmation.PROVISIONAL)
                self.assertEqual(self.c.session.timeline.completed, ())

    def test_missing_candidate_evidence_does_not_confirm_sensor_entry(self):
        self.presence("on", 2)
        self.c.process(Event(
            "legacy-close", "s", Kind.DOOR_CLOSE, at(3), at(7), trace_at=at(6),
        ))
        self.c.advance(at(8))
        self.assert_no_entry()

    def test_proven_cold_entry_survives_later_off_without_new_opening(self):
        minimum = self.c.parameters.values["sauna_min_temperature_c"]
        self.c.set_temperature(minimum - 1, at(1))
        self.presence("on", 2)
        self.c.process(close_event())
        self.assert_no_entry()
        self.presence("off", 8)
        self.c.set_temperature(minimum, at(9))
        gang = self.c.session.timeline.active
        self.assertIsNotNone(gang)
        self.assertEqual(gang.started_at, at(9))
        self.assertEqual(gang.recognition_kind, Kind.PRESENCE_ENTRY)
        self.c.advance(at(10))
        self.assertEqual(self.c.session.timeline.active.gang_id, gang.gang_id)
        self.assertEqual(self.c.session.timeline.completed, ())

    def test_new_opening_retires_the_cold_entry_and_requires_its_own_interval(self):
        cases = (
            ("unchanged_on", None, True),
            ("still_absent", None, False),
            ("on_before_new_candidate", 9.5, True),
            ("on_inside_new_candidate", 11, False),
        )
        for case, next_on, expected in cases:
            with self.subTest(case=case):
                self.setUp()
                minimum = self.c.parameters.values["sauna_min_temperature_c"]
                self.c.set_temperature(minimum - 1, at(1))
                original = self.presence("on", 2)
                self.c.process(close_event())
                self.assert_no_entry()
                if case != "unchanged_on":
                    self.presence("off", 8)
                self.c.process(event("next-open", Kind.DOOR_OPEN, 9))
                # Reaching the minimum while the new door is open must not
                # revive the previously proven, now superseded cold entry.
                self.c.set_temperature(minimum, at(9.25))
                self.assert_no_entry()
                if next_on is not None:
                    self.presence("on", next_on)
                closure = close_event(
                    candidate=10, confirmed=12, received=13, key="next-close",
                )
                self.c.process(closure)
                gang = self.c.session.timeline.active
                if expected:
                    self.assertIsNotNone(gang)
                    self.assertEqual(gang.start_source_event_id, closure.event_id)
                    self.assertEqual(gang.started_at, closure.effective_at)
                    if case == "unchanged_on":
                        self.assertEqual(
                            self.c.direct_presence.report_id, original.report_id,
                        )
                else:
                    self.assert_no_entry()

    def confirmed_entry(self):
        self.presence("on", 2)
        self.c.process(close_event())
        self.c.process(event("water", Kind.INFUSION, 7.5))
        gang = self.c.session.timeline.active
        self.assertEqual(gang.confirmation, Confirmation.CONFIRMED)
        return gang.gang_id

    def test_absence_around_the_exit_opening_does_not_end_the_gang(self):
        gang_id = self.confirmed_entry()
        self.presence("off", 8)
        self.c.process(Event("late-exit-open", "s", Kind.DOOR_OPEN, at(10), at(12)))
        self.presence("off", 11, received=13)
        self.c.advance(at(600))
        self.assertEqual(self.c.session.timeline.active.gang_id, gang_id)
        self.assertEqual(self.c.session.timeline.completed, ())
        self.assertIsNone(self.c.session.after_run)
        self.c.process(event("exit-ventilation", Kind.VENTILATION, 601))
        self.assertIsNone(self.c.session.timeline.active)
        self.assertEqual(self.c.session.timeline.gang_count, 1)
        self.assertEqual(self.c.session.timeline.completed[0].gang_id, gang_id)
        self.assertEqual(self.c.session.timeline.completed[0].ended_at, at(601))
        self.assertEqual(self.c.session.after_run.requested_at, at(601))

    def test_exit_absence_admits_the_next_entry_but_does_not_end_it(self):
        first_id = self.confirmed_entry()
        self.c.process(event("first-exit-open", Kind.DOOR_OPEN, 8))
        self.presence("off", 9)
        self.c.process(event("first-exit-vent", Kind.VENTILATION, 10))
        self.assertIsNone(self.c.session.timeline.active)
        self.presence("on", 11)
        self.c.process(close_event(
            candidate=12, confirmed=14, received=15, key="second-entry",
        ))
        second = self.c.session.timeline.active
        self.assertIsNotNone(second)
        self.assertNotEqual(second.gang_id, first_id)
        self.assertEqual(second.start_source_event_id, "second-entry")
        self.c.process(event("second-water", Kind.INFUSION, 15.5))
        self.assertIsNone(self.c.session.after_run)

        self.c.process(event("second-exit-open", Kind.DOOR_OPEN, 16))
        self.presence("off", 18)
        self.c.advance(at(600))
        self.assertEqual(self.c.session.timeline.active.gang_id, second.gang_id)
        self.assertEqual(self.c.session.timeline.gang_count, 1)
        self.assertIsNone(self.c.session.after_run)
        self.c.process(event("second-exit-vent", Kind.VENTILATION, 601))
        self.assertIsNone(self.c.session.timeline.active)
        self.assertEqual(self.c.session.timeline.gang_count, 2)
        self.assertEqual(self.c.session.timeline.completed[-1].gang_id, second.gang_id)

    def test_old_entry_cannot_fill_an_empty_return_or_poison_a_later_valid_one(self):
        self.confirmed_entry()
        self.c.process(event("exit-open", Kind.DOOR_OPEN, 8))
        self.presence("off", 9)
        self.c.process(event("exit-vent", Kind.VENTILATION, 10))
        self.c.process(close_event(
            candidate=12, confirmed=14, received=15, key="empty-close",
        ))
        self.assertIsNone(self.c.session.timeline.active)
        original = self.presence("on", 17)
        self.assertIsNone(self.c.session.timeline.active)
        self.assertEqual(self.c.session.timeline.gang_count, 1)

        self.c.process(event("return-open", Kind.DOOR_OPEN, 18))
        self.c.process(close_event(
            candidate=19, confirmed=21, received=22, key="occupied-close",
        ))
        gang = self.c.session.timeline.active
        self.assertIsNotNone(gang)
        self.assertEqual(gang.start_source_event_id, "occupied-close")
        self.assertEqual(self.c.direct_presence.report_id, original.report_id)
        self.assertEqual(self.c.session.timeline.gang_count, 1)


class DurablePresenceRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_preserves_supplied_and_missing_detector_evidence(self):
        for evidence in (DoorCloseEvidence(at(3), at(6)), None):
            with self.subTest(evidence_supplied=evidence is not None):
                clock = [at(0)]
                runtime = runtime_with_clock(clock)
                clock[0] = at(1)
                await runtime.receive(event("open", Kind.DOOR_OPEN, 1))
                clock[0] = at(2)
                await runtime.accept_presence(binary_presence(
                    ENTITY, "on", at(2), at(2),
                ))
                clock[0] = at(7)
                detection = Detection(
                    Kind.DOOR_CLOSE, at(3), clock[0],
                    tuple(runtime.configuration_model.measurement_positions),
                    trace_at=at(6), door_close_evidence=evidence,
                )

                def deliver(_through, **callbacks):
                    callbacks["on_detection"](detection)

                with patch.object(runtime.detector, "advance", side_effect=deliver):
                    runtime._deliver_detection(clock[0])
                closure = next(
                    item for item in runtime.session.timeline.processed
                    if item.kind == Kind.DOOR_CLOSE
                )
                self.assertIs(closure.door_close_evidence, evidence)
                self.assertEqual(closure.effective_at, detection.effective_at)
                self.assertEqual(closure.trace_at, detection.trace_at)
                if evidence is None:
                    self.assertIsNone(runtime.session.timeline.active)
                    self.assertFalse(any(
                        item.kind == Kind.PRESENCE_ENTRY
                        for item in runtime.session.timeline.processed
                    ))
                else:
                    self.assertIsNotNone(runtime.session.timeline.active)
                    self.assertEqual(
                        runtime.session.timeline.active.recognition_kind,
                        Kind.PRESENCE_ENTRY,
                    )

    async def test_runtime_delayed_report_establishes_the_original_interval(self):
        clock = [at(0)]
        runtime = runtime_with_clock(clock)
        clock[0] = at(1)
        await runtime.receive(event("open", Kind.DOOR_OPEN, 1))
        clock[0] = at(7)
        await runtime.receive(close_event())
        self.assertIsNone(runtime.session.timeline.active)
        clock[0] = at(8)
        await runtime.accept_presence(binary_presence(ENTITY, "on", at(2), at(8)))
        self.assertIsNotNone(runtime.session.timeline.active)
        self.assertEqual(
            runtime.session.timeline.active.recognition_kind, Kind.PRESENCE_ENTRY,
        )
        self.assertEqual(runtime.session.timeline.active.start_source_event_id, "close")


class DurablePresenceSourceTests(unittest.TestCase):
    def test_proxy_evidence_cannot_become_durable_sensor_presence(self):
        probe = PresenceRuntimeProbe(presence_source="proxy")
        probe.process(event("open", Kind.DOOR_OPEN, 1))
        report(probe, "on", 2)
        probe.process(close_event())
        self.assertIsNone(probe.session.timeline.active)
        probe.process(event("proxy-person", Kind.PERSON_STRONG, 8))
        gang = probe.session.timeline.active
        self.assertIsNotNone(gang)
        self.assertEqual(gang.recognition_kind, Kind.PERSON_STRONG)
        self.assertEqual(gang.confirmation, Confirmation.PROVISIONAL)
        self.assertFalse(any(
            item.kind == Kind.PRESENCE_ENTRY
            for item in probe.session.timeline.processed
        ))

    def test_sensor_recovery_cannot_backfill_a_candidate_started_in_proxy_mode(self):
        probe = direct_controller()
        switched = fallback(probe)
        self.assertEqual(probe.presence_source, "proxy")
        valid_since = switched + 1
        probe.process(event("open", Kind.DOOR_OPEN, valid_since))
        report(probe, "on", valid_since)
        recovery = probe.parameters.values[RECOVERY]
        candidate = valid_since + recovery / 2
        recovered = valid_since + recovery
        probe.set_temperature(
            probe.parameters.values["sauna_min_temperature_c"], at(recovered),
        )
        self.assertTrue(probe.advance_presence_source(at(recovered)))
        self.assertEqual(probe.presence_source, "ha_presence")
        probe.process(close_event(
            candidate=candidate, confirmed=recovered + 1, received=recovered + 2,
        ))
        self.assertIsNone(probe.session.timeline.active)
        probe.process(event("new-open", Kind.DOOR_OPEN, recovered + 3))
        probe.process(close_event(
            candidate=recovered + 4, confirmed=recovered + 5,
            received=recovered + 6, key="new-close",
        ))
        self.assertIsNotNone(probe.session.timeline.active)
        self.assertEqual(probe.session.timeline.active.start_source_event_id, "new-close")
        self.assertEqual(
            probe.session.timeline.active.recognition_kind, Kind.PRESENCE_ENTRY,
        )
