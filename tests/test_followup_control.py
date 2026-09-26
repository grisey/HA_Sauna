"""Produktiver Controller: Quellen -> Gangveto, Türimpulse und Phasenprojektion."""
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest

from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.presence import ProxyPresenceSource, binary_presence
from custom_components.ha_sauna.core.timeline import Event, Kind
from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime
from test_foundation import T0, bindings


def at(s):
    return T0 + timedelta(seconds=s)


def ev(kind, s, *, effective=None):
    return Event(f"{kind}:{s}", "s", kind, at(s if effective is None else effective), at(s))


def controller(temperature=84, **values):
    c = Controller(Parameters({"target_temperature_c": 80, **values}))
    c.set_temperature(temperature, at(0))
    c.begin_session("s", at(0))
    c.report_contactor(False, at(0))
    return c


class FollowupControlTests(unittest.TestCase):
    def test_eligible_close_starts_a_level_demand_and_confirmed_minimum(self):
        c = controller(90)
        c.process(ev(Kind.DOOR_OPEN, 1))
        self.assertFalse(c.last_decision.heat)
        c.process(ev(Kind.DOOR_CLOSE, 2))
        self.assertEqual(c.last_decision.reason, "temporary_door_heat")
        self.assertTrue(c.regulation_inputs.temporary_door_heat)
        c.report_contactor(True, at(3))
        c.report_heating(True, at(3))
        c.set_temperature(100, at(4))
        self.assertEqual(c.last_decision.reason, "minimum_heating")
        self.assertEqual(c.session.heating.intervals[-1].started_at, at(3))
        c.advance(at(603))
        self.assertFalse(c.last_decision.heat)

    def test_door_events_never_create_a_deadline_or_delayed_request(self):
        c = controller(90)
        c.process(ev(Kind.DOOR_OPEN, 1))
        self.assertFalse(any(d.purpose == "door_request" for d in c.session.deadlines))
        c.advance(at(61))
        self.assertFalse(c.last_decision.heat)
        c.process(ev(Kind.DOOR_CLOSE, 62))
        self.assertEqual(c.last_decision.reason, "temporary_door_heat")
        self.assertFalse(any(d.purpose == "door_request" for d in c.session.deadlines))

    def test_gang_heat_demand_starts_and_retraction_releases_it(self):
        c = controller(90, confirmation_minutes=1, minimum_heating_minutes=0)
        c.process(ev(Kind.DOOR_CLOSE, 1))
        person = ev(Kind.PERSON_STRONG, 2)
        c.process_presence(ProxyPresenceSource.present(person), person)
        self.assertTrue(c.last_decision.heat)
        self.assertEqual(c.last_decision.reason, "gang_heat_demand")
        c.advance(at(61))
        self.assertFalse(c.last_decision.heat)
        self.assertEqual(c.last_decision.reason, "temperature_reached")
        c.protection.add("feedback_failure")
        c.process(ev(Kind.DOOR_OPEN, 62))
        c.process(ev(Kind.DOOR_CLOSE, 63))
        protected_person = ev(Kind.PERSON_STRONG, 64)
        c.process_presence(ProxyPresenceSource.present(protected_person), protected_person)
        self.assertFalse(c.last_decision.heat)

    def test_manual_heating_hands_off_to_live_gang_demand(self):
        c = controller(90, minimum_heating_minutes=0)
        c.set_heater_override(True, at(1))
        c.report_heating(True, at(2))
        c.process(ev(Kind.DOOR_CLOSE, 3))
        c.process(ev(Kind.PERSON_STRONG, 4))
        self.assertIsNone(c.heater_override)
        self.assertTrue(c.last_decision.heat)
        self.assertEqual(c.last_decision.reason, "gang_heat_demand")

    def test_door_request_bypasses_upper_limit_but_cooling_blocks_it(self):
        c = controller(90)
        c.process(ev(Kind.DOOR_OPEN, 1))
        c.process(ev(Kind.DOOR_CLOSE, 2))
        self.assertEqual(c.last_decision.reason, "temporary_door_heat")
        c.report_contactor(False, at(3))
        c.report_heating(False, at(3))
        c.process(ev(Kind.INFUSION, 4))
        c.process(ev(Kind.DOOR_OPEN, 5))
        c.process(ev(Kind.VENTILATION, 6))
        self.assertEqual(c.last_decision.reason, "after_run")
        c.process(ev(Kind.DOOR_CLOSE, 7))
        self.assertEqual(c.last_decision.reason, "after_run")

    def test_retracted_candidate_restores_all_background_and_preserves_actual_track(self):
        c = controller(70, confirmation_minutes=1, minimum_heating_minutes=0)
        c.process(ev(Kind.DOOR_CLOSE, 5))
        c.process(ev(Kind.PERSON_STRONG, 10, effective=8))
        c.set_temperature(80, at(20))
        c.report_contactor(True, at(20))
        c.report_contactor(False, at(30))
        c.report_contactor(None, at(40))
        c.report_contactor(False, at(50))
        track = c.session.contactor_history
        c.advance(at(65))
        p = c.phase_projection(at(70))
        self.assertEqual([(i.phase, i.started_at, i.ended_at) for i in p.intervals],
                         [("aufheizen", at(0), at(20)), ("bereit", at(20), at(70))])
        self.assertEqual([(i.started_at, i.ended_at) for i in p.readiness_pauses],
                         [(at(30), at(40)), (at(50), at(70))])
        self.assertEqual(c.session.contactor_history, track)
        self.assertTrue(p.complete)

    def test_backdated_confirmed_gang_has_no_overlap_or_historical_switch(self):
        c = controller(70)
        c.process(ev(Kind.DOOR_CLOSE, 5))
        c.process(ev(Kind.PERSON_STRONG, 20, effective=10))
        c.process(ev(Kind.INFUSION, 25))
        c.process(ev(Kind.DOOR_OPEN, 30))
        c.process(ev(Kind.VENTILATION, 35))
        c.advance(at(40))
        projected = c.phase_projection(at(40)).intervals
        self.assertEqual([(i.phase, i.started_at, i.ended_at) for i in projected], [
            ("aufheizen", at(0), at(5)), ("saunagang", at(5), at(35)),
            ("nachlauf", at(35), at(40))])
        self.assertEqual(sum((i.ended_at-i.started_at).total_seconds() for i in projected), 40)
        self.assertTrue(all(a.at <= b.at for a, b in zip(c.decisions, c.decisions[1:])))
        ids = [e.event_id for e in c.consumer_events]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(any(e.delivery == "archive_correction" for e in c.consumer_events))


class PresenceRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_external_preview_is_separate_and_reload_deduplicates_consumers(self):
        config = Configuration(bindings(), Parameters({}), presence_source="ha_presence")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.sqlite"
            runtime = SaunaRuntime(config, clock=lambda: at(0))
            await runtime.start_archive(path, "test")
            report = binary_presence("binary_sensor.presence", "on", at(-5000), at(0))
            await runtime.accept_presence(report)
            await runtime.accept_presence(replace(report, received_at=at(1)))
            self.assertIsNone(runtime.session)
            self.assertIsNone(runtime.presence.current)
            self.assertEqual(runtime.presence_status["effective_source"], "proxy")
            self.assertEqual(len(runtime.presence.observations), 1)
            self.assertEqual(len(runtime.consumer_events), 1)
            await runtime.close()
            reloaded = SaunaRuntime(config, clock=lambda: at(10))
            await reloaded.start_archive(path, "test")
            await reloaded.accept_presence(replace(report, received_at=at(10)))
            self.assertEqual(reloaded.consumer_events, [])
            self.assertEqual(reloaded.presence.external[report.source].occupancy, "present")
            await reloaded.close()

    async def test_proxy_path_preserves_events_and_retraction_is_not_absence(self):
        now = at(0)
        runtime = SaunaRuntime(Configuration(bindings(), Parameters({"confirmation_minutes": 1})), clock=lambda: now)
        await runtime.begin_session("s")
        now = at(1)
        await runtime.receive(ev(Kind.DOOR_CLOSE, 1))
        now = at(2)
        event = ev(Kind.PERSON_STRONG, 2)
        await runtime.receive(event)
        self.assertEqual(runtime.presence.current.source_ref, event.event_id)
        self.assertEqual(runtime.session.timeline.active.recognition_event_id, event.event_id)
        now = at(61)
        await runtime.tick()
        self.assertEqual(runtime.presence.current.occupancy, "unknown")
        self.assertEqual(runtime.presence.current.assertion, "proxy_retraction")
        self.assertTrue(any(e.kind == "gang_retracted" for e in runtime.consumer_events))
        await runtime.close()
