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
    def test_delayed_door_close_replaces_elapsed_open_door_wait(self):
        c = controller()
        c.report_heating(True, T0)
        c.process(event("open", Kind.DOOR_OPEN, 60))
        self.assertEqual(c.cooling_wait_until, at(660))

        c.process(replace(event("close", Kind.DOOR_CLOSE, 90), detected_at=at(500)))

        self.assertIsNone(c.cooling_wait_until)
        self.assertEqual(c.session.cooling.started_at, at(500))
        self.assertFalse(c.last_decision.heat)

    def test_door_opening_at_budget_boundary_waits_for_person_after_close(self):
        for ready in (False, True):
            with self.subTest(ready=ready):
                c = controller(confirmation_minutes=20)
                c.report_heating(True, T0)
                if ready:
                    c.set_temperature(85, at(1))
                c.process(event("open-wait", Kind.DOOR_OPEN, 60))
                self.assertIsNone(c.session.cooling.started_at)
                self.assertEqual(c.cooling_wait_until, at(660))
                c.process(event("close-wait", Kind.DOOR_CLOSE, 90))
                self.assertEqual(c.cooling_wait_until, at(330))
                c.advance(at(329))
                self.assertIsNone(c.session.cooling.started_at)
                # Ein Signal genau an der Grenze wird noch berücksichtigt.
                c.process(event("person-wait", Kind.PERSON_STRONG, 330))
                self.assertEqual(c.phase, "saunagang")
                self.assertIsNone(c.cooling_wait_until)
                self.assertIsNone(c.session.cooling.started_at)
                self.assertEqual(c.session.timeline.active.started_at, at(90))

    def test_no_person_starts_cooling_after_four_minutes_and_open_door_after_ten(self):
        for close_at, deadline in ((90, 330), (None, 660)):
            with self.subTest(close_at=close_at):
                c = controller()
                c.report_heating(True, T0)
                c.process(event("open-wait", Kind.DOOR_OPEN, 60))
                if close_at:
                    c.process(event("close-wait", Kind.DOOR_CLOSE, close_at))
                c.advance(at(deadline - 1))
                self.assertIsNone(c.session.cooling.started_at)
                c.advance(at(deadline))
                self.assertEqual(c.phase, "zwangskühlung")
                self.assertEqual(c.session.cooling.started_at, at(deadline))
                self.assertIsNone(c.cooling_wait_until)
                self.assertIsNone(c.session.timeline.active)
                self.assertEqual(c.session.timeline.gang_count, 0)

    def test_wait_is_configurable_and_does_not_create_cooling_before_budget(self):
        c = controller(heating_minutes=20, person_wait_minutes=0.5, open_door_wait_minutes=1)
        c.process(event("open-wait", Kind.DOOR_OPEN, 10))
        self.assertEqual(c.cooling_wait_until, at(70))
        c.process(event("close-wait", Kind.DOOR_CLOSE, 20))
        self.assertEqual(c.cooling_wait_until, at(50))
        c.advance(at(50))
        self.assertIsNone(c.session.cooling)
        self.assertIsNone(c.cooling_wait_until)

    def test_running_cooling_is_not_retracted_by_door_or_late_person(self):
        c = controller()
        c.report_heating(True, T0)
        c.advance(at(60))
        c.process(event("open-too-late", Kind.DOOR_OPEN, 61))
        c.process(event("close-too-late", Kind.DOOR_CLOSE, 62))
        result = c.process(event("person-too-late", Kind.PERSON_STRONG, 63))
        self.assertEqual(result.reason, "forced_cooling")
        self.assertEqual(c.session.cooling.started_at, at(60))
        self.assertIsNone(c.cooling_wait_until)

    def test_retracted_person_releases_pending_cooling_without_another_wait(self):
        c = controller(confirmation_minutes=1)
        c.report_heating(True, T0)
        c.process(event("open-wait", Kind.DOOR_OPEN, 59))
        c.process(event("close-wait", Kind.DOOR_CLOSE, 60))
        c.process(event("person-wait", Kind.PERSON_STRONG, 61))
        c.advance(at(120))
        self.assertEqual(c.phase, "zwangskühlung")
        self.assertEqual(c.session.timeline.gang_count, 0)
        self.assertEqual(c.session.timeline.completed, ())
        self.assertIsNone(c.session.after_run)

    def test_explicit_off_cancels_wait_and_duplicate_open_does_not_extend_it(self):
        c = controller()
        e = event("open-wait", Kind.DOOR_OPEN, 20)
        c.process(e)
        original = c.cooling_wait_until
        c.process(e)
        self.assertEqual(c.cooling_wait_until, original)
        c.set_operation(False, at(30))
        self.assertIsNone(c.cooling_wait_until)
        self.assertFalse(c.last_decision.heat)

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
        self.assertIsNone(c.session.ready_at)
        self.assertEqual(c.phase, "aufheizen")
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

    def test_confirmed_gang_and_actual_cooling_clear_readiness(self):
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

        cooling = controller()
        cooling.set_temperature(80, at(1))
        cooling.report_heating(True, at(1))
        cooling.advance(at(61))
        self.assertEqual(cooling.phase, "zwangskühlung")
        self.assertIsNone(cooling.session.ready_at)
        cooling.set_temperature(90, at(62))
        self.assertIsNone(cooling.session.ready_at)

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

    def test_persisting_overtemperature_requests_cooling_again_in_new_session(self):
        c = controller(safety_temperature_c=105, overtemperature_minutes=1,
            forced_cooling_minutes=15, session_gap_minutes=1)
        c.set_temperature(106, at(1))
        c.advance(at(62))
        self.assertTrue(c._temperature_cooling_requested)
        c.set_operation(False, at(63))
        c.set_operation(True, at(123), session_id="new")

        self.assertEqual(c.session.session_id, "new")
        self.assertEqual(c.overtemperature_since, at(1))
        self.assertEqual(c.phase, "zwangskühlung")
        self.assertFalse(c.last_decision.heat)
        self.assertEqual(c.last_decision.reason, "forced_cooling")

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
