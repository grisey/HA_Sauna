"""Tests for the HA-independent button gesture helper."""
import unittest
from datetime import UTC, datetime, timedelta

from custom_components.ha_sauna.core.button import (
    END_HOLD,
    END_RELEASE,
    HEATER_TOGGLE_OVERRIDE,
    START_STANDARD_PROGRAM,
    ButtonGestures,
)


def at(seconds: int) -> datetime:
    return datetime(2026, 9, 20, 12, 0, tzinfo=UTC) + timedelta(seconds=seconds)


THRESHOLD = timedelta(seconds=1)


class ButtonGestureTests(unittest.TestCase):
    def test_press_while_off_starts_once_and_long_cannot_end_it(self):
        button = ButtonGestures(THRESHOLD)
        self.assertEqual(button.handle("press", False, at(0)), START_STANDARD_PROGRAM)
        self.assertIsNone(button.handle("long", True, at(2)))
        self.assertIsNone(button.handle("release", True, at(3)))

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
        self.assertEqual(button.handle("short", False, at(0)), START_STANDARD_PROGRAM)
        self.assertEqual(button.handle("short", True, at(1)), HEATER_TOGGLE_OVERRIDE)

    def test_event_only_multi_click_starts_once_then_handles_each_remaining_press(self):
        button = ButtonGestures(THRESHOLD)
        self.assertEqual(
            button.handle_actions("double", False, at(0)),
            (START_STANDARD_PROGRAM, HEATER_TOGGLE_OVERRIDE),
        )

        button = ButtonGestures(THRESHOLD)
        self.assertEqual(
            button.handle_actions("triple", False, at(0)),
            (
                START_STANDARD_PROGRAM,
                HEATER_TOGGLE_OVERRIDE,
                HEATER_TOGGLE_OVERRIDE,
            ),
        )

    def test_native_multi_click_does_not_repeat_its_already_started_press(self):
        button = ButtonGestures(THRESHOLD)
        self.assertEqual(button.handle("press", False, at(0)), START_STANDARD_PROGRAM)
        self.assertIsNone(button.handle("release", True, at(0)))
        self.assertIsNone(button.handle("press", True, at(1)))
        self.assertIsNone(button.handle("release", True, at(1)))
        self.assertEqual(
            button.handle_actions("double", True, at(1)),
            (HEATER_TOGGLE_OVERRIDE,),
        )

    def test_native_triple_after_start_completes_only_its_two_remaining_presses(self):
        button = ButtonGestures(THRESHOLD)
        self.assertEqual(button.handle("press", False, at(0)), START_STANDARD_PROGRAM)
        self.assertIsNone(button.handle("release", True, at(0)))
        for second in range(1, 3):
            self.assertIsNone(button.handle("press", True, at(second)))
            self.assertIsNone(button.handle("release", True, at(second)))
        self.assertEqual(
            button.handle_actions("triple", True, at(3)),
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

    def test_binary_release_after_start_closes_the_gesture(self):
        button = ButtonGestures(THRESHOLD)
        self.assertEqual(button.handle("on", False, at(0)), START_STANDARD_PROGRAM)
        self.assertIsNone(button.handle("off", True, at(1)))
        self.assertIsNone(button.advance(at(2), True))

    def test_sparse_long_starts_when_off_and_ends_when_enabled(self):
        button = ButtonGestures(THRESHOLD)
        self.assertEqual(button.handle("long", False, at(0)), START_STANDARD_PROGRAM)
        self.assertIsNone(button.handle("release", True, at(1)))

        button = ButtonGestures(THRESHOLD)
        self.assertEqual(button.handle("long", True, at(0)), END_HOLD)
        self.assertEqual(button.handle("release", False, at(1)), END_RELEASE)

    def test_short_after_completed_long_is_suppressed_until_next_press(self):
        button = ButtonGestures(THRESHOLD)
        button.handle("press", True, at(0))
        self.assertEqual(button.handle("long", True, at(1)), END_HOLD)
        self.assertEqual(button.handle("release", False, at(2)), END_RELEASE)
        self.assertIsNone(button.handle("short", False, at(2)))
        self.assertEqual(button.handle("press", False, at(3)), START_STANDARD_PROGRAM)

    def test_release_less_sparse_long_allows_the_next_event_only_click(self):
        button = ButtonGestures(THRESHOLD)
        self.assertEqual(button.handle("long", True, at(0)), END_HOLD)
        self.assertEqual(button.handle("short", False, at(2)), START_STANDARD_PROGRAM)
