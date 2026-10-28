"""Temperaturänderungen ändern Ziele, niemals die Prioritäten des Ablaufkerns."""
import unittest
from datetime import timedelta

from custom_components.ha_sauna.core.display import phase_timer
from custom_components.ha_sauna.core.models import TemperatureTargetMark
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.timeline import Event, Kind
from test_cooling import at, controller
from test_foundation import T0, event


def change(c, second, *, explicit_target=None, new_program=False, program_mode=None, **values):
    merged = {**c.parameters.as_dict(), **values}
    if merged.get("final_temperature_c") is None:
        merged.pop("final_temperature_c", None)
    c.update_temperature_parameters(Parameters(merged), at(second),
        explicit_target=("target_temperature_c" in values if explicit_target is None else explicit_target),
        new_program=new_program, program_mode=program_mode)


def gang(c, index, start):
    if index > 1:
        c.process(event(f"previous-exit-close-{index}", Kind.DOOR_CLOSE, start-2))
    c.process(event(f"entry-open-{index}", Kind.DOOR_OPEN, start-1))
    c.process(event(f"close-{index}", Kind.DOOR_CLOSE, start))
    c.process(event(f"infusion-{index}", Kind.INFUSION, start+1))
    c.process(event(f"open-{index}", Kind.DOOR_OPEN, start+2))
    c.process(event(f"vent-{index}", Kind.VENTILATION, start+3))
    c.report_contactor(False, at(start + 3))
    c.report_heating(False, at(start + 3))


