"""G9, G9b (Nutzer 04.11.2026): verworfene Öffnung mit Präsenz außerhalb eines Gangs.

Die Erkennung entscheidet unverändert und meldet verworfene Kandidaten zusätzlich als Episode: Ein Kandidat, der
nach dem Schlusskandidaten c eines verworfenen und spätestens bei c + T beginnt, gehört dazu (T = 1/k der gelernten
Schlussform); wird er bestätigt, entfällt die Episode. Im Sensorbetrieb wird die Episode ein Türvorgang, wenn nach
Abwesenheit „anwesend“ einsetzt, nach dem letzten Ereignis und spätestens beim letzten Schlusskandidaten. Ein
Eintritt braucht Präsenz bei ihrem Ende E.
"""
import asyncio
import unittest
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

import numpy as np
import test_direct_presence_control as direct
import test_recognition_model as model
from test_cooling import at
from test_foundation import T0
from test_presence_regressions import _runtime

from custom_components.ha_sauna.bindings import Bindings
from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.presence import binary_presence
from custom_components.ha_sauna.core.recognition import (
    ClimateRecognizer,
    RejectedOpening,
    _OpenDoor,
    recognition_event_id,
)
from custom_components.ha_sauna.core.recognition_model import closing_basis
from custom_components.ha_sauna.core.timeline import (
    Confirmation,
    DoorCloseEvidence,
    Event,
    Kind,
)
from custom_components.ha_sauna.runtime import SaunaRuntime


def recognizer(rate=0.05, humidity_rate=0.01):
    """Recognizer on a 2-s grid whose candidates the tests decide directly (as the closing tests do)."""
    params = model.parameters()
    params["close_form"] = {"rates": [[rate, 1 / 500], [rate, 1 / 500],
                                      [humidity_rate, 1 / 500], [humidity_rate, 1 / 500]]}
    r = ClimateRecognizer(params, model.T0)
    t = np.arange(0.0, 300.0, 2.0)
    form = closing_basis(t - 120.0, 0.05, 1 / 500)
    r._grid_t = list(t)
    r._grid_y = [np.array([a, b, 30.0, 28.0]) for a, b in zip(form @ [80.0, -0.08, 0.004],
                                                                 form @ [72.0, -0.07, 0.004])]
    r._steps = len(t)
    r._door.floor = len(t)                       # every step decided unless a test says otherwise
    return r


def moment(seconds):
    return model.T0 + timedelta(seconds=seconds)


class RejectedEpisodeTests(unittest.TestCase):
    def reject(self, r, start, close, decided):
        r._unconfirmed = (start, 0.9)
        out = r._reject(close, moment(decided))
        r._unconfirmed = None
        return out

    def test_the_transition_follows_the_learned_closing_form(self):
        self.assertEqual(recognizer()._closing_transition(), 20.0)
        self.assertEqual(recognizer(rate=0.025)._closing_transition(), 40.0)
        # humidity channels do not count; without a learned form there is no transition
        self.assertEqual(recognizer(humidity_rate=0.001)._closing_transition(), 20.0)
        plain = ClimateRecognizer(model.parameters(), model.T0)
        self.assertEqual(plain._closing_transition(), 0.0)
        # a follower 30 s after the closing candidate joins only the longer transition
        for rate, joined in ((0.05, False), (0.025, True)):
            with self.subTest(rate=rate):
                r = recognizer(rate=rate)
                self.reject(r, 5, 20, 42)
                out = r._follow(35, moment(72))
                self.assertEqual(r._episode is not None and r._episode.follower == 35, joined)
                self.assertEqual(len(out), 0 if joined else 1)

    def test_a_confirmed_follower_is_the_door_and_nothing_is_rescued(self):
        r = recognizer()
        self.reject(r, 5, 20, 42)
        self.assertEqual(r._follow(25, moment(50)), [])
        r._unconfirmed = (25, 0.9)
        opened = r._confirm_opening(moment(52), 25)
        self.assertEqual([d.kind for d in opened], [Kind.DOOR_OPEN])
        self.assertIsNone(r._episode)
        self.assertEqual(r._episode_due(moment(200)), [])
        self.assertEqual(r.decisions, [])

    def test_a_rejected_follower_extends_the_episode_to_one_door(self):
        r = recognizer()
        self.reject(r, 5, 20, 42)
        r._follow(25, moment(50))                  # 10 s after c = 40 s, within T = 20 s
        self.reject(r, 25, 40, 82)
        self.assertEqual(r._episode_due(moment(98)), [])   # E = max(82, 80 + 20) = 100 s
        r._door.floor = 40                         # a candidate up to c + T may still begin
        self.assertEqual(r._episode_due(moment(102)), [])
        r._door.floor = r._steps
        [episode] = r._episode_due(moment(102))
        self.assertIsInstance(episode, RejectedOpening)
        self.assertEqual(episode.candidates, (moment(10), moment(50)))
        self.assertEqual(episode.opened_at, moment(10))
        self.assertEqual(episode.close_candidate_at, moment(80))
        self.assertEqual(episode.ended_at, moment(100))
        self.assertEqual(episode.decided_at, moment(102))
        expected = min(r._closing_time(_OpenDoor(start=5, confirmed=r._steps - 1), 40), moment(100))
        self.assertEqual(episode.closed_at, expected)
        self.assertEqual(episode.door_close_evidence, DoorCloseEvidence(expected, moment(100)))
        self.assertIsNone(r._episode)
        self.assertEqual(r.detections, [])

    def test_a_follower_after_the_transition_starts_a_separate_episode(self):
        r = recognizer()
        self.reject(r, 5, 20, 42)
        [first] = r._follow(35, moment(72))        # 30 s after c: the first episode ends
        self.assertEqual(first.candidates, (moment(10),))
        self.assertEqual(first.ended_at, moment(60))
        self.assertEqual(self.reject(r, 35, 50, 102), [])
        [second] = r._episode_due(moment(122))
        self.assertEqual(second.candidates, (moment(70),))
        self.assertEqual(second.close_candidate_at, moment(100))

    def test_the_episode_ends_at_the_last_decision_when_it_is_later(self):
        r = recognizer()
        self.reject(r, 5, 20, 75)                  # decided 35 s after c
        [episode] = r._episode_due(moment(76))
        self.assertEqual(episode.ended_at, moment(75))


