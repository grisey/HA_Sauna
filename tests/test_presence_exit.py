"""Sensor-Gangende: Lüften oder leere Kabine nach der Austrittsöffnung."""

import unittest

from test_cooling import at
from test_cooling import controller as plain_controller
from test_durable_presence import close_event
from test_foundation import event, ideal_presence, selected_occupancy
from test_presence_handover import (
    ENTITY,
    RECOVERY,
    PresenceRuntimeProbe,
    fallback,
    report,
)

from custom_components.ha_sauna.core.presence import binary_presence
from custom_components.ha_sauna.core.timeline import Confirmation, Kind


def controller(**overrides):
    """Gang control with the sharply timed presence sensor."""
    return ideal_presence(plain_controller(**overrides))


class PresenceExitTests(unittest.TestCase):
    def setUp(self, *, learned=False):
        self.c = plain_controller() if learned else controller()
        self.c.set_temperature(
            self.c.parameters.values["sauna_min_temperature_c"], at(0),
        )
        self.c.process(event("entry-open", Kind.DOOR_OPEN, 1))
        self.presence("on", 2)
        self.c.process(close_event())
        if learned:
            self.c.advance(at(60))
        self.assertIsNotNone(self.c.session.timeline.active)
        self.gang_id = self.c.session.timeline.active.gang_id
        self.assertEqual(
            self.c.session.timeline.active.recognition_kind, Kind.PRESENCE_CONFIRMED,
        )

    def presence(self, state, second):
        value = binary_presence(ENTITY, state, at(second), at(second))
        self.assertTrue(self.c.consume_occupancy_input(
            selected_occupancy(value, at(second)),
        ))
        return value

    def assert_still_running(self):
        self.assertIsNotNone(self.c.session.timeline.active)
        self.assertEqual(self.c.session.timeline.active.gang_id, self.gang_id)
        self.assertEqual(self.c.session.timeline.completed, ())
        self.assertIsNone(self.c.session.after_run)

    def assert_ended_once(self, decided, ended, *, ending=Kind.PRESENCE_ENDED):
        self.assertIsNone(self.c.session.timeline.active)
        self.assertEqual(self.c.session.timeline.gang_count, 1)
        self.assertEqual(len(self.c.session.timeline.completed), 1)
        gang = self.c.session.timeline.completed[0]
        self.assertEqual(gang.gang_id, self.gang_id)
        self.assertEqual(gang.ended_at, at(ended))
        self.assertEqual(
            gang.end_reason,
            "presence_exit" if ending == Kind.PRESENCE_ENDED else "ventilation",
        )
        end_event = next(
            item for item in self.c.session.timeline.processed
            if item.event_id == gang.end_event_id
        )
        self.assertEqual(end_event.kind, ending)
        self.assertEqual(end_event.booking_at, at(decided))
        self.assertEqual(self.c.session.after_run.requested_at, at(decided))
        self.assertEqual(self.c.phase, "nachlauf")
        self.assertFalse(self.c.last_decision.heat)
        self.assertEqual(sum(
            item.kind == "gang_ended" for item in self.c.consumer_events
        ), 1)
        # The operating phase remains the gang until its end was decided.
        phases = self.c.phase_projection(at(decided + 1)).intervals
        gang_phase = next(
            item for item in reversed(phases)
            if item.phase == "saunagang" and item.source_id == self.gang_id
        )
        cooling_phase = next(item for item in phases if item.phase == "nachlauf")
        self.assertTrue(gang_phase.complete)
        self.assertEqual(gang_phase.ended_at, at(decided))
        self.assertEqual(cooling_phase.started_at, gang_phase.ended_at)

    def test_absence_without_a_new_opening_does_not_end_the_gang(self):
        self.presence("off", 8)
        self.c.advance(at(900))
        self.assert_still_running()
        with self.assertRaises(ValueError):
            self.c.process(close_event(
                candidate=910, confirmed=912, received=913, key="unpaired-close",
            ))
        self.assert_still_running()

    def test_absence_after_the_opening_ends_the_gang_at_the_opening(self):
        self.c.process(event("exit-open", Kind.DOOR_OPEN, 8))
        self.presence("off", 9)
        self.c.advance(at(9.5))
        self.assert_still_running()
        self.c.advance(at(12))
        self.assert_ended_once(12, 8)
        self.assertFalse(any(
            item.kind == Kind.VENTILATION for item in self.c.session.timeline.processed
        ))
        closure = close_event(candidate=10, confirmed=12, received=13, key="exit-close")
        self.c.process(closure)
        self.assertFalse(self.c.process(closure).changed)
        self.c.advance(at(14))
        self.assert_ended_once(12, 8)

    def test_presence_at_the_closing_keeps_the_stay_without_another_report(self):
        original_report = self.c.direct_presence.report_id
        self.c.process(event("exit-open", Kind.DOOR_OPEN, 8))
        self.c.process(close_event(
            candidate=10, confirmed=12, received=13, key="occupied-close",
        ))
        self.c.advance(at(900))
        self.assert_still_running()
        self.assertEqual(self.c.direct_presence.report_id, original_report)
        self.assertEqual(sum(
            item.kind == "gang_confirmed" for item in self.c.consumer_events
        ), 1)

    def test_later_absence_cannot_undo_an_established_stay(self):
        self.c.process(event("exit-open", Kind.DOOR_OPEN, 8))
        self.c.process(close_event(
            candidate=10, confirmed=12, received=13, key="occupied-close",
        ))
        self.presence("off", 20)
        self.c.advance(at(4000))
        self.assert_still_running()

    def test_short_presence_during_the_door_operation_is_no_stay(self):
        for changes in (
            (("off", 9), ("on", 10.5), ("off", 11.5)),
            (("off", 9), ("on", 10.5)),
        ):
            with self.subTest(changes=changes):
                self.setUp(learned=True)
                self.c.process(event("exit-open", Kind.DOOR_OPEN, 68))
                for state, second in changes:
                    self.presence(state, second + 60)
                self.assert_still_running()
                self.c.process(close_event(
                    candidate=70, confirmed=72, received=73, key="brief-visit-close",
                ))
                self.assert_ended_once(73, 68)

    def test_learned_hold_after_the_exit_closing_ends_the_gang_at_the_opening(self):
        self.setUp(learned=True)
        self.c.process(event("exit-open", Kind.DOOR_OPEN, 100))
        self.c.process(close_event(candidate=110, confirmed=112, received=113, key="exit-close"))
        self.presence("off", 118)
        self.c.advance(at(200))
        self.assert_still_running()
        self.c.advance(at(1200))
        self.assert_ended_once(1200, 100)
        self.assertIn("exit-close", self.c.session.timeline.rejected_start_sources)

    def test_presence_ending_right_after_the_exit_closing_is_the_sensor_hold(self):
        self.c.process(event("exit-open", Kind.DOOR_OPEN, 8))
        self.presence("off", 11)
        self.c.process(close_event(
            candidate=10, confirmed=12, received=15, key="delayed-exit-close",
        ))
        self.assert_ended_once(15, 8)

    def test_unknown_or_unavailable_sensor_does_not_prove_an_exit(self):
        for missing in ("unknown", "unavailable"):
            with self.subTest(missing=missing):
                self.setUp()
                self.c.process(event("exit-open", Kind.DOOR_OPEN, 8))
                self.presence(missing, 9)
                self.c.process(close_event(
                    candidate=10, confirmed=12, received=13, key="unknown-close",
                ))
                self.c.advance(at(900))
                self.assert_still_running()

    def test_closure_does_not_end_again_after_ventilation_already_finished(self):
        self.c.process(event("exit-open", Kind.DOOR_OPEN, 8))
        self.presence("off", 9)
        self.c.process(event("exit-vent", Kind.VENTILATION, 9.5))
        self.assert_ended_once(9.5, 9.5, ending=Kind.VENTILATION)
        cooling_id = self.c.session.after_run.phase_id
        closure = close_event(
            candidate=12, confirmed=14, received=15, key="after-vent-close",
        )
        self.c.process(closure)
        self.assertFalse(self.c.process(closure).changed)
        self.c.advance(at(16))
        self.assert_ended_once(9.5, 9.5, ending=Kind.VENTILATION)
        self.assertEqual(self.c.session.after_run.phase_id, cooling_id)

    def test_presence_through_the_ventilated_exit_belongs_to_the_departing_person(self):
        self.c.process(event("exit-open", Kind.DOOR_OPEN, 8))
        self.c.process(event("exit-vent", Kind.VENTILATION, 9))
        self.assert_ended_once(9, 9, ending=Kind.VENTILATION)
        self.c.process(close_event(candidate=10, confirmed=12, received=13, key="exit-close"))
        # The sensor reports the departure only well after the closing.
        self.presence("off", 140)
        self.c.advance(at(900))
        self.assertIsNone(self.c.session.timeline.active)
        self.assertEqual(self.c.session.timeline.gang_count, 1)
        self.assertFalse(any(
            item.kind == Kind.PRESENCE_CONFIRMED and item.effective_at > at(3)
            for item in self.c.session.timeline.processed
        ))

    def test_return_after_the_seen_departure_starts_a_new_stay(self):
        self.c.process(event("exit-open", Kind.DOOR_OPEN, 8))
        self.c.process(event("exit-vent", Kind.VENTILATION, 9))
        self.presence("off", 9.5)
        self.presence("on", 11)
        self.c.process(close_event(candidate=12, confirmed=13, received=14, key="return-close"))
        gang = self.c.session.timeline.active
        self.assertIsNotNone(gang)
        self.assertNotEqual(gang.gang_id, self.gang_id)
        self.assertEqual(gang.start_source_event_id, "return-close")
        self.assertEqual(gang.started_at, at(12))

    def test_exit_does_not_need_the_minimum_temperature_again(self):
        self.c.process(event("exit-open", Kind.DOOR_OPEN, 8))
        self.presence("off", 9)
        minimum = self.c.parameters.values["sauna_min_temperature_c"]
        self.c.set_temperature(minimum - 1, at(9.2))
        self.c.process(close_event(
            candidate=10, confirmed=12, received=13, key="cold-exit-close",
        ))
        self.assert_ended_once(13, 8)

    def test_empty_door_operation_without_a_gang_creates_no_completion(self):
        c = controller()
        c.process(event("empty-open", Kind.DOOR_OPEN, 1))
        absence = binary_presence(ENTITY, "off", at(2), at(2))
        c.consume_occupancy_input(selected_occupancy(absence, at(2)))
        c.process(close_event())
        c.advance(at(900))
        self.assertIsNone(c.session.timeline.active)
        self.assertEqual(c.session.timeline.completed, ())
        self.assertIsNone(c.session.after_run)
        self.assertFalse(any(item.kind == "gang_ended" for item in c.consumer_events))


