"""Alte Budgets und Temperaturfristen erzeugen keine zusätzlichen Kühlzyklen."""
from datetime import timedelta
import unittest
from test_cooling import at, controller
from test_foundation import event
from custom_components.ha_sauna.core.timeline import Kind


def finish_gang(c, end_at):
    c.process(event("entry-open", Kind.DOOR_OPEN, 0))
    c.process(event("close", Kind.DOOR_CLOSE, 1))
    c.process(event("infusion", Kind.INFUSION, 2))
    c.process(event("open", Kind.DOOR_OPEN, end_at - 1))
    c.process(event("vent", Kind.VENTILATION, end_at))
    c.report_contactor(False, at(end_at))
    c.report_heating(False, at(end_at))


class OvenCoolingTests(unittest.TestCase):
    def test_gang_end_has_exactly_one_configured_oven_cooling(self):
        c = controller()
        c.report_heating(True, at(0))
        finish_gang(c, 600)
        phase = c.session.after_run
        self.assertIsNotNone(phase.ends_at)
        self.assertGreaterEqual(phase.duration_seconds, c.parameters.seconds("after_run_minutes"))
        self.assertFalse(c.last_decision.heat)
        c.advance(phase.ends_at - timedelta(microseconds=1))
        self.assertFalse(c.last_decision.heat)
        c.advance(phase.ends_at)
        self.assertIsNone(c.session.after_run)
        self.assertIsNone(c.session.cooling)
        self.assertEqual(c.session.cooling_history, ())
        self.assertEqual(len(c.session.after_run_history), 1)
        self.assertTrue(c.last_decision.heat)
        c.advance(phase.ends_at + timedelta(seconds=1))
        self.assertEqual(len(c.session.after_run_history), 1)
        self.assertIsNone(c.session.cooling)

    def test_operation_interruption_cancels_oven_cooling(self):
        c = controller()
        finish_gang(c, 60)
        deadline = c.session.after_run.ends_at
        self.assertIsNotNone(deadline)
        c.set_operation(False, at(100))
        self.assertFalse(c.last_decision.heat)
        self.assertFalse(c.session.operation_enabled)
        c.set_operation(True, at(120))
        self.assertTrue(c.session.operation_enabled)
        self.assertTrue(c.last_decision.heat)

    def test_historical_cycle_cannot_block_or_restart_active_control(self):
        from dataclasses import replace
        from custom_components.ha_sauna.core.models import CoolingCycle
        c = controller()
        cycle = CoolingCycle("old", at(0), 900, started_at=at(0), ends_at=at(900))
        c._session = replace(c.session, cooling=cycle, cooling_history=(cycle,))
        c.advance(at(60))
        self.assertTrue(c.last_decision.heat)
        self.assertFalse(c.recognition_allowed(Kind.INFUSION))
        c.process(event("entry-open", Kind.DOOR_OPEN, 60))
        c.process(event("close-legacy", Kind.DOOR_CLOSE, 61))
        self.assertTrue(c.recognition_allowed(Kind.INFUSION))
        c.process(event("infusion-legacy", Kind.INFUSION, 62))
        self.assertIsNotNone(c.session.timeline.active)
        self.assertEqual(c.session.cooling_history, (cycle,))
        with self.assertRaises(ValueError):
            c.finish_phase("forced_cooling", "old", at(63))