class NextGangTemperatureTests(unittest.TestCase):
    def controller(self):
        c = controller(target_temperature_c=80, final_temperature_c=95,
            temperature_gangs=4)
        c.program_mode = "progressive"
        return c

    def start(self, c, index, start):
        if index > 1:
            c.process(event(f"previous-close-{index}", Kind.DOOR_CLOSE, start - 2))
        c.process(event(f"entry-{index}", Kind.DOOR_OPEN, start - 1))
        c.process(event(f"close-{index}", Kind.DOOR_CLOSE, start))
        c.process(event(f"infusion-{index}", Kind.INFUSION, start + 1))

    def finish(self, c, index, start):
        c.process(event(f"exit-{index}", Kind.DOOR_OPEN, start + 2))
        c.process(event(f"vent-{index}", Kind.VENTILATION, start + 3))
        c.report_contactor(False, at(start + 3))
        c.report_heating(False, at(start + 3))

    def test_choice_during_active_gang_waits_for_its_end(self):
        c = self.controller()
        self.start(c, 1, 10)
        self.assertEqual(c.next_gang_temperature, 85)
        history = c.session.target_history
        c.set_next_gang_temperature(88, at(11))
        self.assertEqual(c.target_temperature, 80)
        self.assertEqual(c.next_gang_temperature, 88)
        self.assertIs(c.session.target_history, history)
        self.finish(c, 1, 10)
        self.assertEqual(c.target_temperature, 88)
        gang(c, 2, 30)
        self.assertEqual(c.target_temperature, 90)
        self.assertEqual(c.session.target_history, (
            TemperatureTargetMark(T0, 80),
            TemperatureTargetMark(at(13), 88),
            TemperatureTargetMark(at(33), 90),
        ))

    def test_new_choice_during_overridden_gang_keeps_its_target(self):
        c = self.controller()
        c.set_next_gang_temperature(88, at(1))
        self.start(c, 1, 10)
        active_id = c.session.timeline.active.gang_id
        c.set_next_gang_temperature(92, at(11))
        self.assertEqual(c.target_temperature, 88)
        self.assertEqual(c.next_gang_temperature, 92)
        self.assertEqual(c.session.active_gang_temperature_id, active_id)
        self.finish(c, 1, 10)
        self.assertEqual(c.target_temperature, 92)
        self.assertIsNone(c.session.active_gang_temperature_id)
        gang(c, 2, 30)
        self.assertEqual(c.target_temperature, 90)
        self.assertIsNone(c.session.next_gang_temperature_c)
        self.assertEqual(c.session.target_history, (
            TemperatureTargetMark(T0, 80),
            TemperatureTargetMark(at(1), 88),
            TemperatureTargetMark(at(13), 92),
            TemperatureTargetMark(at(33), 90),
        ))

    def test_delayed_gang_end_records_target_at_booking_time(self):
        c = self.controller()
        self.start(c, 1, 10)
        c.process(event("exit", Kind.DOOR_OPEN, 12))
        c.process(Event(
            "delayed-vent", "s", Kind.VENTILATION, at(13), at(25),
            booking_at=at(20),
        ))
        self.assertEqual(c.session.timeline.completed[0].ended_at, at(20))
        self.assertEqual(c.target_temperature, 85)
        self.assertEqual(c.session.target_history, (
            TemperatureTargetMark(T0, 80),
            TemperatureTargetMark(at(20), 85),
        ))

    def test_blocking_provisional_gang_retraction_releases_choice(self):
        c = self.controller()
        c.process(event("entry", Kind.DOOR_OPEN, 0))
        c.process(event("close", Kind.DOOR_CLOSE, 1))
        c.process(event("person", Kind.PERSON_STRONG, 2))
        c.set_next_gang_temperature(88, at(3))
        self.assertEqual(c.target_temperature, 80)
        c.process(event("exit", Kind.DOOR_OPEN, 4))
        c.process(event("vent", Kind.VENTILATION, 5))
        self.assertIsNone(c.session.timeline.active)
        self.assertEqual(c.session.timeline.gang_count, 0)
        self.assertEqual(c.target_temperature, 88)
        self.assertEqual(c.session.next_gang_temperature_c, 88)

    def test_overridden_provisional_gang_retraction_does_not_consume_choice(self):
        c = self.controller()
        c.set_next_gang_temperature(88, at(0))
        c.process(event("entry", Kind.DOOR_OPEN, 0))
        c.process(event("close", Kind.DOOR_CLOSE, 1))
        c.process(event("person", Kind.PERSON_STRONG, 2))
        c.process(event("exit", Kind.DOOR_OPEN, 4))
        c.process(event("vent", Kind.VENTILATION, 5))
        self.assertEqual(c.session.timeline.gang_count, 0)
        self.assertEqual(c.target_temperature, 88)
        self.assertEqual(c.session.next_gang_temperature_c, 88)

    def test_named_steps_continue_without_reanchoring(self):
        c = self.controller()
        c.temperature_steps = (80, 87, 82, 95)
        c.set_next_gang_temperature(88, at(1))
        gang(c, 1, 10)
        self.assertEqual(c.target_temperature, 87)
        gang(c, 2, 30)
        self.assertEqual(c.target_temperature, 82)
        self.assertEqual(c.temperature_steps, (80, 87, 82, 95))

    def test_last_program_stage_returns_after_one_overridden_gang(self):
        c = self.controller()
        c.temperature_steps = (80,)
        c.set_next_gang_temperature(88, at(1))
        gang(c, 1, 10)
        self.assertEqual(c.target_temperature, 80)
        self.assertEqual(c.next_gang_temperature, 80)

    def test_explicit_program_selection_clears_pending_choice(self):
        c = self.controller()
        c.set_next_gang_temperature(88, at(1))
        change(c, 2, target_temperature_c=82, explicit_target=False,
            new_program=True, program_mode="progressive")
        self.assertEqual(c.target_temperature, 82)
        self.assertIsNone(c.session.next_gang_temperature_c)

    def test_confirmation_timeout_does_not_consume_pending_choice(self):
        c = controller(target_temperature_c=80, final_temperature_c=95,
            temperature_gangs=4)
        c.program_mode = "progressive"
        c.process(event("entry", Kind.DOOR_OPEN, 0))
        c.process(event("close", Kind.DOOR_CLOSE, 1))
        c.process(event("person", Kind.PERSON_STRONG, 2))
        c.set_next_gang_temperature(88, at(3))
        due_at = at(1 + c.parameters.seconds("confirmation_minutes"))
        booked_at = due_at + timedelta(seconds=7)
        c.advance(booked_at, evaluate=False)
        self.assertIsNone(c.session.timeline.active)
        self.assertEqual(c.target_temperature, 88)
        self.assertEqual(c.session.timeline.gang_count, 0)
        expired = c.session.timeline.processed[-1]
        self.assertEqual(
            (expired.effective_at, expired.booking_at), (due_at, booked_at)
        )
        self.assertEqual(c.session.target_history, (
            TemperatureTargetMark(T0, 80),
            TemperatureTargetMark(booked_at, 88),
        ))


