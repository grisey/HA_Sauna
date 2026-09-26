"""Die Timeranzeige folgt der Schützrückmeldung, nicht der Heizentscheidung."""
import unittest

from test_cooling import at, controller
from test_foundation import event
from custom_components.ha_sauna.core.timeline import Kind


class MechanicalTimerTests(unittest.TestCase):
    def test_start_waits_for_contactor_feedback_not_command_or_heating_measurement(self):
        c = controller()
        self.assertTrue(c.last_decision.heat)
        c.report_heating(True, at(10))
        c.advance(at(20))
        self.assertEqual(c.mechanical_timer_status["remaining_seconds"], 14400)
        self.assertEqual(c.mechanical_timer_status["pause_reason"], "contactor_unavailable")
        self.assertIsNone(c.mechanical_timer_ends_at)
        c.report_contactor(True, at(20))
        self.assertEqual(c.mechanical_timer_ends_at, at(14420))

    def test_contactor_off_and_unknown_pause_without_losing_or_double_counting_time(self):
        c = controller()
        c.report_contactor(True, at(0))
        c.report_contactor(True, at(10))
        c.report_contactor(False, at(20))
        cycle = c.mechanical_timer.cycle_id
        self.assertEqual(c.mechanical_timer_status["pause_reason"], "contactor_off")
        c.report_contactor(None, at(40))
        c.advance(at(60))
        self.assertEqual(c.mechanical_timer_status["remaining_seconds"], 14380)
        self.assertIsNone(c.mechanical_timer_ends_at)
        c.report_contactor(True, at(70))
        c.report_contactor(True, at(80))
        c.report_contactor(False, at(90))
        self.assertEqual(c.mechanical_timer_status["remaining_seconds"], 14360)
        self.assertEqual(c.mechanical_timer.cycle_id, cycle)
        self.assertTrue(c.session.operation_enabled)

    def test_timer_during_oven_cooling_pauses_only_when_contactor_is_actually_off(self):
        c = controller()
        c.report_contactor(True, at(0))
        c.report_heating(True, at(0))
        c.process(event("close", Kind.DOOR_CLOSE, 1))
        c.process(event("infusion", Kind.INFUSION, 2))
        c.advance(at(60))
        c.process(event("open", Kind.DOOR_OPEN, 69))
        c.process(event("vent", Kind.VENTILATION, 70))
        self.assertEqual(c.phase, "nachlauf")
        self.assertFalse(c.last_decision.heat)
        # Der Ausschaltbefehl allein ist noch keine Schützrückmeldung.
        c.advance(at(72))
        self.assertEqual(c.mechanical_timer_status["remaining_seconds"], 14328)
        c.report_contactor(False, at(72))
        c.report_heating(False, at(72))
        c.advance(at(100))
        self.assertIsNone(c.session.after_run)
        self.assertIsNone(c.session.cooling)
        c.advance(at(130))
        self.assertEqual(c.mechanical_timer_status["remaining_seconds"], 14328)
        self.assertTrue(c.last_decision.heat)
        c.report_contactor(True, at(132))
        c.advance(at(142))
        self.assertEqual(c.mechanical_timer_status["remaining_seconds"], 14318)

    def test_expiry_and_warning_time_move_with_contactor_pauses(self):
        c = controller(mechanical_timer_minutes=1)
        c.report_contactor(True, at(0))
        c.report_contactor(False, at(30))
        c.advance(at(100))
        self.assertEqual(c.mechanical_timer_status["state"], "paused")
        self.assertEqual(c.mechanical_timer_status["remaining_seconds"], 30)
        c.report_contactor(True, at(100))
        self.assertEqual(c.mechanical_timer_ends_at, at(130))
        c.advance(at(130))
        self.assertEqual(c.mechanical_timer_status["state"], "expired")
        self.assertTrue(c.last_decision.heat)
        self.assertTrue(c.session.operation_enabled)
        self.assertEqual(c.protection, set())

    def test_operation_off_does_not_restart_timer_on_late_contactor_report(self):
        c = controller()
        c.report_contactor(True, at(0))
        c.set_operation(False, at(20))
        c.report_contactor(True, at(30))
        c.report_contactor(False, at(40))
        c.set_operation(True, at(50))
        self.assertEqual(c.mechanical_timer_status["remaining_seconds"], 14380)
        self.assertIsNone(c.mechanical_timer_ends_at)
        c.report_contactor(True, at(60))
        self.assertEqual(c.mechanical_timer_ends_at, at(14440))
