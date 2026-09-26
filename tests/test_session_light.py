"""Lichtnachlauf nutzt dieselbe Frist wie die pausierte Saunasitzung."""
from datetime import timedelta
import unittest

from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.display import phase_timer
from custom_components.ha_sauna.core.parameters import Parameters
from test_foundation import T0


class SessionLightTests(unittest.TestCase):
    def test_operation_off_starts_light_with_the_shared_session_deadline(self):
        controller = Controller(Parameters({"session_gap_minutes": 1}))
        controller.set_temperature(70, T0)
        controller.set_operation(True, T0)
        controller.set_operation(False, T0 + timedelta(seconds=1))

        light = controller.light_after_run
        self.assertEqual(light.started_at, T0 + timedelta(seconds=1))
        self.assertEqual(light.ends_at, T0 + timedelta(seconds=61))
        self.assertEqual(light.brightness_percent, 50)
        gap = next(
            deadline
            for deadline in controller.session.deadlines
            if deadline.purpose == "session_gap"
        )
        self.assertEqual(gap.due_at, light.ends_at)

        controller.advance(T0 + timedelta(seconds=60))
        self.assertIs(controller.light_after_run, light)
        self.assertIsNotNone(controller.session)
        controller.advance(T0 + timedelta(seconds=61))
        self.assertIsNone(controller.session)
        self.assertIs(controller.light_after_run, light)
        self.assertFalse(controller.last_decision.heat)
        self.assertIsNone(phase_timer(controller, T0 + timedelta(seconds=61)))

    def test_resuming_the_same_session_cancels_light_and_gap(self):
        controller = Controller(Parameters({"session_gap_minutes": 1}))
        controller.set_operation(True, T0)
        controller.set_operation(False, T0 + timedelta(seconds=1))
        controller.set_operation(True, T0 + timedelta(seconds=30))

        self.assertIsNone(controller.light_after_run)
        self.assertFalse(
            any(
                deadline.purpose == "session_gap"
                for deadline in controller.session.deadlines
            )
        )

    def test_light_uses_the_session_gap_and_configured_brightness(self):
        controller = Controller(
            Parameters(
                {
                    "session_gap_minutes": 3,
                    "session_light_brightness_percent": 64,
                }
            )
        )
        controller.set_operation(True, T0)
        controller.set_operation(False, T0)

        self.assertEqual(controller.light_after_run.brightness_percent, 64)
        self.assertEqual(
            controller.light_after_run.ends_at, T0 + timedelta(minutes=3)
        )
        controller.advance(controller.light_after_run.ends_at)
        self.assertIsNone(phase_timer(controller, controller.light_after_run.ends_at))
        self.assertFalse(controller.last_decision.heat)
