"""Controller integration for live heat demands and factual oven cooling."""
from datetime import timedelta
from dataclasses import replace
import unittest

from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.timeline import Event, Kind
from test_foundation import T0, parameters


def at(seconds):
    return T0 + timedelta(seconds=seconds)


def event(controller, key, kind, seconds):
    return controller.process(
        Event(key, controller.session.session_id, kind, at(seconds), at(seconds))
    )


def controller(**overrides):
    value = Controller(Parameters({**parameters().as_dict(), **overrides}))
    value.set_temperature(90, T0)
    value.report_contactor(False, T0)
    value.report_heating(False, T0)
    value.set_operation(True, T0, session_id="phase")
    return value


class PhaseHeatCoolingTests(unittest.TestCase):
    def test_live_provisional_gang_demands_heat_from_an_off_start(self):
        value = controller()
        self.assertFalse(value.last_decision.heat)

        event(value, "close", Kind.DOOR_CLOSE, 1)
        event(value, "person", Kind.PERSON_STRONG, 2)

        self.assertTrue(value.session.timeline.active)
        self.assertTrue(value.last_decision.heat)
        self.assertEqual(value.last_decision.reason, "gang_heat_demand")

    def test_door_close_holds_demand_until_actual_contactor_on(self):
        value = controller()
        value.set_temperature(70, at(1))
        event(value, "open", Kind.DOOR_OPEN, 2)
        event(value, "close", Kind.DOOR_CLOSE, 3)

        self.assertTrue(value.regulation_inputs.temporary_door_heat)
        self.assertEqual(value.last_decision.reason, "temporary_door_heat")
        value.advance(at(4))
        self.assertTrue(value.regulation_inputs.temporary_door_heat)

        value.report_contactor(True, at(5))
        self.assertTrue(value.regulation_inputs.temporary_door_heat)
        value.report_heating(True, at(6))
        self.assertFalse(value.regulation_inputs.temporary_door_heat)

    def test_cooling_timer_waits_for_actual_off_and_freezes_calculation(self):
        value = controller(after_run_minutes=0.5)
        value.report_contactor(True, at(1))
        event(value, "close", Kind.DOOR_CLOSE, 2)
        event(value, "person", Kind.PERSON_STRONG, 3)
        event(value, "infusion", Kind.INFUSION, 4)
        event(value, "open", Kind.DOOR_OPEN, 5)
        event(value, "vent", Kind.VENTILATION, 6)

        phase = value.session.after_run
        self.assertTrue(phase.pending_start)
        self.assertIsNone(phase.ends_at)
        self.assertEqual(value.last_decision.reason, "after_run")

        value.report_contactor(False, at(10))
        phase = value.session.after_run
        self.assertFalse(phase.pending_start)
        self.assertEqual(phase.started_at, at(10))
        self.assertIsNotNone(phase.cooling_calculation)
        frozen = phase.cooling_calculation

        value.report_contactor(True, at(12))
        value.report_contactor(False, at(20))
        self.assertIs(value.session.after_run.cooling_calculation, frozen)
        value.advance(value.session.after_run.ends_at)
        self.assertIsNone(value.session.after_run)

    def test_cooling_has_priority_over_a_contradictory_active_gang(self):
        value = controller()
        event(value, "close", Kind.DOOR_CLOSE, 1)
        event(value, "person", Kind.PERSON_STRONG, 2)
        event(value, "infusion", Kind.INFUSION, 3)
        event(value, "open", Kind.DOOR_OPEN, 4)
        event(value, "vent", Kind.VENTILATION, 5)
        value.report_contactor(False, at(6))

        # Imported/late timeline evidence must not reverse the live cooling OFF.
        active = value.session.timeline.completed[-1]
        value._session = replace(
            value.session,
            timeline=replace(value.session.timeline, active=active),
        )
        value.advance(at(7))
        self.assertFalse(value.last_decision.heat)
        self.assertEqual(value.last_decision.reason, "after_run")

    def test_manual_stop_keeps_a_pending_cooling_from_resetting_the_window(self):
        value = controller()
        value.report_contactor(True, at(1))
        event(value, "close", Kind.DOOR_CLOSE, 1)
        event(value, "person", Kind.PERSON_STRONG, 2)
        event(value, "infusion", Kind.INFUSION, 3)
        event(value, "open", Kind.DOOR_OPEN, 4)
        event(value, "vent", Kind.VENTILATION, 5)

        phase = value.session.after_run
        self.assertTrue(phase.pending_start)
        value.finish_phase("after_run", phase.phase_id, at(6))

        self.assertIsNone(value.session.after_run)
        self.assertTrue(value.session.after_run_history[-1].pending_start)
        self.assertIsNone(value.session.last_completed_oven_cooling_at)

    def test_operation_change_invalidates_only_a_currently_open_door_cycle(self):
        value = controller()
        value.set_temperature(70, at(1))
        value.set_operation(False, at(2))
        value.set_operation(True, at(3))
        event(value, "open-after-resume", Kind.DOOR_OPEN, 4)
        event(value, "close-after-resume", Kind.DOOR_CLOSE, 5)
        self.assertTrue(value.regulation_inputs.temporary_door_heat)

        blocked = controller()
        blocked.set_temperature(70, at(1))
        event(blocked, "open-before-off", Kind.DOOR_OPEN, 2)
        blocked.set_operation(False, at(3))
        blocked.set_operation(True, at(4))
        event(blocked, "close-after-off", Kind.DOOR_CLOSE, 5)
        self.assertFalse(blocked.regulation_inputs.temporary_door_heat)


if __name__ == "__main__":
    unittest.main()
