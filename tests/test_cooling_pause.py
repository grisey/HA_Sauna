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

    def cooling(self, **overrides):
        c = controller(forced_cooling_minutes=15, after_run_minutes=8, **overrides)
        c.report_heating(True, at(0))
        c.advance(at(60))
        self.assertEqual(c.phase, "zwangskühlung")
        return c

    def test_pause_keeps_real_remaining_time_and_repeated_advance_does_not_consume_it(self):
        c = self.cooling()
        c.set_heater_override(True, at(360))  # Fünf echte Kühlminuten.
        self.assertEqual(c.session.cooling.elapsed_seconds, 300)
        self.assertEqual(c.session.cooling.credited_seconds, 0)
        self.assertEqual(c.cooling_remaining_seconds, 600)
        self.assertIsNone(c.session.cooling.ends_at)
        c.advance(at(600))
        c.advance(at(900))  # Die alte Frist läge hier ohne Pause schon hinter uns.
        self.assertIsNotNone(c.session.cooling)
        self.assertEqual(c.cooling_remaining_seconds, 600)

    def test_running_cooling_is_accounted_when_time_advances_without_a_deadline(self):
        c = self.cooling()
        c.advance(at(360))
        self.assertEqual(c.session.cooling.elapsed_seconds, 300)
        self.assertEqual(c.cooling_remaining_seconds, 600)

    def test_automatic_return_resumes_the_frozen_remainder(self):
        c = self.cooling()
        c.set_heater_override(True, at(360))
        c.set_heater_override(None, at(540))  # Drei Minuten manuelle Bedienung.
        self.assertEqual(c.phase, "zwangskühlung")
        self.assertEqual(c.cooling_remaining_seconds, 600)
        self.assertEqual(c.session.cooling.ends_at, at(1140))

    def test_manual_off_resumes_cooling_paused_by_manual_heating(self):
        c = self.cooling()
        c.set_heater_override(True, at(360))
        c.set_heater_override(False, at(540))

        self.assertIsNone(c.session.cooling.paused_at)
        self.assertEqual(c.cooling_remaining_seconds, 600)
        self.assertEqual(c.session.cooling.ends_at, at(1140))
        self.assertFalse(c.last_decision.heat)

    def test_manual_off_does_not_delay_cooling_after_after_run(self):
        c = controller(after_run_minutes=0.5)
        c.report_heating(True, at(0))
        c.process(event("close", Kind.DOOR_CLOSE, 1))
        c.process(event("person", Kind.PERSON_STRONG, 2))
        c.process(event("infusion", Kind.INFUSION, 3))
        c.process(event("open", Kind.DOOR_OPEN, 60))
        c.process(event("vent", Kind.VENTILATION, 61))
        c.report_heating(False, at(61))
        c.set_heater_override(False, at(70))
        c.advance(at(91))

        self.assertIsNone(c.session.after_run)
        self.assertEqual(c.session.cooling.started_at, at(91))
        self.assertEqual(c.session.cooling.credited_seconds, 30)
        self.assertFalse(c.last_decision.heat)

    def test_gang_and_after_run_credit_are_applied_to_paused_cycle(self):
        c = self.cooling()
        c.set_heater_override(True, at(360))
        c.process(event("close", Kind.DOOR_CLOSE, 361))
        c.process(event("person", Kind.PERSON_STRONG, 362))
        c.process(event("infusion", Kind.INFUSION, 363))
        self.assertEqual(c.phase, "saunagang")
        c.process(event("open", Kind.DOOR_OPEN, 364))
        c.process(event("vent", Kind.VENTILATION, 365))
        c.advance(at(845))
        self.assertEqual(c.phase, "zwangskühlung")
        self.assertEqual(c.session.cooling.credited_seconds, 480)
        self.assertEqual(c.cooling_remaining_seconds, 120)
        self.assertEqual(c.session.cooling.ends_at, at(965))

    def test_protection_has_priority_over_manual_on(self):
        c = self.cooling()
        c.set_heater_override(True, at(360))
        c.protection.add("confirmed_controller_failure")
        c.advance(at(361))
        self.assertFalse(c.last_decision.heat)
        self.assertEqual(c.last_decision.reason, "protection:confirmed_controller_failure")
        self.assertIsNone(c.heater_override)
        c.protection.clear()
        c.advance(at(362))
        self.assertFalse(c.last_decision.heat)
        self.assertEqual(c.last_decision.reason, "forced_cooling")

    def test_inhibit_releases_manual_on_without_a_later_restart(self):
        c = self.cooling()
        c.set_heater_override(True, at(360))
        c.inhibits.add("upper_temperature_stale")
        c.advance(at(361))
        c.inhibits.clear()
        c.advance(at(362))

        self.assertIsNone(c.heater_override)
        self.assertFalse(c.last_decision.heat)
        self.assertEqual(c.last_decision.reason, "forced_cooling")

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
        self.assertEqual(c.phase, "zwangskühlung")

    def test_switch_change_releases_override_and_resumes_cooling_without_heat_command(self):
        c = self.cooling()
        c.set_temperature(85, at(120))
        c.set_heater_override(True, at(360))
        # Beim Pausieren zählt die aktuelle gültige Temperatur wieder. Die
        # Übersteuerung merkt sich die dadurch erreichte Bereitschaft mit.
        self.assertEqual(c.phase, "bereit")

        decisions_before = len(c.decisions)
        c.set_temperature(70, at(361))

        self.assertIsNone(c.heater_override)
        self.assertEqual(c.phase, "zwangskühlung")
        self.assertFalse(c.last_decision.heat)
        self.assertEqual(c.last_decision.reason, "forced_cooling")
        self.assertFalse(any(decision.heat for decision in c.decisions[decisions_before:]))

    def test_manual_cooling_pause_survives_refresh_of_an_already_hot_temperature(self):
        c = self.cooling()
        c.set_temperature(85, at(120))
        c.set_heater_override(True, at(360))

        c.set_temperature(85, at(361))

        self.assertTrue(c.heater_override)
        self.assertTrue(c.last_decision.heat)
        self.assertEqual(c.phase, "bereit")
        self.assertIsNotNone(c.session.cooling.paused_at)

    def test_manual_on_is_rejected_without_pausing_or_leaving_a_later_command(self):
        c = self.cooling()
        before = c.session
        c.protection.add("confirmed_controller_failure")
        with self.assertRaisesRegex(ValueError, "Schutz.*Heizsperre"):
            c.set_heater_override(True, at(360))
        self.assertEqual(c.session, before)
        self.assertIsNone(c.heater_override)

        c.protection.clear()
        c.advance(at(361))
        self.assertIsNone(c.heater_override)
        self.assertFalse(c.last_decision.heat)

        c.set_temperature(None, at(362))
        with self.assertRaisesRegex(ValueError, "gültigen oberen Temperaturwert"):
            c.set_heater_override(True, at(363))
        self.assertIsNone(c.heater_override)
        self.assertFalse(c.last_decision.heat)

    def test_manual_off_preserves_the_running_cooling_deadline(self):
        c = self.cooling()
        deadline = next(d for d in c.session.deadlines if d.purpose == "forced_cooling")
        c.set_heater_override(False, at(360))
        self.assertIsNone(c.session.cooling.paused_at)
        self.assertEqual(c.cooling_remaining_seconds, 600)
        self.assertIn(deadline, c.session.deadlines)
        self.assertEqual(c.session.cooling.ends_at, at(960))

        c.advance(at(960))  # ursprüngliches Ende: 60 + 15 Minuten
        self.assertIsNone(c.session.cooling)
        self.assertEqual(c.session.cooling_history[-1].ends_at, at(960))
