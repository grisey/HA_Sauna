"""Session-/Gangketten mit kontrollierter Uhr und tatsächlichen Objektbezügen."""
from dataclasses import replace
from datetime import timedelta
import unittest

from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.models import Deadline
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.timeline import Confirmation, Kind
from test_foundation import T0, event, parameters


def at(seconds):
    return T0 + timedelta(seconds=seconds)


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.controller = Controller(Parameters({**parameters().as_dict(),
            "session_gap_minutes": 2, "confirmation_minutes": 1}))
        self.controller.set_operation(True, T0, session_id="s")

    def send(self, key, kind, second):
        return self.controller.process(event(key, kind, second))

    def start_gang(self):
        self.send("close", Kind.DOOR_CLOSE, 10)
        self.send("person", Kind.PERSON_STRONG, 20)

    def test_off_finishes_confirmed_gang_once_and_resume_only_session(self):
        self.start_gang()
        gang = self.controller.session.timeline.active
        self.send("infusion", Kind.INFUSION, 30)
        self.send("infusion2", Kind.INFUSION, 35)
        self.controller.set_operation(False, at(40))
        self.controller.set_operation(False, at(41))
        ended = self.controller.session.timeline.completed[0]
        self.assertEqual(ended.gang_id, gang.gang_id)
        self.assertEqual(ended.started_at, at(10))
        self.assertEqual(ended.ended_at, at(40))
        self.assertEqual(ended.end_reason, "ausgeschaltet")
        self.assertEqual(len(ended.infusion_events), 2)
        self.assertEqual(self.controller.session.timeline.gang_count, 1)
        self.assertNotIn("confirmation", [d.purpose for d in self.controller.session.deadlines])
        self.controller.set_operation(True, at(100))
        self.assertEqual(self.controller.session.session_id, "s")
        self.assertIsNone(self.controller.session.timeline.active)
        self.assertEqual(self.controller.session.timeline.gang_count, 1)

    def test_off_finishes_provisional_without_count(self):
        self.start_gang()
        self.controller.set_operation(False, at(30))
        self.assertEqual(self.controller.session.timeline.gang_count, 0)
        self.assertEqual(self.controller.session.timeline.completed[0].end_reason, "ausgeschaltet")
        self.controller.set_operation(True, at(40))
        self.assertIsNone(self.controller.session.timeline.active)

    def test_new_session_at_gap_boundary_has_fresh_children_and_old_timer_is_inert(self):
        self.start_gang()
        old = next(d for d in self.controller.session.deadlines if d.purpose == "confirmation")
        self.controller.set_operation(False, at(30))
        before = self.controller.session
        self.controller.set_operation(True, at(150), session_id="new")
        current = self.controller.session
        self.assertEqual(current.session_id, "new")
        self.assertIsNot(before.timeline, current.timeline)
        self.assertIsNot(before.heating, current.heating)
        self.assertEqual(current.timeline.processed, ())
        self.assertEqual(current.deadlines, ())
        self.assertEqual(current.heating.elapsed_seconds, 0)
        self.assertFalse(self.controller.consume_deadline(old, at(151)))
        self.assertEqual(len(self.controller.completed_sessions), 1)
        self.assertEqual(self.controller.completed_sessions[0].ended_at, at(150))
        with self.assertRaises(ValueError):
            self.send("old-input", Kind.INFUSION, 151)
        self.assertIs(self.controller.session, current)

    def test_expiry_retracts_gang_entirely_and_same_episode_cannot_restart(self):
        self.start_gang()
        self.controller.advance(at(69))
        self.assertIsNotNone(self.controller.session.timeline.active)
        self.controller.advance(at(70))
        timeline = self.controller.session.timeline
        self.assertIsNone(timeline.active)
        self.assertEqual(timeline.completed, ())
        self.assertEqual(timeline.gang_count, 0)
        self.assertEqual(len(timeline.retracted), 1)
        self.send("person-again", Kind.PERSON_STRONG, 75)
        self.assertIsNone(self.controller.session.timeline.active)
        self.assertNotIn("confirmation", [d.purpose for d in self.controller.session.deadlines])
        self.send("late-infusion", Kind.INFUSION, 80)
        gang = self.controller.session.timeline.active
        self.assertEqual(gang.started_at, at(10))
        self.assertEqual(gang.detected_at, at(80))
        self.assertEqual(gang.confirmed_at, at(80))
        self.assertEqual(gang.start_source_event_id, "close")
        self.assertEqual(self.controller.session.timeline.gang_count, 0)

    def test_late_person_gets_only_remaining_time(self):
        self.send("close", Kind.DOOR_CLOSE, 10)
        self.send("person", Kind.PERSON_STRONG, 68)
        deadline = self.controller.session.deadlines[0]
        self.assertEqual(deadline.due_at, at(70))
        self.controller.advance(at(70))
        self.assertIsNone(self.controller.session.timeline.active)

    def test_person_recognized_after_deadline_is_immediately_retracted(self):
        self.send("close", Kind.DOOR_CLOSE, 10)
        self.send("person", Kind.PERSON_STRONG, 80)
        self.assertIsNone(self.controller.session.timeline.active)
        self.assertEqual(self.controller.session.timeline.gang_count, 0)

    def test_ventilation_before_infusion_retracts_and_next_episode_can_start(self):
        self.start_gang()
        self.send("open", Kind.DOOR_OPEN, 30)
        self.send("vent", Kind.VENTILATION, 35)
        self.assertIsNone(self.controller.session.timeline.active)
        self.assertEqual(self.controller.session.timeline.completed, ())
        self.assertEqual(self.controller.session.deadlines, ())
        self.send("close2", Kind.DOOR_CLOSE, 40)
        self.send("person2", Kind.PERSON_WEAK, 45)
        self.assertEqual(self.controller.session.timeline.active.started_at, at(40))

    def test_short_door_operation_preserves_confirmation_deadline(self):
        self.start_gang()
        gang = self.controller.session.timeline.active
        deadline = self.controller.session.deadlines[0]
        self.send("open", Kind.DOOR_OPEN, 30)
        self.send("close2", Kind.DOOR_CLOSE, 40)
        self.send("person2", Kind.PERSON_STRONG, 45)
        self.assertEqual(self.controller.session.timeline.active, gang)
        self.assertEqual(self.controller.session.deadlines, (deadline,))
        self.send("infusion", Kind.INFUSION, 50)
        self.assertEqual(self.controller.session.timeline.active.gang_id, gang.gang_id)
        self.controller.advance(at(900))
        self.assertIsNotNone(self.controller.session.timeline.active)

    def test_infusion_at_deadline_confirms_and_no_duration_ends_confirmed_gang(self):
        self.start_gang()
        self.send("infusion", Kind.INFUSION, 70)
        self.controller.advance(at(70))
        self.assertEqual(self.controller.session.timeline.active.confirmation, Confirmation.CONFIRMED)
        self.controller.advance(at(2000))
        self.assertIsNotNone(self.controller.session.timeline.active)

    def test_duplicate_and_late_input_cannot_reanimate_ended_gang(self):
        self.start_gang()
        infusion = event("infusion", Kind.INFUSION, 30)
        self.controller.process(infusion)
        self.controller.set_operation(False, at(40))
        self.assertFalse(self.controller.process(infusion).changed)
        with self.assertRaises(ValueError):
            self.send("late", Kind.INFUSION, 35)
        self.assertIsNone(self.controller.session.timeline.active)
        self.assertEqual(self.controller.session.timeline.gang_count, 1)

    def test_off_dominates_same_time_recognition(self):
        self.start_gang()
        self.controller.set_operation(False, at(30))
        self.send("infusion", Kind.INFUSION, 30)
        self.assertIsNone(self.controller.session.timeline.active)
        self.assertEqual(self.controller.session.timeline.gang_count, 0)
