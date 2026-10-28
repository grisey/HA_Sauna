"""F1 regression: manual oven OFF disposes only the current door help."""

import unittest
from datetime import UTC, datetime, timedelta

from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.timeline import Event, Kind

T0 = datetime(2026, 9, 26, tzinfo=UTC)
OVERRIDE = Parameters({}).seconds("manual_override_minutes")


def at(seconds):
    return T0 + timedelta(seconds=seconds)


def controller(session_id):
    value = Controller(
        Parameters(
            {
                "target_temperature_c": 80,
            }
        )
    )
    value.set_temperature(100, T0)  # normal thermostat stays OFF
    value.report_contactor(False, T0)
    value.report_heating(False, T0)
    value.set_operation(True, T0, session_id=session_id)
    return value


def event(value, name, kind, seconds):
    value.process(Event(name, value.session.session_id, kind, at(seconds), at(seconds)))


class ManualDoorCancelTests(unittest.TestCase):
    def test_pending_close_cannot_revive_after_manual_off(self):
        value = controller("pending")
        event(value, "open", Kind.DOOR_OPEN, 1)
        event(value, "close", Kind.DOOR_CLOSE, 2)
        self.assertTrue(value.regulation_inputs.temporary_door_heat)
        value.set_heater_override(False, at(3))
        value.advance(at(3 + OVERRIDE))
        self.assertFalse(value.regulation_inputs.temporary_door_heat)
        self.assertEqual(value.last_decision.reason, "temperature_reached")

    def test_open_cycle_is_invalidated_before_its_late_close(self):
        value = controller("open")
        event(value, "open", Kind.DOOR_OPEN, 1)
        value.set_heater_override(False, at(2))
        value.advance(at(2 + OVERRIDE))
        event(value, "late-close", Kind.DOOR_CLOSE, 3 + OVERRIDE)
        self.assertFalse(value.regulation_inputs.temporary_door_heat)
        self.assertFalse(value.last_decision.heat)

    def test_late_feedback_and_return_to_automatic_do_not_revive_request(self):
        for automatic_return in (False, True):
            with self.subTest(automatic_return=automatic_return):
                value = controller("late")
                value.report_contactor(True, at(1))
                event(value, "open", Kind.DOOR_OPEN, 2)
                event(value, "close", Kind.DOOR_CLOSE, 3)
                self.assertTrue(value.regulation_inputs.temporary_door_heat)
                value.set_heater_override(False, at(4))
                value.report_heating(True, at(5))
                self.assertFalse(value.last_decision.heat)
                if automatic_return:
                    value.set_heater_override(None, at(6))
                else:
                    value.advance(at(4 + OVERRIDE))
                self.assertFalse(value.regulation_inputs.temporary_door_heat)
                self.assertFalse(value.last_decision.heat)

    def test_independent_thermostat_demand_remains_available(self):
        value = controller("thermostat")
        event(value, "open", Kind.DOOR_OPEN, 1)
        event(value, "close", Kind.DOOR_CLOSE, 2)
        value.set_heater_override(False, at(3))
        value.advance(at(3 + OVERRIDE))
        value.set_temperature(70, at(4 + OVERRIDE))
        self.assertTrue(value.last_decision.heat)
        self.assertFalse(value.regulation_inputs.temporary_door_heat)

    def test_fresh_cycle_and_live_gang_work_after_override(self):
        value = controller("fresh")
        value.set_heater_override(False, at(1))
        value.advance(at(1 + OVERRIDE))
        event(value, "fresh-open", Kind.DOOR_OPEN, 2 + OVERRIDE)
        event(value, "fresh-close", Kind.DOOR_CLOSE, 3 + OVERRIDE)
        self.assertEqual(value.last_decision.reason, "temporary_door_heat")

        other = controller("gang")
        other.set_heater_override(False, at(1))
        other.advance(at(1 + OVERRIDE))
        event(other, "entry-open", Kind.DOOR_OPEN, 1 + OVERRIDE)
        event(other, "close", Kind.DOOR_CLOSE, 2 + OVERRIDE)
        event(other, "person", Kind.PERSON_STRONG, 3 + OVERRIDE)
        self.assertEqual(other.last_decision.reason, "gang_heat_demand")


if __name__ == "__main__":
    unittest.main()
