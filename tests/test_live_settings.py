"""Temperaturänderungen ändern Ziele, niemals die Prioritäten des Ablaufkerns."""
from dataclasses import replace
import unittest

from custom_components.ha_sauna.core.display import phase_timer
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.timeline import Kind
from test_cooling import at, controller
from test_foundation import T0, event


def change(c, second, **values):
    merged = {**c.parameters.as_dict(), **values}
    if merged.get("final_temperature_c") is None:
        merged.pop("final_temperature_c", None)
    c.update_temperature_parameters(Parameters(merged), at(second),
        explicit_target="target_temperature_c" in values)


def gang(c, index, start):
    c.process(event(f"close-{index}", Kind.DOOR_CLOSE, start))
    c.process(event(f"infusion-{index}", Kind.INFUSION, start+1))
    c.process(event(f"open-{index}", Kind.DOOR_OPEN, start+2))
    c.process(event(f"vent-{index}", Kind.VENTILATION, start+3))


class LiveTemperatureTests(unittest.TestCase):
    def test_manual_target_rebases_after_two_gangs_without_recounting_them(self):
        c = controller(target_temperature_c=75, final_temperature_c=100,
            temperature_increase_c=5, after_run_minutes=.1)
        gang(c, 1, 10)
        c.advance(at(20))
        gang(c, 2, 30)
        self.assertEqual(c.target_temperature, 85)
        after_run, deadlines = c.session.after_run, c.session.deadlines
        timer = c.mechanical_timer
        change(c, 34, target_temperature_c=80)
        self.assertEqual(c.target_temperature, 80)
        self.assertEqual(c.session.timeline.gang_count, 2)
        self.assertEqual(c.session.after_run, after_run)
        self.assertEqual(c.session.deadlines, deadlines)
        self.assertEqual(c.mechanical_timer, timer)
        self.assertFalse(c.last_decision.heat)
        c.advance(at(45))
        gang(c, 3, 50)
        self.assertEqual(c.target_temperature, 85)
        change(c, 54, target_temperature_c=80)  # Gleicher gespeicherter Wert, neue Wirkung.
        self.assertEqual(c.target_temperature, 80)

    def test_rate_and_end_only_apply_to_future_gangs(self):
        c = controller(target_temperature_c=75, final_temperature_c=100,
            temperature_increase_c=5, after_run_minutes=.1)
        gang(c, 1, 10)
        change(c, 14, temperature_increase_c=2)
        self.assertEqual(c.target_temperature, 80)
        c.advance(at(20))
        gang(c, 2, 30)
        self.assertEqual(c.target_temperature, 82)
        change(c, 34, final_temperature_c=81)
        self.assertEqual(c.target_temperature, 81)
        change(c, 35, final_temperature_c=None)
        self.assertEqual(c.target_temperature, 81)
        c.advance(at(40))
        gang(c, 3, 50)
        self.assertEqual(c.target_temperature, 81)
        c.set_operation(False, at(60))
        c.advance(at(660))
        c.set_operation(True, at(661))
        self.assertEqual(c.target_temperature, 75)
        self.assertIsNone(c.session.temperature_base_c)

    def test_live_settings_preserve_cooling_after_run_and_door_wait(self):
        for phase in ("cooling", "after_run", "door_wait"):
            with self.subTest(phase=phase):
                c = controller()
                c.report_heating(True, T0)
                if phase == "door_wait":
                    c.process(event("wait", Kind.DOOR_OPEN, 59))
                elif phase == "after_run":
                    gang(c, 1, 57)
                c.advance(at(60))
                before = c.session
                change(c, 61, target_temperature_c=95, final_temperature_c=100, temperature_increase_c=1)
                self.assertEqual(c.session.session_id, before.session_id)
                self.assertEqual(c.session.cooling, before.cooling)
                self.assertEqual(c.session.after_run, before.after_run)
                self.assertEqual(c.session.deadlines, before.deadlines)
                self.assertGreaterEqual(c.session.heating.elapsed_seconds, before.heating.elapsed_seconds)
                if phase != "door_wait":
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
        change(c, 3, target_temperature_c=50)
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
        change(c, 7, target_temperature_c=105)
        self.assertFalse(c.last_decision.heat)
        self.assertEqual(c.session.deadlines, gap)

    def test_locked_parameter_is_rejected_before_any_state_change(self):
        c = controller()
        before = c.session
        with self.assertRaises(ValueError):
            change(c, 20, heating_minutes=200)
        self.assertIs(c.session, before)


class PhaseTimerTests(unittest.TestCase):
    def test_timer_follows_actual_phase_without_changing_deadlines(self):
        c = controller()
        c.report_heating(True, T0)
        self.assertEqual(phase_timer(c, at(1))["kind"], "minimum_heating")
        c.process(event("open", Kind.DOOR_OPEN, 2))
        self.assertEqual(phase_timer(c, at(2))["kind"], "person_wait")
        c.process(event("close", Kind.DOOR_CLOSE, 3))
        c.process(event("person", Kind.PERSON_STRONG, 4))
        self.assertEqual(phase_timer(c, at(5)), {"kind":"gang", "label":"Saunagang seit", "seconds":2, "mode":"elapsed"})
        c.process(event("infusion", Kind.INFUSION, 6))
        c.process(event("open2", Kind.DOOR_OPEN, 61))
        c.process(event("vent", Kind.VENTILATION, 62))
        c.report_heating(False, at(62))
        self.assertEqual(phase_timer(c, at(63))["kind"], "after_run")
        c.advance(at(92))
        self.assertEqual(phase_timer(c, at(92))["kind"], "cooling")
        before = c.session
        self.assertEqual(phase_timer(c, at(93))["seconds"], 29)
        self.assertIs(c.session, before)
        c.set_operation(False, at(94))
        self.assertEqual(phase_timer(c, at(94))["kind"], "session_gap")
        c.advance(at(694))
        self.assertIsNone(phase_timer(c, at(694)))
