"""Zusammenhängende Regelketten; Zeit und reale Rückmeldung separat steuerbar."""
from datetime import timedelta
import unittest

from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.timeline import Kind
from test_foundation import T0, event, parameters


def at(seconds):
    return T0 + timedelta(seconds=seconds)


def controller(**overrides):
    values = {**parameters().as_dict(), "target_temperature_c": 80,
        "safety_temperature_c": 110, "readiness_offset_c": 5,
        "readiness_hysteresis_c": 3, "thermostat_cooldown_minutes": 0,
        "heating_minutes": 1, "heating_reduction_minutes": 0.25,
        "forced_cooling_minutes": 1, "after_run_minutes": 0.5,
        "session_gap_minutes": 10, "heat_reset_minutes": 10, **overrides}
    result = Controller(Parameters(values))
    result.set_temperature(70, T0)
    result.set_operation(True, T0, session_id="s")
    return result


class CoolingTests(unittest.TestCase):
    def start(self, c):
        c.process(event("close", Kind.DOOR_CLOSE, 1))
        c.process(event("person", Kind.PERSON_STRONG, 2))
        c.process(event("infusion", Kind.INFUSION, 3))

    def finish(self, c):
        c.process(event("open", Kind.DOOR_OPEN, 70))
        c.process(event("vent", Kind.VENTILATION, 71))
        c.report_heating(False, at(71))

    def test_limit_during_gang_then_after_run_and_only_remaining_cooling(self):
        for after_seconds in (30, 60, 90):
            with self.subTest(after_seconds=after_seconds):
                c = controller(after_run_minutes=after_seconds / 60)
                c.report_heating(True, T0)
                self.start(c)
                gang_id = c.session.timeline.active.gang_id
                c.advance(at(60))
                self.assertEqual(c.session.timeline.active.gang_id, gang_id)
                self.assertTrue(c.last_decision.heat)
                self.assertIsNone(c.session.cooling.started_at)
                self.finish(c)
                self.assertEqual(c.phase, "nachlauf")
                self.assertFalse(c.last_decision.heat)
                self.assertEqual(c.session.timeline.gang_count, 1)
                c.process(event("close-again", Kind.DOOR_CLOSE, 72))
                blocked = c.process(event("during-after", Kind.INFUSION, 73))
                self.assertEqual(blocked.reason, "after_run")
                self.assertIsNone(c.session.timeline.active)
                c.advance(at(71 + after_seconds))
                if after_seconds < 60:
                    self.assertEqual(c.phase, "zwangskühlung")
                    self.assertEqual(c.session.cooling.credited_seconds, after_seconds)
                    self.assertEqual(c.session.cooling.ends_at, at(131))
                    blocked = c.process(event("during-cooling", Kind.INFUSION, 102))
                    self.assertEqual(blocked.reason, "forced_cooling")
                    c.advance(at(131))
                self.assertIsNone(c.session.cooling)
                self.assertIsNone(c.session.after_run)
                self.assertEqual(len(c.session.cooling_history), 1)
                cycle = c.session.cooling_history[0]
                self.assertEqual(cycle.credited_seconds, after_seconds)
                if after_seconds >= 60:
                    self.assertIsNone(cycle.started_at)
                self.assertEqual(c.heating_limit_seconds, 45)
                self.assertEqual(c.session.heating.elapsed_seconds, 0)
                self.assertTrue(c.last_decision.heat)
                # Ein gesperrter Start darf auch später nicht rückwirkend entstehen.
                restart = max(131, 71 + after_seconds) + 1
                c.process(event("new-infusion", Kind.INFUSION, restart))
                self.assertEqual(c.session.timeline.active.started_at, at(restart))

    def test_interrupting_operation_does_not_restart_or_double_credit_after_run(self):
        c = controller()
        c.report_heating(True, T0)
        self.start(c)
        self.finish(c)
        ends = c.session.after_run.ends_at
        c.set_operation(False, at(80))
        c.set_operation(True, at(90))
        self.assertEqual(c.session.after_run.ends_at, ends)
        self.assertIsNone(c.session.timeline.active)
        c.advance(at(100))
        self.assertEqual(c.phase, "nachlauf")
        c.advance(at(101))
        self.assertEqual(c.session.cooling.credited_seconds, 30)
        c.advance(at(110))
        self.assertEqual(c.session.cooling.credited_seconds, 30)
        self.assertEqual(c.session.cooling.ends_at, at(131))

    def test_defaults_reduce_once_and_new_session_restores_first_budget(self):
        c = controller(heating_minutes=90, heating_reduction_minutes=30,
                       forced_cooling_minutes=15, session_gap_minutes=1)
        self.assertEqual(c.heating_limit_seconds, 5400)
        c.report_heating(True, T0)
        c.advance(at(5400))
        self.assertEqual(c.phase, "zwangskühlung")
        c.report_heating(False, at(5400))
        c.advance(at(6300))
        self.assertEqual(c.heating_limit_seconds, 3600)
        c.report_heating(True, at(6300))
        c.advance(at(9900))
        c.report_heating(False, at(9900))
        c.advance(at(10800))
        self.assertEqual(c.heating_limit_seconds, 3600)
        self.assertEqual(len(c.session.cooling_history), 2)
        c.set_operation(False, at(10800))
        c.set_operation(True, at(10860), session_id="new")
        self.assertEqual(c.heating_limit_seconds, 5400)
        self.assertEqual(c.session.cooling_history, ())

    def test_provisional_retraction_has_no_after_run_or_count(self):
        c = controller(confirmation_minutes=1)
        c.process(event("close", Kind.DOOR_CLOSE, 1))
        c.process(event("person", Kind.PERSON_STRONG, 2))
        c.set_temperature(90, at(3))
        self.assertTrue(c.last_decision.heat)
        c.advance(at(61))
        self.assertIsNone(c.session.after_run)
        self.assertEqual(c.session.timeline.completed, ())
        self.assertEqual(c.session.timeline.gang_count, 0)
        self.assertFalse(c.last_decision.heat)

    def test_readiness_requires_reaching_offset_then_holds_configured_band(self):
        c = controller()
        self.assertEqual(c.phase, "aufheizen")
        c.set_temperature(84, at(1))
        self.assertEqual(c.phase, "aufheizen")
        self.assertTrue(c.last_decision.heat)
        c.set_temperature(85, at(2))
        self.assertEqual(c.phase, "bereit")
        self.assertFalse(c.last_decision.heat)
        c.set_temperature(83, at(3))
        self.assertEqual(c.phase, "bereit")
        self.assertFalse(c.last_decision.heat)
        c.set_temperature(82, at(4))
        self.assertEqual(c.phase, "bereit")
        self.assertTrue(c.last_decision.heat)
        self.assertEqual(c.session.session_id, "s")

    def test_actual_feedback_separate_from_demand_and_local_reset(self):
        c = controller(heat_reset_minutes=1)
        self.assertTrue(c.last_decision.heat)
        c.advance(at(100))
        self.assertEqual(c.session.heating.elapsed_seconds, 0)
        c.report_heating(True, at(100))
        c.report_heating(False, at(120))
        c.advance(at(179))
        self.assertEqual(c.session.heating.elapsed_seconds, 20)
        c.advance(at(180))
        self.assertEqual(c.session.heating.elapsed_seconds, 0)
        self.assertEqual(c.session.session_id, "s")
        self.assertEqual(c.session.cooling_history, ())

    def test_safety_survives_new_session_and_cannot_be_overridden_by_gang(self):
        c = controller(session_gap_minutes=1)
        self.start(c)
        c.protection.add("confirmed_controller_failure")
        c.advance(at(4))
        self.assertFalse(c.last_decision.heat)
        c.set_operation(False, at(5))
        c.set_temperature(70, at(6))
        c.set_operation(True, at(65), session_id="new")
        self.assertIn("confirmed_controller_failure", c.protection)
        self.assertFalse(c.last_decision.heat)

    def test_overtemperature_requires_more_than_ten_minutes_and_uses_double_cooling(self):
        c = controller(safety_temperature_c=105, overtemperature_minutes=10,
            forced_cooling_minutes=15, overtemperature_cooling_factor=2)
        c.set_temperature(106, at(1))
        c.advance(at(601))
        self.assertIsNone(c.session.cooling)
        c.advance(at(602))
        self.assertEqual(c.phase, "zwangskühlung")
        self.assertEqual(c.session.cooling.duration_seconds, 1800)
        self.assertEqual(c.session.cooling.reason, "overtemperature")
        self.assertEqual(c.session.cooling.ends_at, at(2402))
        self.assertTrue(c.session.operation_enabled)
        self.assertEqual(c.protection, set())
        c.advance(at(603))
        self.assertEqual(c.session.cooling.ends_at, at(2402))

    def test_temperature_recovery_or_missing_measurement_restarts_continuity_proof(self):
        for recovered in (105, 104, None):
            with self.subTest(recovered=recovered):
                c = controller(safety_temperature_c=105, overtemperature_minutes=10)
                c.set_temperature(106, at(1))
                c.set_temperature(recovered, at(600))
                c.set_temperature(106, at(601))
                c.advance(at(1201))
                self.assertIsNone(c.session.cooling)
                c.advance(at(1202))
                self.assertIsNotNone(c.session.cooling)

    def test_temperature_cooling_preserves_gang_then_credits_after_run(self):
        c = controller(safety_temperature_c=105, overtemperature_minutes=10,
            forced_cooling_minutes=15, overtemperature_cooling_factor=2)
        self.start(c)
        gang_id = c.session.timeline.active.gang_id
        c.set_temperature(106, at(4))
        c.advance(at(605))
        self.assertEqual(c.session.timeline.active.gang_id, gang_id)
        self.assertTrue(c.last_decision.heat)
        self.assertIsNone(c.session.cooling.started_at)
        c.process(event("open-end", Kind.DOOR_OPEN, 606))
        c.process(event("vent-end", Kind.VENTILATION, 607))
        c.advance(at(637))
        self.assertEqual(c.session.cooling.credited_seconds, 30)
        self.assertEqual(c.session.cooling.ends_at, at(2407))
        self.assertEqual(c.session.timeline.gang_count, 1)
        self.assertTrue(c.session.operation_enabled)

    def test_mechanical_timer_is_wall_time_and_not_reset_by_thermostat_or_operation_pause(self):
        c = controller(mechanical_timer_minutes=240)
        self.assertEqual(c.mechanical_timer_ends_at, at(14400))
        c.set_operation(False, at(20))
        c.set_operation(True, at(30))
        self.assertEqual(c.mechanical_timer_ends_at, at(14400))
        c.advance(at(14401))
        self.assertEqual(c.session.heating.elapsed_seconds, 0)
        self.assertTrue(c.session.operation_enabled)
