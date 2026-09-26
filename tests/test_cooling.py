"""Zusammenhängende Regelketten; Zeit und reale Rückmeldung separat steuerbar."""
from datetime import timedelta
from dataclasses import replace
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
        c.report_contactor(False, at(71))
        c.report_heating(False, at(71))

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
        self.assertEqual(c.session.ready_at, at(3))
        self.assertEqual(c.phase, "bereit")
        self.assertFalse(c.last_decision.heat)

    def test_readiness_latches_at_setpoint_and_survives_a_temperature_drop(self):
        c = controller()
        self.assertEqual(c.phase, "aufheizen")
        c.set_temperature(79, at(1))
        self.assertEqual(c.phase, "aufheizen")
        self.assertTrue(c.last_decision.heat)
        c.set_temperature(80, at(2))
        self.assertEqual(c.phase, "bereit")
        self.assertTrue(c.last_decision.heat)
        c.set_temperature(70, at(3))
        self.assertEqual(c.phase, "bereit")
        self.assertTrue(c.last_decision.heat)
        self.assertEqual(c.session.session_id, "s")

    def test_confirmed_gang_clears_readiness(self):
        c = controller()
        c.set_temperature(80, at(1))
        self.assertIsNotNone(c.session.ready_at)
        c.process(event("close", Kind.DOOR_CLOSE, 2))
        c.process(event("person", Kind.PERSON_STRONG, 3))
        self.assertIsNotNone(c.session.ready_at)  # Vorläufige Erkennung rollt zurück.
        c.process(event("infusion", Kind.INFUSION, 4))
        self.assertIsNone(c.session.ready_at)
        c.set_temperature(90, at(5))
        self.assertIsNone(c.session.ready_at)

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

    def test_mechanical_timer_is_independent_of_heating_feedback_and_pauses_with_operation_off(self):
        c = controller(mechanical_timer_minutes=240)
        c.report_contactor(True, at(0))
        self.assertEqual(c.mechanical_timer_ends_at, at(14400))
        c.report_heating(False, at(10))
        self.assertEqual(c.mechanical_timer_ends_at, at(14400))
        c.set_operation(False, at(20))
        self.assertIsNone(c.mechanical_timer_ends_at)
        self.assertEqual(c.mechanical_timer_status["state"], "paused")
        c.advance(at(29))
        self.assertEqual(c.mechanical_timer_status["remaining_seconds"], 14380)
        c.set_operation(True, at(30))
        self.assertEqual(c.mechanical_timer_ends_at, at(14410))
        c.advance(at(14411))
        self.assertEqual(c.mechanical_timer_status["state"], "expired")
        self.assertTrue(c.session.operation_enabled)

    def test_empty_session_preserves_timer_and_counted_session_resets_on_next_start(self):
        c = controller(session_gap_minutes=1)
        c.report_contactor(True, at(0))
        c.set_operation(False, at(20))
        c.advance(at(80))
        self.assertIsNone(c.session)
        c.set_operation(True, at(100), session_id="s2")
        self.assertEqual(c.mechanical_timer_status["remaining_seconds"], 14380)
        c.process(event("close", Kind.DOOR_CLOSE, 101, session="s2"))
        c.process(event("infusion", Kind.INFUSION, 102, session="s2"))
        c.set_operation(False, at(120))
        self.assertEqual(c.session.timeline.gang_count, 1)
        c.advance(at(180))
        self.assertTrue(c.mechanical_timer_status["reset_pending"])
        self.assertEqual(c.mechanical_timer_status["remaining_seconds"], 14360)
        c.set_operation(True, at(200), session_id="s3")
        self.assertEqual(c.mechanical_timer_status["remaining_seconds"], 14400)

    def test_temperature_program_distributes_then_holds_for_unlimited_gangs(self):
        c = controller(target_temperature_c=80, final_temperature_c=95,
                       temperature_gangs=4, after_run_minutes=.1,
                       heating_minutes=20)
        c.program_mode = "progressive"
        c.set_temperature(85, at(1))
        self.assertEqual(c.phase, "bereit")
        for index, expected in enumerate((85, 90, 95, 95, 95, 95)):
            start=10+index*20
            c.process(event(f"close{index}", Kind.DOOR_CLOSE, start))
            c.process(event(f"infusion{index}", Kind.INFUSION, start+1))
            c.process(event(f"open{index}", Kind.DOOR_OPEN, start+2))
            end_event=event(f"vent{index}", Kind.VENTILATION, start+3)
            c.process(end_event)
            c.report_contactor(False, at(start + 3))
            c.report_heating(False, at(start + 3))
            self.assertEqual(c.target_temperature, expected)
            self.assertFalse(c.process(end_event).changed)
            self.assertEqual(c.target_temperature, expected)
            self.assertFalse(c.last_decision.heat)  # Nachlauf hat Vorrang.
            c.advance(at(start+10))
            if index==0:
                self.assertEqual(c.phase, "aufheizen")
        c.set_operation(False, at(140))
        c.set_operation(True, at(150))
        self.assertEqual(c.target_temperature, 95)
        c.set_operation(False, at(160))
        c.advance(at(760))
        c.set_operation(True, at(761))
        self.assertEqual(c.target_temperature, 80)

    def test_retracted_gang_does_not_increase_target(self):
        c = controller(final_temperature_c=95)
        c.process(event("close", Kind.DOOR_CLOSE, 1))
        c.process(event("person", Kind.PERSON_STRONG, 2))
        c.process(event("open", Kind.DOOR_OPEN, 3))
        c.process(event("vent", Kind.VENTILATION, 4))
        self.assertEqual(c.session.timeline.gang_count, 0)
        self.assertEqual(c.target_temperature, 80)
