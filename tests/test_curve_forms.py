"""Independent geometry and audit contracts for the common curve-form family."""

from datetime import timedelta
import gzip
import json
from math import isclose, log
from pathlib import Path
import unittest
from unittest.mock import patch

from test_curve_change import (
    curve_policy,
    level_trace_points,
    recovery_trace_points,
)
from test_detector import trace
from test_foundation import T0

from custom_components.ha_sauna.core import curve_change as curves
from custom_components.ha_sauna.core.curve_change import curve_form


ROOT = Path(__file__).resolve().parent


def turning_shapes():
    """Known valleys with fixture-derived amplitudes and original fixture noise.

    abs(x) changes slope at zero. -x + max(0, x + 1/2)^2 changes
    curvature at -1/2 and has its sole minimum at zero. Neither oracle uses
    a fitted product coefficient or a particular winning model.
    """
    source = level_trace_points()
    middle = source[len(source) // 2][0]
    half_span = (source[-1][0] - middle).total_seconds()
    amplitude = (trace(70)[0] - trace(71)[0]) * half_span
    piecewise = {"hinge1": [], "hinge2": []}
    for at, value in source:
        x = (at - middle).total_seconds() / half_span
        noise = value - trace(100)[1]
        for model, shape in (
            ("hinge1", abs(x)),
            ("hinge2", -x + max(0, x + 1 / 2)**2),
        ):
            piecewise[model].append((at, trace(0)[0] + amplitude * shape + noise))
    return {
        "quadratic": recovery_trace_points(degree=2),
        "cubic": recovery_trace_points(degree=3),
        **piecewise,
    }


def replay_slice(recording, start, end):
    """Read only original humidity reports in an independently selected slice."""
    points = []
    with gzip.open(
        ROOT / f"fixtures/replay/{recording}.samples.jsonl.gz",
        "rt", encoding="utf-8",
    ) as stream:
        for line in stream:
            row = json.loads(line)
            if row["t"] > end:
                break
            if row["t"] >= start and row["role"] == "upper_humidity":
                points.append((T0 + timedelta(seconds=row["t"]), row["value"]))
    return points


class CurveFormTests(unittest.TestCase):
    def assert_score_close(self, actual, expected):
        self.assertTrue(
            isclose(actual, expected, rel_tol=1e-8, abs_tol=1e-8),
            msg=f"{actual!r} != {expected!r}",
        )

    def assert_supported(self, result, before, after):
        self.assertIsNotNone(result)
        self.assertEqual(result.evidence, "supports", result)
        self.assertEqual(result.form_evidence, "supports", result)
        self.assertEqual(result.influence_evidence, "supports", result)
        self.assertGreater(result.score_gain, 0, result)
        self.assertEqual(result.before_trend.direction, before, result)
        self.assertEqual(result.after_trend.direction, after, result)
        self.assertGreaterEqual(result.before_count, curve_policy().min_side_samples)
        self.assertGreaterEqual(result.after_count, curve_policy().min_side_samples)
        self.assert_score_close(
            result.score_gain, result.counter_score - result.positive_score,
        )

    def test_shape_class_accepts_smooth_and_both_piecewise_forms(self):
        center = trace(0)[0]
        for model, valley in turning_shapes().items():
            for form, sign in (("recovery", 1), ("peak", -1)):
                with self.subTest(generator=model, form=form):
                    points = [(at, center + sign * (value - center))
                              for at, value in valley]
                    result = curve_form(points, policy=curve_policy(), form=form)
                    self.assert_supported(result, -sign, sign)
                    # The generator's family can belong to the positive class;
                    # another positive family may legitimately fit better.
                    self.assertTrue(any(
                        item.model == model and item.compatible is True
                        for item in result.candidate_models
                    ), result)
                    self.assertIn(
                        result.positive_model, {"quadratic", "cubic", "hinge1", "hinge2"},
                    )
                    self.assertLess(points[len(points) // 3][0], result.turn_at)
                    self.assertLess(result.turn_at, points[2 * len(points) // 3][0])
                    self.assertFalse(hasattr(result, "onset_at"))

    def test_value_sign_translation_scale_and_time_units_preserve_geometry(self):
        source = recovery_trace_points(degree=3)
        base = curve_form(source, policy=curve_policy(), form="recovery")
        self.assert_supported(base, -1, 1)
        origin = source[0][0]
        for value_scale, offset, time_scale, shift in (
            (1 / 2, trace(0)[0], 2, timedelta(days=1)),
            (-2, 2 * trace(0)[0], 1 / 2, -timedelta(days=1)),
        ):
            form = "recovery" if value_scale > 0 else "peak"
            with self.subTest(value_scale=value_scale, time_scale=time_scale):
                points = [
                    (origin + shift + (at - origin) * time_scale,
                     offset + value_scale * value)
                    for at, value in source
                ]
                result = curve_form(points, policy=curve_policy(), form=form)
                sign = 1 if value_scale > 0 else -1
                self.assert_supported(result, -sign, sign)
                self.assertEqual(result.positive_model, base.positive_model)
                self.assert_score_close(result.score_gain, base.score_gain)
                self.assert_score_close(
                    result.noise_scale, abs(value_scale) * base.noise_scale,
                )
                expected_turn = origin + shift + (base.turn_at - origin) * time_scale
                self.assertLessEqual(
                    abs(result.turn_at - expected_turn), 4 * timedelta.resolution,
                )

    def test_models_share_originals_noise_and_account_for_parameters_and_search(self):
        points = recovery_trace_points()
        calls = []
        fit = curves._fit

        def observed_fit(rows, values, noise, policy):
            calls.append((tuple(tuple(row) for row in rows), tuple(values), noise))
            return fit(rows, values, noise, policy)

        with patch.object(curves, "_fit", side_effect=observed_fit):
            result = curve_form(points, policy=curve_policy(), form="recovery")
        self.assert_supported(result, -1, 1)
        self.assertEqual(result.count, len(points))
        self.assertEqual(result.original_count, len(points))
        full_fits = [item for item in calls if len(item[1]) == len(points)]
        self.assertGreaterEqual(len(full_fits), len(result.candidate_models))
        _, first_values, first_noise = full_fits[0]
        original_times = next(
            tuple(row[1] for row in rows)
            for rows, _, _ in full_fits if len(rows[0]) > 1
        )
        self.assertEqual(first_noise, result.noise_scale)
        for rows, values, noise in full_fits:
            self.assertEqual(values, first_values)
            self.assertEqual(noise, first_noise)
            if len(rows[0]) > 1:
                self.assertEqual(
                    tuple(row[1] for row in rows), original_times,
                )
        hinges = [item for item in result.candidate_models if item.hinge_order]
        self.assertTrue(hinges)
        self.assertLessEqual(len(hinges), 2 * curve_policy().max_candidates)
        for item in result.candidate_models:
            with self.subTest(model=item.model, knot=item.knot_at):
                self.assert_score_close(
                    item.score,
                    item.loss + item.parameter_count * log(result.count) + item.search_cost,
                )
                if item.hinge_order:
                    self.assertEqual(item.parameter_count, item.coefficient_count + 1)
                    self.assertEqual(item.search_count, len(hinges))
                    self.assert_score_close(item.search_cost, 2 * log(len(hinges)))
                else:
                    self.assertEqual(item.parameter_count, item.coefficient_count)
                    self.assertEqual(item.search_cost, 0)
        self.assertTrue(any(
            item.compatible is True and item.model == result.positive_model
            and isclose(item.score, result.positive_score)
            for item in result.candidate_models
        ), result)
        self.assertTrue(any(
            item.compatible is False and item.model == result.counter_model
            and isclose(item.score, result.counter_score)
            for item in result.candidate_models
        ), result)

    def test_reference_stays_frozen_and_only_later_original_times_are_fresh(self):
        points = recovery_trace_points()
        initial = points[:-curve_policy().min_side_samples]
        first = curve_form(initial, policy=curve_policy(), form="recovery")
        self.assert_supported(first, -1, 1)
        self.assertEqual(first.fresh_original_count, 0)
        reference = first.reference
        self.assertEqual(reference.initial_newest_at, initial[-1][0])
        correction = list(initial)
        at, value = correction[-1]
        correction[-1] = (at, value + trace(151)[1] - trace(150)[1])
        for originals, expected_fresh in (
            (initial, 0),
            (correction, 0),
            (points, len(points) - len(initial)),
        ):
            with self.subTest(newest=originals[-1][0], fresh=expected_fresh):
                result = curve_form(
                    originals, policy=curve_policy(), form="recovery",
                    reference=reference,
                )
                self.assert_supported(result, -1, 1)
                self.assertEqual(result.reference, reference)
                self.assertEqual(result.turn_at, reference.turn_at)
                self.assertEqual(result.reference_evidence, "supports", result)
                self.assertEqual(result.fresh_original_count, expected_fresh)
                self.assert_score_close(
                    result.reference_score_gain,
                    result.counter_score - result.reference_score,
                )
                bound = result.reference_model
                self.assertEqual(bound.parameter_count, reference.parameter_count)
                self.assertEqual(bound.search_count, reference.search_count)
                self.assert_score_close(
                    bound.score,
                    bound.loss + reference.parameter_count * log(result.count)
                    + 2 * log(reference.search_count),
                )
                self.assert_score_close(result.reference_score, bound.score)
        self.assertIsNone(curve_form(
            points, policy=curve_policy(), form="recovery", reference=reference,
            current_not_before=reference.turn_at,
        ))

    def test_monotone_braking_and_isolated_excursion_do_not_supply_a_turn(self):
        source = level_trace_points()
        start, end = source[0][0], source[-1][0]
        span = (end - start).total_seconds()
        step = trace(70)[0] - trace(71)[0]
        variants = {"monotone": [], "continuing slower fall": []}
        for at, value in source:
            elapsed = (at - start).total_seconds()
            noise = value - trace(100)[1]
            variants["monotone"].append((at, trace(0)[0] - step * elapsed + noise))
            # Its derivative is strictly negative before the final endpoint;
            # flattening never supplies a positive observed recovery flank.
            variants["continuing slower fall"].append((
                at, trace(0)[0] + step * (span - elapsed)**2 / (2 * span) + noise,
            ))
        spike = list(source)
        middle = len(spike) // 2
        spike[middle] = (spike[middle][0], trace(69)[1])
        variants["one spike"] = spike
        for name, points in variants.items():
            for form in ("peak", "recovery"):
                with self.subTest(shape=name, form=form):
                    result = curve_form(points, policy=curve_policy(), form=form)
                    self.assertTrue(
                        result is None or result.evidence != "supports", result,
                    )
        resolved = curve_form(
            variants["monotone"], policy=curve_policy(), form="recovery",
        )
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.form_evidence, "rejects", resolved)
        # Missing and degenerate evidence remain distinct from that counterform.
        for unresolved in (
            source[:curve_policy().min_side_samples],
            level_trace_points(noisy=False),
        ):
            self.assertIsNone(curve_form(
                unresolved, policy=curve_policy(), form="recovery",
            ))

    def test_r03_and_r04_user_referenced_pulses_have_observed_peak_form(self):
        # Independent references: R03's single pulse near t730 and R04's first
        # pulse t10625–10650. These short windows assess form only; they do not
        # assert event counts, physical onsets, episode completion or contact.
        for recording, start, end in (
            ("R03", 720, 750),
            ("R04", 10620, 10650),
        ):
            with self.subTest(recording=recording):
                points = replay_slice(recording, start, end)
                self.assertGreaterEqual(len(points), 2 * curve_policy().min_side_samples)
                result = curve_form(points, policy=curve_policy(), form="peak")
                self.assert_supported(result, 1, -1)
                self.assertEqual(result.start_at, points[0][0])
                self.assertEqual(result.newest_at, points[-1][0])
                self.assertLess(points[0][0], result.turn_at)
                self.assertLess(result.turn_at, points[-1][0])
                self.assertEqual(result.original_count, len(points))


if __name__ == "__main__":
    unittest.main()
