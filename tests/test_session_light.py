"""Lichtnachlauf beginnt einmalig am Sitzungsende, ohne Heizwirkung."""
from datetime import timedelta
import unittest

from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.display import phase_timer
from custom_components.ha_sauna.core.parameters import Parameters
from test_foundation import T0


class SessionLightTests(unittest.TestCase):
    def test_default_deadline_and_new_session_cancel_old_light_timer(self):
        c = Controller(Parameters({"session_gap_minutes": 1}))
        c.set_temperature(70, T0)
        c.set_operation(True, T0)
        c.set_operation(False, T0 + timedelta(seconds=1))
        c.advance(T0 + timedelta(seconds=60))
        self.assertIsNone(c.light_after_run)
        c.advance(T0 + timedelta(seconds=80))  # verspäteter Tick verlängert nicht
        light = c.light_after_run
        self.assertEqual(light.started_at, T0 + timedelta(seconds=61))
        self.assertEqual(light.ends_at, T0 + timedelta(seconds=661))
        self.assertEqual(light.brightness_percent, 50)
        self.assertIsNone(c.session)
        self.assertFalse(c.last_decision.heat)
        self.assertEqual(phase_timer(c, T0 + timedelta(seconds=80))["seconds"], 581)
        c.advance(T0 + timedelta(seconds=90))
        self.assertIs(c.light_after_run, light)
        c.set_operation(True, T0 + timedelta(seconds=100))
        self.assertIsNone(c.light_after_run)
        self.assertNotEqual(phase_timer(c, T0 + timedelta(seconds=662))["kind"], "session_light")

    def test_custom_duration_brightness_and_zero_duration(self):
        for minutes in (0, 3):
            with self.subTest(minutes=minutes):
                c = Controller(Parameters({"session_gap_minutes": 1,
                    "session_light_minutes": minutes, "session_light_brightness_percent": 64}))
                c.set_operation(True, T0)
                c.set_operation(False, T0)
                c.advance(T0 + timedelta(seconds=60))
                self.assertEqual(c.light_after_run.brightness_percent, 64)
                self.assertEqual(c.light_after_run.ends_at, T0 + timedelta(seconds=60 + minutes*60))
                self.assertIsNone(phase_timer(c, c.light_after_run.ends_at))
                self.assertFalse(c.last_decision.heat)

