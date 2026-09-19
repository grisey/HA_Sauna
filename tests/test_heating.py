from datetime import timedelta
import unittest

from custom_components.ha_sauna.core import heating
from custom_components.ha_sauna.core.models import HeatingTime
from test_foundation import T0


class HeatingTests(unittest.TestCase):
    def test_only_feedback_counts_and_idle_pauses_without_credit(self):
        state = heating.advance(HeatingTime(), T0, 60)
        state = heating.advance(state, T0 + timedelta(seconds=10), 60)
        self.assertEqual(state.elapsed_seconds, 0)
        state = heating.report(state, True, T0 + timedelta(seconds=10), 60)
        state = heating.report(state, False, T0 + timedelta(seconds=30), 60)
        state = heating.advance(state, T0 + timedelta(seconds=70), 60)
        self.assertEqual(state.elapsed_seconds, 20)
        self.assertEqual(len(state.intervals), 1)
        self.assertEqual(state.intervals[0].started_at, T0 + timedelta(seconds=10))
        self.assertEqual(state.intervals[0].ended_at, T0 + timedelta(seconds=30))
        state = heating.report(state, True, T0 + timedelta(seconds=70), 60)
        state = heating.advance(state, T0 + timedelta(seconds=80), 60)
        self.assertEqual(state.elapsed_seconds, 30)

    def test_continuous_off_resets_only_budget_and_preserves_intervals(self):
        state = heating.report(HeatingTime(), True, T0, 60)
        state = heating.report(state, False, T0 + timedelta(seconds=30), 60)
        state = heating.advance(state, T0 + timedelta(seconds=89), 60)
        self.assertEqual(state.elapsed_seconds, 30)
        intervals = state.intervals
        state = heating.advance(state, T0 + timedelta(seconds=90), 60)
        self.assertEqual(state.elapsed_seconds, 0)
        self.assertEqual(state.last_reset_at, T0 + timedelta(seconds=90))
        self.assertEqual(state.intervals, intervals)
        self.assertEqual(heating.advance(state, T0 + timedelta(seconds=99), 60).last_reset_at, state.last_reset_at)

    def test_unknown_feedback_stops_counting_but_is_not_proven_off_time(self):
        state = heating.report(HeatingTime(), True, T0, 60)
        state = heating.report(state, None, T0 + timedelta(seconds=30), 60)
        state = heating.advance(state, T0 + timedelta(seconds=300), 60)
        self.assertEqual(state.elapsed_seconds, 30)
        self.assertIsNone(state.off_since)
        self.assertIsNone(state.last_reset_at)
        self.assertEqual(state.intervals[0].end_reason, "feedback_unknown")

    def test_duplicate_reports_do_not_split_interval_and_late_report_is_rejected(self):
        state = heating.report(HeatingTime(), True, T0, 60)
        state = heating.report(state, True, T0 + timedelta(seconds=10), 60)
        self.assertEqual(len(state.intervals), 1)
        self.assertEqual(state.elapsed_seconds, 10)
        with self.assertRaises(ValueError):
            heating.report(state, False, T0 + timedelta(seconds=5), 60)
