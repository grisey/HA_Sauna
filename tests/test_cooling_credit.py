"""Nachlaufanrechnung folgt den abgeschlossenen Phasen genau einmal."""
import unittest

from test_cooling import at, controller
from test_foundation import event
from custom_components.ha_sauna.core.timeline import Kind


def finish_gang(c, end_at):
    c.process(event("close", Kind.DOOR_CLOSE, 1))
    c.process(event("infusion", Kind.INFUSION, 2))
    c.process(event("open", Kind.DOOR_OPEN, end_at - 1))
    c.process(event("vent", Kind.VENTILATION, end_at))


class CoolingCreditTests(unittest.TestCase):
    def test_insufficient_remaining_budget_attaches_cooling_to_after_run(self):
        c = controller(heating_minutes=15, forced_cooling_minutes=15,
                       after_run_minutes=8, minimum_heating_minutes=10)
        c.report_heating(True, at(0))
        finish_gang(c, 600)  # Noch fünf Minuten Heizbudget.
        self.assertIsNotNone(c.session.cooling)
        self.assertFalse(c.last_decision.heat)
        c.advance(at(1080))
        self.assertEqual(c.phase, "zwangskühlung")
        self.assertEqual(c.session.cooling.credited_seconds, 480)
        self.assertEqual(c.session.cooling.ends_at, at(1500))  # Noch sieben Minuten.

    def test_exact_minimum_budget_does_not_attach_cooling_to_after_run(self):
        c = controller(heating_minutes=15, after_run_minutes=8,
                       minimum_heating_minutes=10)
        c.report_heating(True, at(0))
        finish_gang(c, 300)  # Genau zehn Minuten Heizbudget.
        self.assertIsNone(c.session.cooling)
        c.advance(at(780))
        self.assertIsNone(c.session.cooling)
        self.assertTrue(c.last_decision.heat)

    def test_completed_after_run_credits_later_cooling_once(self):
        c = controller(heating_minutes=20, forced_cooling_minutes=15,
                       after_run_minutes=8, minimum_heating_minutes=10)
        c.report_heating(True, at(0))
        finish_gang(c, 300)
        c.advance(at(780))
        self.assertIsNone(c.session.cooling)
        c.advance(at(1200))
        self.assertEqual(c.session.cooling.credited_seconds, 480)
        self.assertEqual(c.session.cooling.ends_at, at(1620))
        c.advance(at(1620))
        c.advance(at(2820))
        self.assertEqual(c.session.cooling.credited_seconds, 0)

    def test_multiple_after_runs_are_all_assigned_to_one_cooling_cycle(self):
        c = controller(heating_minutes=40, forced_cooling_minutes=15,
                       after_run_minutes=8, minimum_heating_minutes=10)
        c.report_heating(True, at(0))
        finish_gang(c, 300)
        c.advance(at(780))
        c.process(event("close-again", Kind.DOOR_CLOSE, 800))
        c.process(event("infusion-again", Kind.INFUSION, 801))
        c.process(event("open-again", Kind.DOOR_OPEN, 899))
        c.process(event("vent-again", Kind.VENTILATION, 900))
        c.advance(at(1380))
        c.advance(at(2400))
        self.assertIsNone(c.session.cooling)
        self.assertEqual(c.session.cooling_history[-1].credited_seconds, 960)
        c.advance(at(4800))
        self.assertEqual(c.session.cooling.credited_seconds, 0)
