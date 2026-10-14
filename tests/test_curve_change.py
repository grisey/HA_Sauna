"""Lokale Kurvenevidenz aus bestehenden Original- und Zeitreihenfixtures."""

from dataclasses import replace
from datetime import datetime, timedelta
import gzip
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from test_detector import trace
from test_foundation import T0, parameters
from test_settings import fixture

from custom_components.ha_sauna.core import curve_change as curves
from custom_components.ha_sauna.core.curve_change import CurvePolicy, curve_change


ROOT = Path(__file__).resolve().parent


def curve_policy():
    values = parameters().values
    return CurvePolicy(
        min_side_samples=values["curve_min_side_samples"],
        max_points=values["curve_max_points"],
        max_candidates=values["curve_max_candidates"],
        robust_iterations=values["curve_robust_iterations"],
        huber_delta=values["curve_huber_delta"],
    )


def trace_points(stop):
    return [(T0 + timedelta(seconds=second), trace(second)[0]) for second in range(stop)]


class CurveChangeTests(unittest.TestCase):
    def test_r04_short_closure_is_visible_while_humidity_still_falls(self):
        # Independent accepted reference: closure near 16:02:40 local time.
        # The observation is causal at +5 s; the input window remains exactly
        # the caller's configured lookback, including the preceding opening.
        reference = datetime(2000, 1, 1, 16, 2, 40, tzinfo=ZoneInfo("Europe/Berlin"))
        observed_at = reference + timedelta(seconds=5)
        window_start = observed_at - timedelta(seconds=parameters().values["curve_window_seconds"])
        origin = datetime.fromisoformat(fixture("session")["origin"])
        samples = {"upper_humidity": [], "lower_humidity": []}
        with gzip.open(ROOT / "fixtures/replay/R04.samples.jsonl.gz", "rt", encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                at = origin + timedelta(seconds=row["t"])
                if at > observed_at:
                    break
                if at >= window_start and row["role"] in samples:
                    samples[row["role"]].append((at, row["value"]))

        for role, points in samples.items():
            with self.subTest(role=role):
                result = curve_change(points, policy=curve_policy())
                self.assertIsNotNone(result)
                self.assertEqual(result.evidence, "supports")
                self.assertLessEqual(abs((result.onset_at - reference).total_seconds()), 10)
                self.assertLess(result.slope_before, result.slope_after)
                self.assertLess(result.slope_after, 0)
                self.assertIn(result.onset_at, {at for at, _ in points})
                self.assertEqual(result.newest_at, points[-1][0])

    def test_positive_change_can_leave_both_slopes_negative(self):
        policy = replace(curve_policy(), max_candidates=curve_policy().max_points)
        base = trace(0)[0]
        falling_step = trace(70)[0] - trace(71)[0]
        points = [
            (at, base - 2 * falling_step * (at - T0).total_seconds() - (value - base))
            for at, value in trace_points(80)
        ]
        result = curve_change(points, policy=policy)
        self.assertIsNotNone(result)
        self.assertEqual(result.evidence, "supports")
        self.assertGreater(result.slope_change, 0)
        self.assertLess(result.slope_before, result.slope_after)
        self.assertLess(result.slope_after, 0)

    def test_smooth_trends_and_an_isolated_outlier_do_not_support_a_change(self):
        policy = curve_policy()
        source = trace_points(80)
        base = source[0][1]
        amplitude = trace(70)[0] - trace(85)[0]
        span = (source[-1][0] - source[0][0]).total_seconds()
        variants = {
            f"smooth degree {degree}": [
                (at, base - amplitude * ((at - T0).total_seconds() / span) ** degree)
                for at, _ in source
            ]
            for degree in (1, 2, 3)
        }
        outlier = [(at, base) for at, _ in source]
        outlier[len(outlier) // 2] = (outlier[len(outlier) // 2][0], trace(85)[0])
        variants["one isolated original"] = outlier
        for name, points in variants.items():
            with self.subTest(name=name):
                result = curve_change(points, policy=policy)
                self.assertTrue(result is None or result.evidence == "rejects", result)

    def test_frozen_onset_cannot_move_or_become_an_interpolated_point(self):
        policy = replace(curve_policy(), max_candidates=curve_policy().max_points)
        initial = curve_change(trace_points(80), policy=policy)
        self.assertIsNotNone(initial)
        self.assertEqual(initial.evidence, "supports")
        self.assertEqual(initial.onset_at, T0 + timedelta(seconds=70))
        later = curve_change(trace_points(85), policy=policy, onset_at=initial.onset_at)
        self.assertIsNotNone(later)
        self.assertEqual(later.evidence, "supports")
        self.assertEqual(later.onset_at, initial.onset_at)
        self.assertIsNone(curve_change(
            trace_points(85), policy=policy,
            onset_at=initial.onset_at + timedelta(microseconds=1),
        ))

    def test_fit_budget_retains_only_original_points_and_pins_the_frozen_onset(self):
        policy = curve_policy()
        count = 2 * policy.max_points + 1
        points = [
            (T0 + timedelta(seconds=79 * index / (count - 1)), trace(79 * index / (count - 1))[0])
            for index in range(count)
        ]
        onset_at = points[len(points) // 2][0]
        selections = []
        bounded = curves._bounded_samples

        def observed_selection(samples, limit, onset):
            selected = bounded(samples, limit, onset)
            selections.append(selected)
            return selected

        with patch.object(curves, "_bounded_samples", side_effect=observed_selection), patch.object(
            curves, "_models", wraps=curves._models,
        ) as fits:
            curve_change(points, policy=policy, onset_at=onset_at)

        self.assertEqual(len(selections), 1)
        selected = selections[0]
        self.assertLessEqual(len(selected), policy.max_points)
        self.assertTrue(set(selected).issubset(points))
        self.assertEqual((selected[0], selected[-1]), (points[0], points[-1]))
        self.assertIn(onset_at, {at for at, _ in selected})
        self.assertGreaterEqual(fits.call_count, 1)
        self.assertLessEqual(fits.call_count, 2)
        for call in fits.call_args_list:
            times, values, _, knots, _ = call.args
            self.assertLessEqual(len(times), policy.max_points)
            self.assertEqual(len(times), len(values))
            self.assertLessEqual(len(knots), policy.max_candidates)
