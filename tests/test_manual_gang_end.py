"""GE1 (Nutzer 10.11.2026): Ein laufender Gang lässt sich von Hand beenden.

Die Folgen sind dieselben wie bei einem erkannten Gangende (GE1-1). Das Gangende ist
der Tastendruck (GE1-3), in Automatik und Handbetrieb (GE1-4). Ein vorläufiger Gang
wird zurückgenommen. Der nächste Gang beginnt erst mit der nächsten Tür; ein
Gangende von Hand ergibt keinen Austritt-Türvorgang (GE1-6).
"""
import unittest

import test_direct_presence_control as direct
from test_cooling import at
from test_cooling import controller as automatic_controller
from test_foundation import event, parameters, selected_occupancy

from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.presence import binary_presence
from custom_components.ha_sauna.core.timeline import (
    Confirmation,
    Kind,
    project_door_cycles,
    project_gang_intervals,
)


class ManualGangEndTests(unittest.TestCase):
    setUp = direct.DirectPresenceControlTests.setUp
    report = direct.DirectPresenceControlTests.report
    door = direct.DirectPresenceControlTests.door
    enter = direct.DirectPresenceControlTests.enter
    start = direct.DirectPresenceControlTests.start

    def test_confirmed_gang_ends_at_the_press_like_a_detected_end(self):
        self.start()
        gang = self.c.session.timeline.active
        self.c.end_gang(gang.gang_id, at(10))
        timeline = self.c.session.timeline
        self.assertIsNone(timeline.active)
        ended = timeline.completed[-1]
        self.assertEqual(ended.gang_id, gang.gang_id)
        self.assertEqual(ended.end_reason, "manual")
        self.assertEqual(ended.ended_at, at(10))
        end = next(e for e in timeline.processed if e.event_id == ended.end_event_id)
        self.assertEqual(end.kind, Kind.MANUAL_GANG_END)
        self.assertEqual(end.effective_at, at(10))
        self.assertFalse(self.c.regulation_inputs.gang_heat_demand)
        self.assertEqual(sum(e.kind == "gang_ended" for e in self.c.consumer_events), 1)

    def test_consequences_equal_those_of_the_absence_exit_at_the_same_instant(self):
        def finish(manual):
            self.setUp()
            self.c.report_contactor(True, at(0))
            self.c.report_heating(True, at(0))
            self.start()
            if manual:
                self.c.end_gang(self.c.session.timeline.active.gang_id, at(20))
            else:
                self.door(Kind.DOOR_OPEN, 5)
                self.door(Kind.DOOR_CLOSE, 7)
                self.report("off", 20)
            self.c.report_contactor(False, at(21))
            self.c.report_heating(False, at(21))
            self.c.advance(at(30))
            s = self.c.session
            return (s.timeline.active, len(s.timeline.completed), s.timeline.completed[-1].ended_at,
                    self.c.phase, s.after_run is not None, s.ready_at,
                    s.next_gang_temperature_c, self.c.regulation_inputs.gang_heat_demand,
                    [e.kind for e in self.c.consumer_events if e.kind.startswith("gang_")])

        self.assertEqual(finish(True), finish(False))
        self.assertEqual(self.c.phase, "nachlauf")

    def test_provisional_gang_is_retracted(self):
        gang = self.enter()
        self.c.end_gang(gang.gang_id, at(5))
        timeline = self.c.session.timeline
        self.assertIsNone(timeline.active)
        self.assertEqual(timeline.completed, ())
        self.assertEqual(timeline.retracted[-1].gang_id, gang.gang_id)
        self.assertEqual(timeline.retracted[-1].confirmation, Confirmation.PROVISIONAL)

    def test_only_the_running_gang_can_be_ended(self):
        with self.assertRaisesRegex(ValueError, "läuft nicht mehr"):
            self.c.end_gang("missing", at(1))
        self.start()
        gang = self.c.session.timeline.active
        with self.assertRaisesRegex(ValueError, "läuft nicht mehr"):
            self.c.end_gang("other", at(5))
        self.c.end_gang(gang.gang_id, at(6))
        with self.assertRaisesRegex(ValueError, "läuft nicht mehr"):
            self.c.end_gang(gang.gang_id, at(7))
        self.assertEqual(len(self.c.session.timeline.completed), 1)

    def test_last_closed_door_operation_starts_no_new_gang(self):
        self.start()
        # Exit door while the sensor still reports presence: the gang continues.
        self.door(Kind.DOOR_OPEN, 5)
        self.door(Kind.DOOR_CLOSE, 7)
        gang = self.c.session.timeline.active
        self.assertIsNotNone(gang)
        self.c.end_gang(gang.gang_id, at(10))
        self.report("on", 11)
        self.c.advance(at(200))
        self.assertIsNone(self.c.session.timeline.active)
        # The next door with presence begins the next gang.
        self.door(Kind.DOOR_OPEN, 300)
        self.door(Kind.DOOR_CLOSE, 302)
        self.assertEqual(self.c.session.timeline.active.recognition_kind, Kind.PRESENCE_ENTRY)

    def test_door_open_at_the_press_starts_no_new_gang_with_its_closing(self):
        self.start()
        self.door(Kind.DOOR_OPEN, 5)
        gang = self.c.session.timeline.active
        self.c.end_gang(gang.gang_id, at(6))
        self.door(Kind.DOOR_CLOSE, 8)
        self.c.advance(at(200))
        self.assertIsNone(self.c.session.timeline.active)
        self.door(Kind.DOOR_OPEN, 300)
        self.door(Kind.DOOR_CLOSE, 302)
        self.assertIsNotNone(self.c.session.timeline.active)

    def test_projection_ends_at_the_press_without_an_exit_door(self):
        self.start()
        self.door(Kind.DOOR_OPEN, 5)
        self.door(Kind.DOOR_CLOSE, 7)
        gang = self.c.session.timeline.active
        self.c.end_gang(gang.gang_id, at(10))
        timeline = self.c.session.timeline
        interval = next(i for i in project_gang_intervals(timeline) if i["gang_id"] == gang.gang_id)
        self.assertEqual(interval["ended_at"], at(10).isoformat())
        self.assertTrue(interval["complete"])
        roles = [link["role"] for cycle in project_door_cycles(timeline)
                 for link in cycle["gang_links"] if link["gang_id"] == gang.gang_id]
        self.assertEqual(roles, ["entry"])


