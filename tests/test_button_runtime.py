"""Runtime wiring for the HA-free sauna-button gesture model."""

import asyncio
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

from custom_components.ha_sauna.bindings import ROLES, Bindings
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.timeline import Event, Kind
from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime


def bindings(*, binary=False):
    values = {
        role.key: f"{role.domains[0]}.test_{role.key}"
        for role in ROLES
        if not role.optional or role.device_class in {"temperature", "humidity"}
    }
    if binary:
        values["control_input"] = "binary_sensor.test_control_input"
    return values


class ButtonRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 20, 12, tzinfo=UTC)
        self.runtime = SaunaRuntime(
            Configuration(Bindings(bindings()), Parameters({}),
                          control_input_mode="button", button_session_gesture="long"), lambda: self.now
        )

    def _use_binary_input(self):
        self.runtime = SaunaRuntime(
            replace(
                self.runtime.configuration,
                bindings=Bindings(bindings(binary=True)),
                control_input_mode="button",
            ),
            lambda: self.now,
        )

    def _event(self, name, seconds=0):
        self.now += timedelta(seconds=seconds)
        asyncio.run(self._handle(name))

    def _advancing_clock(self):
        self.now += timedelta(microseconds=1)
        return self.now

    def _program_distinct_from_standard(self):
        standard = self.runtime.configuration.parameters.values["standard_temperature_c"]
        program = next((
            program for program in reversed(self.runtime.configuration.temperature_programs)
            if program.start_c != standard
        ), None)
        self.assertIsNotNone(program, "The catalog must distinguish program and standard targets")
        return program

    def test_idle_short_uses_one_action_time_with_an_advancing_clock(self):
        program = self._program_distinct_from_standard()
        self.runtime = SaunaRuntime(
            replace(
                self.runtime.configuration,
                parameters=Parameters({
                    **self.runtime.configuration.parameters.as_dict(),
                    "target_temperature_c": program.start_c,
                }),
            ),
            self._advancing_clock,
        )
        values = self.runtime.configuration.parameters.values
        self.assertNotEqual(values["target_temperature_c"], values["standard_temperature_c"])
        self.assertLess(
            values["sauna_min_temperature_c"],
            values["standard_temperature_c"] - values["readiness_hysteresis_c"],
        )
        self.runtime.controller.set_temperature(
            values["sauna_min_temperature_c"], self.now,
        )
        action_at = self.now

        self._event("short")

        self.assertGreater(self.now, action_at)
        self.assertEqual(self.runtime.session.timeline.session_started_at, action_at)

    def test_manual_long_uses_one_action_time_with_an_advancing_clock(self):
        program = self._program_distinct_from_standard()
        for button_program in ("constant", program.id):
            with self.subTest(button_program=button_program):
                self.setUp()
                self.runtime = SaunaRuntime(
                    replace(
                        self.runtime.configuration,
                        control_mode="manual",
                        selected_program_id=program.id,
                        button_program=button_program,
                    ),
                    self._advancing_clock,
                )
                values = self.runtime.configuration.parameters.values
                self.assertNotEqual(program.start_c, values["standard_temperature_c"])
                self.assertLess(
                    values["sauna_min_temperature_c"],
                    min(values["standard_temperature_c"], program.start_c)
                    - values["readiness_hysteresis_c"],
                )
                self.runtime.controller.set_temperature(
                    values["sauna_min_temperature_c"], self.now,
                )
                self._event("short")
                manual_id = self.runtime.session.session_id
                started_at = self.runtime.session.timeline.session_started_at
                manual_phase = self.runtime.session.base_phases[-1]
                action_at = self.now

                self._event("long")

                self.assertGreater(self.now, action_at)
                self.assertEqual(self.runtime.controller.completed_sessions, ())
                self.assertEqual(self.runtime.session.session_id, manual_id)
                self.assertEqual(self.runtime.session.timeline.session_started_at, started_at)
                self.assertIn(manual_phase, self.runtime.session.base_phases)
                self.assertEqual(self.runtime.session.base_phases[-1].at, action_at)
                self.assertEqual(self.runtime.session.base_phases[-1].phase, "aufheizen")

    def test_received_binary_hold_duration_survives_monotone_action_time(self):
        threshold = self.runtime.configuration.parameters.values["button_hold_seconds"]
        async def gesture(duration):
            at = self.now + timedelta(seconds=10)
            await self.runtime._handle_button_event("on", at, received_at=self.now)
            await self.runtime._handle_button_event(
                "off", at, received_at=self.now + timedelta(seconds=duration)
            )

        for duration in (threshold / 2, threshold * 2):
            with self.subTest(duration=duration):
                self.setUp()
                self._use_binary_input()
                self._start_with_temperature()
                asyncio.run(gesture(duration))
                self.assertEqual(self.runtime.session.operation_enabled, duration < threshold)

    def test_automatic_idle_heater_command_requires_explicit_mode_change(self):
        with self.assertRaisesRegex(ValueError, "Saunabetrieb"):
            asyncio.run(self.runtime.set_heater_override(True))
        self.assertEqual(self.runtime.configuration.control_mode, "automatic")
        self.assertIsNone(self.runtime.controller.heater_override)
        self.assertIsNone(self.runtime.session)

    async def _handle(self, name):
        async with self.runtime._lock:
            await self.runtime._handle_button_event(name, self.now)
            await self.runtime._cycle()

    def _start_with_temperature(self):
        self.runtime.controller.set_temperature(70, self.now)
        self._event("long")
        return self.now

    def _complete_gang(self, started_at):
        controller = self.runtime.controller
        session_id = controller.session.session_id
        for name, kind, seconds in (
            ("entry-open", Kind.DOOR_OPEN, 290),
            ("close", Kind.DOOR_CLOSE, 300),
            ("person", Kind.PERSON_STRONG, 360),
            ("infusion", Kind.INFUSION, 480),
            ("open", Kind.DOOR_OPEN, 1260),
            ("vent", Kind.VENTILATION, 1290),
        ):
            self.now = started_at + timedelta(seconds=seconds)
            controller.process(Event(name, session_id, kind, self.now, self.now))

    def test_long_resumes_pending_session_or_starts_after_gap_expiry(self):
        gap = Parameters({}).seconds("session_gap_minutes")
        for seconds in (gap - 1, gap, gap + 1):
            with self.subTest(seconds=seconds):
                self.setUp()
                self.runtime = SaunaRuntime(
                    Configuration(Bindings(bindings()), Parameters({}),
                                  button_session_gesture="long"),
                    lambda: self.now,
                )
                self._start_with_temperature()
                old_id = self.runtime.session.session_id
                asyncio.run(self.runtime.set_operation(False))
                self._event("long", seconds)
                self.assertTrue(self.runtime.session.operation_enabled)
                self.assertEqual(self.runtime.session.session_id == old_id, seconds < gap)

    def test_short_resume_persists_mode_without_changing_recording_identity(self):
        self._start_with_temperature()
        identity = self.runtime.session.session_id
        asyncio.run(self.runtime.set_operation(False))
        self.runtime.save_configuration = Mock()
        self._event("short", 1)
        self.assertEqual(self.runtime.session.session_id, identity)
        self.assertTrue(self.runtime.session.operation_enabled)
        self.runtime.save_configuration.assert_called_once()
        saved = self.runtime.save_configuration.call_args.args[0]
        self.assertEqual(saved.control_mode, "manual")
        self.assertEqual(self.runtime.controller.completed_sessions, ())

    def test_short_mode_choice_calls_configuration_persistence(self):
        self.runtime.controller.set_temperature(70, self.now)
        self.runtime.save_configuration = Mock()
        self._event("short")
        self.runtime.save_configuration.assert_called_once()
        saved = self.runtime.save_configuration.call_args.args[0]
        self.assertEqual(saved.control_mode, "manual")

    def test_short_toggles_manual_enable_while_temperature_is_satisfied(self):
        values = self.runtime.configuration.parameters.values
        self.runtime.controller.set_temperature(
            values["target_temperature_c"] + values["readiness_offset_c"], self.now
        )
        for enabled in (True, False, True):
            self._event("short", 1)
            self.assertEqual(self.runtime.controller.heater_override, enabled)
            self.assertFalse(self.runtime.controller.last_decision.heat)
            self.assertEqual(self.runtime.session.operation_enabled, enabled)
        self.assertEqual(self.runtime.controller.completed_sessions, ())

    def test_binary_hold_ignores_native_suffix_before_release(self):
        self._use_binary_input()
        self.runtime._set_control_mode("manual")
        self.runtime.controller.set_temperature(70, self.now)
        self._event("short")
        self._event("on")
        threshold = self.runtime.configuration.parameters.values["button_hold_seconds"]
        self.now += timedelta(seconds=threshold)
        asyncio.run(self.runtime.tick())
        identity = self.runtime.session.session_id
        self.assertTrue(self.runtime.button_start_hold_active)
        self._event("long", 1)
        self.assertEqual(self.runtime.session.session_id, identity)
        self._event("off")
        self.assertFalse(self.runtime.button_start_hold_active)

    def test_unselected_native_double_does_not_start_manual_operation(self):
        self.runtime.controller.set_temperature(70, self.now)
        self.runtime.controller.report_contactor(False, self.now)
        for _ in range(2):
            self._event("press")
            self._event("release")
        self._event("double")
        self.assertIsNone(self.runtime.session)
        self.assertFalse(self.runtime.controller.last_decision.heat)
        self._event("double")
        self.assertFalse(self.runtime.controller.last_decision.heat)

    def test_native_event_source_has_no_second_hold_timer(self):
        self.assertEqual(self.runtime.configuration_model.button.input_kind, "event")
        self.assertIsNone(self.runtime._button.hold_threshold)
        self._event("triple")
        self.assertIsNone(self.runtime.session)
        self.assertIsNone(self.runtime.controller.heater_override)

    def test_long_release_stops_once_and_starts_light_after_run_on_release(self):
        self._use_binary_input()
        self._event("long")
        session_id = self.runtime.session.session_id
        self._event("on")
        self.now += timedelta(
            seconds=self.runtime.configuration.parameters.values["button_hold_seconds"]
        )
        asyncio.run(self.runtime.tick())
        self.assertEqual(self.runtime.session.session_id, session_id)
        self.assertFalse(self.runtime.session.operation_enabled)
        deadline = next(d for d in self.runtime.session.deadlines if d.purpose == "session_gap")
        self.assertEqual(self.runtime.controller.light_after_run.ends_at, deadline.due_at)
        self._event("off")
        self.assertEqual(self.runtime.controller.light_after_run.session_id, session_id)
        self._event("off")
        self.assertEqual(self.runtime.controller.completed_sessions, ())
        self.assertIn(deadline, self.runtime.session.deadlines)
        self.assertEqual(self.runtime.controller.light_after_run.ends_at, deadline.due_at)

    def test_sparse_long_starts_without_lingering_start_acknowledgement(self):
        self.runtime.controller.set_temperature(70, self.now)
        self._event("long")
        self.assertTrue(self.runtime.session.operation_enabled)
        self.assertFalse(self.runtime.button_start_hold_active)
        self._event("long", 10)
        self.assertFalse(self.runtime.session.operation_enabled)
        self.assertFalse(self.runtime.controller.last_decision.heat)
        self.assertIsNotNone(self.runtime.controller.light_after_run)
        self._event("release")
        self.assertIsNotNone(self.runtime.controller.light_after_run)

    def test_release_after_recording_expiry_cannot_restart_the_light_period(self):
        self._use_binary_input()
        self._event("long")
        self._event("on")
        self.now += timedelta(
            seconds=self.runtime.configuration.parameters.values["button_hold_seconds"]
        )
        asyncio.run(self.runtime.tick())
        deadline = next(d for d in self.runtime.session.deadlines if d.purpose == "session_gap")
        self.now = deadline.due_at + timedelta(microseconds=1)
        asyncio.run(self.runtime.tick())
        self._event("off")
        self.assertIsNone(self.runtime.session)
        self.assertEqual(self.runtime.controller.completed_sessions[-1].ended_at, deadline.due_at)
        self.assertEqual(self.runtime.controller.light_after_run.ends_at, deadline.due_at)

    def test_binary_start_requires_long_received_duration(self):
        threshold = self.runtime.configuration.parameters.values["button_hold_seconds"]
        async def gesture(duration):
            action_at = self.now + timedelta(seconds=10)
            await self.runtime._handle_button_event(
                "on", action_at, received_at=self.now
            )
            self.assertIsNone(self.runtime.session)
            self.assertFalse(self.runtime.controller.last_decision.heat)
            await self.runtime._handle_button_event(
                "off", action_at, received_at=self.now + timedelta(seconds=duration)
            )
            self.now = action_at
            await self.runtime._cycle()

        for duration in (threshold / 2, threshold, threshold * 2):
            with self.subTest(duration=duration):
                self.setUp()
                self._use_binary_input()
                self.runtime.controller.set_temperature(70, self.now)
                asyncio.run(gesture(duration))
                self.assertIsNotNone(self.runtime.session)
                self.assertEqual(self.runtime.controller.automatic_operation_enabled, duration >= threshold)
                self.assertEqual(
                    self.runtime.controller.last_decision.heat, True
                )
                self.assertIs(self.runtime.controller.heater_override, True if duration < threshold else None)
                self.assertIsNone(self.runtime.controller.light_after_run)

    def test_delayed_binary_release_stops_and_starts_light_after_run(self):
        self._use_binary_input()
        self._event("long")
        session_id = self.runtime.session.session_id
        self._event("on")
        self.now += timedelta(
            seconds=self.runtime.configuration.parameters.values["button_hold_seconds"]
        )
        asyncio.run(self._handle("off"))
        self.assertEqual(self.runtime.session.session_id, session_id)
        self.assertFalse(self.runtime.session.operation_enabled)
        self.assertEqual(self.runtime.controller.light_after_run.session_id, session_id)

    def test_new_start_makes_an_old_release_harmless(self):
        self._use_binary_input()
        self._event("long")
        self._event("on")
        self.now += timedelta(
            seconds=self.runtime.configuration.parameters.values["button_hold_seconds"]
        )
        asyncio.run(self.runtime.tick())
        self.runtime._set_operation(True)
        new_session_id = self.runtime.session.session_id
        self._event("off")
        self.assertEqual(self.runtime.session.session_id, new_session_id)
        self.assertIsNone(self.runtime.controller.light_after_run)

    def test_button_cannot_heat_through_protection_during_after_run(self):
        started_at = self._start_with_temperature()
        controller = self.runtime.controller
        self._complete_gang(started_at)
        controller.protection.add("heater_service_unavailable")

        with self.assertRaisesRegex(ValueError, "aktivem Schutz"):
            self._event("short", 1)

        self.assertIsNone(controller.heater_override)
        self.assertFalse(controller.last_decision.heat)

if __name__ == "__main__":
    unittest.main()
