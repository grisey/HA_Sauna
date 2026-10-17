"""Algebraic and causal contracts on existing curve-fixture original times."""

from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from math import fsum
import unittest
from unittest.mock import patch

from test_curve_change import curve_policy, level_trace_points
from test_detector import trace

from custom_components.ha_sauna.core import curve_change as curves
from custom_components.ha_sauna.core.curve_change import (
    CurveCalibrationExample,
    CurveCalibrationPolicy,
    bind_curve_template,
    curve_template_value,
    fit_curve_calibration,
    score_curve_continuation,
)


CHANNELS = (
    "upper_temperature",
    "upper_humidity",
    "lower_temperature",
    "lower_humidity",
)
GAINS = (1, 1 / 2, 3 / 4, 1 / 4)
LAGS = (0, 1 / 4, -1 / 4, 1 / 2)


def calibration_policy():
    numerical = curve_policy()
    noise = trace(151)[1] - trace(150)[1]
    return CurveCalibrationPolicy(
        curve=numerical,
        channels=CHANNELS,
        noise_floors=(noise,) * len(CHANNELS),
        min_examples=1,
        min_continuation_samples=numerical.min_side_samples,
        residual_quantile=1,
        residual_limit_multiplier=2,
        residual_loss_floor=1,
        minimum_gain=0,
        max_phase_x=1,
    )


def known_shape(basis, x):
    """Independent zero-origin forms with reference value -1 at x=1."""
    if basis == "P2":
        return -2 * x + x * x
    if basis == "P3":
        return -3 * x + 3 * x * x - x**3
    if basis == "M1":
        return -3 * x + x * x + 2 * max(0, x - 1 / 2)
    return -3 * x + x * x + 4 * max(0, x - 1 / 2) ** 2


