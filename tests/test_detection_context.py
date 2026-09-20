"""Erkennung prüft nur noch wirksame Signale aus dem führenden Gangkontext."""
from datetime import timedelta
import unittest

from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.detector import Detector
from custom_components.ha_sauna.core.models import Position, Quantity
from custom_components.ha_sauna.core.timeline import Event, Kind
from test_detector import detection_parameters, measurement, sample, trace
from test_foundation import T0, event
from test_manual_phases import after_run
from test_cooling import at, controller


class DetectionContextTests(unittest.TestCase):
    def test_only_the_possible_door_transition_is_evaluated(self):
        d = Detector(detection_parameters(), T0)
        events = []
        for second in range(401):
            events += sample(d, second, *trace(second))
            checks, conditions = d.diagnostic["checks"], d.diagnostic["conditions"]
            self.assertNotEqual(checks["door_open"], checks["door_close"])
            inactive = "door_close" if checks["door_open"] else "door_open"
            self.assertIsNone(conditions[inactive])
        self.assertEqual([e.kind for e in events if e.kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE)],
                         [Kind.DOOR_OPEN, Kind.DOOR_CLOSE, Kind.DOOR_OPEN])

    def test_person_search_is_disabled_for_provisional_and_confirmed_gang(self):
        c = controller()
        c.process(event("close", Kind.DOOR_CLOSE, 0))
        self.assertTrue(c.recognition_allowed(Kind.PERSON_STRONG))
        c.process(event("person", Kind.PERSON_STRONG, 1))
        for kind in (Kind.PERSON_STRONG, Kind.PERSON_WEAK):
            self.assertFalse(c.recognition_allowed(kind))
        self.assertTrue(c.recognition_allowed(Kind.INFUSION))
        c.process(event("infusion", Kind.INFUSION, 2))
        self.assertFalse(c.recognition_allowed(Kind.PERSON_STRONG))
        self.assertTrue(c.recognition_allowed(Kind.INFUSION))

    def test_retracted_start_stays_suppressed_until_new_door_episode_but_infusion_can_confirm(self):
        c = controller(confirmation_minutes=1)
        c.process(event("close", Kind.DOOR_CLOSE, 0))
        c.process(event("person", Kind.PERSON_STRONG, 1))
        c.advance(at(60))
        self.assertIsNone(c.session.timeline.active)
        self.assertFalse(c.recognition_allowed(Kind.PERSON_STRONG))
        self.assertTrue(c.recognition_allowed(Kind.INFUSION))
        c.process(event("open", Kind.DOOR_OPEN, 61))
        c.process(event("close2", Kind.DOOR_CLOSE, 62))
        self.assertTrue(c.recognition_allowed(Kind.PERSON_STRONG))

    def test_after_run_cooling_and_operation_off_suppress_gang_signals_without_losing_next_start(self):
        c = after_run()
        for second, phase in ((70,"nachlauf"),(100,"zwangskühlung")):
            c.advance(at(second))
            self.assertEqual(c.phase,phase)
            for kind in (Kind.PERSON_STRONG,Kind.PERSON_WEAK,Kind.INFUSION):
                self.assertFalse(c.recognition_allowed(kind))
        c.advance(at(130))
        self.assertTrue(c.recognition_allowed(Kind.PERSON_STRONG))
        self.assertTrue(c.recognition_allowed(Kind.INFUSION))
        # Alte Lüftung darf nach beendetem Nachlauf keine neue schwache
        # Erkennung freigeben, selbst wenn der Detektor noch Kontext besitzt.
        self.assertFalse(c.recognition_allowed(Kind.PERSON_WEAK))
        c.set_operation(False,at(140))
        self.assertFalse(c.recognition_allowed(Kind.PERSON_STRONG))
        self.assertFalse(c.recognition_allowed(Kind.INFUSION))

    def test_direct_infusion_suppresses_later_person_signals_even_in_one_catchup_batch(self):
        p = detection_parameters()
        c = Controller(p)
        c.begin_session("s",T0)
        c.process(event("close",Kind.DOOR_CLOSE,0))
        controlled, raw = Detector(p,T0), Detector(p,T0)
        for second in range(181):
            temperature = 70 + second*.01
            humidity = 20 + second*.001 + max(0,second-70)*.02 + (2 if second>=70 else 0) + (2 if second>=140 else 0)
            for position in (Position.UPPER,Position.LOWER):
                for quantity,value in ((Quantity.TEMPERATURE,temperature),(Quantity.HUMIDITY,humidity)):
                    m=measurement(position,quantity,value,second)
                    controlled.accept(m);raw.accept(m)
        def received(d):
            c.process(Event(f"signal:{d.kind}:{d.effective_at}","s",d.kind,d.effective_at,d.detected_at))
        end=T0+timedelta(seconds=180)
        expected=raw.advance(end,enabled=True)
        actual=controlled.advance(end,enabled=True,allowed=c.recognition_allowed,on_detection=received)
        self.assertIn(Kind.PERSON_STRONG,[d.kind for d in expected])
        self.assertEqual([d.kind for d in actual],[Kind.INFUSION,Kind.INFUSION])
        self.assertEqual(len(c.session.timeline.active.infusion_events),2)
        self.assertFalse(controlled.diagnostic["checks"]["strong"])
        self.assertTrue(controlled.diagnostic["checks"]["infusion"])
        self.assertNotIn("strong_temperature_slope",controlled.diagnostic["metrics"]["upper"])

    def test_skipped_person_checks_do_not_accumulate_hold_time(self):
        d=Detector(detection_parameters(),T0)
        now=0
        def allowed(kind):
            return kind in (Kind.PERSON_STRONG,Kind.PERSON_WEAK) and now>=130
        events=[]
        for now in range(151):
            for position in (Position.UPPER,Position.LOWER):
                for quantity,value in ((Quantity.TEMPERATURE,70+now*.01),(Quantity.HUMIDITY,20+now*.02)):
                    d.accept(measurement(position,quantity,value,now))
            events+=d.advance(at(now),enabled=True,allowed=allowed)
        people=[e for e in events if e.kind==Kind.PERSON_STRONG]
        self.assertEqual(len(people),1)
        self.assertEqual(people[0].effective_at,at(140))
