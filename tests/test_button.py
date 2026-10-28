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
from custom_components.ha_sauna.core.defaults import instance_default
from custom_components.ha_sauna.core.parameters import Parameters


def at(seconds: int) -> datetime:
    return datetime(2026, 9, 20, 12, 0, tzinfo=UTC) + timedelta(seconds=seconds)


THRESHOLD = timedelta(seconds=Parameters({}).values["button_hold_seconds"])


class ButtonGestureTests(unittest.TestCase):
    def test_omitted_session_gesture_uses_catalog_default(self):
        button = ButtonGestures(None)
        self.assertEqual(
            button.session_gesture, instance_default("button_session_gesture")
        )

    def test_selected_native_multi_click_starts_and_ends_once_without_hold(self):
        for selected in ("double", "triple"):
            with self.subTest(selected=selected):
                button = ButtonGestures(None, selected)
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
                button = ButtonGestures(None, selected)
                self.assertEqual(button.handle("short", False, at(0)), HEATER_TOGGLE_OVERRIDE)
                self.assertEqual(button.handle_actions(selected, False, at(1)),
                                 (START_STANDARD_PROGRAM,))
                self.assertEqual(button.handle_actions(selected, True, at(2)),
                                 (END_RELEASE,))
                self.assertIsNone(button.handle("long", False, at(3)))

    def test_session_gesture_rejects_single_and_invalid_values(self):
        for value in ("short", "single", 1, True, None, [], "quadruple"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ButtonGestures(None, value)

    def test_native_off_press_waits_for_short_classification(self):
        button = ButtonGestures(None, "long")
        self.assertIsNone(button.handle("press", False, at(0)))
        self.assertIsNone(button.handle("release", False, at(0.2)))
        self.assertEqual(button.handle("short", False, at(0.3)), HEATER_TOGGLE_OVERRIDE)
        self.assertIsNone(button.handle("short", True, at(0.4)))
        self.assertIsNone(button.handle("long", True, at(2)))
        self.assertIsNone(button.handle("release", True, at(3)))

    def test_native_off_long_starts_and_release_finishes_acknowledgement(self):
        button = ButtonGestures(None, "long")
        self.assertIsNone(button.handle("press", False, at(0)))
        self.assertEqual(button.handle("long", False, at(2)), START_HOLD)
        self.assertEqual(button.handle_actions("double", False, at(2)), ())
        self.assertEqual(button.handle("release", True, at(3)), START_RELEASE)
        self.assertIsNone(button.handle("short", False, at(3)))
        self.assertEqual(button.handle_actions("triple", False, at(3)), ())
        self.assertIsNone(button.handle("press", False, at(4)))
        self.assertEqual(button.handle("short", False, at(4)), HEATER_TOGGLE_OVERRIDE)

    def test_short_before_release_consumes_native_followup_classifications(self):
        button = ButtonGestures(None, "long")
        self.assertIsNone(button.handle("press", False, at(0)))
        self.assertEqual(button.handle("short", False, at(0.2)), HEATER_TOGGLE_OVERRIDE)
        self.assertIsNone(button.handle("release", True, at(0.3)))
        self.assertIsNone(button.handle("short", True, at(0.4)))
        self.assertIsNone(button.handle("long", True, at(2)))

    def test_short_after_release_toggles_heater_during_session(self):
        button = ButtonGestures(None, "long")
        self.assertIsNone(button.handle("press", True, at(0)))
        self.assertIsNone(button.handle("release", True, at(1)))
        self.assertEqual(button.handle("short", True, at(1)), HEATER_TOGGLE_OVERRIDE)

    def test_long_hold_ends_then_release_starts_light_afterrun(self):
        button = ButtonGestures(None, "long")
        button.handle("press", True, at(0))
        self.assertEqual(button.handle("long", True, at(1)), END_HOLD)
        self.assertEqual(button.handle("release", False, at(2)), END_RELEASE)

    def test_duplicate_long_has_no_second_effect(self):
        button = ButtonGestures(None, "long")
        button.handle("press", True, at(0))
        self.assertEqual(button.handle("long", True, at(1)), END_HOLD)
        self.assertIsNone(button.handle("long", True, at(2)))

    def test_event_only_single_is_a_complete_gesture(self):
        button = ButtonGestures(None, "long")
        self.assertEqual(button.handle("short", False, at(0)), HEATER_TOGGLE_OVERRIDE)
        self.assertEqual(button.handle("short", True, at(1)), HEATER_TOGGLE_OVERRIDE)

    def test_unselected_event_only_multi_click_has_no_additional_meaning(self):
        for gesture in ("double", "triple"):
            for enabled in (False, True):
                with self.subTest(gesture=gesture, enabled=enabled):
                    button = ButtonGestures(None, "long")
                    self.assertEqual(button.handle_actions(gesture, enabled, at(0)), ())
                    self.assertEqual(
                        button.handle("long", enabled, at(1)),
                        END_RELEASE if enabled else START_STANDARD_PROGRAM,
                    )

    def test_unselected_native_multi_click_consumes_its_edges_without_action(self):
        button = ButtonGestures(None, "long")
        self.assertIsNone(button.handle("press", False, at(0)))
        self.assertIsNone(button.handle("release", False, at(0)))
        self.assertIsNone(button.handle("press", False, at(1)))
        self.assertIsNone(button.handle("release", False, at(1)))
        self.assertEqual(
            button.handle_actions("double", False, at(1)),
            (),
        )
        self.assertEqual(button.handle_actions("double", True, at(2)), ())
        self.assertIsNone(button.handle("short", True, at(2)))
        self.assertIsNone(button.handle("long", True, at(2)))
        self.assertIsNone(button.handle("release", True, at(2)))

    def test_unselected_native_triple_does_not_expand_into_short_gestures(self):
        button = ButtonGestures(None, "long")
        for second in range(3):
            self.assertIsNone(button.handle("press", False, at(second)))
            self.assertIsNone(button.handle("release", False, at(second)))
        self.assertEqual(
            button.handle_actions("triple", False, at(3)),
            (),
        )
        self.assertIsNone(button.handle("long", True, at(4)))
        self.assertIsNone(button.handle("release", True, at(4)))
        button.handle("press", True, at(5))
        self.assertEqual(button.handle("long", True, at(6)), END_HOLD)

    def test_native_summary_after_completed_single_cannot_repeat_its_action(self):
        button = ButtonGestures(None, "long")
        button.handle("press", False, at(0))
        self.assertEqual(button.handle("short", False, at(0.2)), HEATER_TOGGLE_OVERRIDE)
        button.handle("release", True, at(0.3))
        self.assertEqual(button.handle_actions("double", True, at(0.4)), ())
        self.assertEqual(button.handle_actions("triple", True, at(0.5)), ())

    def test_unselected_native_summary_cannot_repeat_an_older_single(self):
        button = ButtonGestures(None, "long")
        button.handle("press", False, at(0))
        button.handle("release", False, at(0.2))
        self.assertEqual(button.handle("short", False, at(0.3)), HEATER_TOGGLE_OVERRIDE)
        # One of the new group's edge pairs was lost; its summary is complete.
        button.handle("press", True, at(1))
        button.handle("release", True, at(1.2))
        self.assertEqual(
            button.handle_actions("double", True, at(2.3)),
            (),
        )

    def test_release_without_a_press_never_restarts_operation(self):
        button = ButtonGestures(None, "long")
        self.assertIsNone(button.handle("release", False, at(0)))

    def test_binary_press_uses_injected_clock_for_long_threshold(self):
        button = ButtonGestures(THRESHOLD, "long")
        self.assertIsNone(button.handle("on", True, at(0)))
        self.assertIsNone(button.advance(at(0) + THRESHOLD / 2, True))
        self.assertEqual(button.advance(at(0) + THRESHOLD, True), END_HOLD)
        self.assertEqual(button.handle("off", False, at(1) + THRESHOLD), END_RELEASE)

    def test_binary_short_is_completed_on_off(self):
        button = ButtonGestures(THRESHOLD, "long")
        button.handle("on", True, at(0))
        self.assertEqual(button.handle("off", True, at(0)), HEATER_TOGGLE_OVERRIDE)

    def test_binary_long_release_is_correct_even_if_timer_callback_was_delayed(self):
        button = ButtonGestures(THRESHOLD, "long")
        button.handle("on", True, at(0))
        self.assertEqual(button.handle("off", True, at(1) + THRESHOLD), END_RELEASE)
        self.assertIsNone(button.advance(at(2) + THRESHOLD, False))

    def test_advance_never_converts_a_native_press_into_a_long_hold(self):
        button = ButtonGestures(None, "long")
        button.handle("press", True, at(0))
        self.assertIsNone(button.advance(at(0) + THRESHOLD * 2, True))
        self.assertIsNone(button.hold_threshold)

    def test_binary_edges_require_a_configured_binary_duration(self):
        button = ButtonGestures(None, "long")
        with self.assertRaisesRegex(ValueError, "binary button input"):
            button.handle("on", False, at(0))

    def test_binary_off_short_toggles_only_on_release(self):
        button = ButtonGestures(THRESHOLD, "long")
        self.assertIsNone(button.handle("on", False, at(0)))
        self.assertEqual(button.handle("off", False, at(0.5)), HEATER_TOGGLE_OVERRIDE)
        self.assertIsNone(button.handle("off", True, at(1)))
        self.assertIsNone(button.advance(at(2), True))

    def test_binary_off_long_starts_with_or_without_a_timely_threshold_tick(self):
        for duration in (THRESHOLD.total_seconds(), THRESHOLD.total_seconds() * 2):
            for advance in (False, True):
                with self.subTest(duration=duration, advance=advance):
                    button = ButtonGestures(THRESHOLD, "long")
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
                button = ButtonGestures(THRESHOLD, "long")
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
        button = ButtonGestures(THRESHOLD, "long")
        button.handle("on", True, at(0))
        self.assertEqual(button.advance(at(0) + THRESHOLD, True), END_HOLD)
        self.assertIsNone(button.handle("unavailable", False, at(1) + THRESHOLD))
        self.assertIsNone(button.advance(at(2) + THRESHOLD, False))
        self.assertEqual(button.handle("off", False, at(3) + THRESHOLD), END_RELEASE)
        self.assertIsNone(button.handle("off", False, at(4) + THRESHOLD))

    def test_event_only_long_is_complete_without_a_future_release(self):
        button = ButtonGestures(None, "long")
        self.assertEqual(button.handle("long", False, at(0)), START_STANDARD_PROGRAM)
        self.assertEqual(button.handle("long", True, at(10)), END_RELEASE)
        self.assertIsNone(button.handle("release", False, at(11)))

    def test_native_long_received_after_release_is_already_complete(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                button = ButtonGestures(None, "long")
                button.handle("press", enabled, at(0))
                button.handle("release", enabled, at(1))
                self.assertEqual(
                    button.handle("long", enabled, at(2)),
                    END_RELEASE if enabled else START_STANDARD_PROGRAM,
                )

    def test_short_after_completed_long_is_suppressed_until_next_press(self):
        button = ButtonGestures(None, "long")
        button.handle("press", True, at(0))
        self.assertEqual(button.handle("long", True, at(1)), END_HOLD)
        self.assertEqual(button.handle("release", False, at(2)), END_RELEASE)
        self.assertIsNone(button.handle("short", False, at(2)))
        self.assertIsNone(button.handle("press", False, at(3)))
        self.assertEqual(button.handle("short", False, at(3)), HEATER_TOGGLE_OVERRIDE)

    def test_release_less_sparse_long_allows_the_next_event_only_click(self):
        button = ButtonGestures(None, "long")
        self.assertEqual(button.handle("long", False, at(0)), START_STANDARD_PROGRAM)
        self.assertEqual(button.handle("short", False, at(1)), HEATER_TOGGLE_OVERRIDE)
        self.assertEqual(button.handle("long", True, at(2)), END_RELEASE)
        self.assertEqual(button.handle("short", False, at(4)), HEATER_TOGGLE_OVERRIDE)
