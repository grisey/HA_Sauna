from datetime import timedelta
import unittest

from custom_components.ha_sauna.core import heating
from custom_components.ha_sauna.core.models import (
    HeatingInterval,
    HeatingStateMark,
    HeatingTime,
)
from test_foundation import T0, parameters

RESET = parameters().seconds("heat_reset_minutes")


class HeatingTests(unittest.TestCase):
    def test_only_feedback_counts_and_idle_pauses_without_credit(self):
        state = heating.advance(HeatingTime(), T0, RESET)
        state = heating.advance(state, T0 + timedelta(seconds=10), RESET)
        self.assertEqual(state.elapsed_seconds, 0)
        self.assertEqual(state.state_history, ())
        state = heating.report(state, True, T0 + timedelta(seconds=10), RESET)
        self.assertEqual(
            state.state_history,
            (HeatingStateMark(T0 + timedelta(seconds=10), True),),
        )
        state = heating.report(state, False, T0 + timedelta(seconds=30), RESET)
        history = state.state_history
        state = heating.advance(state, T0 + timedelta(seconds=70), RESET)
        self.assertEqual(state.elapsed_seconds, 20)
        self.assertIs(state.state_history, history)
        self.assertEqual(
            state.state_history,
            (
                HeatingStateMark(T0 + timedelta(seconds=10), True),
                HeatingStateMark(T0 + timedelta(seconds=30), False),
            ),
        )
        self.assertEqual(len(state.intervals), 1)
        self.assertEqual(state.intervals[0].started_at, T0 + timedelta(seconds=10))
        self.assertEqual(state.intervals[0].ended_at, T0 + timedelta(seconds=30))
        state = heating.report(state, True, T0 + timedelta(seconds=70), RESET)
        state = heating.advance(state, T0 + timedelta(seconds=80), RESET)
        self.assertEqual(state.elapsed_seconds, 30)
        self.assertEqual(
            state.state_history,
            (
                HeatingStateMark(T0 + timedelta(seconds=10), True),
                HeatingStateMark(T0 + timedelta(seconds=30), False),
                HeatingStateMark(T0 + timedelta(seconds=70), True),
            ),
        )
        self.assertEqual(
            state.intervals,
            (
                HeatingInterval(
                    T0 + timedelta(seconds=10),
                    T0 + timedelta(seconds=30),
                    "feedback_off",
                ),
                HeatingInterval(T0 + timedelta(seconds=70)),
            ),
        )

    def test_continuous_off_resets_only_budget_and_preserves_intervals(self):
        state = heating.report(HeatingTime(), True, T0, RESET)
        state = heating.report(state, False, T0 + timedelta(seconds=30), RESET)
        state = heating.advance(state, T0 + timedelta(seconds=30 + RESET - 1), RESET)
        self.assertEqual(state.elapsed_seconds, 30)
        intervals = state.intervals
        history = state.state_history
        state = heating.advance(state, T0 + timedelta(seconds=30 + RESET), RESET)
        self.assertEqual(state.elapsed_seconds, 0)
        self.assertEqual(state.last_reset_at, T0 + timedelta(seconds=30 + RESET))
        self.assertEqual(state.intervals, intervals)
        self.assertIs(state.state_history, history)
        self.assertEqual(
            state.state_history,
            (
                HeatingStateMark(T0, True),
                HeatingStateMark(T0 + timedelta(seconds=30), False),
            ),
        )
        self.assertEqual(heating.advance(state, T0 + timedelta(seconds=30 + RESET + 9), RESET).last_reset_at, state.last_reset_at)

    def test_unknown_feedback_stops_counting_but_is_not_proven_off_time(self):
        state = heating.report(HeatingTime(), True, T0, RESET)
        state = heating.report(state, None, T0 + timedelta(seconds=30), RESET)
        history = state.state_history
        state = heating.advance(state, T0 + timedelta(seconds=30 + RESET * 5), RESET)
        self.assertEqual(state.elapsed_seconds, 30)
        self.assertIsNone(state.off_since)
        self.assertIsNone(state.last_reset_at)
        self.assertEqual(state.intervals[0].end_reason, "feedback_unknown")
        self.assertIs(state.state_history, history)
        self.assertEqual(
            state.state_history,
            (
                HeatingStateMark(T0, True),
                HeatingStateMark(T0 + timedelta(seconds=30), None),
            ),
        )
        self.assertEqual(
            state.intervals,
            (HeatingInterval(T0, T0 + timedelta(seconds=30), "feedback_unknown"),),
        )

    def test_duplicate_reports_do_not_split_interval_and_late_report_is_rejected(self):
        state = heating.report(HeatingTime(), True, T0, RESET)
        history = state.state_history
        state = heating.report(state, True, T0 + timedelta(seconds=10), RESET)
        self.assertEqual(len(state.intervals), 1)
        self.assertEqual(state.elapsed_seconds, 10)
        self.assertIs(state.state_history, history)
        self.assertEqual(state.state_history, (HeatingStateMark(T0, True),))
        self.assertEqual(state.intervals, (HeatingInterval(T0),))
        with self.assertRaises(ValueError):
            heating.report(state, False, T0 + timedelta(seconds=5), RESET)
        self.assertIs(state.state_history, history)

    def test_first_unknown_or_off_report_starts_history_at_report_time(self):
        for feedback in (None, False):
            with self.subTest(feedback=feedback):
                state = heating.advance(HeatingTime(), T0, RESET)
                at = T0 + timedelta(seconds=10)
                state = heating.report(state, feedback, at, RESET)
                self.assertEqual(
                    state.state_history, (HeatingStateMark(at, feedback),)
                )
                history = state.state_history
                state = heating.report(state, feedback, at, RESET)
                state = heating.advance(state, T0 + timedelta(seconds=30), RESET)
                self.assertIs(state.state_history, history)
                self.assertEqual(state.elapsed_seconds, 0)
                self.assertEqual(state.intervals, ())

    def test_same_time_reports_keep_only_the_final_state(self):
        for final in (False, True):
            with self.subTest(final=final):
                state = heating.report(HeatingTime(), None, T0, RESET)
                state = heating.report(state, final, T0, RESET)
                self.assertEqual(
                    state.state_history, (HeatingStateMark(T0, final),)
                )
                self.assertIs(state.reported_heating, final)
                self.assertEqual(state.elapsed_seconds, 0)
                self.assertEqual(
                    state.intervals, (HeatingInterval(T0),) if final else ()
                )
                history = state.state_history
                state = heating.report(state, final, T0, RESET)
                state = heating.advance(state, T0, RESET)
                self.assertIs(state.state_history, history)

        state = heating.report(HeatingTime(), False, T0, RESET)
        history = state.state_history
        at = T0 + timedelta(seconds=10)
        state = heating.report(state, None, at, RESET)
        state = heating.report(state, False, at, RESET)
        self.assertEqual(state.state_history, (HeatingStateMark(T0, False),))
        self.assertEqual(history, (HeatingStateMark(T0, False),))
        self.assertIs(state.reported_heating, False)
        self.assertEqual(state.elapsed_seconds, 0)
        self.assertEqual(state.intervals, ())

    def test_unknown_interrupts_the_continuous_off_reset_period(self):
        state = heating.report(HeatingTime(), True, T0, RESET)
        off_at = T0 + timedelta(seconds=30)
        state = heating.report(state, False, off_at, RESET)
        intervals = state.intervals
        unknown_at = off_at + timedelta(seconds=RESET - 1)
        state = heating.report(state, None, unknown_at, RESET)
        self.assertIsNone(state.off_since)
        next_off_at = off_at + timedelta(seconds=RESET)
        state = heating.report(state, False, next_off_at, RESET)
        self.assertEqual(state.off_since, next_off_at)
        self.assertEqual(state.elapsed_seconds, 30)
        self.assertIsNone(state.last_reset_at)
        history = state.state_history
        self.assertEqual(
            history,
            (
                HeatingStateMark(T0, True),
                HeatingStateMark(off_at, False),
                HeatingStateMark(unknown_at, None),
                HeatingStateMark(next_off_at, False),
            ),
        )
        state = heating.advance(
            state, next_off_at + timedelta(seconds=RESET - 1), RESET
        )
        self.assertEqual(state.elapsed_seconds, 30)
        self.assertIsNone(state.last_reset_at)
        reset_at = next_off_at + timedelta(seconds=RESET)
        state = heating.advance(state, reset_at, RESET)
        self.assertEqual(state.elapsed_seconds, 0)
        self.assertEqual(state.last_reset_at, reset_at)
        self.assertEqual(state.intervals, intervals)
        self.assertEqual(
            state.intervals, (HeatingInterval(T0, off_at, "feedback_off"),)
        )
        self.assertIs(state.state_history, history)

    def test_legacy_intervals_do_not_reconstruct_feedback_history(self):
        intervals = (
            HeatingInterval(
                T0 + timedelta(seconds=10),
                T0 + timedelta(seconds=30),
                "feedback_off",
            ),
        )
        state = HeatingTime(
            elapsed_seconds=20,
            accounted_at=T0 + timedelta(seconds=30),
            intervals=intervals,
        )
        state = heating.advance(state, T0 + timedelta(seconds=70), RESET)
        self.assertEqual(state.state_history, ())
        at = T0 + timedelta(seconds=80)
        state = heating.report(state, None, at, RESET)
        self.assertEqual(state.state_history, (HeatingStateMark(at, None),))
        self.assertEqual(state.elapsed_seconds, 20)
        self.assertEqual(state.intervals, intervals)
