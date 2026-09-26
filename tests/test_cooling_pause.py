"""Manuelle Ofenübersteuerung pausiert, statt Kühlung zu verwerfen."""
import unittest

from test_cooling import at, controller
from test_foundation import event
from custom_components.ha_sauna.core.timeline import Kind


class CoolingPauseTests(unittest.TestCase):
    def test_return_to_automatic_without_a_session_keeps_heater_off(self):
        from custom_components.ha_sauna.core.controller import Controller
        from custom_components.ha_sauna.core.parameters import Parameters
        c = Controller(Parameters({}))
        c.set_heater_override(False, at(0))
        c.set_heater_override(None, at(1))
        self.assertIsNone(c.session)
        self.assertIsNone(c.heater_override)
        self.assertFalse(c.last_decision.heat)
        with self.assertRaisesRegex(ValueError, "Saunabetrieb einschalten"):
            c.set_heater_override(True, at(2))
        self.assertIsNone(c.heater_override)
        self.assertFalse(c.last_decision.heat)

    def test_missing_temperature_releases_manual_after_run_override_and_resumes_cooling(self):
        c = controller(after_run_minutes=0.5)
        c.report_heating(True, at(0))
        c.process(event("close", Kind.DOOR_CLOSE, 1))
        c.process(event("person", Kind.PERSON_STRONG, 2))
        c.process(event("infusion", Kind.INFUSION, 3))
        c.process(event("open", Kind.DOOR_OPEN, 60))
        c.process(event("vent", Kind.VENTILATION, 61))
        self.assertEqual(c.phase, "nachlauf")

        c.set_heater_override(True, at(62))
        self.assertTrue(c.last_decision.heat)
        c.set_temperature(None, at(63))

        self.assertIsNone(c.heater_override)
        self.assertFalse(c.last_decision.heat)
        self.assertEqual(c.last_decision.reason, "after_run")
        # Die eine tatsächlich gelaufene Nachlaufsekunde bleibt gezählt;
        # die durch manuelles Heizen pausierte Sekunde verlängert die Frist.
        c.advance(at(92))
        self.assertIsNone(c.session.after_run)
        self.assertIsNone(c.session.cooling)
        self.assertFalse(c.last_decision.heat)