class ProxyExitTests(unittest.TestCase):
    def test_proxy_uses_person_confirmation_and_ventilation_without_sensor_presence(self):
        for confirmed in (False, True):
            with self.subTest(confirmed=confirmed):
                probe = PresenceRuntimeProbe(presence_source="proxy")
                probe.set_temperature(
                    probe.parameters.values["sauna_min_temperature_c"], at(0),
                )
                probe.process(event("entry-open", Kind.DOOR_OPEN, 1))
                probe.process(close_event(candidate=2, confirmed=3, received=3))
                probe.process(event("person", Kind.PERSON_STRONG, 4))
                self.assertEqual(
                    probe.session.timeline.active.confirmation, Confirmation.PROVISIONAL,
                )
                if confirmed:
                    probe.process(event("infusion", Kind.INFUSION, 5))
                    self.assertEqual(
                        probe.session.timeline.active.confirmation, Confirmation.CONFIRMED,
                    )
                gang_id = probe.session.timeline.active.gang_id
                probe.process(event("first-open", Kind.DOOR_OPEN, 8))
                probe.process(close_event(
                    candidate=10, confirmed=12, received=13, key="first-close",
                ))
                self.assertIsNotNone(probe.session.timeline.active)
                self.assertEqual(probe.session.timeline.active.gang_id, gang_id)
                self.assertEqual(probe.session.timeline.completed, ())
                self.assertIsNone(probe.session.after_run)
                self.assertIsNone(probe.direct_presence)

                probe.process(event("exit-open", Kind.DOOR_OPEN, 14))
                probe.process(event("exit-vent", Kind.VENTILATION, 15))
                self.assertIsNone(probe.session.timeline.active)
                if confirmed:
                    self.assertEqual(probe.session.timeline.gang_count, 1)
                    self.assertEqual(probe.session.timeline.completed[0].ended_at, at(15))
                else:
                    self.assertEqual(probe.session.timeline.completed, ())
                    self.assertEqual(probe.session.timeline.retracted[0].gang_id, gang_id)
                    self.assertIsNone(probe.session.after_run)

    def test_external_sensor_off_does_not_end_a_gang_in_selected_proxy_mode(self):
        probe = PresenceRuntimeProbe(presence_source="proxy")
        probe.set_temperature(
            probe.parameters.values["sauna_min_temperature_c"], at(0),
        )
        probe.process(event("entry-open", Kind.DOOR_OPEN, 1))
        probe.process(close_event(candidate=2, confirmed=3, received=3))
        probe.process(event("infusion", Kind.INFUSION, 4))
        gang_id = probe.session.timeline.active.gang_id
        probe.process(event("exit-open", Kind.DOOR_OPEN, 8))
        report(probe, "off", 9)
        probe.process(close_event(
            candidate=10, confirmed=12, received=13, key="exit-close",
        ))
        probe.advance(at(900))
        self.assertEqual(probe.presence_source, "proxy")
        self.assertEqual(probe.session.timeline.active.gang_id, gang_id)
        self.assertEqual(probe.session.timeline.completed, ())
        self.assertIsNone(probe.session.after_run)