class RejectedOpeningControllerTests(unittest.TestCase):
    setUp = direct.DirectPresenceControlTests.setUp
    report = direct.DirectPresenceControlTests.report
    door = direct.DirectPresenceControlTests.door
    enter = direct.DirectPresenceControlTests.enter
    start = direct.DirectPresenceControlTests.start

    def rescue(self, opened=10, closed=20, candidate=25, ended=45, decided=46):
        """Book an episode as the runtime does: a door operation at its report, if the gang model takes it."""
        self.c.advance(at(decided), inclusive_confirmation=False)
        if not self.c.rejected_opening_is_door(at(opened), at(closed), at(candidate), at(ended), at(decided)):
            return False
        for kind, effective, proof in ((Kind.DOOR_OPEN, opened, None),
                                       (Kind.DOOR_CLOSE, closed, DoorCloseEvidence(at(closed), at(ended)))):
            self.c.process(Event(recognition_event_id("s", kind, at(effective)), "s", kind, at(effective),
                                 at(decided), trace_at=at(decided), door_close_evidence=proof),
                           defer_confirmation=True, recognition_at=at(decided), person_evidence_qualified=False)
        return True

    def doors(self):
        return [(e.kind, e.effective_at) for e in self.c.session.timeline.processed
                if e.kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE)]

    def test_presence_at_the_end_makes_the_door_and_a_provisional_gang(self):
        self.report("off", 1)
        self.report("on", 12)
        self.assertTrue(self.rescue())
        self.assertEqual(self.doors(), [(Kind.DOOR_OPEN, at(10)), (Kind.DOOR_CLOSE, at(20))])
        gang = self.c.session.timeline.active
        self.assertEqual(gang.recognition_kind, Kind.PRESENCE_ENTRY)
        self.assertEqual(gang.confirmation, Confirmation.PROVISIONAL)
        self.assertEqual(gang.started_at, at(20))
        self.door(Kind.INFUSION, 50)
        gang = self.c.session.timeline.active
        self.assertEqual(gang.confirmation, Confirmation.CONFIRMED)
        self.assertEqual(gang.started_at, at(20))

    def test_presence_ending_before_the_end_leaves_a_door_without_entry(self):
        self.report("off", 1)
        self.report("on", 12)
        self.report("off", 30)                     # after the closing candidate 25, before E = 45
        self.assertTrue(self.rescue())
        self.assertEqual(self.doors(), [(Kind.DOOR_OPEN, at(10)), (Kind.DOOR_CLOSE, at(20))])
        self.assertIsNone(self.c.session.timeline.active)
        self.report("on", 60)
        self.c.advance(at(4000))
        self.assertIsNone(self.c.session.timeline.active)
        self.assertEqual(self.c.session.timeline.retracted, ())

    def test_presence_after_the_closing_candidate_rescues_nothing(self):
        self.report("off", 1)
        self.report("on", 26)
        self.assertFalse(self.rescue())
        self.assertEqual(self.doors(), [])

    def test_presence_without_a_preceding_absence_rescues_nothing(self):
        self.report("on", 1)
        self.assertFalse(self.rescue())
        self.assertEqual(self.doors(), [])

    def test_within_a_gang_the_episode_stays_rejected(self):
        for begin in (self.start, self.enter):
            with self.subTest(gang=begin.__name__):
                self.setUp()
                begin()
                self.report("off", 5)
                self.report("on", 12)
                known = self.doors()
                self.assertFalse(self.rescue())
                self.assertEqual(self.doors(), known)

    def test_proxy_operation_rescues_nothing(self):
        self.assertFalse(self.rescue())
        self.assertEqual(self.doors(), [])

    def test_before_the_first_minimum_temperature_rescues_nothing(self):
        minimum = self.c.parameters.values["sauna_min_temperature_c"]
        self.c = Controller(self.c.parameters)
        self.c.set_temperature(minimum - 1, at(0))
        self.c.set_operation(True, at(0), session_id="s")
        self.report("off", 1)
        self.report("on", 12)
        self.assertFalse(self.rescue())
        self.assertEqual(self.doors(), [])
        self.assertIsNone(self.c.session.timeline.active)

    def test_an_event_within_the_episode_keeps_it_rejected(self):
        self.report("off", 1)
        self.report("on", 12)
        self.door(Kind.INFUSION, 15)
        self.assertFalse(self.rescue())
        self.assertEqual(self.doors(), [])

    def test_confirmed_doors_keep_the_rules_of_the_closing(self):
        # G8 unchanged: presence at the closing candidate that ends before its confirmation is still the entry
        self.door(Kind.DOOR_OPEN, 1)
        self.report("on", 2)
        self.report("off", 4)
        self.c.process(Event("close", "s", Kind.DOOR_CLOSE, at(3), at(5),
                             door_close_evidence=DoorCloseEvidence(at(3), at(5))),
                       person_evidence_qualified=False)
        self.assertEqual(self.c.session.timeline.active.started_at, at(3))


