"""Independent value, normalization and frozen-fit contracts for curve profiles."""

from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from math import fsum, isclose
import unittest
from unittest.mock import patch

from test_curve_change import curve_policy, level_trace_points, trace_points
from test_curve_forms import turning_shapes
from test_detector import trace
from test_foundation import T0

from custom_components.ha_sauna.core import curve_change as curves
from custom_components.ha_sauna.core.curve_change import (
    CurveFitSnapshot,
    CurveProfile,
    curve_change,
    curve_deviation,
    curve_form,
    curve_value,
)


def fixture_snapshot(profile, *, noise_scale):
    points = level_trace_points()
    return CurveFitSnapshot(
        profile=profile,
        time_origin=points[0][0],
        time_scale_seconds=(points[-1][0] - points[0][0]).total_seconds(),
        value_center=trace(0)[0],
        newest_at=points[-1][0],
        noise_scale=noise_scale,
    )


class CurveProfileTests(unittest.TestCase):
    def test_fixture_algebra_survives_time_and_value_transformations(self):
        source = level_trace_points()
        origin, newest = source[0][0], source[-1][0]
        middle = len(source) // 2
        quarter = middle // 2
        half_span = (newest - source[middle][0]).total_seconds()
        amplitude = (trace(70)[0] - trace(71)[0]) * half_span
        noise = trace(151)[1] - trace(150)[1]
        # Criteria: these are analytic shapes in u=(t-middle)/half_span.
        # The coefficients are their algebraic expansion in x=(u+1)/2.
        # Both hinges include a quadratic background to exercise every term.
        shapes = (
            (CurveProfile("constant", (amplitude,)), lambda u: 1),
            (CurveProfile("linear", (-amplitude, 2 * amplitude)), lambda u: u),
            (CurveProfile("quadratic", (amplitude, -4 * amplitude, 4 * amplitude)),
             lambda u: u**2),
            (CurveProfile("cubic", (
                -amplitude, 6 * amplitude, -12 * amplitude, 8 * amplitude,
            )), lambda u: u**3),
            (CurveProfile("hinge1", (
                2 * amplitude, -6 * amplitude, 4 * amplitude, 4 * amplitude,
            ), 1 / 2), lambda u: u**2 + abs(u)),
            (CurveProfile("hinge2", (
                2 * amplitude, -6 * amplitude, 4 * amplitude, 4 * amplitude,
            ), 1 / 4), lambda u: u**2 - u + (
                (u + 1 / 2)**2 if u > -1 / 2 else 0
            )),
        )
        times = [source[index][0] for index in (
            0, quarter - 1, quarter, quarter + 1,
            middle - 1, middle, middle + 1, len(source) - 1,
        )]
        # Include mathematical extrapolation as well as both sides of each knot.
        times.extend((origin - (newest - origin), newest + (newest - origin)))
        for profile, shape in shapes:
            for value_scale, offset, time_scale, shift in (
                (1, 0, 1, timedelta()),
                (1 / 2, trace(100)[1], 2, timedelta(days=1)),
                (-2, 2 * trace(0)[0], 1 / 2, -timedelta(days=1)),
            ):
                edited = replace(profile, coefficients=tuple(
                    value_scale * value for value in profile.coefficients
                ))
                snapshot = replace(
                    fixture_snapshot(edited, noise_scale=abs(value_scale) * noise),
                    time_origin=origin + shift,
                    time_scale_seconds=(newest - origin).total_seconds() * time_scale,
                    value_center=offset + value_scale * trace(0)[0],
                    newest_at=origin + shift + (newest - origin) * time_scale,
                )
                for at in times:
                    with self.subTest(model=profile.model, scale=value_scale, at=at):
                        u = (at - source[middle][0]).total_seconds() / half_span
                        expected = offset + value_scale * (
                            trace(0)[0] + amplitude * shape(u)
                        )
                        self.assertAlmostEqual(curve_value(
                            edited, origin + shift + (at - origin) * time_scale,
                            snapshot=snapshot,
                        ), expected)

    def test_explicit_edit_changes_values_without_mutating_profile_or_snapshot(self):
        step = trace(70)[0] - trace(71)[0]
        original = CurveProfile("quadratic", (step, -4 * step, 4 * step))
        snapshot = fixture_snapshot(original, noise_scale=step)
        edited = replace(original, coefficients=(5 * step, -4 * step, 4 * step))
        for at in (snapshot.time_origin, snapshot.newest_at):
            with self.subTest(at=at):
                self.assertAlmostEqual(
                    curve_value(original, at, snapshot=snapshot), trace(0)[0] + step,
                )
                self.assertAlmostEqual(
                    curve_value(edited, at, snapshot=snapshot), trace(0)[0] + 5 * step,
                )
        self.assertIs(snapshot.profile, original)
        self.assertEqual(original.coefficients, (step, -4 * step, 4 * step))
        with self.assertRaises(FrozenInstanceError):
            original.coefficients = edited.coefficients
        with self.assertRaises(FrozenInstanceError):
            snapshot.profile = edited
        with self.assertRaises(TypeError):
            original.coefficients[0] = 5 * step

    def test_deviation_keeps_observed_minus_expected_and_initial_noise(self):
        step = trace(70)[0] - trace(71)[0]
        noise = trace(151)[1] - trace(150)[1]
        profile = CurveProfile("linear", (0, step))
        snapshot = fixture_snapshot(profile, noise_scale=noise)
        at = snapshot.newest_at + (snapshot.newest_at - snapshot.time_origin)
        expected = trace(0)[0] + 2 * step
        # Criterion: signed errors of three supplied noise units, no event cutoff.
        for sign in (-1, 1):
            observed = expected + sign * 3 * noise
            result = curve_deviation(profile, (at, observed), snapshot=snapshot)
            self.assertAlmostEqual(result["expected"], expected)
            self.assertEqual(result["observed"], observed)
            self.assertAlmostEqual(result["residual"], sign * 3 * noise)
            self.assertAlmostEqual(result["normalized_residual"], sign * 3)
            self.assertEqual(result["noise_scale"], noise)
        zero = curve_deviation(
            profile, (snapshot.time_origin, trace(0)[0]), snapshot=snapshot,
        )
        self.assertEqual(zero["residual"], 0)
        self.assertEqual(zero["normalized_residual"], 0)
        for missing_noise in (None, 0):
            with self.subTest(noise=missing_noise):
                result = curve_deviation(
                    profile, (at, expected + 3 * noise),
                    snapshot=replace(snapshot, noise_scale=missing_noise),
                )
                self.assertAlmostEqual(result["residual"], 3 * noise)
                self.assertEqual(result["noise_scale"], missing_noise)
                self.assertIsNone(result["normalized_residual"])
        self.assertEqual(snapshot.noise_scale, noise)
        for invalid in (None, True, "70", float("nan"), float("inf")):
            with self.subTest(observed=invalid):
                self.assertIsNone(curve_deviation(
                    profile, (at, invalid), snapshot=snapshot,
                ))

    def test_actual_snapshots_keep_the_bound_curve_after_later_fits(self):
        policy = curve_policy()
        originals = trace_points(80)
        change = curve_change(
            originals, policy=policy, onset_at=T0 + timedelta(seconds=70),
            direction=-1,
        )
        self.assertIsNotNone(change)
        self.assertEqual(change.evidence, "supports", change)
        snapshot = change.fit
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.profile.model, "hinge1")
        self.assertEqual(snapshot.time_origin, originals[0][0])
        self.assertEqual(snapshot.newest_at, change.newest_at)
        self.assertEqual(snapshot.noise_scale, change.noise_scale)
        saved_change = replace(snapshot)
        saved_values = tuple(curve_value(snapshot.profile, at, snapshot=snapshot)
                             for at, _ in originals)
        for actual, (_, expected) in zip(saved_values, originals, strict=True):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(
            snapshot.time_origin + timedelta(
                seconds=snapshot.profile.knot_x * snapshot.time_scale_seconds,
            ), change.onset_at,
        )
        later = curve_change(
            trace_points(85), policy=policy, onset_at=change.onset_at, direction=-1,
        )
        self.assertIsNotNone(later)
        self.assertGreater(later.fit.newest_at, snapshot.newest_at)
        self.assertEqual(snapshot, saved_change)
        self.assertEqual(saved_values, tuple(
            curve_value(snapshot.profile, at, snapshot=snapshot) for at, _ in originals
        ))

        source = level_trace_points()
        center = trace(0)[0]
        old_peak = [(at, 2 * center - value)
                    for at, value in turning_shapes()["hinge1"]]
        # Existing structural V criterion: cover every original candidate time,
        # so moving one original changes the searched knot without tuning scores.
        policy = replace(policy, max_candidates=len(source))
        first = curve_form(old_peak, policy=policy, form="peak")
        self.assertIsNotNone(first)
        self.assertEqual(first.evidence, "supports", first)
        self.assertEqual(first.reference.model, "hinge1")
        bound = first.fit
        self.assertIsNotNone(bound)
        saved_form = replace(bound)
        saved_values = tuple(curve_value(bound.profile, at, snapshot=bound)
                             for at, _ in old_peak)
        shifted_turn = source[len(source) // 2 + 1][0]
        slope = trace(70)[0] - trace(71)[0]
        shifted_peak = [
            (at, center - slope * abs((at - shifted_turn).total_seconds())
             - (value - trace(100)[1]))
            for at, value in source
        ]
        result = curve_form(
            shifted_peak, policy=policy, form="peak", reference=first.reference,
        )
        self.assertIsNotNone(result)
        self.assertIsNotNone(result.fit)
        self.assertEqual(result.positive_model, "hinge1", result)
        self.assertEqual(result.count, len(shifted_peak))
        best = next(item for item in result.candidate_models if (
            item.model == result.positive_model and item.score == result.positive_score
        ))
        self.assertNotEqual(best.knot_at, first.reference.knot_at)
        fixed = result.fit
        self.assertEqual(fixed.profile.model, first.reference.model)
        self.assertEqual(
            fixed.time_origin + timedelta(
                seconds=fixed.profile.knot_x * fixed.time_scale_seconds,
            ), first.reference.knot_at,
        )
        # Independent definition of twice the standard Huber loss. Agreement
        # binds evaluated values to the fixed fit, rather than the free winner.
        ratios = [abs((value - curve_value(fixed.profile, at, snapshot=fixed))
                      / fixed.noise_scale) for at, value in shifted_peak]
        loss = fsum(
            ratio**2 if ratio <= policy.huber_delta
            else policy.huber_delta * (2 * ratio - policy.huber_delta)
            for ratio in ratios
        )
        # Roundoff criterion from the existing curve-form score comparisons.
        self.assertTrue(isclose(
            loss, result.reference_model.loss, rel_tol=1e-8, abs_tol=1e-8,
        ), (loss, result.reference_model))
        self.assertEqual(bound, saved_form)
        self.assertEqual(saved_values, tuple(
            curve_value(bound.profile, at, snapshot=bound) for at, _ in old_peak
        ))

    def test_evaluation_never_fits_and_invalid_inputs_stay_unresolved(self):
        step = trace(70)[0] - trace(71)[0]
        profile = CurveProfile("linear", (0, step))
        snapshot = fixture_snapshot(profile, noise_scale=step)
        with (
            patch.object(curves, "_fit", side_effect=AssertionError("no refit")),
            patch.object(
                curves, "_least_squares", side_effect=AssertionError("no fit"),
            ),
        ):
            self.assertAlmostEqual(curve_value(
                profile, snapshot.newest_at, snapshot=snapshot,
            ), trace(0)[0] + step)
            self.assertIsNotNone(curve_deviation(
                profile, (snapshot.newest_at, trace(0)[0]), snapshot=snapshot,
            ))
            for at in (None, "later", snapshot.newest_at.replace(tzinfo=None)):
                with self.subTest(at=at):
                    self.assertIsNone(curve_value(profile, at, snapshot=snapshot))
                    self.assertIsNone(curve_deviation(
                        profile, (at, trace(0)[0]), snapshot=snapshot,
                    ))
            for invalid_profile, invalid_snapshot in (
                (None, snapshot), (profile, None), (profile, profile),
            ):
                with self.subTest(profile=invalid_profile, snapshot=invalid_snapshot):
                    self.assertIsNone(curve_value(
                        invalid_profile, snapshot.newest_at, snapshot=invalid_snapshot,
                    ))
            for sample in (None, (), (snapshot.newest_at,),
                           (snapshot.newest_at, trace(0)[0], step)):
                with self.subTest(sample=sample):
                    self.assertIsNone(curve_deviation(
                        profile, sample, snapshot=snapshot,
                    ))
        for model, coefficients, knot in (
            ("unknown", (step,), None), ("linear", (step,), None),
            ("linear", [0, step], None), ("linear", (True, step), None),
            ("linear", (0, float("nan")), None),
            ("linear", (0, float("inf")), None),
            ("linear", (0, step), 1 / 2),
            ("hinge1", (0, step, 0, step), None),
            ("hinge2", (0, step, 0, step), float("nan")),
        ):
            with self.subTest(model=model, coefficients=coefficients, knot=knot):
                with self.assertRaises(ValueError):
                    CurveProfile(model, coefficients, knot)
        for changes in (
            {"profile": None}, {"time_origin": None},
            {"newest_at": snapshot.time_origin},
            {"newest_at": snapshot.newest_at.replace(tzinfo=None)},
            {"time_scale_seconds": 0}, {"time_scale_seconds": -step},
            {"time_scale_seconds": True}, {"time_scale_seconds": float("inf")},
            {"value_center": float("nan")}, {"noise_scale": -step},
            {"noise_scale": True}, {"noise_scale": float("inf")},
        ):
            with self.subTest(snapshot_changes=changes):
                with self.assertRaises(ValueError):
                    replace(snapshot, **changes)


if __name__ == "__main__":
    unittest.main()
