"""Runtime wiring for the HA-free sauna-button gesture model."""

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

from custom_components.ha_sauna.bindings import ROLES, Bindings
from custom_components.ha_sauna.core.defaults import instance_default
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.timeline import Event, Kind
from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime


def bindings():
    return {
        role.key: f"{role.domains[0]}.test_{role.key}"
        for role in ROLES
        if not role.optional or role.device_class in {"temperature", "humidity"}
    }


class ButtonRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 20, 12, tzinfo=UTC)
        self.runtime = SaunaRuntime(
            Configuration(Bindings(bindings()), Parameters({"button_hold_seconds": 2}),
                          button_session_gesture="long"), lambda: self.now
        )

    def _event(self, name, seconds=0):
        self.now += timedelta(seconds=seconds)
        asyncio.run(self._handle(name))

    def test_selected_multi_click_starts_automatic_and_finishes_without_hold(self):
        for gesture in ("double", "triple"):
            with self.subTest(gesture=gesture):
                self.setUp()
                self.runtime = SaunaRuntime(
                    Configuration(Bindings(bindings()), Parameters({}),
                                  control_mode="manual", button_session_gesture=gesture),
                    clock=lambda: self.now,
                )
                self.runtime.controller.set_temperature(70, self.now)
                self._event(gesture)
                identity = self.runtime.session.session_id
                self.assertEqual(self.runtime.configuration.control_mode, "automatic")
                self.assertFalse(self.runtime.button_start_hold_active)
                self.assertIsNone(self.runtime._button_hold_session_id)
                self._event(gesture, 1)
                self.assertIsNone(self.runtime.session)
                self.assertEqual(self.runtime.controller.light_after_run.session_id, identity)
                self.assertIsNone(self.runtime._button_hold_session_id)

    def test_received_binary_hold_duration_survives_monotone_action_time(self):
        async def gesture(duration):
            at = self.now + timedelta(seconds=10)
            await self.runtime._handle_button_event("on", at, received_at=self.now)
            await self.runtime._handle_button_event(
                "off", at, received_at=self.now + timedelta(seconds=duration)
            )

        for duration in (1, 3):
            with self.subTest(duration=duration):
                self.setUp()
                self._start_with_temperature()
                asyncio.run(gesture(duration))
                self.assertEqual(self.runtime.session is None, duration == 3)

    def test_automatic_idle_heater_command_requires_explicit_mode_change(self):
        with self.assertRaisesRegex(ValueError, "Saunabetrieb"):
            asyncio.run(self.runtime.set_heater_override(True))
        self.assertEqual(self.runtime.configuration.control_mode, "automatic")
        self.assertIsNone(self.runtime.controller.heater_override)
        self.assertIsNone(self.runtime.session)

    def test_idle_heater_command_keeps_session_recording_off(self):
        self.runtime._set_control_mode("manual")
        self.runtime.controller.set_temperature(70, self.now)
        asyncio.run(self.runtime.set_heater_override(True))
        self.assertEqual(self.runtime.configuration.control_mode, "manual")
        self.assertIsNone(self.runtime.session)
        self.assertTrue(self.runtime.controller.heater_override)
        self.assertTrue(self.runtime.controller.last_decision.heat)

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
        for seconds in (59, 60, 61):
            with self.subTest(seconds=seconds):
                self.setUp()
                self.runtime = SaunaRuntime(
                    Configuration(Bindings(bindings()), Parameters({"session_gap_minutes": 1}),
                                  button_session_gesture="long"),
                    lambda: self.now,
                )
                self._start_with_temperature()
                old_id = self.runtime.session.session_id
                asyncio.run(self.runtime.set_operation(False))
                self._event("long", seconds)
                self.assertTrue(self.runtime.session.operation_enabled)
                self.assertEqual(self.runtime.session.session_id == old_id, seconds < 60)

    def test_short_during_pending_session_does_not_enter_manual_or_resume(self):
        self._start_with_temperature()
        identity = self.runtime.session.session_id
        asyncio.run(self.runtime.set_operation(False))
        with self.assertRaisesRegex(ValueError, "Saunabetrieb"):
            self._event("short", 1)
        self.assertEqual(self.runtime.session.session_id, identity)
        self.assertFalse(self.runtime.session.operation_enabled)
        self.assertEqual(self.runtime.configuration.control_mode, "automatic")
        self.assertIsNone(self.runtime.controller.heater_override)

    def test_short_outside_session_enters_manual_and_never_records_a_session(self):
        self.runtime.controller.set_temperature(70, self.now)
        self.runtime.save_configuration = Mock()
        for value in (True, False, True):
            self._event("press", 1)
            self._event("release")
            self._event("short")
            self.assertIsNone(self.runtime.session)
            self.assertEqual(self.runtime.controller.heater_override, value)
            self.assertEqual(self.runtime.controller.last_decision.heat, value)
            self.assertEqual(self.runtime.configuration.control_mode, "manual")
        self.runtime.save_configuration.assert_called()
        self.assertEqual(self.runtime.controller.completed_sessions, ())

    def test_long_manual_start_switches_to_automatic_and_holds_acknowledgement(self):
        self.runtime._set_control_mode("manual")
        self.runtime.controller.set_temperature(70, self.now)
        self._event("press")
        self._event("long", 2)
        identity = self.runtime.session.session_id
        self.assertEqual(self.runtime.configuration.control_mode, "automatic")
        self.assertTrue(self.runtime.session.operation_enabled)
        self.assertTrue(self.runtime.button_start_hold_active)
        self._event("long", 1)
        self.assertEqual(self.runtime.session.session_id, identity)
        self._event("release")
        self.assertFalse(self.runtime.button_start_hold_active)
        self._event("short")
        self.assertEqual(self.runtime.session.session_id, identity)
        self.assertIsNone(self.runtime.controller.heater_override)

    def test_native_double_toggles_each_press_without_starting(self):
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

    def test_event_only_triple_applies_each_short_press(self):
        self.runtime.controller.set_temperature(70, self.now)
        self._event("triple")
        self.assertIsNone(self.runtime.session)
        self.assertTrue(self.runtime.controller.heater_override)

    def test_long_release_finishes_once_and_starts_light_after_run_on_release(self):
        self._event("long")
        session_id = self.runtime.session.session_id
        self._event("press")
        self._event("long", 2)
        self.assertIsNone(self.runtime.session)
        self.assertIsNone(self.runtime.controller.light_after_run)
        self._event("release")
        self.assertEqual(self.runtime.controller.light_after_run.session_id, session_id)
        self._event("release")
        self.assertEqual(len(self.runtime.controller.completed_sessions), 1)

    def test_sparse_long_starts_without_lingering_start_acknowledgement(self):
        self.runtime.controller.set_temperature(70, self.now)
        self._event("long")
        self.assertTrue(self.runtime.session.operation_enabled)
        self.assertFalse(self.runtime.button_start_hold_active)
        self._event("long", 10)
        self.assertIsNone(self.runtime.session)
        self.assertFalse(self.runtime.controller.last_decision.heat)
        self._event("release")
        self.assertIsNotNone(self.runtime.controller.light_after_run)

    def test_binary_start_requires_long_received_duration(self):
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

        for duration in (1, 2, 3):
            with self.subTest(duration=duration):
                self.setUp()
                self.runtime.controller.set_temperature(70, self.now)
                asyncio.run(gesture(duration))
                self.assertEqual(self.runtime.session is not None, duration >= 2)
                self.assertEqual(
                    self.runtime.controller.last_decision.heat, True
                )
                self.assertIs(self.runtime.controller.heater_override, True if duration == 1 else None)
                self.assertIsNone(self.runtime.controller.light_after_run)

    def test_delayed_binary_release_finishes_and_starts_light_after_run(self):
        self._event("long")
        session_id = self.runtime.session.session_id
        self._event("on")
        self.now += timedelta(seconds=2)
        asyncio.run(self._handle("off"))
        self.assertIsNone(self.runtime.session)
        self.assertEqual(self.runtime.controller.light_after_run.session_id, session_id)

    def test_new_start_makes_an_old_release_harmless(self):
        self._event("long")
        self._event("press")
        self._event("long", 2)
        self.runtime._set_operation(True)
        new_session_id = self.runtime.session.session_id
        self._event("release")
        self.assertEqual(self.runtime.session.session_id, new_session_id)
        self.assertIsNone(self.runtime.controller.light_after_run)

    def test_button_cannot_pause_or_reenter_active_after_run(self):
        started_at = self._start_with_temperature()
        controller = self.runtime.controller
        controller.report_contactor(True, started_at + timedelta(seconds=1))
        self._complete_gang(started_at)

        phase = controller.session.after_run
        for _ in range(2):
            with self.assertRaisesRegex(ValueError, "Ofenkühlung"):
                self._event("short", 1)
            self.assertIsNone(controller.heater_override)
            self.assertEqual(controller.session.after_run, phase)
            self.assertFalse(controller.last_decision.heat)

    def test_long_heat_has_no_independent_cooling_and_button_uses_feedback(self):
        started_at = self._start_with_temperature()
        controller = self.runtime.controller
        controller.report_heating(True, started_at)
        self.now = started_at + timedelta(minutes=90)
        controller.advance(self.now)
        controller.report_contactor(True, self.now)
        self.assertIsNone(controller.session.cooling)
        self.assertIsNone(controller.session.after_run)

        self._event("short", 1)
        self.assertFalse(controller.heater_override)
        self.assertFalse(controller.last_decision.heat)

        self._event("short", 1)
        self.assertIsNone(controller.heater_override)
        self.assertIsNone(controller.session.cooling)
        self.assertTrue(controller.last_decision.heat)

    def test_button_cannot_heat_through_protection_during_after_run(self):
        started_at = self._start_with_temperature()
        controller = self.runtime.controller
        self._complete_gang(started_at)
        controller.protection.add("heater_service_unavailable")

        with self.assertRaisesRegex(ValueError, "aktivem Schutz"):
            self._event("short", 1)

        self.assertIsNone(controller.heater_override)
        self.assertFalse(controller.last_decision.heat)

    def test_legacy_budget_does_not_create_cooling_during_a_gang(self):
        self.runtime = SaunaRuntime(
            Configuration(
                Bindings(bindings()),
                Parameters({"heating_minutes": 1, "heating_reduction_minutes": 0.25}),
                button_session_gesture="long",
            ),
            lambda: self.now,
        )
        started_at = self._start_with_temperature()
        controller = self.runtime.controller
        session_id = controller.session.session_id
        controller.report_heating(True, started_at)
        for name, kind, seconds in (
            ("entry-open", Kind.DOOR_OPEN, 0),
            ("close", Kind.DOOR_CLOSE, 1),
            ("person", Kind.PERSON_STRONG, 2),
        ):
            self.now = started_at + timedelta(seconds=seconds)
            controller.process(Event(name, session_id, kind, self.now, self.now))
        self.now = started_at + timedelta(minutes=1)
        controller.advance(self.now)
        controller.report_contactor(True, self.now)
        self.assertIsNone(controller.session.cooling)

        self._event("short", 1)

        self.assertFalse(controller.heater_override)

    def test_removed_hold_brightness_parameter_is_not_restored(self):
        values = Parameters({"button_hold_brightness_percent": 1}).values
        self.assertNotIn("button_hold_brightness_percent", values)

    def test_button_start_uses_its_frozen_constant_temperature(self):
        self.runtime = SaunaRuntime(
            Configuration(
                Bindings(bindings()),
                Parameters({"target_temperature_c": 86}),
                button_session_gesture="long", button_program="constant",
                button_temperature_c=74,
            ),
            lambda: self.now,
        )

        self._event("long")

        self.assertEqual(self.runtime.controller.target_temperature, 74)
        self.assertEqual(self.runtime.configuration.button_temperature_c, 74)

    def test_button_start_uses_the_independent_factory_constant_temperature(self):
        self.runtime = SaunaRuntime(
            Configuration(Bindings(bindings()), Parameters({"target_temperature_c": 74}),
                          button_session_gesture="long", button_program="constant"),
            lambda: self.now,
        )
        self._event("long")
        self.assertEqual(self.runtime.controller.program_mode, "constant")
        self.assertEqual(
            self.runtime.controller.target_temperature,
            instance_default("button_temperature_c"),
        )

    def test_button_start_uses_the_selected_named_program(self):
        program = self.runtime.configuration.temperature_programs[-1]
        self.runtime = SaunaRuntime(
            Configuration(
                Bindings(bindings()),
                Parameters({"target_temperature_c": 70}),
                button_session_gesture="long", button_program=program.id,
            ),
            lambda: self.now,
        )

        self._event("long")

        self.assertEqual(self.runtime.controller.program_mode, "progressive")
        self.assertEqual(self.runtime.controller.target_temperature, program.start_c)


if __name__ == "__main__":
    unittest.main()
