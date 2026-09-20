"""Manuelles Heizen hält nur die echte Nachlaufuhr an."""
import unittest

from custom_components.ha_sauna.core.display import phase_timer, start_availability
from custom_components.ha_sauna.core.timeline import Kind
from test_cooling import at, controller
from test_foundation import event


def after_run():
    c = controller(after_run_minutes=8, forced_cooling_minutes=15)
    c.report_heating(True, at(0))
    c.process(event("close", Kind.DOOR_CLOSE, 1))
    c.process(event("person", Kind.PERSON_STRONG, 2))
    c.process(event("infusion", Kind.INFUSION, 3))
    c.process(event("open", Kind.DOOR_OPEN, 60))
    c.process(event("vent", Kind.VENTILATION, 61))
    return c


class AfterRunPauseTests(unittest.TestCase):
    def test_manual_heating_pauses_remaining_after_run_then_credits_only_eight_real_minutes(self):
        c = after_run()
        c.advance(at(181))  # Zwei echte Nachlaufminuten.
        c.set_heater_override(True, at(181))
        phase = c.session.after_run
        self.assertEqual(phase.elapsed_seconds, 120)
        self.assertEqual(phase.remaining_seconds, 360)
        self.assertIsNone(phase.ends_at)
        self.assertEqual(phase_timer(c, at(400)), {
            "kind": "after_run", "label": "Nachlauf pausiert, noch",
            "seconds": 360, "mode": "paused"})
        self.assertEqual(start_availability(c, at(400))["blocker"],
                         {"kind": "after_run_paused", "seconds": 360})

        c.advance(at(481))  # Fünf Minuten manuelles Heizen zählen nicht.
        self.assertEqual(c.session.after_run.elapsed_seconds, 120)
        c.set_heater_override(None, at(481))
        self.assertEqual(c.session.after_run.ends_at, at(841))
        decisions = len(c.decisions)
        c.advance(at(841))

        self.assertEqual(c.phase, "zwangskühlung")
        self.assertEqual(c.session.cooling.credited_seconds, 480)
        self.assertEqual(c.cooling_remaining_seconds, 420)
        self.assertEqual(c.session.cooling.ends_at, at(1261))
        self.assertFalse(any(decision.heat for decision in c.decisions[decisions:]))

    def test_repeated_pause_resume_cancels_old_deadlines_without_consuming_paused_time(self):
        c = after_run()
        original = next(d for d in c.session.deadlines if d.purpose == "after_run")
        c.advance(at(181))
        c.set_heater_override(True, at(181))
        c.advance(original.due_at)
        self.assertEqual(c.session.after_run.remaining_seconds, 360)
        self.assertFalse(c.consume_deadline(original, original.due_at))

        c.set_heater_override(None, at(541))
        c.advance(at(601))
        c.set_heater_override(True, at(601))
        self.assertEqual(c.session.after_run.remaining_seconds, 300)
        c.set_heater_override(None, at(901))
        self.assertEqual(c.session.after_run.ends_at, at(1201))
        c.advance(at(1201))
        self.assertEqual(c.session.cooling.credited_seconds, 480)

    def test_finish_paused_phase_uses_identity_and_only_elapsed_after_run_for_credit(self):
        c = after_run()
        c.advance(at(181))
        phase = c.session.after_run
        c.set_heater_override(True, at(181))
        c.finish_phase("after_run", phase.phase_id, at(481))

        self.assertIsNone(c.session.after_run)
        self.assertEqual(c.session.after_run_history[-1].elapsed_seconds, 120)
        self.assertEqual(c.session.cooling.credited_seconds, 120)
        with self.assertRaisesRegex(ValueError, "passender Nachlauf"):
            c.finish_phase("after_run", phase.phase_id, at(482))

    def test_operation_off_ends_manual_heating_and_resumes_paused_after_run(self):
        c = after_run()
        c.advance(at(181))
        c.set_heater_override(True, at(181))
        c.set_operation(False, at(481))

        self.assertIsNone(c.heater_override)
        self.assertFalse(c.last_decision.heat)
        self.assertEqual(c.session.after_run.remaining_seconds, 360)
        self.assertEqual(c.session.after_run.ends_at, at(841))

    def test_manual_off_never_keeps_the_after_run_clock_paused(self):
        c = after_run()
        c.advance(at(181))
        c.set_heater_override(True, at(181))
        c.set_heater_override(False, at(481))

        self.assertEqual(c.session.after_run.remaining_seconds, 360)
        self.assertEqual(c.session.after_run.ends_at, at(841))

    def test_protection_ends_manual_heating_and_resumes_paused_after_run(self):
        c = after_run()
        c.advance(at(181))
        c.set_heater_override(True, at(181))
        c.protection.add("heater_service_unavailable")
        c.advance(at(481))

        self.assertIsNone(c.heater_override)
        self.assertFalse(c.last_decision.heat)
        self.assertEqual(c.session.after_run.remaining_seconds, 360)
        self.assertEqual(c.session.after_run.ends_at, at(841))