class RejectedOpeningRuntimeTests(unittest.TestCase):
    def test_the_runtime_books_the_rescued_door_at_the_report(self):
        async def exercise():
            clock = [T0]
            configuration = _runtime(clock).configuration
            configuration = replace(configuration, presence_source="ha_presence",
                bindings=Bindings({**configuration.bindings.values,
                                   "presence": "binary_sensor.presence"}))
            runtime = SaunaRuntime(configuration, clock=lambda: clock[0])
            runtime.controller.set_temperature(70, T0)
            await runtime.set_operation(True)
            sid = runtime.session.session_id

            async def presence(value, second):
                clock[0] = T0 + timedelta(seconds=second)
                await runtime.accept_presence(binary_presence(
                    "binary_sensor.presence", value, clock[0], clock[0]))

            def t(second):
                return T0 + timedelta(seconds=second)

            await presence("off", 1)
            await presence("on", 12)
            episode = RejectedOpening(t(10), t(20), t(25), t(45), t(46), (t(10),), ("Tu", "Tl", "Au", "Al"),
                                      DoorCloseEvidence(t(20), t(45)))
            delivered = []

            def advance(through, **callbacks):
                if through >= t(46) and not delivered:
                    delivered.append(through)
                    callbacks["on_rejected_opening"](episode)
                return []

            with patch.object(runtime.detector, "advance", side_effect=advance):
                clock[0] = t(47)
                runtime._deliver_detection(clock[0])
            doors = [(e.event_id, e.effective_at, e.booking_at) for e in runtime.session.timeline.processed
                     if e.kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE)]
            self.assertEqual(doors, [
                (recognition_event_id(sid, Kind.DOOR_OPEN, t(10)), t(10), t(46)),
                (recognition_event_id(sid, Kind.DOOR_CLOSE, t(20)), t(20), t(46)),
            ])
            gang = runtime.session.timeline.active
            self.assertEqual(gang.confirmation, Confirmation.PROVISIONAL)
            self.assertEqual(gang.started_at, t(20))
            self.assertEqual(runtime.detector.detections, [])

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
