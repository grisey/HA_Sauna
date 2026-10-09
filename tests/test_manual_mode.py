"""Manual control drives outputs without creating or recording a session."""

import unittest
from datetime import timedelta

from test_foundation import T0, event, parameters

from custom_components.ha_sauna.core.controller import Controller
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

    def reject_session_event(self, controller, name, kind, second):
        with self.assertRaisesRegex(ValueError, "ohne Session"):
            controller.process(event(name, kind, second))

    def test_mode_can_only_change_between_sessions(self):
        controller = Controller(parameters())
        controller.set_control_mode("manual")
        self.assertEqual(controller.control_mode, "manual")
        controller.set_control_mode("automatic")
        controller.set_operation(True, at(0), session_id="s")
        with self.assertRaises(ValueError):
            controller.set_control_mode("manual")

    def test_manual_mode_rejects_both_session_start_entries(self):
        for direct in (False, True):
            with self.subTest(direct=direct):
                controller = self.controller()
                self.start(controller)
                with self.assertRaisesRegex(ValueError, "keine Saunasitzung"):
                    if direct:
                        controller.begin_session("s", at(1))
                    else:
                        controller.set_operation(True, at(1), session_id="s")
                self.assertIsNone(controller.session)
                self.assertFalse(controller.last_decision.heat)

    def test_manual_mode_never_starts_thermostat_without_a_demand(self):
        controller = self.controller()
        self.assertIs(controller.heater_override, False)
        self.start(controller)
        self.assertIs(controller.heater_override, False)
        self.assertEqual(controller.phase, "aus")
        self.assertFalse(controller.last_decision.heat)
        self.assertEqual(controller.last_decision.reason, "manual_mode")
        availability = start_availability(
            controller, at(0), estimated_ready_seconds=100
        )
        self.assertIsNone(availability["until_ready_seconds"])
        self.assertNotIn("start_window_seconds", availability)
        self.assertIsNone(phase_timer(controller, at(0)))
        self.assertIsNone(controller.session)

    def test_mode_change_starts_off_and_return_releases_the_manual_selection(self):
        controller = Controller(parameters())
        controller.set_control_mode("manual")
        self.assertIs(controller.heater_override, False)
        self.start(controller)
        controller.set_heater_override(True, at(1))
        controller.set_control_mode("manual")
        self.assertIs(controller.heater_override, True)
        controller.set_operation(False, at(2))
        self.assertIs(controller.heater_override, False)
        self.assertIsNone(controller.session)
        controller.set_control_mode("automatic")
        self.assertIsNone(controller.heater_override)
        controller.set_control_mode("manual")
        self.assertIs(controller.heater_override, False)

    def test_explicit_demand_does_not_create_gangs_from_presence_events(self):
        controller = self.controller()
        self.start(controller)
        controller.set_heater_override(True, at(1))
        self.reject_session_event(controller, "close", Kind.DOOR_CLOSE, 2)
        self.reject_session_event(controller, "person", Kind.PERSON_STRONG, 3)
        self.assertEqual(controller.phase, "aus")
        self.assertTrue(controller.last_decision.heat)
        self.reject_session_event(controller, "infusion", Kind.INFUSION, 4)
        self.reject_session_event(controller, "open", Kind.DOOR_OPEN, 5)
        self.reject_session_event(controller, "ventilation", Kind.VENTILATION, 6)
        self.assertIsNone(controller.session)
        self.assertTrue(controller.last_decision.heat)
        self.assertEqual(controller.completed_sessions, ())

    def test_manual_heat_does_not_create_session_heating_records(self):
        controller = self.controller(heating_minutes=1, heat_reset_minutes=10)
        self.start(controller)
        controller.report_heating(True, at(0))
        controller.advance(at(70))
        self.assertIsNone(controller.session)
        self.assertEqual(controller.completed_sessions, ())
        self.assertEqual(controller.phase, "aus")

    def test_missing_temperature_blocks_manual_demand_but_recording_off_does_not(self):
        controller = self.controller()
        with self.assertRaises(ValueError):
            controller.set_heater_override(True, at(1))
        controller.set_temperature(60, at(2))
        controller.set_operation(False, at(3))
        controller.set_heater_override(True, at(4))
        self.assertTrue(controller.last_decision.heat)
        self.assertIsNone(controller.session)

    def test_manual_idle_demand_uses_protection_without_a_session(self):
        controller = self.controller()
        controller.set_temperature(60, at(0))
        controller.set_heater_override(True, at(1))
        controller.report_contactor(True, at(1))
        controller.advance(at(11))
        self.assertTrue(controller.last_decision.heat)
        self.assertIsNone(controller.session)
        controller.set_heater_override(False, at(11))
        controller.report_contactor(False, at(11))
        controller.advance(at(21))
        self.assertFalse(controller.last_decision.heat)
        controller.set_heater_override(True, at(21))
        controller.protection.add("heater_feedback_mismatch")
        controller.advance(at(22))
        self.assertFalse(controller.last_decision.heat)
        self.assertFalse(controller.heater_override)
        self.assertIsNone(controller.session)

    def test_old_temperature_threshold_without_a_gang_keeps_manual_demand(self):
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
        self.assertTrue(controller.heater_override)
        self.assertEqual(controller.phase, "aus")
        self.assertIsNone(controller.session)
        self.assertTrue(controller.last_decision.heat)

    def test_presence_and_old_temperature_threshold_create_no_manual_session(self):
        controller = self.controller(
            safety_temperature_c=80,
            overtemperature_minutes=1,
            forced_cooling_minutes=2,
            overtemperature_cooling_factor=2,
        )
        self.start(controller)
        controller.set_heater_override(True, at(1))
        self.reject_session_event(controller, "close", Kind.DOOR_CLOSE, 2)
        self.reject_session_event(controller, "person", Kind.PERSON_STRONG, 3)
        self.reject_session_event(controller, "infusion", Kind.INFUSION, 4)
        controller.set_temperature(81, at(5))
        controller.advance(at(66))

        self.assertEqual(controller.phase, "aus")
        self.assertTrue(controller.heater_override)
        self.assertTrue(controller.last_decision.heat)
        self.assertIsNone(controller.session)

    def test_technical_protection_still_revokes_manual_heat(self):
        controller = self.controller(
            safety_temperature_c=80,
            overtemperature_minutes=1,
        )
        self.start(controller)
        controller.set_heater_override(True, at(1))
        self.reject_session_event(controller, "close", Kind.DOOR_CLOSE, 2)
        self.reject_session_event(controller, "person", Kind.PERSON_STRONG, 3)
        self.reject_session_event(controller, "infusion", Kind.INFUSION, 4)
        controller.set_temperature(81, at(5))
        controller.protection.add("confirmed_controller_failure")
        controller.advance(at(66))

        self.assertEqual(controller.phase, "aus")
        self.assertIs(controller.heater_override, False)
        self.assertFalse(controller.last_decision.heat)

    def test_finish_session_can_defer_then_start_light(self):
        controller = Controller(Parameters({**parameters().as_dict(), "session_gap_minutes": 2}))
        self.start(controller)
        controller.set_operation(True, at(0), session_id="s")
        controller.finish_session(at(5), light_after_run=False)
        self.assertIsNone(controller.session)
        self.assertIsNone(controller.light_after_run)
        controller.start_session_light("s", at(7))
        self.assertEqual(controller.light_after_run.started_at, at(7))
        self.assertEqual(controller.light_after_run.ends_at, at(127))

    def test_manual_off_does_not_start_a_session_gap_or_light_timer(self):
        controller = self.controller(session_gap_minutes=1)
        self.start(controller)
        controller.set_operation(False, at(1))
        controller.advance(at(61))
        self.assertIsNone(controller.session)
        self.assertIsNone(controller.light_after_run)

    def test_deferred_light_release_cannot_target_a_new_session(self):
        controller = Controller(parameters())
        self.start(controller)
        controller.set_operation(True, at(0), session_id="s")
        controller.finish_session(at(5), light_after_run=False)
        controller.set_operation(True, at(6), session_id="new")
        with self.assertRaises(ValueError):
            controller.start_session_light("s", at(7))