class PresenceExitSourceChangeTests(unittest.TestCase):
    def test_proxy_ventilation_finishes_a_gang_started_with_sensor_presence(self):
        probe = PresenceRuntimeProbe()
        probe.process(event("entry-open", Kind.DOOR_OPEN, 1))
        report(probe, "on", 2)
        probe.process(close_event())
        original = probe.session.timeline.active
        switched = fallback(probe, 8)
        self.assertEqual(probe.presence_source, "proxy")
        self.assertEqual(probe.session.timeline.active, original)
        probe.process(event("exit-open", Kind.DOOR_OPEN, switched + 1))
        probe.process(event("exit-vent", Kind.VENTILATION, switched + 2))
        self.assertIsNone(probe.session.timeline.active)
        completed = probe.session.timeline.completed[0]
        self.assertEqual(completed.recognition_kind, Kind.PRESENCE_CONFIRMED)
        self.assertEqual(completed.gang_id, original.gang_id)
        self.assertEqual(completed.ended_at, at(switched + 2))
        self.assertEqual(completed.end_reason, "ventilation")
        self.assertEqual(probe.session.timeline.gang_count, 1)

    def test_recovered_sensor_ends_a_proxy_gang_by_absence_after_its_opening(self):
        probe = PresenceRuntimeProbe()
        switched = fallback(probe)
        for key, kind, offset in (
            ("entry-open", Kind.DOOR_OPEN, 1),
            ("entry-close", Kind.DOOR_CLOSE, 2),
            ("person", Kind.PERSON_STRONG, 3),
            ("infusion", Kind.INFUSION, 4),
            ("exit-open", Kind.DOOR_OPEN, 5),
        ):
            probe.process(event(key, kind, switched + offset))
        original = probe.session.timeline.active
        report(probe, "on", switched + 6)
        returned = switched + 6 + probe.parameters.values[RECOVERY]
        probe.advance_presence_source(at(returned))
        self.assertEqual(probe.presence_source, "ha_presence")
        self.assertEqual(probe.session.timeline.active, original)
        report(probe, "off", returned + 1)
        probe.advance(at(returned + 4))
        self.assertIsNone(probe.session.timeline.active)
        completed = probe.session.timeline.completed[0]
        self.assertEqual(completed.gang_id, original.gang_id)
        self.assertEqual(completed.recognition_kind, Kind.PERSON_STRONG)
        self.assertEqual(completed.end_reason, "presence_exit")
        self.assertEqual(completed.ended_at, at(switched + 5))
