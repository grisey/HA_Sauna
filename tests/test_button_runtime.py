"""Runtime wiring for the HA-free sauna-button gesture model."""

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

from custom_components.ha_sauna.bindings import ROLES, Bindings
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
            Configuration(Bindings(bindings()), Parameters({})), lambda: self.now
        )

    def _event(self, name, seconds=0):
        self.now += timedelta(seconds=seconds)
        asyncio.run(self._handle(name))

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

    def test_manual_only_heater_command_rechecks_mode_when_executed(self):
        with self.assertRaisesRegex(ValueError, "Betriebsart"):
            asyncio.run(self.runtime.set_heater_override(True, manual_only=True))
        self.assertIsNone(self.runtime.controller.heater_override)
        self.assertIsNone(self.runtime.session)

    async def _handle(self, name):
        async with self.runtime._lock:
            await self.runtime._handle_button_event(name, self.now)
            await self.runtime._cycle()

    def _start_with_temperature(self):
        self._event("short")
        self.runtime.controller.set_temperature(70, self.now)
        return self.now

    def _complete_gang(self, started_at):
        controller = self.runtime.controller
        session_id = controller.session.session_id
        for name, kind, seconds in (
            ("close", Kind.DOOR_CLOSE, 300),
            ("person", Kind.PERSON_STRONG, 360),
            ("infusion", Kind.INFUSION, 480),
            ("open", Kind.DOOR_OPEN, 1260),
            ("vent", Kind.VENTILATION, 1290),
        ):
            self.now = started_at + timedelta(seconds=seconds)
            controller.process(Event(name, session_id, kind, self.now, self.now))

    def test_physical_start_at_expired_manual_gap_finishes_once(self):
        for seconds in (59, 60, 61):
            with self.subTest(seconds=seconds):
                self.now = datetime(2026, 9, 20, 12, tzinfo=UTC)
                configuration = Configuration(
                    Bindings(bindings()),
                    Parameters({"session_gap_minutes": 1}),
                    control_mode="manual",
                    button_temperature_c=90,
                )
                self.runtime = SaunaRuntime(configuration, lambda: self.now)
                asyncio.run(self.runtime.set_operation(True))
                old_id = self.runtime.session.session_id
                asyncio.run(self.runtime.set_operation(False))
                self._event("short", seconds)
                self.assertTrue(self.runtime.session.operation_enabled)
                self.assertNotEqual(self.runtime.session.session_id, old_id)
                self.assertEqual(self.runtime.controller.control_mode, "automatic")
                self.assertEqual(
                    [session.session_id for session in self.runtime.controller.completed_sessions],
                    [old_id],
                )

    def test_physical_program_start_archives_old_manual_configuration(self):
        configuration = Configuration(
            Bindings(bindings()),
            Parameters({"target_temperature_c": 75}),
            control_mode="manual",
            button_temperature_c=90,
        )
        self.runtime = SaunaRuntime(configuration, lambda: self.now)
        self.runtime.archive = Mock()
        asyncio.run(self.runtime.set_operation(True))
        old_id = self.runtime.session.session_id
        asyncio.run(self.runtime.set_operation(False))
        self._event("short", 5)
        saved_old = [
            call.args[2]
            for call in self.runtime.archive.save_session.call_args_list
            if call.args[0].session_id == old_id
        ]
        self.assertTrue(saved_old)
        self.assertEqual(saved_old[-1]["control_mode"], "manual")
        self.assertEqual(saved_old[-1]["parameters"]["target_temperature_c"], 75)
        self.assertEqual(self.runtime.configuration.control_mode, "automatic")
        self.assertEqual(self.runtime.configuration.parameters.values["target_temperature_c"], 90)

    def test_press_release_single_starts_once_and_short_toggles_override(self):
        self._event("press")
        self.assertIsNone(self.runtime.session)
        self.assertFalse(self.runtime.controller.last_decision.heat)
        self._event("release")
        self.assertIsNone(self.runtime.session)
        self._event("short")
        session_id = self.runtime.session.session_id
        self.assertIsNone(self.runtime.controller.heater_override)
        self._event("short")
        self._event("long")
        self._event("release")
        self.assertEqual(self.runtime.session.session_id, session_id)
        self.assertIsNone(self.runtime.controller.heater_override)
        self.runtime.controller.set_temperature(80, self.now)
        self._event("press")
        self._event("release")
        self._event("short")
        self.assertTrue(self.runtime.controller.heater_override)
        self._event("press")
        self._event("release")
        self._event("short")
        self.assertIsNone(self.runtime.controller.heater_override)

    def test_native_double_starts_at_summary_then_toggles_the_second_press(self):
        self.runtime.controller.set_temperature(80, self.now)
        self._event("press")
        self.assertIsNone(self.runtime.session)
        self._event("release")
        self._event("press")
        self._event("release")
        self.assertIsNone(self.runtime.session)
        self._event("double")
        session_id = self.runtime.session.session_id
        self.assertTrue(self.runtime.controller.heater_override)
        self._event("double")
        self._event("short")
        self._event("long")
        self._event("release")
        self.assertEqual(self.runtime.session.session_id, session_id)
        self.assertTrue(self.runtime.controller.heater_override)
        self.assertEqual(len(self.runtime.controller.completed_sessions), 0)
        self.assertIsNone(self.runtime.controller.light_after_run)

    def test_event_only_triple_applies_each_short_press(self):
        self._event("short")
        self.runtime.controller.set_temperature(80, self.now)
        self._event("triple")
        self.assertTrue(self.runtime.session.operation_enabled)
        self.assertTrue(self.runtime.controller.heater_override)

    def test_native_short_in_running_manual_mode_keeps_direct_heater_selection(self):
        self.runtime = SaunaRuntime(
            Configuration(Bindings(bindings()), Parameters({}), control_mode="manual"),
            lambda: self.now,
        )
        self.runtime.controller.set_temperature(70, self.now)
        asyncio.run(self.runtime.set_operation(True))
        session_id = self.runtime.session.session_id
        for selection in (True, False):
            self._event("press", 1)
            self._event("release")
            self._event("short")
            self.assertEqual(self.runtime.controller.heater_override, selection)
            self.assertEqual(self.runtime.controller.last_decision.heat, selection)
            self.assertEqual(self.runtime.controller.control_mode, "manual")
            self.assertEqual(self.runtime.session.session_id, session_id)
            self.runtime.controller.report_contactor(selection, self.now)

    def test_long_release_finishes_once_and_starts_light_after_run_on_release(self):
        self._event("short")
        session_id = self.runtime.session.session_id
        self._event("press")
        self._event("long", 2)
        self.assertIsNone(self.runtime.session)
        self.assertIsNone(self.runtime.controller.light_after_run)
        self._event("release")
        self.assertEqual(self.runtime.controller.light_after_run.session_id, session_id)
        self._event("release")
        self.assertEqual(len(self.runtime.controller.completed_sessions), 1)

    def test_native_off_long_never_starts_and_next_short_starts(self):
        self.runtime.controller.set_temperature(70, self.now)
        for event, seconds in (("press", 0), ("long", 2), ("release", 1), ("short", 0)):
            self._event(event, seconds)
            self.assertIsNone(self.runtime.session)
            self.assertFalse(self.runtime.controller.last_decision.heat)
            self.assertIsNone(self.runtime.controller.heater_override)
            self.assertIsNone(self.runtime.controller.light_after_run)
            self.assertEqual(len(self.runtime.controller.completed_sessions), 0)
        self._event("press", 1)
        self._event("release")
        self._event("short")
        self.assertTrue(self.runtime.session.operation_enabled)
        self.assertTrue(self.runtime.controller.last_decision.heat)
        self.assertIsNone(self.runtime.controller.heater_override)

    def test_sparse_off_long_never_starts_but_running_long_releases_heating(self):
        button = self.runtime._button
        self.runtime.controller.set_temperature(70, self.now)
        self._event("long")
        self.assertIsNone(self.runtime.session)
        self.assertFalse(self.runtime.controller.last_decision.heat)
        self._event("short", 1)
        session_id = self.runtime.session.session_id
        self.assertTrue(self.runtime.controller.last_decision.heat)

        self._event("long", 10)

        self.assertIs(self.runtime._button, button)
        self.assertIsNone(self.runtime.session)
        self.assertFalse(self.runtime.controller.last_decision.heat)
        self.assertEqual(len(self.runtime.controller.completed_sessions), 1)
        self.assertIsNone(self.runtime.controller.light_after_run)
        self._event("release", 1)
        self.assertEqual(self.runtime.controller.light_after_run.session_id, session_id)

    def test_binary_off_start_requires_short_received_duration(self):
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
                self.assertEqual(self.runtime.session is not None, duration == 1)
                self.assertEqual(
                    self.runtime.controller.last_decision.heat, duration == 1
                )
                self.assertIsNone(self.runtime.controller.heater_override)
                self.assertIsNone(self.runtime.controller.light_after_run)

    def test_delayed_binary_release_finishes_and_starts_light_after_run(self):
        self._event("short")
        session_id = self.runtime.session.session_id
        self._event("on")
        self.now += timedelta(seconds=2)
        asyncio.run(self._handle("off"))
        self.assertIsNone(self.runtime.session)
        self.assertEqual(self.runtime.controller.light_after_run.session_id, session_id)

    def test_new_start_makes_an_old_release_harmless(self):
        self._event("short")
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

        self._event("short", 1)
        self.assertTrue(controller.heater_override)
        self.assertIsNone(controller.session.after_run.paused_at)
        self.assertFalse(controller.last_decision.heat)

        self._event("short", 1)
        self.assertIsNone(controller.heater_override)
        self.assertIsNone(controller.session.after_run.paused_at)
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
            ),
            lambda: self.now,
        )
        started_at = self._start_with_temperature()
        controller = self.runtime.controller
        session_id = controller.session.session_id
        controller.report_heating(True, started_at)
        for name, kind, seconds in (
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

    def test_button_parameters_have_the_decided_defaults(self):
        values = Parameters({"button_hold_brightness_percent": 1}).values
        self.assertEqual(values["button_hold_seconds"], 2)
        self.assertNotIn("button_hold_brightness_percent", values)

    def test_button_start_uses_its_frozen_constant_temperature(self):
        self.runtime = SaunaRuntime(
            Configuration(
                Bindings(bindings()),
                Parameters({"target_temperature_c": 86}),
                button_temperature_c=74,
            ),
            lambda: self.now,
        )

        self._event("short")

        self.assertEqual(self.runtime.controller.target_temperature, 74)
        self.assertEqual(self.runtime.configuration.button_temperature_c, 74)

    def test_button_start_uses_the_selected_named_program(self):
        self.runtime = SaunaRuntime(
            Configuration(
                Bindings(bindings()),
                Parameters({"target_temperature_c": 70}),
                button_program="gipfelstuermer",
            ),
            lambda: self.now,
        )

        self._event("short")

        self.assertEqual(self.runtime.controller.program_mode, "progressive")
        self.assertEqual(self.runtime.controller.target_temperature, 84)


if __name__ == "__main__":
    unittest.main()
