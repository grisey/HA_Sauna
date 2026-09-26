"""Manuelles Phasenende nutzt denselben Folgeablauf mit wirklichen Zeitpunkten."""
import unittest

from test_cooling import Controller, at, controller
from test_foundation import event
from custom_components.ha_sauna.core.timeline import Kind


def after_run(**values):
    c = controller(**values)
    c.report_heating(True, at(0))
    c.process(event("close", Kind.DOOR_CLOSE, 1))
    c.process(event("infusion", Kind.INFUSION, 2))
    c.process(event("open", Kind.DOOR_OPEN, 69))
    c.process(event("vent", Kind.VENTILATION, 70))
    c.report_contactor(False, at(70))
    c.report_heating(False, at(70))
    return c


class ManualPhaseTests(unittest.TestCase):
    def test_early_oven_cooling_returns_to_regulation_without_extra_cycle(self):
        c = after_run()
        phase = c.session.after_run
        c.finish_phase("after_run", phase.phase_id, at(80))
        self.assertIsNone(c.session.cooling)
        self.assertEqual(c.session.after_run_history[-1].ends_at, at(80))
        self.assertEqual(c.session.timeline.gang_count, 1)
        self.assertTrue(c.last_decision.heat)
        self.assertIsNone(c.session.after_run)
        with self.assertRaises(ValueError):
            c.finish_phase("after_run", phase.phase_id, at(80))
        self.assertIsNone(c.session.cooling)

    def test_after_run_without_cooling_returns_to_regulation_and_preserves_session(self):
        c = after_run(heating_minutes=20)
        identity, energy = c.session.session_id, c.session.energy
        c.finish_phase("after_run", c.session.after_run.phase_id, at(80))
        self.assertIsNone(c.session.cooling)
        self.assertIsNone(c.session.after_run)
        self.assertTrue(c.last_decision.heat)
        self.assertEqual(c.session.session_id, identity)
        self.assertEqual(c.session.energy.total_kwh, energy.total_kwh)
        self.assertEqual(c.session.timeline.gang_count, 1)

    def test_manual_end_preserves_operation_off_and_technical_protection(self):
        for disabled in (False, True):
            with self.subTest(disabled=disabled):
                c = after_run()
                if disabled:
                    c.set_operation(False, at(75))
                else:
                    c.protection.add("heater_service_unavailable")
                if c.session.after_run is not None:
                    c.finish_phase("after_run", c.session.after_run.phase_id, at(80))
                self.assertFalse(c.last_decision.heat)
                self.assertEqual(c.session.operation_enabled, not disabled)
                if not disabled:
                    self.assertEqual(c.protection, {"heater_service_unavailable"})

    def test_pending_cooling_and_other_deadlines_cannot_be_ended(self):
        c = controller()
        c.report_heating(True, at(0))
        c.process(event("close", Kind.DOOR_CLOSE, 1))
        c.process(event("infusion", Kind.INFUSION, 2))
        c.advance(at(60))
        self.assertIsNone(c.session.cooling)
        with self.assertRaises(ValueError):
            c.finish_phase("forced_cooling", "obsolete", at(60))
        with self.assertRaises(ValueError):
            c.finish_phase("confirmation", "unused", at(60))
        self.assertEqual(c.phase, "saunagang")

    def test_finish_without_session_is_a_conflict_not_an_attribute_error(self):
        with self.assertRaises(ValueError):
            Controller(controller().parameters).finish_phase("after_run", "old", at(0))

    def test_expired_phase_is_not_processed_twice(self):
        c = after_run()
        token = c.session.after_run.phase_id
        c.advance(c.session.after_run.ends_at)
        with self.assertRaises(ValueError):
            c.finish_phase("after_run", token, at(100))
        self.assertEqual(len(c.session.after_run_history), 1)
        self.assertIsNone(c.session.cooling)
