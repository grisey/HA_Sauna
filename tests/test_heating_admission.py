"""Reguläre Thermostatstarts bleiben trotz alter Heizbudget-Einstellungen frei."""
from datetime import timedelta
import unittest

from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.timeline import Kind
from test_foundation import T0, event, parameters


MINIMUM = parameters().seconds("minimum_heating_minutes")
COOLDOWN = parameters().seconds("thermostat_cooldown_minutes")


def at(seconds):
    return T0 + timedelta(seconds=seconds)


def controller(**overrides):
    values = {**parameters().as_dict(), "target_temperature_c": 80,
        **overrides}
    result = Controller(Parameters(values))
    result.set_temperature(70, T0)
    result.set_operation(True, T0, session_id="admission")
    return result


class HeatingAdmissionTests(unittest.TestCase):
    def prepare_regular_restart(self, c):
        c.report_heating(True, at(0))
        c.set_temperature(85, at(MINIMUM + 0))
        c.report_heating(False, at(MINIMUM + 0))

    def test_running_gang_and_open_door_do_not_create_cooling(self):
        c = controller()
        self.prepare_regular_restart(c)
        c.process(event("open", Kind.DOOR_OPEN, MINIMUM + 10, session="admission"))
        c.set_temperature(c.thermostat_restart_temperature, at(MINIMUM + COOLDOWN))

        self.assertIsNone(c.session.cooling)
        self.assertFalse(any(d.purpose in ("person_opportunity", "forced_cooling") for d in c.session.deadlines))
        self.assertTrue(c.last_decision.heat)
        self.assertEqual(c.last_decision.reason, "below_target")

        c = controller()
        self.prepare_regular_restart(c)
        c.process(event("entry-open", Kind.DOOR_OPEN, MINIMUM + 0, session="admission"))
        c.process(event("close", Kind.DOOR_CLOSE, MINIMUM + 1, session="admission"))
        c.process(event("person", Kind.PERSON_STRONG, MINIMUM + 2, session="admission"))
        c.process(event("infusion", Kind.INFUSION, MINIMUM + 3, session="admission"))
        c.set_temperature(80, at(MINIMUM + COOLDOWN))

        self.assertIsNone(c.session.cooling)
        self.assertEqual(c.phase, "saunagang")
        self.assertTrue(c.last_decision.heat)

    def test_manual_override_and_protection_do_not_create_cooling(self):
        c = controller()
        c.report_heating(True, at(0))
        c.set_temperature(85, at(MINIMUM + 1))
        c.report_heating(False, at(MINIMUM + 1))
        c.set_temperature(80, at(MINIMUM + 1))
        c.set_heater_override(True, at(MINIMUM + 1))
        c.advance(at(MINIMUM + 1 + parameters().seconds("manual_override_minutes") / 2))

        self.assertIsNone(c.session.cooling)
        self.assertTrue(c.last_decision.heat)
        self.assertEqual(c.last_decision.reason, "manual_override")

        c = controller()
        self.prepare_regular_restart(c)
        c.protection.add("confirmed_controller_failure")
        c.set_temperature(80, at(MINIMUM + COOLDOWN))

        self.assertIsNone(c.session.cooling)
        self.assertFalse(c.last_decision.heat)
        self.assertEqual(c.last_decision.reason, "protection:confirmed_controller_failure")
