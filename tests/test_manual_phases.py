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
    c.report_heating(False, at(70))
    return c


class ManualPhaseTests(unittest.TestCase):
    def test_early_after_run_credits_actual_time_and_keeps_next_cooling(self):
        c = after_run()
        phase = c.session.after_run
        original = next(d for d in c.session.deadlines if d.purpose == "after_run")
        c.finish_phase("after_run", phase.phase_id, at(80))
        self.assertEqual(c.phase, "zwangskühlung")
        self.assertEqual(c.session.cooling.credited_seconds, 10)
        self.assertEqual(c.session.cooling.started_at, at(80))
        self.assertEqual(c.session.cooling.ends_at, at(130))
        self.assertEqual(c.session.after_run_history[-1].ends_at, at(80))
        self.assertEqual(c.session.timeline.gang_count, 1)
        self.assertFalse(c.last_decision.heat)
        self.assertFalse(c.consume_deadline(original, at(100)))
        with self.assertRaises(ValueError):
            c.finish_phase("after_run", phase.phase_id, at(80))
        self.assertEqual(c.session.cooling.credited_seconds, 10)

    def test_early_cooling_has_normal_reset_and_old_click_cannot_end_next_cycle(self):
        c = after_run()
        c.finish_phase("after_run", c.session.after_run.phase_id, at(80))
        cycle = c.session.cooling
        c.finish_phase("forced_cooling", cycle.cycle_id, at(90))
        self.assertEqual(c.session.cooling_history[-1].ends_at, at(90))
        self.assertEqual(c.session.heating.elapsed_seconds, 0)
        self.assertEqual(c.heating_limit_seconds, 45)
        self.assertTrue(c.last_decision.heat)
        c.report_heating(True, at(90))
        c.advance(at(135))
        next_cycle = c.session.cooling
        with self.assertRaises(ValueError):
            c.finish_phase("forced_cooling", cycle.cycle_id, at(135))
        self.assertEqual(c.session.cooling, next_cycle)
        self.assertEqual(len(c.session.cooling_history), 1)
        self.assertEqual(c.session.timeline.gang_count, 1)

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
                c.finish_phase("after_run", c.session.after_run.phase_id, at(80))
                c.finish_phase("forced_cooling", c.session.cooling.cycle_id, at(90))
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
        self.assertIsNone(c.session.cooling.started_at)
        with self.assertRaises(ValueError):
            c.finish_phase("forced_cooling", c.session.cooling.cycle_id, at(60))
        with self.assertRaises(ValueError):
            c.finish_phase("confirmation", "unused", at(60))
        self.assertEqual(c.phase, "saunagang")

    def test_paused_started_cooling_can_be_ended_with_its_current_cycle_id(self):
        c = after_run()
        c.finish_phase("after_run", c.session.after_run.phase_id, at(80))
        cycle = c.session.cooling
        c.set_heater_override(True, at(90))

        self.assertEqual(cycle.cycle_id, c.session.cooling.cycle_id)
        self.assertIsNotNone(c.session.cooling.started_at)
        self.assertIsNone(c.session.cooling.ends_at)
        c.finish_phase("forced_cooling", cycle.cycle_id, at(100))

        self.assertIsNone(c.session.cooling)
        self.assertEqual(c.session.cooling_history[-1].cycle_id, cycle.cycle_id)
        self.assertEqual(c.session.cooling_history[-1].ends_at, at(100))

    def test_finish_without_session_is_a_conflict_not_an_attribute_error(self):
        with self.assertRaises(ValueError):
            Controller(controller().parameters).finish_phase("after_run", "old", at(0))

    def test_expired_phase_is_not_processed_twice(self):
        c = after_run()
        token = c.session.after_run.phase_id
        with self.assertRaises(ValueError):
            c.finish_phase("after_run", token, at(100))
        self.assertEqual(len(c.session.after_run_history), 1)
        self.assertEqual(c.session.cooling.credited_seconds, 30)
