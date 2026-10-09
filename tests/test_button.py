"""Tests for the HA-independent button gesture helper."""
import unittest
from datetime import UTC, datetime, timedelta

from custom_components.ha_sauna.core.button import (
    END_HOLD,
    END_RELEASE,
    HEATER_TOGGLE_OVERRIDE,
    START_HOLD,
    START_RELEASE,
    START_STANDARD_PROGRAM,
    ButtonGestures,
)


def at(seconds: int) -> datetime:
    return datetime(2026, 9, 20, 12, 0, tzinfo=UTC) + timedelta(seconds=seconds)


THRESHOLD = timedelta(seconds=1)


class ButtonGestureTests(unittest.TestCase):
    def test_selected_native_multi_click_starts_and_ends_once_without_hold(self):
        for selected in ("double", "triple"):
            with self.subTest(selected=selected):
                button = ButtonGestures(THRESHOLD, selected)
                button.handle("press", False, at(0))
                button.handle("release", False, at(.1))
                self.assertEqual(button.handle_actions(selected, False, at(.2)),
                                 (START_STANDARD_PROGRAM,))
                self.assertEqual(button.handle_actions(selected, True, at(.3)), ())
                button.handle("press", True, at(1))
                button.handle("release", True, at(1.1))
                self.assertEqual(button.handle_actions(selected, True, at(1.2)),
                                 (END_RELEASE,))
                self.assertIsNone(button.handle("release", False, at(2)))

    def test_selected_event_only_multi_click_and_single_keep_separate_actions(self):
        for selected in ("double", "triple"):
            with self.subTest(selected=selected):
                button = ButtonGestures(THRESHOLD, selected)
                self.assertEqual(button.handle("short", False, at(0)), HEATER_TOGGLE_OVERRIDE)
                self.assertEqual(button.handle_actions(selected, False, at(1)),
                                 (START_STANDARD_PROGRAM,))
                self.assertEqual(button.handle_actions(selected, True, at(2)),
                                 (END_RELEASE,))
                self.assertIsNone(button.handle("long", False, at(3)))

    def test_session_gesture_rejects_single_and_invalid_values(self):
        for value in ("short", "single", 1, True, None, [], "quadruple"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ButtonGestures(THRESHOLD, value)

    def test_native_off_press_waits_for_short_classification(self):
        button = ButtonGestures(THRESHOLD)
        self.assertIsNone(button.handle("press", False, at(0)))
        self.assertIsNone(button.handle("release", False, at(0.2)))
        self.assertEqual(button.handle("short", False, at(0.3)), HEATER_TOGGLE_OVERRIDE)
        self.assertIsNone(button.handle("short", True, at(0.4)))
        self.assertIsNone(button.handle("long", True, at(2)))
        self.assertIsNone(button.handle("release", True, at(3)))

    def test_native_off_long_starts_and_release_finishes_acknowledgement(self):
        button = ButtonGestures(THRESHOLD)
        self.assertIsNone(button.handle("press", False, at(0)))
        self.assertEqual(button.handle("long", False, at(2)), START_HOLD)
        self.assertEqual(button.handle_actions("double", False, at(2)), ())
        self.assertEqual(button.handle("release", True, at(3)), START_RELEASE)
        self.assertIsNone(button.handle("short", False, at(3)))
        self.assertEqual(button.handle_actions("triple", False, at(3)), ())
        self.assertIsNone(button.handle("press", False, at(4)))
        self.assertEqual(button.handle("short", False, at(4)), HEATER_TOGGLE_OVERRIDE)

    def test_short_before_release_consumes_native_followup_classifications(self):
        button = ButtonGestures(THRESHOLD)
        self.assertIsNone(button.handle("press", False, at(0)))
        self.assertEqual(button.handle("short", False, at(0.2)), HEATER_TOGGLE_OVERRIDE)
        self.assertIsNone(button.handle("release", True, at(0.3)))
        self.assertIsNone(button.handle("short", True, at(0.4)))
        self.assertIsNone(button.handle("long", True, at(2)))

    def test_short_after_release_toggles_heater_during_session(self):
        button = ButtonGestures(THRESHOLD)
        self.assertIsNone(button.handle("press", True, at(0)))
        self.assertIsNone(button.handle("release", True, at(1)))
        self.assertEqual(button.handle("short", True, at(1)), HEATER_TOGGLE_OVERRIDE)

    def test_long_hold_ends_then_release_starts_light_afterrun(self):
        button = ButtonGestures(THRESHOLD)
        button.handle("press", True, at(0))
        self.assertEqual(button.handle("long", True, at(1)), END_HOLD)
        self.assertEqual(button.handle("release", False, at(2)), END_RELEASE)

    def test_duplicate_long_has_no_second_effect(self):
        button = ButtonGestures(THRESHOLD)
        button.handle("press", True, at(0))
        self.assertEqual(button.handle("long", True, at(1)), END_HOLD)
        self.assertIsNone(button.handle("long", True, at(2)))

    def test_event_only_single_is_a_complete_gesture(self):
        button = ButtonGestures(THRESHOLD)
        self.assertEqual(button.handle("short", False, at(0)), HEATER_TOGGLE_OVERRIDE)
        self.assertEqual(button.handle("short", True, at(1)), HEATER_TOGGLE_OVERRIDE)

    def test_event_only_multi_click_toggles_every_press_without_starting(self):
        button = ButtonGestures(THRESHOLD)
        self.assertEqual(
            button.handle_actions("double", False, at(0)),
            (HEATER_TOGGLE_OVERRIDE, HEATER_TOGGLE_OVERRIDE),
        )
        self.assertEqual(button.handle("long", True, at(1)), END_HOLD)

        button = ButtonGestures(THRESHOLD)
        self.assertEqual(
            button.handle_actions("triple", False, at(0)),
            (
                HEATER_TOGGLE_OVERRIDE,
                HEATER_TOGGLE_OVERRIDE,
                HEATER_TOGGLE_OVERRIDE,
            ),
        )
        self.assertEqual(button.handle("long", True, at(1)), END_HOLD)

    def test_native_multi_click_toggles_only_at_the_short_summary(self):
        button = ButtonGestures(THRESHOLD)
        self.assertIsNone(button.handle("press", False, at(0)))
        self.assertIsNone(button.handle("release", False, at(0)))
        self.assertIsNone(button.handle("press", False, at(1)))
        self.assertIsNone(button.handle("release", False, at(1)))
        self.assertEqual(
            button.handle_actions("double", False, at(1)),
            (HEATER_TOGGLE_OVERRIDE, HEATER_TOGGLE_OVERRIDE),
        )
        self.assertEqual(button.handle_actions("double", True, at(2)), ())
        self.assertIsNone(button.handle("short", True, at(2)))
        self.assertIsNone(button.handle("long", True, at(2)))
        self.assertIsNone(button.handle("release", True, at(2)))

    def test_native_triple_toggles_each_press(self):
        button = ButtonGestures(THRESHOLD)
        for second in range(3):
            self.assertIsNone(button.handle("press", False, at(second)))
            self.assertIsNone(button.handle("release", False, at(second)))
        self.assertEqual(
            button.handle_actions("triple", False, at(3)),
            (HEATER_TOGGLE_OVERRIDE, HEATER_TOGGLE_OVERRIDE, HEATER_TOGGLE_OVERRIDE),
        )
        self.assertIsNone(button.handle("long", True, at(4)))
        self.assertIsNone(button.handle("release", True, at(4)))
        button.handle("press", True, at(5))
        self.assertEqual(button.handle("long", True, at(6)), END_HOLD)

    def test_native_summary_after_completed_single_cannot_repeat_its_action(self):
        button = ButtonGestures(THRESHOLD)
        button.handle("press", False, at(0))
        self.assertEqual(button.handle("short", False, at(0.2)), HEATER_TOGGLE_OVERRIDE)
        button.handle("release", True, at(0.3))
        self.assertEqual(button.handle_actions("double", True, at(0.4)), ())
        self.assertEqual(button.handle_actions("triple", True, at(0.5)), ())

    def test_native_summary_excludes_an_older_independent_single(self):
        button = ButtonGestures(THRESHOLD)
        button.handle("press", False, at(0))
        button.handle("release", False, at(0.2))
        self.assertEqual(button.handle("short", False, at(0.3)), HEATER_TOGGLE_OVERRIDE)
        # One of the new group's edge pairs was lost; its summary is complete.
        button.handle("press", True, at(1))
        button.handle("release", True, at(1.2))
        self.assertEqual(
            button.handle_actions("double", True, at(2.3)),
            (HEATER_TOGGLE_OVERRIDE,) * 2,
        )

    def test_native_triple_while_running_keeps_every_short_press(self):
        button = ButtonGestures(THRESHOLD)
        for second in range(3):
            self.assertIsNone(button.handle("press", True, at(second)))
            self.assertIsNone(button.handle("release", True, at(second)))
        self.assertEqual(
            button.handle_actions("triple", True, at(3)),
            (HEATER_TOGGLE_OVERRIDE,) * 3,
        )

    def test_release_without_a_press_never_restarts_operation(self):
        button = ButtonGestures(THRESHOLD)
        self.assertIsNone(button.handle("release", False, at(0)))

    def test_binary_press_uses_injected_clock_for_long_threshold(self):
        button = ButtonGestures(timedelta(seconds=2))
        self.assertIsNone(button.handle("on", True, at(0)))
        self.assertIsNone(button.advance(at(1), True))
        self.assertEqual(button.advance(at(2), True), END_HOLD)
        self.assertEqual(button.handle("off", False, at(3)), END_RELEASE)

    def test_binary_short_is_completed_on_off(self):
        button = ButtonGestures(THRESHOLD)
        button.handle("on", True, at(0))
        self.assertEqual(button.handle("off", True, at(0)), HEATER_TOGGLE_OVERRIDE)

    def test_binary_long_release_is_correct_even_if_timer_callback_was_delayed(self):
        button = ButtonGestures(timedelta(seconds=2))
        button.handle("on", True, at(0))
        self.assertEqual(button.handle("off", True, at(3)), END_RELEASE)
        self.assertIsNone(button.advance(at(4), False))

    def test_advance_never_converts_a_native_press_into_a_long_hold(self):
        button = ButtonGestures(THRESHOLD)
        button.handle("press", True, at(0))
        self.assertIsNone(button.advance(at(2), True))

    def test_binary_off_short_toggles_only_on_release(self):
        button = ButtonGestures(THRESHOLD)
        self.assertIsNone(button.handle("on", False, at(0)))
        self.assertEqual(button.handle("off", False, at(0.5)), HEATER_TOGGLE_OVERRIDE)
        self.assertIsNone(button.handle("off", True, at(1)))
        self.assertIsNone(button.advance(at(2), True))

    def test_binary_off_long_starts_with_or_without_a_timely_threshold_tick(self):
        for duration in (1, 2):
            for advance in (False, True):
                with self.subTest(duration=duration, advance=advance):
                    button = ButtonGestures(THRESHOLD)
                    self.assertIsNone(button.handle("on", False, at(0)))
                    if advance:
                        self.assertEqual(button.advance(at(duration), False), START_HOLD)
                    self.assertEqual(
                        button.handle("off", advance, at(duration)),
                        START_RELEASE if advance else START_STANDARD_PROGRAM,
                    )
                    self.assertIsNone(button.handle("short", True, at(duration)))

    def test_binary_source_gap_discards_unconfirmed_hold_and_short(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                button = ButtonGestures(THRESHOLD)
                self.assertIsNone(button.handle("on", enabled, at(0)))
                self.assertIsNone(button.handle("unavailable", enabled, at(0.2)))
                self.assertIsNone(button.advance(at(2), enabled))
                self.assertIsNone(button.handle("off", enabled, at(3)))
                self.assertIsNone(button.handle("on", enabled, at(4)))
                self.assertEqual(
                    button.handle("off", enabled, at(4.2)),
                    HEATER_TOGGLE_OVERRIDE,
                )

    def test_binary_source_gap_keeps_confirmed_hold_until_release(self):
        button = ButtonGestures(THRESHOLD)
        button.handle("on", True, at(0))
        self.assertEqual(button.advance(at(1), True), END_HOLD)
        self.assertIsNone(button.handle("unavailable", False, at(2)))
        self.assertIsNone(button.advance(at(3), False))
        self.assertEqual(button.handle("off", False, at(4)), END_RELEASE)
        self.assertIsNone(button.handle("off", False, at(5)))

    def test_sparse_long_starts_without_hold_acknowledgement_and_ends_when_enabled(self):
        button = ButtonGestures(THRESHOLD)
        self.assertEqual(button.handle("long", False, at(0)), START_STANDARD_PROGRAM)
        self.assertEqual(button.handle("long", True, at(10)), END_HOLD)
        self.assertEqual(button.handle("release", False, at(11)), END_RELEASE)

    def test_short_after_completed_long_is_suppressed_until_next_press(self):
        button = ButtonGestures(THRESHOLD)
        button.handle("press", True, at(0))
        self.assertEqual(button.handle("long", True, at(1)), END_HOLD)
        self.assertEqual(button.handle("release", False, at(2)), END_RELEASE)
        self.assertIsNone(button.handle("short", False, at(2)))
        self.assertIsNone(button.handle("press", False, at(3)))
        self.assertEqual(button.handle("short", False, at(3)), HEATER_TOGGLE_OVERRIDE)

    def test_release_less_sparse_long_allows_the_next_event_only_click(self):
        button = ButtonGestures(THRESHOLD)
        self.assertEqual(button.handle("long", False, at(0)), START_STANDARD_PROGRAM)
        self.assertEqual(button.handle("short", False, at(1)), HEATER_TOGGLE_OVERRIDE)
        self.assertEqual(button.handle("long", True, at(2)), END_HOLD)
        self.assertEqual(button.handle("short", False, at(4)), HEATER_TOGGLE_OVERRIDE)