class ManualGangEndOperationModeTests(unittest.TestCase):
    def test_manual_mode_only_closes_the_gang(self):
        c = Controller(Parameters({**parameters().as_dict(), "target_temperature_c": 80}),
                       control_mode="manual")
        c.set_temperature(70, at(0))
        c.set_heater_override(True, at(1))
        c.set_temperature(80, at(2))
        c.consume_occupancy_input(selected_occupancy(binary_presence(
            "binary_sensor.presence", "on", at(3), at(3)), at(3)))
        sid = c.session.session_id
        c.process(event("entry-open", Kind.DOOR_OPEN, 4, session=sid))
        c.process(event("entry-close", Kind.DOOR_CLOSE, 5, session=sid))
        c.process(event("water", Kind.INFUSION, 6, session=sid))
        gang = c.session.timeline.active
        self.assertEqual(c.phase, "saunagang")
        c.end_gang(gang.gang_id, at(30))
        self.assertEqual(c.session.timeline.completed[-1].end_reason, "manual")
        self.assertIsNone(c.session.after_run)
        self.assertEqual(c.phase, "manuell")
        self.assertTrue(c.heater_override)

    def test_automatic_proxy_gang_ends_and_starts_the_cooling(self):
        c = automatic_controller()
        c.report_contactor(True, at(0))
        c.report_heating(True, at(0))
        c.process(event("entry-open", Kind.DOOR_OPEN, 0))
        c.process(event("close", Kind.DOOR_CLOSE, 1))
        c.process(event("person", Kind.PERSON_STRONG, 2))
        c.process(event("water", Kind.INFUSION, 3))
        gang = c.session.timeline.active
        c.end_gang(gang.gang_id, at(60))
        c.report_contactor(False, at(61))
        c.report_heating(False, at(61))
        self.assertEqual(c.session.timeline.completed[-1].end_reason, "manual")
        self.assertIsNotNone(c.session.after_run)
        self.assertEqual(c.phase, "nachlauf")

    def test_ended_session_rejects(self):
        c = automatic_controller()
        c.set_operation(False, at(1))
        with self.assertRaisesRegex(ValueError, "keine Session"):
            c.end_gang("x", at(2))


if __name__ == "__main__":
    unittest.main()
