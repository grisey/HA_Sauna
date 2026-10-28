"""Lichtplanung mit den konsumierten Laufzeiten und Helligkeiten des Katalogs."""
import unittest
from dataclasses import FrozenInstanceError, astuple

from custom_components.ha_sauna.core.light_output import LightOutput
from custom_components.ha_sauna.core.parameters import Parameters


class LightOutputTests(unittest.TestCase):
    def setUp(self):
        self.parameters = Parameters({})
        self.light = LightOutput(self.parameters)
        self.fade = self.parameters.values["light_transition_seconds"]
        self.low = self.parameters.values["after_run_brightness_percent"]
        self.session_light = self.parameters.values["session_light_brightness_percent"]
        self.end = max(self.parameters.seconds("after_run_minutes"), 4 * self.fade)

    def update(self, now, key="normal", phase="aufheizen", target=40, actual=40, end=None, **kwargs):
        return self.light.update(now, key, phase, target, actual, end, **kwargs)

    def test_after_run_fades_for_the_configured_transition(self):
        self.assertEqual(self.update(0).brightness_percent, 40)
        self.assertEqual(self.update(0, "run", "nachlauf", 40, 40, self.end).brightness_percent, 40)
        self.assertEqual(self.update(self.fade / 2, "run", "nachlauf", 40, 40, self.end).brightness_percent,
                         (40 + self.low) / 2)
        self.assertEqual(self.update(self.fade, "run", "nachlauf", 40, 40, self.end).brightness_percent,
                         self.low)

    def test_session_end_stays_off_while_light_feedback_is_delayed(self):
        self.update(0, "session-light", "session_light", 50, 50, self.end)
        self.update(self.end - 1, "session-light", "session_light", 50, 50, self.end)
        plan = self.update(self.end, "idle", "aus", 0, 50)
        self.assertEqual(plan.brightness_percent, 0)
        self.assertEqual(self.light.last_automatic_brightness, 0)
        self.assertEqual(self.update(self.end + self.fade, "idle", "aus", 0, 50).brightness_percent, 0)

    def test_after_run_rises_to_temperature_target_at_deadline(self):
        self.update(0, "run", "nachlauf", 40, 40, self.end)
        midpoint = (self.fade + self.end) / 2
        self.assertEqual(self.update(midpoint, "run", "nachlauf", 40, self.low, self.end).brightness_percent,
                         (self.low + 40) / 2)
        self.assertEqual(self.update(self.end, "run", "nachlauf", 40, self.low, self.end).brightness_percent, 40)

    def test_joint_session_light_and_manual_expiry_keeps_automatic_end_off(self):
        self.update(0, "heat", target=21)
        self.update(1, "session", "session_light", 0, 21, self.end)
        self.light.set_manual(80, phase_key="session", ends_at=self.end)
        self.assertEqual(self.update(self.end - 1, "session", "session_light", 0, 80, self.end).brightness_percent, 80)
        self.assertTrue(self.light.expire_manual(self.end))
        for second in (self.end, self.end + 1, self.end + self.fade / 2, self.end + self.fade):
            with self.subTest(second=second):
                plan = self.update(second, "idle", "aus", 0, 0)
                self.assertEqual(plan.brightness_percent, 0)
                self.assertTrue(plan.automatic)
                self.assertEqual(self.light.last_automatic_brightness, 0)

    def test_paused_after_run_holds_its_current_value_and_resumes_from_it(self):
        self.update(0, "run", "nachlauf", 40, 40, self.end)
        pause_at = (self.fade + self.end) / 2
        before = self.update(pause_at, "run", "nachlauf", 40, self.low, self.end).brightness_percent
        paused = self.update(pause_at, "run", "nachlauf", 40, before, None, phase_paused=True)
        self.assertAlmostEqual(paused.brightness_percent, before)
        resume_at = pause_at + self.fade
        self.assertAlmostEqual(self.update(resume_at, "run", "nachlauf", 90, before, None,
                                           phase_paused=True).brightness_percent, before)
        resumed_end = self.end + self.fade
        resumed = self.update(resume_at, "run", "nachlauf", 90, before, resumed_end)
        self.assertAlmostEqual(resumed.brightness_percent, before)
        middle = self.update((resume_at + resumed_end) / 2, "run", "nachlauf", 90, before, resumed_end)
        self.assertAlmostEqual(middle.brightness_percent, (before + 90) / 2)

    def test_session_light_uses_its_snapshot_after_parameter_reload(self):
        # Snapshot and later selection are explicit user brightness inputs.
        self.assertEqual(self.update(0, "session", "session_light", 0, 20, self.end,
                                     phase_brightness_percent=64).brightness_percent, 20)
        self.light.parameters = Parameters({"session_light_brightness_percent": 20})
        self.assertEqual(self.update(self.fade, "session", "session_light", 0, 20, self.end,
                                     phase_brightness_percent=64).brightness_percent, 64)

    def test_new_phase_and_deadline_begin_from_observed_value(self):
        self.update(0, "run-a", "nachlauf", 40, 60, self.end)
        changed_at = self.fade / 2
        observed = (60 + self.low) / 2
        self.assertEqual(self.update(changed_at, "run-a", "nachlauf", 40, 60, self.end).brightness_percent, observed)
        end = self.end + changed_at
        self.assertEqual(self.update(changed_at, "run-b", "nachlauf", 40, observed, end).brightness_percent, observed)
        self.assertEqual(self.update(changed_at + self.fade, "run-b", "nachlauf", 40, observed, end).brightness_percent,
                         self.low)

    def test_manual_override_holds_until_phase_key_changes(self):
        self.update(0, "heat", target=40)
        self.light.set_manual(73, ends_at=self.end, at=0, session_id="session-a")
        self.assertEqual(self.update(1, "heat", target=20).brightness_percent, 73)
        plan = self.update(2, "ready", "bereit", 40, 73)
        self.assertEqual((plan.brightness_percent, plan.automatic, plan.manual_override), (73, True, False))
        self.assertEqual(self.update(2 + self.fade, "ready", "bereit", 40, 73).brightness_percent, 40)
        changes = self.light.drain_manual_changes()
        self.assertEqual(tuple(astuple(change) for change in changes), (
            (0, "selection", None, 73, None, self.end, None, "heat", None, "session-a"),
            (2, "phase_change", 73, None, self.end, None, "heat", None, "session-a", None),
        ))
        self.assertEqual(self.light.drain_manual_changes(), ())

        with self.subTest(planner_phase_changed=False):
            self.light = LightOutput(self.parameters)
            self.update(0, "heat", target=40)
            self.light.set_manual(73, phase_key="unobserved", ends_at=self.end, at=0,
                                  session_id="session-a")
            plan = self.update(2, "heat", target=40, actual=73)
            self.assertEqual((plan.brightness_percent, plan.automatic, plan.manual_override), (40, True, False))
            changes = self.light.drain_manual_changes()
            self.assertEqual(tuple(astuple(change) for change in changes), (
                (0, "selection", None, 73, None, self.end, None, "unobserved", None, "session-a"),
                (2, "phase_change", 73, None, self.end, None, "unobserved", None, "session-a", None),
            ))
            self.assertEqual(self.light.drain_manual_changes(), ())

    def test_explicit_manual_override_expires_without_a_phase_change(self):
        expiry = self.parameters.seconds("manual_override_minutes")
        for expired_at in (expiry, expiry + 1):
            with self.subTest(expired_at=expired_at):
                self.light = LightOutput(self.parameters)
                self.update(0, "heat", target=40)
                self.light.set_manual(73, phase_key="heat", ends_at=expiry, at=0,
                                      session_id="session-a")
                self.assertFalse(self.light.expire_manual(expiry - 1))
                self.assertEqual(self.update(expiry - 1, "heat", target=20).brightness_percent, 73)
                self.assertTrue(self.light.expire_manual(expired_at))
                self.assertIsNone(self.light.manual_brightness)
                self.assertIsNone(self.light.manual_ends_at)
                self.assertFalse(self.light.expire_manual(expired_at))
                self.assertEqual(self.update(expired_at, "heat", target=20, actual=73).brightness_percent, 73)
                self.assertEqual(self.update(expired_at + self.fade, "heat", target=20).brightness_percent, 20)
                changes = self.light.drain_manual_changes()
                self.assertEqual(tuple(astuple(change) for change in changes), (
                    (0, "selection", None, 73, None, expiry, None, "heat", None, "session-a"),
                    (expired_at, "timeout", 73, None, expiry, None, "heat", None, "session-a", None),
                ))
                self.assertEqual(self.light.drain_manual_changes(), ())

    def test_return_to_automatic_starts_from_observed_not_requested_value(self):
        self.update(0, target=40)
        self.light.set_manual(70, ends_at=self.end, at=0, session_id="session-a")
        self.update(1, target=40)
        self.light.keep_manual(at=1)
        self.assertEqual(self.light.manual_brightness, 70)
        self.assertIsNone(self.light.manual_ends_at)
        self.light.keep_manual(at=1)
        self.light.return_to_automatic(at=2)
        self.light.return_to_automatic(at=2)
        changes = self.light.drain_manual_changes()
        self.assertEqual(tuple(astuple(change) for change in changes), (
            (0, "selection", None, 70, None, self.end, None, "normal", None, "session-a"),
            (1, "keep", 70, 70, self.end, None, "normal", "normal", "session-a", "session-a"),
            (2, "explicit", 70, None, None, None, "normal", None, "session-a", None),
        ))
        self.assertEqual(self.light.drain_manual_changes(), ())
        self.assertEqual(self.update(2, target=20, actual=68).brightness_percent, 68)
        self.assertEqual(self.update(2 + self.fade / 2, target=20, actual=68).brightness_percent, 44)
        self.assertEqual(self.update(2 + self.fade, target=20).brightness_percent, 20)

    def test_return_to_automatic_keeps_following_a_changed_target(self):
        self.update(0, target=40)
        self.light.set_manual(70)
        self.update(1, target=40)
        self.light.return_to_automatic()
        self.assertEqual(self.update(2, target=20, actual=70).brightness_percent, 70)
        self.assertEqual(self.update(2 + self.fade / 2, target=30).brightness_percent, 50)
        self.assertEqual(self.update(2 + self.fade, target=30).brightness_percent, 30)

    def test_return_to_automatic_keeps_live_session_light(self):
        self.update(0, "session", "session_light", 0, 20, self.end)
        self.update(self.fade, "session", "session_light", 0, self.session_light, self.end)
        self.light.set_manual(80, phase_key="session")
        self.assertEqual(self.update(self.fade + 1, "session", "session_light", 0, 80, self.end).brightness_percent, 80)
        self.light.return_to_automatic()
        start = self.fade + 2
        for second, expected in ((start, 80), (start + self.fade / 2, (80 + self.session_light) / 2),
                                 (start + self.fade, self.session_light), (self.end - 1, self.session_light), (self.end, 0)):
            with self.subTest(second=second):
                self.assertEqual(self.update(second, "session", "session_light", 0, 80, self.end).brightness_percent, expected)

    def test_expired_override_keeps_live_cooling_curve(self):
        self.update(0, "cooling", "nachlauf", 40, 40, self.end)
        expiry = self.fade + (self.end - self.fade) / 3
        self.light.set_manual(80, phase_key="cooling", ends_at=expiry)
        self.update(expiry - 1, "cooling", "nachlauf", 40, 80, self.end)
        self.assertTrue(self.light.expire_manual(expiry))
        self.assertEqual(self.update(expiry, "cooling", "nachlauf", 40, 80, self.end).brightness_percent, 80)
        for second in (expiry + self.fade / 2, expiry + self.fade, self.end):
            # Independent linear phase contract; no second product planner as oracle.
            automatic = self.low + (40 - self.low) * (second - self.fade) / (self.end - self.fade)
            expected = (80 + automatic) / 2 if second == expiry + self.fade / 2 else automatic
            self.assertAlmostEqual(self.update(second, "cooling", "nachlauf", 40, 80, self.end).brightness_percent, expected)

    def test_return_transition_is_bounded_by_the_existing_cooling_deadline(self):
        self.update(0, "cooling", "nachlauf", 40, 40, self.end)
        self.light.set_manual(80, phase_key="cooling")
        self.light.return_to_automatic()
        self.assertEqual(self.update(self.end - self.fade / 3, "cooling", "nachlauf", 40, 80, self.end).brightness_percent, 80)
        self.assertEqual(self.update(self.end, "cooling", "nachlauf", 40, 80, self.end).brightness_percent, 40)

    def test_due_off_interrupts_return_transition(self):
        self.update(0, "session", "session_light", 0, 50, self.end)
        self.light.set_manual(80, phase_key="session")
        self.light.return_to_automatic()
        remaining = self.fade / 3
        self.assertEqual(self.update(self.end - remaining, "session", "session_light", 0, 80, self.end).brightness_percent, 80)
        self.assertEqual(self.update(self.end - remaining / 2, "session", "session_light", 0, 80, self.end).brightness_percent,
                         (80 + self.session_light) / 2)
        self.assertEqual(self.update(self.end, "session", "session_light", 0, 80, self.end).brightness_percent, 0)

    def test_new_manual_selection_interrupts_return_immediately(self):
        self.update(0)
        self.light.set_manual(80, session_id="session-a")
        self.light.return_to_automatic()
        self.update(1, actual=80)
        self.light.set_manual(19, at=2, session_id="session-b")
        self.light.set_manual(19, at=2, session_id="session-b")
        self.assertEqual(self.update(2, actual=80).brightness_percent, 19)
        changes = self.light.drain_manual_changes()
        self.assertIsInstance(changes, tuple)
        self.assertEqual(tuple(astuple(change) for change in changes), (
            (None, "selection", None, 80, None, None, None, "normal", None, "session-a"),
            (None, "explicit", 80, None, None, None, "normal", None, "session-a", None),
            (2, "selection", None, 19, None, None, None, "normal", None, "session-b"),
            (2, "selection", 19, 19, None, None, "normal", "normal", "session-b", "session-b"),
        ))
        with self.assertRaises(FrozenInstanceError):
            changes[0].value = 0
        self.assertEqual(self.light.drain_manual_changes(), ())

    def test_normal_phase_change_fades_from_the_observed_manual_value(self):
        self.update(0, "heat", target=40)
        self.light.set_manual(50)
        self.assertEqual(self.update(1, "heat", target=40).brightness_percent, 50)
        self.assertEqual(self.update(2, "ready", "bereit", 40, 50).brightness_percent, 50)
        self.assertEqual(self.update(2 + self.fade / 2, "ready", "bereit", 30, 50).brightness_percent, 40)
        self.assertEqual(self.update(2 + self.fade, "ready", "bereit", 30, 50).brightness_percent, 30)

    def test_new_manual_command_survives_a_stale_planner_phase(self):
        self.update(0, "heat", target=40)
        self.light.set_manual(67, phase_key="ready")
        plan = self.update(1, "ready", "bereit", 30, 40)
        self.assertEqual((plan.brightness_percent, plan.automatic, plan.manual_override), (67, False, True))
        plan = self.update(2, "later", "bereit", 30, 67)
        self.assertEqual((plan.automatic, plan.manual_override), (True, False))

    def test_expired_session_light_allows_manual_light_outside_the_session(self):
        self.update(0, "session", "session_light", 0, 20, self.end)
        self.update(self.end - 1, "session", "session_light", 0, 50, self.end)
        self.light.set_manual(50, phase_key="aus", at=self.end, session_id="session-a")
        plan = self.update(self.end + 1, "aus", "aus", 0, 0)
        self.assertEqual((plan.brightness_percent, plan.automatic, plan.manual_override), (50, False, True))
        self.light.set_manual(35, phase_key="aus", at=self.end + 2, session_id="session-b")
        self.assertEqual(self.update(self.end + 2, "aus", "aus", 0, 50).brightness_percent, 35)
        self.light.set_manual(0, phase_key="aus", at=self.end + 3, session_id="session-b")
        self.assertEqual(self.update(self.end + 3, "aus", "aus", 0, 35).brightness_percent, 0)
        changes = self.light.drain_manual_changes()
        self.assertEqual(tuple(astuple(change) for change in changes), (
            (self.end, "selection", None, 50, None, None, None, "aus", None, "session-a"),
            (self.end + 2, "selection", 50, 35, None, None, "aus", "aus", "session-a", "session-b"),
            (self.end + 3, "selection", 35, 0, None, None, "aus", "aus", "session-b", "session-b"),
        ))
        self.assertEqual(self.light.drain_manual_changes(), ())

    def test_manual_choice_in_unobserved_hold_phase_ends_at_release(self):
        self.update(0, "heat", target=40)
        self.light.set_manual(80, phase_key="aus")
        plan = self.update(1, "released", "session_light", 0, 0, self.end)
        self.assertTrue(plan.automatic)
        self.assertIsNone(self.light.manual_brightness)
        self.assertEqual(self.update(1 + self.fade, "released", "session_light", 0, 0, self.end).brightness_percent,
                         self.session_light)

    def test_finished_automatic_output_returns_manual_light_to_off(self):
        self.update(0, "session", "session_light", 0, 20, self.end)
        self.update(self.fade, "session", "session_light", 0, 20, self.end)
        self.light.set_manual(37, phase_key="aus")
        self.light.finish_automatic()
        self.assertEqual(self.update(self.end + 1, "aus", "aus", 0, 50).brightness_percent, 37)
        self.light.return_to_automatic()
        self.assertEqual(self.update(self.end + 2, "aus", "aus", 0, 37).brightness_percent, 0)

    def test_session_light_switches_off_immediately_at_end(self):
        self.assertEqual(self.update(0, "session", "session_light", 0, 20, self.end).brightness_percent, 20)
        self.assertEqual(self.update(self.fade, "session", "session_light", 0, 20, self.end).brightness_percent, self.session_light)
        self.assertEqual(self.update(self.end - 1, "session", "session_light", 0, self.session_light, self.end).brightness_percent,
                         self.session_light)
        self.assertEqual(self.update(self.end, "session", "session_light", 0, self.session_light, self.end).brightness_percent, 0)


if __name__ == "__main__":
    unittest.main()
