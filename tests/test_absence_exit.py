"""G8 (K2, Nutzer 03.11.2026): Im Sensorbetrieb beendet ein Türvorgang mit Abwesenheit den Gang.

Der abgeschlossene Türvorgang im Gang bleibt bis zur nächsten Öffnung oder zum nächsten Aufguss als
Austritt verfügbar. Abwesenheit nach seiner Öffnung, die die letzte Meldung ist, beendet den Gang mit
diesem Türvorgang. Abwesenheit ohne Türvorgang beendet nie einen Gang; das Proxyverfahren bleibt
unverändert.
"""
import unittest

import test_direct_presence_control as direct
from test_cooling import at

from custom_components.ha_sauna.core.timeline import Confirmation, Kind


class AbsenceExitTests(unittest.TestCase):
    setUp = direct.DirectPresenceControlTests.setUp
    report = direct.DirectPresenceControlTests.report
    door = direct.DirectPresenceControlTests.door
    enter = direct.DirectPresenceControlTests.enter
    start = direct.DirectPresenceControlTests.start

    def assert_ended_by_absence(self, booked):
        timeline = self.c.session.timeline
        self.assertIsNone(timeline.active)
        self.assertEqual(len(timeline.completed), 1)
        gang = timeline.completed[-1]
        self.assertEqual(gang.end_reason, "absence")
        self.assertEqual(gang.ended_at, at(booked))
        end = next(e for e in timeline.processed if e.event_id == gang.end_event_id)
        self.assertEqual(end.kind, Kind.ABSENCE_EXIT)
        self.assertFalse(self.c.regulation_inputs.gang_heat_demand)
        self.assertEqual(sum(e.kind == "gang_ended" for e in self.c.consumer_events), 1)

    def test_door_operation_with_absence_ends_the_gang_at_the_closing(self):
        self.start()
        self.door(Kind.DOOR_OPEN, 5)
        self.report("off", 6)
        # While the door is open the absence ends only the gang heat.
        self.assertIsNotNone(self.c.session.timeline.active)
        self.assertFalse(self.c.regulation_inputs.gang_heat_demand)
        self.door(Kind.DOOR_CLOSE, 7)
        self.assert_ended_by_absence(7)
        self.c.advance(at(4000))
        self.assertEqual(len(self.c.session.timeline.completed), 1)

    def test_absence_after_the_closing_ends_the_gang_with_this_door_operation(self):
        self.start()
        self.door(Kind.DOOR_OPEN, 5)
        self.door(Kind.DOOR_CLOSE, 7)
        self.assertIsNotNone(self.c.session.timeline.active)
        self.report("off", 20)
        self.assert_ended_by_absence(20)
        gang = self.c.session.timeline.completed[-1]
        end = next(e for e in self.c.session.timeline.processed if e.event_id == gang.end_event_id)
        self.assertEqual(end.effective_at, at(7))

    def test_infusion_after_the_opening_keeps_the_gang(self):
        self.start()
        self.door(Kind.DOOR_OPEN, 5)
        self.report("off", 6)
        self.door(Kind.DOOR_CLOSE, 7)
        self.assert_ended_by_absence(7)
        self.setUp()
        self.start()
        self.door(Kind.DOOR_OPEN, 5)
        self.door(Kind.DOOR_CLOSE, 7)
        self.door(Kind.INFUSION, 8)
        self.report("off", 20)
        self.c.advance(at(4000))
        self.assertIsNotNone(self.c.session.timeline.active)
        self.assertEqual(self.c.session.timeline.completed, ())

    def test_presence_again_before_the_evaluation_keeps_the_gang(self):
        self.start()
        self.door(Kind.DOOR_OPEN, 5)
        self.report("off", 6)
        self.report("on", 6.5)
        self.door(Kind.DOOR_CLOSE, 7)
        self.c.advance(at(4000))
        self.assertIsNotNone(self.c.session.timeline.active)
        self.assertEqual(self.c.session.timeline.completed, ())
        self.assertTrue(self.c.regulation_inputs.gang_heat_demand)

    def test_absence_without_a_door_operation_never_ends_the_gang(self):
        self.start()
        self.report("off", 4)
        self.c.advance(at(4000))
        self.assertIsNotNone(self.c.session.timeline.active)
        self.assertEqual(self.c.session.timeline.completed, ())

    def test_unknown_or_unavailable_does_not_end_the_gang(self):
        for missing in ("unknown", "unavailable"):
            with self.subTest(missing=missing):
                self.setUp()
                self.start()
                self.door(Kind.DOOR_OPEN, 5)
                self.report(missing, 6)
                self.door(Kind.DOOR_CLOSE, 7)
                self.c.advance(at(4000))
                self.assertIsNotNone(self.c.session.timeline.active)

    def test_closing_with_absence_retracts_an_unconfirmed_gang(self):
        gang = self.enter()
        self.door(Kind.DOOR_OPEN, 5)
        self.report("off", 6)
        self.door(Kind.DOOR_CLOSE, 7)
        timeline = self.c.session.timeline
        self.assertIsNone(timeline.active)
        self.assertEqual(timeline.retracted[-1].gang_id, gang.gang_id)
        self.assertEqual(timeline.gang_count, 0)
        self.assertEqual(timeline.completed, ())

    def test_the_exit_door_operation_is_no_entry(self):
        self.start()
        self.door(Kind.DOOR_OPEN, 5)
        self.report("off", 6)
        self.door(Kind.DOOR_CLOSE, 7)
        self.assert_ended_by_absence(7)
        self.report("on", 9)
        self.c.advance(at(60))
        self.assertIsNone(self.c.session.timeline.active)

    def test_return_through_a_new_door_operation_starts_a_new_gang(self):
        self.start()
        self.door(Kind.DOOR_OPEN, 5)
        self.report("off", 6)
        self.door(Kind.DOOR_CLOSE, 7)
        first = self.c.session.timeline.completed[-1]
        self.door(Kind.DOOR_OPEN, 30)
        self.report("on", 31)
        self.door(Kind.DOOR_CLOSE, 32)
        gang = self.c.session.timeline.active
        self.assertIsNotNone(gang)
        self.assertNotEqual(gang.gang_id, first.gang_id)
        self.assertEqual(gang.confirmation, Confirmation.PROVISIONAL)
        self.assertEqual(gang.started_at, at(32))


if __name__ == "__main__":
    unittest.main()