def calibration_example(basis="P2", *, amplitude_scale=1, shift=timedelta()):
    """Use the existing plateau's actual time grid and fixture-derived units.

    Known affine baselines and four relative shapes are mathematical test
    criteria, not event labels or newly recorded measurements. Relative flank
    timing is encoded in x*(1-x), within every tested basis family.
    """
    source = level_trace_points(noisy=False)
    onset = source[len(source) // 4][0]
    binding_until = source[len(source) // 2][0]
    duration = (source[-1][0] - onset).total_seconds()
    amplitude = amplitude_scale * (trace(70)[0] - trace(85)[0])
    slope_unit = trace(280)[0] - trace(281)[0]
    channels = []
    for index, name in enumerate(CHANNELS):
        level = trace(0)[index % 2]
        slope = slope_unit / (index + 1)
        points = []
        for at, _ in source:
            seconds = (at - onset).total_seconds()
            x = seconds / duration
            shape = (
                GAINS[index] * known_shape(basis, x) + LAGS[index] * x * (1 - x)
                if seconds >= 0
                else 0
            )
            points.append((at + shift, level + slope * seconds + amplitude * shape))
        channels.append((name, tuple(points)))
    return (
        CurveCalibrationExample(
            onset + shift,
            binding_until + shift,
            tuple(channels),
        ),
        duration,
        amplitude,
    )


def split_example(example):
    before, prefix, later = [], [], []
    for name, samples in example.channels:
        before.append(
            (name, tuple(sample for sample in samples if sample[0] < example.onset_at))
        )
        prefix.append(
            (
                name,
                tuple(
                    sample
                    for sample in samples
                    if example.onset_at <= sample[0] <= example.binding_until
                ),
            )
        )
        later.append(
            (
                name,
                tuple(
                    sample for sample in samples if sample[0] > example.binding_until
                ),
            )
        )
    return tuple(before), tuple(prefix), tuple(later)


def learned_profile(basis="P2", *, policy=None):
    first, duration, _ = calibration_example(basis)
    second, _, _ = calibration_example(
        basis,
        amplitude_scale=2,
        shift=timedelta(days=1),
    )
    return fit_curve_calibration(
        (first, second),
        policy=policy or calibration_policy(),
        duration_seconds=duration,
        reference_channel=CHANNELS[0],
        reference_sign=-1,
        basis=basis,
        knot_x=1 / 2 if basis.startswith("M") else None,
    )


class CurveCalibrationTests(unittest.TestCase):
    def test_all_declared_bases_learn_known_multichannel_algebra(self):
        for basis in ("P2", "P3", "M1", "M2"):
            with self.subTest(basis=basis):
                example, duration, amplitude = calibration_example(basis)
                profile = learned_profile(basis)
                self.assertIsNotNone(profile)
                self.assertEqual(profile.duration_seconds, duration)
                self.assertEqual(profile.calibration_count, 2)
                before, prefix, later = split_example(example)
                binding = bind_curve_template(
                    profile,
                    before,
                    prefix,
                    onset_at=example.onset_at,
                    binding_until=example.binding_until,
                )
                self.assertIsNotNone(binding)
                self.assertAlmostEqual(binding.amplitude, amplitude)
                for index, (name, samples) in enumerate(example.channels):
                    baseline = binding.channels[index]
                    self.assertAlmostEqual(baseline.level, trace(0)[index % 2])
                    self.assertAlmostEqual(
                        baseline.slope,
                        (trace(280)[0] - trace(281)[0]) / (index + 1),
                    )
                    for at, observed in samples:
                        if at >= example.onset_at:
                            self.assertAlmostEqual(
                                curve_template_value(binding, name, at),
                                observed,
                            )
                    # This endpoint criterion fixes common amplitude units.
                    at = example.onset_at + timedelta(seconds=duration)
                    reference_delta = curve_template_value(
                        binding, name, at
                    ) - curve_template_value(binding, name, at, null=True)
                    self.assertAlmostEqual(reference_delta, -amplitude * GAINS[index])
                score = score_curve_continuation(binding, later)
                self.assertIsNotNone(score)
                self.assertEqual(score.status, "supports")
                self.assertAlmostEqual(score.template_loss, 0)
                self.assertGreater(score.score_gain, 0)

    def test_binding_and_continuation_cannot_refit_from_future_originals(self):
        profile = learned_profile()
        example, _, _ = calibration_example()
        before, prefix, later = split_example(example)
        binding = bind_curve_template(
            profile,
            before,
            prefix,
            onset_at=example.onset_at,
            binding_until=example.binding_until,
        )
        saved = replace(binding)
        predicted = tuple(
            curve_template_value(binding, name, samples[-1][0])
            for name, samples in later
        )
        altered = tuple(
            (name, tuple((at, value + 2 * binding.amplitude) for at, value in samples))
            for name, samples in later
        )
        with (
            patch.object(curves, "_fit", side_effect=AssertionError("frozen fit")),
            patch.object(
                curves, "_least_squares", side_effect=AssertionError("frozen QR")
            ),
        ):
            good = score_curve_continuation(binding, later)
            bad = score_curve_continuation(binding, altered)
        self.assertEqual(good.status, "supports")
        self.assertEqual(bad.status, "rejects")
        self.assertEqual(binding, saved)
        self.assertEqual(
            predicted,
            tuple(
                curve_template_value(binding, name, samples[-1][0])
                for name, samples in altered
            ),
        )
        with self.assertRaises(FrozenInstanceError):
            binding.amplitude = binding.amplitude * 2
        contaminated_pre = tuple(
            (name, (*samples, prefix[index][1][0]))
            for index, (name, samples) in enumerate(before)
        )
        self.assertIsNone(
            bind_curve_template(
                profile,
                contaminated_pre,
                prefix,
                onset_at=example.onset_at,
                binding_until=example.binding_until,
            )
        )
        self.assertIsNone(
            bind_curve_template(
                profile,
                before,
                tuple(
                    (name, (*samples, later[index][1][0]))
                    for index, (name, samples) in enumerate(prefix)
                ),
                onset_at=example.onset_at,
                binding_until=example.binding_until,
            )
        )

    def test_loss_uses_frozen_noise_same_null_and_each_channel_band(self):
        profile = learned_profile()
        example, _, _ = calibration_example()
        before, prefix, later = split_example(example)
        binding = bind_curve_template(
            profile,
            before,
            prefix,
            onset_at=example.onset_at,
            binding_until=example.binding_until,
        )
        # Most channels still match. One channel violates its learned band;
        # a large overall gain alone must not supply support.
        index = len(CHANNELS) - 1
        noise = binding.channels[index].noise_scale
        offset = 2 * profile.policy.curve.huber_delta * noise
        changed = tuple(
            (
                name,
                tuple(
                    (at, value + offset if i == index else value)
                    for at, value in samples
                ),
            )
            for i, (name, samples) in enumerate(later)
        )
        score = score_curve_continuation(binding, changed)
        self.assertIsNotNone(score)
        self.assertGreater(score.score_gain, profile.policy.minimum_gain)
        self.assertFalse(score.channels[index].within_residual_limit)
        self.assertEqual(score.status, "rejects")
        for i, (name, samples) in enumerate(changed):
            ratios = [
                (observed - curve_template_value(binding, name, at))
                / binding.channels[i].noise_scale
                for at, observed in samples
            ]
            delta = profile.policy.curve.huber_delta
            expected_loss = fsum(
                ratio**2 if abs(ratio) <= delta else delta * (2 * abs(ratio) - delta)
                for ratio in ratios
            ) / len(ratios)
            self.assertAlmostEqual(score.channels[i].template_loss, expected_loss)
            self.assertAlmostEqual(
                score.channels[i].score_gain,
                score.channels[i].null_loss - score.channels[i].template_loss,
            )
        self.assertAlmostEqual(
            score.template_loss,
            fsum(channel.template_loss for channel in score.channels) / len(CHANNELS),
        )

    def test_training_residual_bands_use_prefix_binding_and_declared_quantile(self):
        example, duration, _ = calibration_example()
        before, prefix, later = split_example(example)
        policy = replace(calibration_policy(), residual_loss_floor=1 / 2)
        # A non-polynomial balanced fixture perturbation makes the band data
        # dependent. The noise floor and the prefix remain independently fixed.
        noisy = dict(level_trace_points())
        center = trace(100)[1]
        altered = CurveCalibrationExample(
            example.onset_at,
            example.binding_until,
            tuple(
                (
                    name,
                    tuple(
                        (
                            at,
                            value + (noisy[at] - center)
                            if at > example.binding_until
                            else value,
                        )
                        for at, value in samples
                    ),
                )
                for name, samples in example.channels
            ),
        )
        profile = fit_curve_calibration(
            (example, altered),
            policy=policy,
            duration_seconds=duration,
            reference_channel=CHANNELS[0],
            reference_sign=-1,
            basis="P2",
        )
        self.assertIsNotNone(profile)
        training_scores = []
        for originals in (example, altered):
            before, prefix, later = split_example(originals)
            binding = bind_curve_template(
                profile,
                before,
                prefix,
                onset_at=originals.onset_at,
                binding_until=originals.binding_until,
            )
            training_scores.append(score_curve_continuation(binding, later))
        self.assertAlmostEqual(
            profile.residual_limit,
            policy.residual_limit_multiplier
            * max(
                policy.residual_loss_floor,
                *(score.template_loss for score in training_scores),
            ),
        )
        for index, channel in enumerate(profile.channels):
            self.assertAlmostEqual(
                channel.residual_limit,
                policy.residual_limit_multiplier
                * max(
                    policy.residual_loss_floor,
                    *(score.channels[index].template_loss for score in training_scores),
                ),
            )

    def test_duplicate_prefix_future_and_nonfinite_inputs_stay_unresolved(self):
        profile = learned_profile()
        example, duration, _ = calibration_example()
        before, prefix, later = split_example(example)
        binding = bind_curve_template(
            profile,
            before,
            prefix,
            onset_at=example.onset_at,
            binding_until=example.binding_until,
        )
        for invalid in (
            tuple((name, (*samples[:1], *samples)) for name, samples in later),
            tuple(
                (name, (prefix[index][1][-1], *samples))
                for index, (name, samples) in enumerate(later)
            ),
            tuple(
                (name, ((samples[0][0], float("nan")), *samples[1:]))
                for name, samples in later
            ),
            tuple(
                (name, samples[: profile.policy.min_continuation_samples - 1])
                for name, samples in later
            ),
        ):
            self.assertIsNone(score_curve_continuation(binding, invalid))
        beyond = example.onset_at + timedelta(seconds=duration) + timedelta.resolution
        self.assertIsNone(curve_template_value(binding, CHANNELS[0], beyond))
        self.assertIsNone(
            fit_curve_calibration(
                (example,),
                policy=profile.policy,
                duration_seconds=duration,
                reference_channel=CHANNELS[0],
                reference_sign=-1,
                basis="M1",
            )
        )
        for changes in (
            {"residual_quantile": 0},
            {"minimum_gain": float("inf")},
            {"noise_floors": (0,) * len(CHANNELS)},
            {"min_examples": True},
            {"channels": (CHANNELS[0],) * len(CHANNELS)},
        ):
            with self.assertRaises(ValueError):
                replace(profile.policy, **changes)
        with self.assertRaises(ValueError):
            replace(binding, amplitude=float("nan"))
        with self.assertRaises(ValueError):
            replace(profile, duration_seconds=0)


if __name__ == "__main__":
    unittest.main()
