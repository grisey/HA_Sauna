"""Taster-Wiedereinstieg aus pausierten automatischen Kühlphasen."""
import unittest

from custom_components.ha_sauna.core.display import start_availability
from custom_components.ha_sauna.core.timeline import Kind
from custom_components.ha_sauna.runtime import SaunaRuntime
from test_cooling import at, controller
from test_foundation import event


def completed_after_run():
    c = controller(after_run_minutes=8, forced_cooling_minutes=15)
    c.report_heating(True, at(0))
    for key, kind, second in (("close", Kind.DOOR_CLOSE, 1), ("person", Kind.PERSON_STRONG, 2),
                              ("infusion", Kind.INFUSION, 3), ("open", Kind.DOOR_OPEN, 60),
                              ("vent", Kind.VENTILATION, 61)):
        c.process(event(key, kind, second))
    c.advance(at(181))
    c.set_heater_override(True, at(181))
    return c


class CoolingReentryTests(unittest.TestCase):
    def test_new_gang_cancels_paused_after_run_and_credits_only_its_elapsed_time_once(self):
        c = completed_after_run()
        self.assertTrue(c.recognition_allowed(Kind.PERSON_STRONG))
        self.assertEqual(start_availability(c, at(181))["minimum_wait_seconds"], 0)
        c.process(event("close-new", Kind.DOOR_CLOSE, 182))
        c.process(event("person-new", Kind.PERSON_STRONG, 183))
        self.assertIsNotNone(c.session.timeline.active)
        self.assertIsNone(c.session.after_run)
        # Der alte Nachlauf wird beim tatsächlichen Gangstart storniert: sein
        # ungezählter Rest kehrt später nicht zurück.
        self.assertEqual(c.session.after_run_history[-1].elapsed_seconds, 120)
        self.assertEqual(c.session.cooling.credited_seconds, 120)
        c.process(event("infusion-new", Kind.INFUSION, 184))
        c.process(event("open-new", Kind.DOOR_OPEN, 200))
        c.process(event("vent-new", Kind.VENTILATION, 201))
        c.advance(at(681))
        self.assertEqual(c.session.after_run_history[-1].elapsed_seconds, 480)
        self.assertEqual(c.session.cooling.credited_seconds, 600)
        self.assertEqual(c.cooling_remaining_seconds, 300)
        self.assertEqual(c.session.cooling.ends_at, at(981))

    def test_manual_heat_without_a_gang_times_out_then_runs_the_after_run_remainder(self):
        c = completed_after_run()
        c.advance(at(781))  # 10-min Override endet, ohne neues Personensignal.
        self.assertIsNone(c.heater_override)
        self.assertIsNotNone(c.session.after_run)
        self.assertEqual(c.session.after_run.remaining_seconds, 360)
        self.assertEqual(c.session.after_run.ends_at, at(1141))
        self.assertEqual(c.session.cooling.credited_seconds, 0)

    def test_paused_cooling_allows_a_gang_then_applies_its_after_run_to_the_same_cycle(self):
        c = controller(after_run_minutes=8, forced_cooling_minutes=15)
        c.report_heating(True, at(0))
        c.advance(at(60))
        c.set_heater_override(True, at(61))
        c.process(event("close", Kind.DOOR_CLOSE, 62))
        c.process(event("person", Kind.PERSON_STRONG, 63))
        self.assertIsNotNone(c.session.timeline.active)
        c.process(event("infusion", Kind.INFUSION, 64))
        c.process(event("open", Kind.DOOR_OPEN, 100))
        c.process(event("vent", Kind.VENTILATION, 101))
        c.advance(at(581))
        self.assertEqual(c.session.cooling.credited_seconds, 480)
        self.assertEqual(c.cooling_remaining_seconds, 419)

    def test_protection_keeps_reentry_closed(self):
        c = completed_after_run()
        c.protection.add("heater_service_unavailable")
        c.advance(at(182))
        self.assertIsNone(c.heater_override)
        self.assertFalse(c.recognition_allowed(Kind.PERSON_STRONG))

    def test_button_turns_an_existing_manual_off_back_to_on_in_a_cooling_phase(self):
        c = controller(forced_cooling_minutes=15)
        c.report_heating(True, at(0))
        c.advance(at(60))
        c.set_heater_override(False, at(61))
        runtime = object.__new__(SaunaRuntime)
        runtime.controller, runtime.device = c, None
        runtime._toggle_button_heater_override(at(62))
        self.assertIs(c.heater_override, True)
        self.assertTrue(c.last_decision.heat)
