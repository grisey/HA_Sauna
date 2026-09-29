"""F2 regression: a door request is fulfilled only by actual heating feedback."""

import unittest
from datetime import UTC, datetime, timedelta

from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.timeline import Event, Kind

T0 = datetime(2030, 1, 1, tzinfo=UTC)


def at(seconds):
    return T0 + timedelta(seconds=seconds)


def event(kind, seconds):
    return Event(f"{kind.value}-{seconds}", "s", kind, at(seconds), at(seconds))


def controller(*, feedback=False, contactor=False):
    value = Controller(
        Parameters(
            {
                "target_temperature_c": 80,
                "readiness_offset_c": 3,
                "readiness_hysteresis_c": 3,
                "minimum_heating_minutes": 10,
            }
        )
    )
    value.set_temperature(90, at(0))
    value.report_contactor(contactor, at(0))
    value.report_heating(feedback, at(0))
    value.begin_session("s", at(0))
    return value


def suitable_close(value, seconds=2):
    value.process(event(Kind.DOOR_OPEN, seconds - 1))
    value.process(event(Kind.DOOR_CLOSE, seconds))


class DoorHeatingFeedbackTests(unittest.TestCase):
    def test_contactor_on_without_heat_feedback_keeps_demand(self):
        value = controller(contactor=True, feedback=False)
        suitable_close(value)
        self.assertEqual(value.last_decision.reason, "temporary_door_heat")
        self.assertTrue(value.regulation_inputs.temporary_door_heat)

    def test_unknown_heat_feedback_keeps_demand_but_protection_wins(self):
        value = controller(contactor=True, feedback=None)
        suitable_close(value)
        self.assertTrue(value.regulation_inputs.temporary_door_heat)
        value.protection.add("feedback_failure")
        value.advance(at(3))
        self.assertFalse(value.last_decision.heat)
        self.assertEqual(value.last_decision.reason, "protection:feedback_failure")

    def test_actual_feedback_starts_minimum_at_feedback_time(self):
        value = controller(contactor=True, feedback=False)
        suitable_close(value)
        value.report_heating(True, at(100))
        self.assertFalse(value.regulation_inputs.temporary_door_heat)
        value.advance(at(699))
        self.assertEqual(value.last_decision.reason, "minimum_heating")
        value.advance(at(700))
        self.assertEqual(value.last_decision.reason, "temperature_reached")

    def test_both_feedback_orders_and_repetition_do_not_stale_or_extend(self):
        # Contactor first: demand remains through contactor acknowledgement.
        first = controller(contactor=False, feedback=False)
        suitable_close(first)
        first.report_contactor(True, at(3))
        self.assertTrue(first.regulation_inputs.temporary_door_heat)
        first.report_heating(True, at(4))
        first.report_heating(True, at(5))
        first.advance(at(604))
        self.assertEqual(first.last_decision.reason, "temperature_reached")

        # Heating first: transfer the pending request immediately, without
        # waiting for a second contactor report or moving the minimum start.
        second = controller(contactor=False, feedback=False)
        suitable_close(second)
        second.report_heating(True, at(3))
        self.assertFalse(second.regulation_inputs.temporary_door_heat)
        second.report_contactor(True, at(4))
        second.report_heating(True, at(5))
        self.assertEqual(second.session.heating.intervals[-1].started_at, at(3))
        second.advance(at(602.999999))
        self.assertTrue(second.last_decision.heat)
        second.advance(at(603))
        self.assertEqual(second.last_decision.reason, "temperature_reached")

    def test_existing_confirmed_run_keeps_original_start(self):
        value = controller(contactor=True, feedback=True)
        started = value.session.heating.intervals[-1].started_at
        suitable_close(value)
        self.assertFalse(value.regulation_inputs.temporary_door_heat)
        self.assertEqual(value.session.heating.intervals[-1].started_at, started)


if __name__ == "__main__":
    unittest.main()
