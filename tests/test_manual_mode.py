"""Manual thermostat control and observation share no automatic actuations."""

import unittest
from datetime import timedelta

from test_foundation import T0, event, parameters

from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.display import phase_timer, start_availability
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.presence import binary_presence
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

    def observe(self, controller, name, kind, second):
        controller.process(event(name, kind, second, session=controller.session.session_id))

    def test_manual_hysteresis_switches_without_minimum_heat_or_pause(self):
        controller = self.controller(
            target_temperature_c=80, readiness_offset_c=5,
            readiness_hysteresis_c=3, minimum_heating_minutes=10,
            thermostat_cooldown_minutes=10,
        )
        controller.set_temperature(77, at(0))
        controller.set_heater_override(True, at(1))
        identity = controller.session.session_id
        controller.report_heating(True, at(1))
        for second, temperature, heat in (
            (2, 84, True), (3, 85, False), (4, 78, False),
            (5, 77, True), (6, 80, True),
        ):
            controller.set_temperature(temperature, at(second))
            self.assertEqual(controller.last_decision.heat, heat)
            self.assertIs(controller.heater_override, True)
            self.assertEqual(controller.session.session_id, identity)
        controller.set_heater_override(False, at(7))
        self.assertIsNone(controller.session)
        self.assertEqual(len(controller.completed_sessions), 1)
        self.assertEqual(controller.completed_sessions[0].ended_at, at(7))
        self.assertIsNone(controller.light_after_run)

    def test_manual_target_changes_apply_immediately_without_starting_a_program(self):
        controller = Controller(
            Parameters({**parameters().as_dict(), "target_temperature_c": 80,
                        "final_temperature_c": 100}),
            control_mode="manual", program_mode="progressive",
            temperature_steps=(60, 90, 100),
        )
        self.assertEqual(controller.target_temperature, 80)
        controller.set_temperature(70, at(0))
        controller.set_heater_override(True, at(1))
        self.assertTrue(controller.last_decision.heat)
        controller.update_temperature_parameters(
            Parameters({**controller.parameters.as_dict(), "target_temperature_c": 60}),
            at(2), explicit_target=True,
        )
        self.assertEqual(controller.target_temperature, 60)
        self.assertFalse(controller.last_decision.heat)
        self.assertTrue(controller.heater_override)
        controller.update_temperature_parameters(
            Parameters({**controller.parameters.as_dict(), "target_temperature_c": 80}),
            at(3), explicit_target=True,
        )
        self.assertTrue(controller.last_decision.heat)
        self.assertIsNotNone(controller.session)
        self.assertEqual(controller.completed_sessions, ())

    def test_off_and_mode_change_clear_manual_hysteresis_memory(self):
        for clear in ("off", "release", "mode"):
            with self.subTest(clear=clear):
                controller = self.controller(target_temperature_c=80)
                self.start(controller)
                controller.set_heater_override(True, at(1))
                controller.set_temperature(80, at(2))
                self.assertTrue(controller.last_decision.heat)
                if clear == "mode":
                    controller.set_control_mode("automatic")
                    controller.set_control_mode("manual")
                else:
                    controller.set_heater_override(False if clear == "off" else None, at(3))
                controller.set_heater_override(True, at(4))
                self.assertFalse(controller.last_decision.heat)
                self.assertTrue(controller.heater_override)

    def test_invalid_measurement_revokes_manual_thermostat_enable(self):
        for temperature in (None, float("nan"), float("inf")):
            with self.subTest(temperature=temperature):
                controller = self.controller()
                self.start(controller)
                controller.set_heater_override(True, at(1))
                controller.set_temperature(temperature, at(2))
                self.assertFalse(controller.last_decision.heat)
                self.assertFalse(controller.heater_override)
                controller.set_temperature(60, at(3))
                self.assertFalse(controller.last_decision.heat)

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
                with self.assertRaisesRegex(ValueError, "Ofen Ein"):
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

    def test_recognition_observes_entry_gang_and_exit_without_controlling_outputs(self):
        for source in ("proxy", "ha_presence"):
            with self.subTest(source=source):
                controller = self.controller(target_temperature_c=80)
                controller.presence_source = source
                controller.presence_entity = "binary_sensor.presence"
                controller.set_temperature(80, at(0))
                controller.set_heater_override(True, at(1))
                def presence(state, second):
                    controller.observe_direct_presence(binary_presence(
                        controller.presence_entity, state, at(second), at(second),
                    ), at(second))
                presence("on", 2)
                self.observe(controller, "entry-open", Kind.DOOR_OPEN, 3)
                self.observe(controller, "entry-close", Kind.DOOR_CLOSE, 4)
                self.observe(controller, "infusion", Kind.INFUSION, 5)
                self.assertIsNotNone(controller.session.timeline.active)
                self.assertFalse(controller.last_decision.heat)
                self.assertTrue(controller.heater_override)
                self.assertFalse(controller.regulation_inputs.gang_heat_demand)
                self.assertFalse(controller.regulation_inputs.temporary_door_heat)
                self.observe(controller, "exit", Kind.DOOR_OPEN, 6)
                presence("off", 7)
                self.observe(controller, "ventilation", Kind.VENTILATION, 8)
                self.assertEqual(controller.session.timeline.gang_count, 1)
                self.assertEqual(controller.target_temperature, 80)
                self.assertIsNone(controller.session.after_run)
                self.assertIsNone(controller.light_after_run)
                controller.set_temperature(controller.thermostat_restart_temperature, at(9))
                self.assertTrue(controller.last_decision.heat)
                controller.set_heater_override(False, at(10))
                self.assertIsNone(controller.session)
                saved = controller.completed_sessions[-1]
                self.assertEqual(saved.timeline.gang_count, 1)
                self.assertEqual(saved.ended_at, at(10))
                self.assertEqual(saved.deadlines, ())
                self.assertIsNone(controller.light_after_run)

    def test_manual_entry_waits_for_minimum_and_exit_remains_possible_below_it(self):
        controller = self.controller()
        controller.presence_source = "ha_presence"
        controller.presence_entity = "binary_sensor.presence"
        minimum = controller.parameters.values["sauna_min_temperature_c"]
        controller.set_temperature(minimum - 1, at(0))
        controller.set_heater_override(True, at(1))
        controller.observe_direct_presence(binary_presence(
            controller.presence_entity, "on", at(2), at(2),
        ), at(2))
        self.observe(controller, "entry-open", Kind.DOOR_OPEN, 3)
        self.observe(controller, "entry-close", Kind.DOOR_CLOSE, 4)
        self.assertIsNone(controller.session.timeline.active)
        controller.set_temperature(minimum, at(5))
        self.assertEqual(controller.session.timeline.active.started_at, at(5))
        controller.set_temperature(minimum - 1, at(6))
        self.observe(controller, "exit", Kind.DOOR_OPEN, 7)
        controller.observe_direct_presence(binary_presence(
            controller.presence_entity, "off", at(8), at(8),
        ), at(8))
        self.observe(controller, "ventilation", Kind.VENTILATION, 9)
        self.assertEqual(controller.session.timeline.gang_count, 1)
        self.assertTrue(controller.last_decision.heat)
        self.assertIsNone(controller.session.after_run)

    def test_external_heating_feedback_alone_does_not_begin_a_recording(self):
        controller = self.controller(heat_reset_minutes=10)
        self.start(controller)
        controller.report_heating(True, at(0))
        controller.advance(at(70))
        self.assertIsNone(controller.session)
        self.assertEqual(controller.completed_sessions, ())
        self.assertEqual(controller.phase, "aus")

    def test_rejected_manual_on_creates_no_recording_and_valid_on_starts_it(self):
        controller = self.controller()
        with self.assertRaises(ValueError):
            controller.set_heater_override(True, at(1))
        controller.set_temperature(60, at(2))
        controller.set_operation(False, at(3))
        controller.set_heater_override(True, at(4))
        self.assertTrue(controller.last_decision.heat)
        self.assertIsNotNone(controller.session)

    def test_protection_ends_manual_recording_with_revoked_heater_enable(self):
        controller = self.controller()
        controller.set_temperature(60, at(0))
        controller.set_heater_override(True, at(1))
        controller.report_contactor(True, at(1))
        controller.advance(at(11))
        self.assertTrue(controller.last_decision.heat)
        self.assertIsNotNone(controller.session)
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

    def test_temperature_cutoff_keeps_manual_enable_without_starting_cooling(self):
        controller = self.controller(
            safety_temperature_c=80,
            overtemperature_minutes=1,
            forced_cooling_minutes=2,
            overtemperature_cooling_factor=2,
        )
        self.start(controller)
        controller.set_heater_override(True, at(1))
        controller.set_temperature(controller.thermostat_target, at(2))
        controller.advance(at(63))
        self.assertTrue(controller.heater_override)
        self.assertEqual(controller.phase, "manuell")
        self.assertIsNotNone(controller.session)
        self.assertIsNone(controller.session.after_run)
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
