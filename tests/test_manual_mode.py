"""Manual control is an operating mode, separate from a temporary override."""

import unittest
from datetime import timedelta

from test_foundation import T0, event, parameters

from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.display import phase_timer, start_availability
from custom_components.ha_sauna.core.display import phase_timer, start_availability
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.timeline import Kind


def at(seconds):
    return T0 + timedelta(seconds=seconds)


class ManualModeTests(unittest.TestCase):
    def controller(self, **values):
        return Controller(
            Parameters({**parameters().as_dict(), **values}), control_mode="manual"
        )

    def start(self, controller):
        controller.set_temperature(60, at(0))
        controller.set_operation(True, at(0), session_id="s")

    def test_mode_can_only_change_between_sessions(self):
        controller = Controller(parameters())
        controller.set_control_mode("manual")
        self.assertEqual(controller.control_mode, "manual")
        controller.set_operation(True, at(0), session_id="s")
        with self.assertRaises(ValueError):
            controller.set_control_mode("automatic")

    def test_manual_mode_never_starts_thermostat_without_a_demand(self):
        controller = self.controller()
        self.start(controller)
        self.assertEqual(controller.phase, "manuell")
        self.assertFalse(controller.last_decision.heat)
        self.assertEqual(controller.last_decision.reason, "manual_mode")
        availability = start_availability(controller, at(0), temperature_rate=0.1)
        self.assertIsNone(availability["until_ready_seconds"])
        self.assertIsNone(availability["start_window_seconds"])
        self.assertEqual(phase_timer(controller, at(0))["kind"], "manual")

    def test_explicit_demand_survives_normal_gang_changes(self):
        controller = self.controller()
        self.start(controller)
        controller.set_heater_override(True, at(1))
        controller.process(event("close", Kind.DOOR_CLOSE, 2))
        controller.process(event("person", Kind.PERSON_STRONG, 3))
        self.assertEqual(controller.phase, "saunagang")
        self.assertTrue(controller.last_decision.heat)
        controller.process(event("infusion", Kind.INFUSION, 4))
        controller.process(event("open", Kind.DOOR_OPEN, 5))
        controller.process(event("ventilation", Kind.VENTILATION, 6))
        self.assertEqual(controller.session.timeline.gang_count, 1)
        self.assertTrue(controller.last_decision.heat)
        self.assertIsNone(controller.session.after_run)

    def test_heating_budget_is_only_recorded_in_manual_mode(self):
        controller = self.controller(heating_minutes=1, heat_reset_minutes=10)
        self.start(controller)
        controller.report_heating(True, at(0))
        controller.advance(at(70))
        self.assertGreater(controller.session.heating.elapsed_seconds, 60)
        self.assertIsNone(controller.session.cooling)
        self.assertEqual(controller.phase, "manuell")

    def test_missing_temperature_and_operation_off_block_manual_demand(self):
        controller = self.controller()
        controller.set_operation(True, at(0), session_id="s")
        with self.assertRaises(ValueError):
            controller.set_heater_override(True, at(1))
        controller.set_temperature(60, at(2))
        controller.set_operation(False, at(3))
        with self.assertRaises(ValueError):
            controller.set_heater_override(True, at(4))

    def test_confirmed_overtemperature_clears_demand_and_cools(self):
        controller = self.controller(
            safety_temperature_c=80,
            overtemperature_minutes=1,
            forced_cooling_minutes=2,
            overtemperature_cooling_factor=2,
        )
        self.start(controller)
        controller.set_heater_override(True, at(1))
        controller.set_temperature(81, at(2))
        controller.advance(at(63))
        self.assertIsNone(controller.heater_override)
        self.assertEqual(controller.phase, "zwangskühlung")
        self.assertEqual(controller.session.cooling.duration_seconds, 240)
        self.assertFalse(controller.last_decision.heat)
        availability = start_availability(controller, at(63))
        self.assertEqual(availability["blocker"]["kind"], "cooling")
        self.assertGreater(availability["minimum_wait_seconds"], 0)
        with self.assertRaises(ValueError):
            controller.set_heater_override(True, at(64))

    def test_finish_session_can_defer_then_start_light(self):
        controller = self.controller(session_light_minutes=2)
        self.start(controller)
        controller.finish_session(at(5), light_after_run=False)
        self.assertIsNone(controller.session)
        self.assertIsNone(controller.light_after_run)
        controller.start_session_light("s", at(7))
        self.assertEqual(controller.light_after_run.started_at, at(7))
        self.assertEqual(controller.light_after_run.ends_at, at(127))

    def test_manual_session_gap_does_not_start_a_light_timer(self):
        controller = self.controller(session_gap_minutes=1)
        self.start(controller)
        controller.set_operation(False, at(1))
        controller.advance(at(61))
        self.assertIsNone(controller.session)
        self.assertIsNone(controller.light_after_run)

    def test_deferred_light_release_cannot_target_a_new_session(self):
        controller = self.controller()
        self.start(controller)
        controller.finish_session(at(5), light_after_run=False)
        controller.set_operation(True, at(6), session_id="new")
        with self.assertRaises(ValueError):
            controller.start_session_light("s", at(7))
