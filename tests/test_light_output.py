"""Reine Tests fuer die Licht-Ausgabeplanung ohne Home-Assistant-Imports."""
import unittest

from custom_components.ha_sauna.core.light_output import LightOutput
from custom_components.ha_sauna.core.parameters import Parameters


class LightOutputTests(unittest.TestCase):
    def setUp(self):
        self.light = LightOutput(Parameters({"light_transition_seconds": 30,
            "after_run_brightness_percent": 15, "cooling_brightness_percent": 5,
            "session_light_brightness_percent": 50}))

    def update(self, now, key="normal", phase="aufheizen", target=40, actual=40, end=None, **kwargs):
        return self.light.update(now, key, phase, target, actual, end, **kwargs)

    def test_after_run_fades_for_30_seconds(self):
        self.assertEqual(self.update(0).brightness_percent, 40)
        self.assertEqual(self.update(0, "run", "nachlauf", 40, 40, 120).brightness_percent, 40)
        self.assertEqual(self.update(15, "run", "nachlauf", 40, 40, 120).brightness_percent, 27.5)
        self.assertEqual(self.update(30, "run", "nachlauf", 40, 40, 120).brightness_percent, 15)

    def test_session_end_stays_off_while_light_feedback_is_delayed(self):
        self.update(0, "session-light", "session_light", 50, 50, 600)
        self.update(590, "session-light", "session_light", 50, 50, 600)
        plan = self.update(600, "idle", "aus", 0, 50)
        self.assertEqual(plan.brightness_percent, 0)
        self.assertEqual(self.light.last_automatic_brightness, 0)
        self.assertEqual(self.update(610, "idle", "aus", 0, 50).brightness_percent, 0)

    def test_after_run_rises_to_temperature_target_at_deadline(self):
        self.update(0, "run", "nachlauf", 40, 40, 120)
        self.assertEqual(self.update(75, "run", "nachlauf", 40, 15, 120).brightness_percent, 27.5)
        self.assertEqual(self.update(120, "run", "nachlauf", 40, 15, 120).brightness_percent, 40)

    def test_paused_after_run_holds_its_current_value_and_resumes_from_it(self):
        self.update(0, "run", "nachlauf", 40, 40, 120)
        before_pause = self.update(45, "run", "nachlauf", 40, 15, 120).brightness_percent
        paused = self.update(45, "run", "nachlauf", 40, before_pause, None,
                             phase_paused=True)
        self.assertAlmostEqual(paused.brightness_percent, before_pause)
        self.assertAlmostEqual(self.update(100, "run", "nachlauf", 90, before_pause, None,
                                           phase_paused=True).brightness_percent, before_pause)
        resumed = self.update(100, "run", "nachlauf", 90, before_pause, 175)
        self.assertAlmostEqual(resumed.brightness_percent, before_pause)
        self.assertGreater(self.update(137.5, "run", "nachlauf", 90, before_pause, 175).brightness_percent,
                           before_pause)
        self.assertLess(self.update(137.5, "run", "nachlauf", 90, before_pause, 175).brightness_percent, 90)

    def test_session_light_uses_its_snapshot_after_parameter_reload(self):
        self.assertEqual(self.update(0, "session", "session_light", 0, 20, 120,
                                     phase_brightness_percent=64).brightness_percent, 20)
        self.light.parameters = Parameters({"light_transition_seconds": 30,
            "after_run_brightness_percent": 15, "cooling_brightness_percent": 5,
            "session_light_brightness_percent": 20})
        self.assertEqual(self.update(30, "session", "session_light", 0, 20, 120,
                                     phase_brightness_percent=64).brightness_percent, 64)

    def test_new_phase_and_deadline_begin_from_observed_value(self):
        self.update(0, "run-a", "nachlauf", 40, 60, 120)
        self.assertEqual(self.update(20, "run-a", "nachlauf", 40, 60, 120).brightness_percent, 30)
        plan = self.update(20, "run-b", "nachlauf", 40, 30, 140)
        self.assertEqual(plan.brightness_percent, 30)
        self.assertEqual(self.update(50, "run-b", "nachlauf", 40, 30, 140).brightness_percent, 15)

    def test_manual_override_holds_until_phase_key_changes(self):
        self.update(0, "heat", target=40)
        self.light.set_manual(73)
        self.assertEqual(self.update(10, "heat", target=20).brightness_percent, 73)
        plan = self.update(11, "ready", "bereit", 40, 73)
        self.assertEqual((plan.brightness_percent, plan.automatic, plan.manual_override), (73, True, False))
        self.assertEqual(self.update(41, "ready", "bereit", 40, 73).brightness_percent, 40)

    def test_explicit_manual_override_expires_without_a_phase_change(self):
        self.update(0, "heat", target=40)
        self.light.set_manual(73, phase_key="heat", ends_at=10)
        self.assertFalse(self.light.expire_manual(9))
        self.assertEqual(self.update(9, "heat", target=20).brightness_percent, 73)
        self.assertTrue(self.light.expire_manual(10))
        self.assertEqual(self.light.manual_brightness, None)
        self.assertEqual(self.light.manual_ends_at, None)
        self.assertEqual(self.update(10, "heat", target=20).brightness_percent, 40)

    def test_return_to_automatic_starts_from_last_automatic_value(self):
        self.update(0, target=40)
        self.light.set_manual(70)
        self.update(1, target=40)
        self.light.return_to_automatic()
        self.assertEqual(self.update(2, target=20).brightness_percent, 40)
        self.assertEqual(self.update(32, target=20).brightness_percent, 20)

    def test_return_to_automatic_keeps_following_a_changed_target(self):
        self.update(0, target=40)
        self.light.set_manual(70)
        self.update(1, target=40)
        self.light.return_to_automatic()
        self.assertEqual(self.update(2, target=20).brightness_percent, 40)
        # Eine spätere Dämmerungs- oder Temperaturänderung darf die Rückkehr
        # nicht an das Ziel beim ersten Tick binden.
        self.assertEqual(self.update(17, target=30).brightness_percent, 35)
        self.assertEqual(self.update(32, target=30).brightness_percent, 30)

    def test_normal_phase_change_fades_from_the_observed_manual_value(self):
        self.update(0, "heat", target=40)
        self.light.set_manual(50)
        self.assertEqual(self.update(1, "heat", target=40).brightness_percent, 50)
        self.assertEqual(self.update(2, "ready", "bereit", 40, 50).brightness_percent, 50)
        self.assertEqual(self.update(17, "ready", "bereit", 30, 50).brightness_percent, 40)
        self.assertEqual(self.update(32, "ready", "bereit", 30, 50).brightness_percent, 30)

    def test_new_manual_command_survives_a_stale_planner_phase(self):
        self.update(0, "heat", target=40)
        # Der Controller ist bereits in ``ready``, nur der Licht-Planner hat
        # den Phasenwechsel noch nicht beobachtet.
        self.light.set_manual(67, phase_key="ready")
        plan = self.update(1, "ready", "bereit", 30, 40)
        self.assertEqual((plan.brightness_percent, plan.automatic, plan.manual_override), (67, False, True))
        # Beim folgenden Wechsel endet auch dieser neue Override.
        plan = self.update(2, "later", "bereit", 30, 67)
        self.assertEqual((plan.automatic, plan.manual_override), (True, False))

    def test_expired_session_light_allows_manual_light_outside_the_session(self):
        self.update(0, "session", "session_light", 0, 20, 120)
        self.update(119, "session", "session_light", 0, 50, 120)
        # Das Timer-OFF ist fällig, der nächste Planner-Tick aber noch nicht
        # gelaufen. Der Adapter übergibt deshalb bereits den Außen-Schlüssel.
        self.light.set_manual(50, phase_key="aus")
        plan = self.update(121, "aus", "aus", 0, 0)
        self.assertEqual((plan.brightness_percent, plan.automatic, plan.manual_override), (50, False, True))
        self.light.set_manual(35, phase_key="aus")  # "normal" aus dem Adapter
        self.assertEqual(self.update(122, "aus", "aus", 0, 50).brightness_percent, 35)
        self.light.set_manual(0, phase_key="aus")
        self.assertEqual(self.update(123, "aus", "aus", 0, 35).brightness_percent, 0)

    def test_finished_automatic_output_returns_manual_light_to_off(self):
        self.update(0, "session", "session_light", 0, 20, 120)
        self.update(30, "session", "session_light", 0, 20, 120)
        self.light.set_manual(37, phase_key="aus")
        self.light.finish_automatic()
        self.assertEqual(self.update(121, "aus", "aus", 0, 50).brightness_percent, 37)
        self.light.return_to_automatic()
        self.assertEqual(self.update(122, "aus", "aus", 0, 37).brightness_percent, 0)

    def test_session_light_switches_off_immediately_at_end(self):
        self.assertEqual(self.update(0, "session", "session_light", 0, 20, 120).brightness_percent, 20)
        self.assertEqual(self.update(30, "session", "session_light", 0, 20, 120).brightness_percent, 50)
        self.assertEqual(self.update(119, "session", "session_light", 0, 50, 120).brightness_percent, 50)
        self.assertEqual(self.update(120, "session", "session_light", 0, 50, 120).brightness_percent, 0)


if __name__ == "__main__":
    unittest.main()
