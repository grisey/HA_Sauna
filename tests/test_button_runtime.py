"""Runtime wiring for the HA-free sauna-button gesture model."""

import asyncio
import unittest
from datetime import UTC, datetime, timedelta

from custom_components.ha_sauna.bindings import ROLES, Bindings
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.timeline import Event, Kind
from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime


def bindings():
    return {
        role.key: f"{role.domains[0]}.test_{role.key}"
        for role in ROLES
        if not role.optional
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

    def test_press_release_single_starts_once_and_short_toggles_override(self):
        self._event("press")
        session_id = self.runtime.session.session_id
        self._event("release")
        self._event("short")
        self.assertEqual(self.runtime.session.session_id, session_id)
        self.runtime.controller.set_temperature(80, self.now)
        self._event("press")
        self._event("release")
        self._event("short")
        self.assertTrue(self.runtime.controller.heater_override)
        self._event("press")
        self._event("release")
        self._event("short")
        self.assertIsNone(self.runtime.controller.heater_override)

    def test_native_double_after_start_only_toggles_the_second_press(self):
        self._event("press")
        session_id = self.runtime.session.session_id
        self.runtime.controller.set_temperature(80, self.now)
        self._event("release")
        self._event("press")
        self._event("release")
        self._event("double")
        self.assertEqual(self.runtime.session.session_id, session_id)
        self.assertTrue(self.runtime.controller.heater_override)

    def test_event_only_triple_applies_each_short_press(self):
        self._event("short")
        self.runtime.controller.set_temperature(80, self.now)
        self._event("triple")
        self.assertTrue(self.runtime.session.operation_enabled)
        self.assertTrue(self.runtime.controller.heater_override)

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

    def test_delayed_on_feedback_first_restarts_after_run_then_returns_to_auto(self):
        started_at = self._start_with_temperature()
        controller = self.runtime.controller
        controller.report_contactor(True, started_at + timedelta(seconds=1))
        self._complete_gang(started_at)

        self._event("short", 1)
        self.assertTrue(controller.heater_override)
        self.assertIsNotNone(controller.session.after_run.paused_at)
        self.assertTrue(controller.last_decision.heat)

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
        values = Parameters({}).values
        self.assertEqual(values["button_hold_seconds"], 2)
        self.assertEqual(values["button_hold_brightness_percent"], 1)

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