class LiveTemperatureTests(unittest.TestCase):
    def test_target_history_keeps_final_same_time_values_without_tick_copies(self):
        c = controller()
        change(c, 0, target_temperature_c=85)
        change(c, 0, target_temperature_c=90)
        self.assertEqual(
            c.session.target_history, (TemperatureTargetMark(T0, 90),)
        )
        change(c, 10, target_temperature_c=95)
        change(c, 10, target_temperature_c=90)
        self.assertEqual(
            c.session.target_history, (TemperatureTargetMark(T0, 90),)
        )
        history = c.session.target_history
        c.advance(at(20))
        c.advance(at(30))
        change(c, 40, target_temperature_c=90)
        self.assertIs(c.session.target_history, history)

    def test_target_histories_end_with_the_session_and_restart_separately(self):
        c = controller()
        change(c, 1, target_temperature_c=85)
        c.set_operation(False, at(2))
        gap = c.parameters.seconds("session_gap_minutes")
        c.advance(at(3 + gap))
        self.assertIsNone(c.session)
        completed = c.completed_sessions[-1]
        old_history = (
            TemperatureTargetMark(T0, 80), TemperatureTargetMark(at(1), 85)
        )
        self.assertEqual(
            (completed.ended_at, completed.target_history),
            (at(2 + gap), old_history),
        )
        self.assertTrue(all(
            mark.at <= completed.ended_at for mark in completed.target_history
        ))
        c.set_operation(True, at(4 + gap), session_id="new")
        self.assertEqual(
            c.session.target_history, (TemperatureTargetMark(at(4 + gap), 85),)
        )
        change(c, 5 + gap, target_temperature_c=90)
        self.assertEqual(c.session.target_history, (
            TemperatureTargetMark(at(4 + gap), 85),
            TemperatureTargetMark(at(5 + gap), 90),
        ))
        self.assertEqual(c.completed_sessions[-1].target_history, old_history)

    def test_distribution_does_not_limit_real_gangs_after_end_change(self):
        c = controller(target_temperature_c=80, final_temperature_c=95,
            temperature_gangs=4)
        c.program_mode = "progressive"
        gang(c, 1, 10)
        gang(c, 2, 30)
        change(c, 34, final_temperature_c=100, explicit_target=False)
        self.assertEqual(c.target_temperature, 90)
        for index in range(3, 13):
            gang(c, index, 10 + (index - 1) * 20)
            self.assertEqual(c.target_temperature, 100)
            self.assertEqual(c.session.timeline.gang_count, index)
            self.assertTrue(c.session.operation_enabled)

    def test_end_change_keeps_current_stage_then_reaches_new_end(self):
        c = controller(target_temperature_c=80, final_temperature_c=95,
            temperature_gangs=4)
        c.program_mode = "progressive"
        gang(c, 1, 10)
        self.assertEqual(c.target_temperature, 85)
        history = c.session.target_history
        change(c, 14, final_temperature_c=100, explicit_target=False)
        self.assertEqual(c.target_temperature, 85)
        self.assertIs(c.session.target_history, history)
        gang(c, 2, 30)
        self.assertEqual(c.target_temperature, 93)
        gang(c, 3, 50)
        self.assertEqual(c.target_temperature, 100)
        gang(c, 4, 70)
        self.assertEqual(c.target_temperature, 100)
        self.assertEqual(c.session.target_history, (
            TemperatureTargetMark(T0, 80),
            TemperatureTargetMark(at(13), 85),
            TemperatureTargetMark(at(33), 93),
            TemperatureTargetMark(at(53), 100),
        ))

    def test_live_distribution_count_uses_gangs_since_program_selection(self):
        c = controller(target_temperature_c=80, final_temperature_c=95,
            temperature_gangs=4)
        c.program_mode = "progressive"
        gang(c, 1, 10)
        gang(c, 2, 30)
        self.assertEqual(c.target_temperature, 90)
        change(c, 34, temperature_gangs=6, explicit_target=False)
        self.assertEqual(c.session.temperature_program_gangs, 6)
        self.assertEqual(c.target_temperature, 90)
        gang(c, 3, 50)
        self.assertEqual(c.target_temperature, 92)
        gang(c, 4, 70)
        self.assertEqual(c.target_temperature, 93)
        gang(c, 5, 90)
        self.assertEqual(c.target_temperature, 95)
        gang(c, 6, 110)
        self.assertEqual(c.target_temperature, 95)

    def test_manual_step_selection_reanchors_at_the_current_actual_gang(self):
        c = controller(
            target_temperature_c=80,
            final_temperature_c=95,
            temperature_gangs=4,
        )
        c.program_mode = "progressive"
        gang(c, 1, 10)
        gang(c, 2, 30)
        c.update_temperature_parameters(
            Parameters(
                {
                    **c.parameters.as_dict(),
                    "target_temperature_c": 80,
                    "final_temperature_c": 90,
                    "temperature_gangs": 3,
                }
            ),
            at(34),
            program_mode="progressive",
            new_program=True,
            temperature_steps=(80, 86, 90),
        )
        self.assertEqual(c.target_temperature, 80)
        gang(c, 3, 50)
        self.assertEqual(c.target_temperature, 86)
        gang(c, 4, 70)
        self.assertEqual(c.target_temperature, 90)

    def test_end_edit_replaces_manual_steps_but_keeps_the_current_target(self):
        c = controller(
            target_temperature_c=80,
            final_temperature_c=90,
            temperature_gangs=3,
        )
        c.program_mode = "progressive"
        c.temperature_steps = (80, 86, 90)
        gang(c, 1, 10)
        self.assertEqual(c.target_temperature, 86)
        c.update_temperature_parameters(
            Parameters({**c.parameters.as_dict(), "final_temperature_c": 100}),
            at(14),
            explicit_target=False,
            temperature_steps=None,
        )
        self.assertEqual(c.target_temperature, 86)
        gang(c, 2, 30)
        self.assertEqual(c.target_temperature, 100)

    def test_repeated_end_changes_remember_gangs_since_program_selection(self):
        c = controller(target_temperature_c=80, final_temperature_c=95,
            temperature_gangs=4)
        c.program_mode = "progressive"
        gang(c, 1, 10)
        self.assertEqual(c.target_temperature, 85)
        change(c, 14, final_temperature_c=100, explicit_target=False)
        self.assertEqual(c.target_temperature, 85)
        gang(c, 2, 30)
        self.assertEqual(c.target_temperature, 93)
        change(c, 34, final_temperature_c=98, explicit_target=False)
        self.assertEqual(c.target_temperature, 93)
        gang(c, 3, 50)
        self.assertEqual(c.target_temperature, 98)
        gang(c, 4, 70)
        self.assertEqual(c.target_temperature, 98)

    def test_new_program_restarts_distribution_at_confirmed_gang(self):
        c = controller(target_temperature_c=80, final_temperature_c=95,
            temperature_gangs=4)
        c.program_mode = "progressive"
        gang(c, 1, 10)
        self.assertEqual(c.session.timeline.gang_count, 1)
        change(c, 14, target_temperature_c=70, final_temperature_c=90,
            temperature_gangs=3, explicit_target=False, new_program=True,
            program_mode="progressive")
        self.assertEqual(c.target_temperature, 70)
        self.assertEqual(c.session.timeline.gang_count, 1)
        gang(c, 2, 30)
        self.assertEqual(c.target_temperature, 80)
        gang(c, 3, 50)
        self.assertEqual(c.target_temperature, 90)
        self.assertEqual(c.session.target_history, (
            TemperatureTargetMark(T0, 80),
            TemperatureTargetMark(at(13), 85),
            TemperatureTargetMark(at(14), 70),
            TemperatureTargetMark(at(33), 80),
            TemperatureTargetMark(at(53), 90),
        ))

    def test_live_settings_preserve_oven_cooling_and_open_door(self):
        for phase in ("after_run", "open_door"):
            with self.subTest(phase=phase):
                c = controller()
                c.report_heating(True, T0)
                if phase == "open_door":
                    c.process(event("wait", Kind.DOOR_OPEN, 59))
                elif phase == "after_run":
                    gang(c, 1, 57)
                c.advance(at(60))
                before = c.session
                change(c, 61, target_temperature_c=95, final_temperature_c=100, temperature_gangs=6)
                self.assertEqual(c.session.session_id, before.session_id)
                self.assertIsNone(c.session.cooling)
                if before.after_run is not None:
                    self.assertEqual(c.session.after_run.phase_id, before.after_run.phase_id)
                    self.assertEqual(c.session.after_run.ends_at, before.after_run.ends_at)
                    self.assertEqual(
                        c.session.after_run.remaining_seconds,
                        before.after_run.remaining_seconds - 1,
                    )
                else:
                    self.assertIsNone(c.session.after_run)
                self.assertEqual(c.session.deadlines, before.deadlines)
                self.assertGreaterEqual(c.session.heating.elapsed_seconds, before.heating.elapsed_seconds)
                if phase != "open_door":
                    self.assertFalse(c.last_decision.heat)
                self.assertIsNone(c.session.timeline.active)

    def test_target_change_does_not_cancel_minimum_heat_or_thermostat_pause(self):
        c = controller()
        c.report_heating(True, T0)
        c.set_temperature(90, at(1))
        change(c, 2, target_temperature_c=60)
        self.assertTrue(c.last_decision.heat)
        self.assertEqual(c.last_decision.reason, "minimum_heating")
        minimum = c.parameters.seconds("minimum_heating_minutes")
        c.advance(at(minimum))
        c.report_heating(False, at(minimum))
        end = c.session.thermostat.cooldown_until
        change(c, minimum + 1, target_temperature_c=100)
        self.assertFalse(c.last_decision.heat)
        self.assertEqual(c.session.thermostat.cooldown_until, end)
        self.assertEqual(c.last_decision.reason, "thermostat_cooldown")
        c.advance(end)
        self.assertTrue(c.last_decision.heat)

    def test_lower_target_preserves_gang_but_never_overrides_protection(self):
        c = controller()
        c.process(event("entry-open", Kind.DOOR_OPEN, 0))
        c.process(event("close", Kind.DOOR_CLOSE, 1))
        c.process(event("person", Kind.PERSON_STRONG, 2))
        before = c.session.timeline.active
        change(c, 3, target_temperature_c=60)
        self.assertTrue(c.last_decision.heat)
        self.assertEqual(c.session.timeline.active, before)
        c.protection.add("confirmed_controller_failure")
        change(c, 4, target_temperature_c=95)
        self.assertFalse(c.last_decision.heat)
        self.assertEqual(c.session.timeline.active, before)
        c.protection.clear()
        c.inhibits.add("upper_temperature_stale")
        change(c, 5, target_temperature_c=100)
        self.assertFalse(c.last_decision.heat)
        c.set_operation(False, at(6))
        gap = c.session.deadlines
        change(c, 7, target_temperature_c=100)
        self.assertFalse(c.last_decision.heat)
        self.assertEqual(c.session.deadlines, gap)

    def test_locked_parameter_is_rejected_before_any_state_change(self):
        c = controller()
        before = c.session
        with self.assertRaises(ValueError):
            change(c, 20, after_run_minutes=c.parameters.values["after_run_minutes"] + 1)
        self.assertIs(c.session, before)


