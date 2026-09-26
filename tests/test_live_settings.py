"""Temperaturänderungen ändern Ziele, niemals die Prioritäten des Ablaufkerns."""
from dataclasses import replace
import unittest

from custom_components.ha_sauna.core.display import phase_timer
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.timeline import Kind
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
    c.process(event(f"close-{index}", Kind.DOOR_CLOSE, start))
    c.process(event(f"infusion-{index}", Kind.INFUSION, start+1))
    c.process(event(f"open-{index}", Kind.DOOR_OPEN, start+2))
    c.process(event(f"vent-{index}", Kind.VENTILATION, start+3))


class LiveTemperatureTests(unittest.TestCase):
    def test_distribution_does_not_limit_real_gangs_after_end_change(self):
        c = controller(target_temperature_c=80, final_temperature_c=95,
            temperature_gangs=4, after_run_minutes=.1, heating_minutes=20)
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
            temperature_gangs=4, after_run_minutes=.1, heating_minutes=20)
        c.program_mode = "progressive"
        gang(c, 1, 10)
        self.assertEqual(c.target_temperature, 85)
        change(c, 14, final_temperature_c=100, explicit_target=False)
        self.assertEqual(c.target_temperature, 85)
        gang(c, 2, 30)
        self.assertEqual(c.target_temperature, 92.5)
        gang(c, 3, 50)
        self.assertEqual(c.target_temperature, 100)
        gang(c, 4, 70)
        self.assertEqual(c.target_temperature, 100)

    def test_live_distribution_count_uses_gangs_since_program_selection(self):
        c = controller(target_temperature_c=80, final_temperature_c=95,
            temperature_gangs=4, after_run_minutes=.1, heating_minutes=20)
        c.program_mode = "progressive"
        gang(c, 1, 10)
        gang(c, 2, 30)
        self.assertEqual(c.target_temperature, 90)
        change(c, 34, temperature_gangs=6, explicit_target=False)
        self.assertEqual(c.session.temperature_program_gangs, 6)
        self.assertEqual(c.target_temperature, 90)
        gang(c, 3, 50)
        self.assertEqual(c.target_temperature, 91.66666666666667)
        gang(c, 4, 70)
        self.assertEqual(c.target_temperature, 93.33333333333333)
        gang(c, 5, 90)
        self.assertEqual(c.target_temperature, 95)
        gang(c, 6, 110)
        self.assertEqual(c.target_temperature, 95)

    def test_one_distribution_point_reaches_a_different_end_after_one_gang(self):
        c = controller(target_temperature_c=80, final_temperature_c=95,
            temperature_gangs=1, after_run_minutes=.1, heating_minutes=20)
        c.program_mode = "progressive"
        self.assertEqual(c.target_temperature, 80)
        gang(c, 1, 10)
        self.assertEqual(c.target_temperature, 95)
        gang(c, 2, 30)
        self.assertEqual(c.target_temperature, 95)

    def test_manual_steps_follow_actual_gangs_and_hold_the_last_step(self):
        c = controller(
            target_temperature_c=80,
            final_temperature_c=90,
            temperature_gangs=3,
            after_run_minutes=.1,
            heating_minutes=20,
        )
        c.program_mode = "progressive"
        c.temperature_steps = (80, 86, 90)
        self.assertEqual(c.target_temperature, 80)
        for index, expected in enumerate((86, 90, 90, 90), start=1):
            gang(c, index, 10 + index * 20)
            self.assertEqual(c.target_temperature, expected)
            self.assertEqual(c.session.timeline.gang_count, index)

    def test_manual_step_selection_reanchors_at_the_current_actual_gang(self):
        c = controller(
            target_temperature_c=80,
            final_temperature_c=95,
            temperature_gangs=4,
            after_run_minutes=.1,
            heating_minutes=20,
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
            after_run_minutes=.1,
            heating_minutes=20,
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
            temperature_gangs=4, after_run_minutes=.1, heating_minutes=20)
        c.program_mode = "progressive"
        gang(c, 1, 10)
        self.assertEqual(c.target_temperature, 85)
        change(c, 14, final_temperature_c=100, explicit_target=False)
        self.assertEqual(c.target_temperature, 85)
        gang(c, 2, 30)
        self.assertEqual(c.target_temperature, 92.5)
        change(c, 34, final_temperature_c=98, explicit_target=False)
        self.assertEqual(c.target_temperature, 92.5)
        gang(c, 3, 50)
        self.assertEqual(c.target_temperature, 98)
        gang(c, 4, 70)
        self.assertEqual(c.target_temperature, 98)

    def test_direct_target_stays_constant_for_all_later_gangs(self):
        c = controller(target_temperature_c=80, final_temperature_c=95,
            temperature_gangs=4, after_run_minutes=.1, heating_minutes=20)
        c.program_mode = "progressive"
        gang(c, 1, 10)
        change(c, 14, target_temperature_c=80)
        self.assertEqual(c.target_temperature, 80)
        for index, start in enumerate((30, 50, 70), start=2):
            gang(c, index, start)
            self.assertEqual(c.target_temperature, 80)

    def test_new_program_restarts_distribution_at_confirmed_gang(self):
        c = controller(target_temperature_c=80, final_temperature_c=95,
            temperature_gangs=4, after_run_minutes=.1, heating_minutes=20)
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
                    self.assertEqual(c.session.after_run.remaining_seconds,
                                     before.after_run.remaining_seconds - 1)
                else:
                    self.assertIsNone(c.session.after_run)
                self.assertEqual(c.session.deadlines, before.deadlines)
                self.assertGreaterEqual(c.session.heating.elapsed_seconds, before.heating.elapsed_seconds)
                if phase != "open_door":
                    self.assertFalse(c.last_decision.heat)
                self.assertIsNone(c.session.timeline.active)

    def test_target_change_does_not_cancel_minimum_heat_or_thermostat_pause(self):
        c = controller(heating_minutes=90, thermostat_cooldown_minutes=5)
        c.report_heating(True, T0)
        c.set_temperature(90, at(1))
        change(c, 2, target_temperature_c=60)
        self.assertTrue(c.last_decision.heat)
        self.assertEqual(c.last_decision.reason, "minimum_heating")
        c.advance(at(600))
        c.report_heating(False, at(600))
        end = c.session.thermostat.cooldown_until
        change(c, 601, target_temperature_c=100)
        self.assertFalse(c.last_decision.heat)
        self.assertEqual(c.session.thermostat.cooldown_until, end)
        self.assertEqual(c.last_decision.reason, "thermostat_cooldown")
        c.advance(end)
        self.assertTrue(c.last_decision.heat)

    def test_lower_target_preserves_gang_but_never_overrides_protection(self):
        c = controller()
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
            change(c, 20, after_run_minutes=200)
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
        c.report_heating(False, at(62))
        self.assertEqual(phase_timer(c, at(63))["kind"], "after_run")
        c.advance(at(92))
        self.assertIsNone(c.session.after_run)
        self.assertIsNone(c.session.cooling)
        before = c.session
        self.assertNotEqual((phase_timer(c, at(93)) or {}).get("kind"), "cooling")
        self.assertIs(c.session, before)
        c.set_operation(False, at(94))
        self.assertEqual(phase_timer(c, at(94))["kind"], "session_gap")
        c.advance(at(694))
        self.assertIsNone(phase_timer(c, at(694)))
