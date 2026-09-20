"""Runtime wiring for the HA-free sauna-button gesture model."""

import asyncio
import unittest
from datetime import UTC, datetime, timedelta

from custom_components.ha_sauna.bindings import ROLES, Bindings
from custom_components.ha_sauna.core.parameters import Parameters
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

    async def _handle(self, name):
        async with self.runtime._lock:
            await self.runtime._handle_button_event(name, self.now)
            await self.runtime._cycle()

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

    def test_button_parameters_have_the_decided_defaults(self):
        values = Parameters({}).values
        self.assertEqual(values["button_hold_seconds"], 2)
        self.assertEqual(values["button_hold_brightness_percent"], 1)


if __name__ == "__main__":
    unittest.main()