class PhaseTimerTests(unittest.TestCase):
    def test_timer_follows_actual_phase_without_changing_deadlines(self):
        c = controller()
        c.report_heating(True, T0)
        self.assertEqual(phase_timer(c, at(1))["kind"], "minimum_heating")
        c.process(event("open", Kind.DOOR_OPEN, 2))
        self.assertEqual(phase_timer(c, at(2))["kind"], "minimum_heating")
        c.process(event("close", Kind.DOOR_CLOSE, 3))
        c.process(event("person", Kind.PERSON_STRONG, 4))
        self.assertEqual(phase_timer(c, at(5)), {"kind":"gang", "label":"Saunagang seit", "seconds":2, "mode":"elapsed"})
        c.process(event("infusion", Kind.INFUSION, 6))
        c.process(event("open2", Kind.DOOR_OPEN, 61))
        c.process(event("vent", Kind.VENTILATION, 62))
        c.report_contactor(False, at(62))
        c.report_heating(False, at(62))
        self.assertEqual(phase_timer(c, at(63))["kind"], "after_run")
        end = c.session.after_run.ends_at
        c.advance(end)
        self.assertIsNone(c.session.after_run)
        self.assertIsNone(c.session.cooling)
        before = c.session
        self.assertNotEqual((phase_timer(c, end) or {}).get("kind"), "cooling")
        self.assertIs(c.session, before)
        c.set_operation(False, end)
        self.assertEqual(phase_timer(c, end)["kind"], "session_gap")
        complete = end + timedelta(seconds=c.parameters.seconds("session_gap_minutes"))
        c.advance(complete)
        self.assertIsNone(phase_timer(c, complete))
