"""Reguläre Thermostatstarts bleiben trotz alter Heizbudget-Einstellungen frei."""
from datetime import timedelta
import unittest

from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.timeline import Kind
from test_foundation import T0, event, parameters


def at(seconds):
    return T0 + timedelta(seconds=seconds)


def controller(**overrides):
    values = {**parameters().as_dict(), "target_temperature_c": 80,
        "safety_temperature_c": 110, "readiness_offset_c": 5,
        "readiness_hysteresis_c": 3, "forced_cooling_minutes": 5,
        "after_run_minutes": 8, "session_gap_minutes": 30,
        "heat_reset_minutes": 30, **overrides}
    result = Controller(Parameters(values))
    result.set_temperature(70, T0)
    result.set_operation(True, T0, session_id="admission")
    return result


class HeatingAdmissionTests(unittest.TestCase):
    def prepare_regular_restart(self, c):
        c.report_heating(True, at(0))
        c.set_temperature(85, at(1020))
        c.report_heating(False, at(1020))

    def test_old_short_remainder_does_not_block_regular_restart(self):
        c = controller(heating_minutes=25, heating_reduction_minutes=5,
                       minimum_heating_minutes=10, thermostat_cooldown_minutes=5,
                       forced_cooling_minutes=15)
        self.prepare_regular_restart(c)

        c.set_temperature(80, at(1320))

        self.assertIsNone(c.session.cooling)
        self.assertTrue(c.last_decision.heat)
        self.assertEqual(c.last_decision.reason, "below_target")
        c.report_heating(True, at(1320))
        c.advance(at(1800))
        self.assertIsNone(c.session.cooling)
        self.assertTrue(c.last_decision.heat)

    def test_exact_minimum_remainder_allows_regular_restart(self):
        c = controller(heating_minutes=25, heating_reduction_minutes=5,
                       minimum_heating_minutes=10, thermostat_cooldown_minutes=5)
        c.report_heating(True, at(0))
        c.set_temperature(85, at(900))
        c.report_heating(False, at(900))

        c.set_temperature(80, at(1200))

        self.assertIsNone(c.session.cooling)
        self.assertTrue(c.last_decision.heat)
        self.assertEqual(c.last_decision.reason, "below_target")

    def test_running_gang_and_open_door_do_not_create_cooling(self):
        c = controller(heating_minutes=25, heating_reduction_minutes=5,
                       minimum_heating_minutes=10, thermostat_cooldown_minutes=0)
        self.prepare_regular_restart(c)
        c.process(event("open", Kind.DOOR_OPEN, 1030, session="admission"))
        c.set_temperature(80, at(1320))

        self.assertIsNone(c.session.cooling)
        self.assertFalse(any(d.purpose in ("person_opportunity", "forced_cooling") for d in c.session.deadlines))
        self.assertTrue(c.last_decision.heat)

        c = controller(heating_minutes=25, heating_reduction_minutes=5,
                       minimum_heating_minutes=10, thermostat_cooldown_minutes=0)
        self.prepare_regular_restart(c)
        c.process(event("close", Kind.DOOR_CLOSE, 1021, session="admission"))
        c.process(event("person", Kind.PERSON_STRONG, 1022, session="admission"))
        c.process(event("infusion", Kind.INFUSION, 1023, session="admission"))
        c.set_temperature(80, at(1320))

        self.assertIsNone(c.session.cooling)
        self.assertEqual(c.phase, "saunagang")
        self.assertTrue(c.last_decision.heat)

    def test_manual_override_and_protection_do_not_create_cooling(self):
        c = controller(heating_minutes=25, heating_reduction_minutes=5,
                       minimum_heating_minutes=10, thermostat_cooldown_minutes=0)
        c.report_heating(True, at(0))
        c.set_temperature(85, at(800))
        c.report_heating(False, at(800))
        c.set_temperature(80, at(800))
        c.set_heater_override(True, at(800))
        c.advance(at(1000))

        self.assertIsNone(c.session.cooling)
        self.assertTrue(c.last_decision.heat)
        self.assertEqual(c.last_decision.reason, "manual_override")

        c = controller(heating_minutes=25, heating_reduction_minutes=5,
                       minimum_heating_minutes=10, thermostat_cooldown_minutes=0)
        self.prepare_regular_restart(c)
        c.protection.add("confirmed_controller_failure")
        c.set_temperature(80, at(1320))

        self.assertIsNone(c.session.cooling)
        self.assertFalse(c.last_decision.heat)
        self.assertEqual(c.last_decision.reason, "protection:confirmed_controller_failure")

    def test_short_initial_budget_does_not_create_cooling_at_session_start(self):
        c = controller(heating_minutes=5, heating_reduction_minutes=0,
                       minimum_heating_minutes=10, thermostat_cooldown_minutes=0)

        self.assertIsNone(c.session.cooling)
        self.assertTrue(c.last_decision.heat)
        c.report_heating(True, at(0))
        c.advance(at(300))
        self.assertIsNone(c.session.cooling)
        c.report_heating(False, at(300))
        c.advance(at(600))

        self.assertIsNone(c.session.cooling)
        self.assertTrue(c.last_decision.heat)
