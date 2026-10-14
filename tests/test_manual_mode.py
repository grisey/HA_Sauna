"""Manual thermostat control and observation share no automatic actuations."""

import unittest
from datetime import timedelta

from test_foundation import T0, event, parameters

from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.defaults import instance_default
from custom_components.ha_sauna.core.display import phase_timer, start_availability
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.presence import binary_presence
from custom_components.ha_sauna.core.timeline import Kind


def at(seconds):
    return T0 + timedelta(seconds=seconds)


class ManualModeTests(unittest.TestCase):
    def controller(
        self, *, presence_source=instance_default("presence_source"), presence_entity=None, **values,
    ):
        return Controller(
            Parameters({**parameters().as_dict(), **values}), control_mode="manual",
            presence_source=presence_source, presence_entity=presence_entity,
        )

    def start(self, controller):
        controller.set_temperature(60, at(0))

    def observe(self, controller, name, kind, second):
        controller.process(event(name, kind, second, session=controller.session.session_id))

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
                    controller.set_heater_override(False, at(3))
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

    def test_active_automatic_operation_locks_mode_changes(self):
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
        identity = controller.session.session_id
        deadline = next(d for d in controller.session.deadlines if d.purpose == "session_gap")
        self.assertFalse(controller.session.operation_enabled)
        controller.set_control_mode("automatic")
        self.assertIsNone(controller.heater_override)
        controller.set_control_mode("manual")
        self.assertIs(controller.heater_override, False)
        self.assertEqual(controller.session.session_id, identity)
        self.assertIn(deadline, controller.session.deadlines)

    def test_manual_entry_waits_for_minimum_and_exit_remains_possible_below_it(self):
        controller = self.controller(
            presence_source="ha_presence", presence_entity="binary_sensor.presence",
        )
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
        controller = self.controller()
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

    def test_protection_revokes_manual_enable_and_keeps_the_recording_gap(self):
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
        self.assertFalse(controller.session.operation_enabled)
        self.assertTrue(any(d.purpose == "session_gap" for d in controller.session.deadlines))

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
        controller = Controller(parameters())
        self.start(controller)
        controller.set_operation(True, at(0), session_id="s")
        controller.finish_session(at(5), light_after_run=False)
        self.assertIsNone(controller.session)
        self.assertIsNone(controller.light_after_run)
        controller.start_session_light("s", at(7))
        self.assertEqual(controller.light_after_run.started_at, at(7))
        self.assertEqual(controller.light_after_run.ends_at,
                         at(7 + controller.parameters.seconds("session_gap_minutes")))

    def test_idle_manual_off_creates_no_session_gap_or_light_timer(self):
        controller = self.controller()
        self.start(controller)
        controller.set_operation(False, at(1))
        controller.advance(at(1 + controller.parameters.seconds("session_gap_minutes")))
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
