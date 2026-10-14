"""Stateless local curve evidence from distinct, causal original measurements.

The detector owns source continuity, candidate identity, and every event decision.
Here a quadratic with a hinge competes with a line, a smooth quadratic, and a
smooth cubic. All share one normal-consistent MAD noise scale and Huber loss.
The score uses BIC parameter cost and a separate cost for the searched knots;
it is local model evidence, not a calibrated event probability.
A separate fixed-tail comparison assesses a freely fitted level against the
same smooth trends. The detector owns tail selection and event completion.
Direction after a chosen knot is assessed separately on its actual right-hand
originals; a global hinge derivative cannot supply that observed direction.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import starmap
from math import fsum, isfinite, log, sqrt, ulp
from operator import mul
from statistics import NormalDist, median


_MAD_NORMALIZATION = 1 / NormalDist().inv_cdf(0.75)
_FLOAT_EPSILON = ulp(1.0)


@dataclass(frozen=True)
class CurvePolicy:
    """Numerical work limits supplied by the central parameter catalogue."""

    min_side_samples: int
    max_points: int
    max_candidates: int
    robust_iterations: int
    huber_delta: float


@dataclass(frozen=True)
class CurveFeatures:
    """Local evidence; slope units are input units per second.

    Positive slope_change means a more positive trend, even if both slopes
    remain negative. ``rejects`` is usable counterevidence at a fixed onset;
    None from curve_change means insufficient or degenerate observations.
    The three slopes describe the global hinge fit. after_trend independently
    describes the original observations strictly after its fixed onset. An
    absent after_trend is insufficient direction, while direction zero is level
    evidence; neither provides a direction from the global hinge derivative.
    """

    evidence: str
    onset_at: datetime
    newest_at: datetime
    before_count: int
    after_count: int
    noise_scale: float
    slope_before: float
    slope_after: float
    slope_change: float
    signed_strength: float
    smooth_score: float
    change_score: float
    score_gain: float
    smooth_model: str
    after_trend: CurveTrendFeatures | None = None


@dataclass(frozen=True)
class CurveTrendFeatures:
    """Direction supported by one supplied original segment, without an event.

    direction is -1 or +1 when a line is preferred to a level and its direction
    survives an influential-point check; zero means a level is preferred.
    Exact constant or machine-resolved collinear originals need no estimated
    noise or normalized scores. None from curve_trend remains insufficient.
    """

    direction: int
    slope: float
    noise_scale: float
    level_score: float | None
    trend_score: float | None
    score_gain: float | None
    exact_constant: bool
    exact_linear: bool


@dataclass(frozen=True)
class CurveLevelFeatures:
    """Observed level evidence on the supplied tail, without event semantics.

    ``supports`` means a level is preferred to the smooth trend family, or all
    original values are exactly equal. The latter has no normalized model
    scores: its noise_scale is zero and exact_constant explicitly records why
    it is usable. This is no claim that an unobserved physical trend is zero.
    """

    evidence: str
    start_at: datetime
    newest_at: datetime
    count: int
    noise_scale: float
    level: float
    level_score: float | None
    trend_score: float | None
    score_gain: float | None
    trend_model: str | None
    exact_constant: bool


def _policy_valid(policy):
    counts = (
        policy.min_side_samples, policy.max_points,
        policy.max_candidates, policy.robust_iterations,
    )
    return (
        all(isinstance(value, int) and not isinstance(value, bool) for value in counts)
        and policy.min_side_samples >= 3
        and policy.max_points >= 2 * policy.min_side_samples
        and policy.max_candidates >= 1
        and policy.robust_iterations >= 0
        and not isinstance(policy.huber_delta, bool)
        and isfinite(policy.huber_delta)
        and policy.huber_delta > 0
    )


def _indices(length, limit):
    """Keep actual points at deterministic positions, including both bounds."""
    if length <= limit:
        return list(range(length))
    if limit == 1:
        return [length - 1]
    return [index * (length - 1) // (limit - 1) for index in range(limit)]


def _sample_index_at(samples, at):
    left, right = 0, len(samples)
    while left < right:
        middle = (left + right) // 2
        if samples[middle][0] < at:
            left = middle + 1
        else:
            right = middle
    return left


def _bounded_samples(samples, limit, onset_at):
    """Keep original points nearest deterministic time nodes, plus a fixed onset."""
    pinned = _sample_index_at(samples, onset_at) if onset_at is not None else None
    if pinned is not None and (
        pinned == len(samples) or samples[pinned][0] != onset_at
    ):
        return None
    if len(samples) <= limit:
        return list(samples)
    count = limit - (pinned is not None)
    indices = {0, len(samples) - 1}
    start, end = samples[0][0], samples[-1][0]
    for node in range(1, count - 1):
        target = start + (end - start) * (node / (count - 1))
        right = min(_sample_index_at(samples, target), len(samples) - 1)
        left = max(0, right - 1)
        nearer_left = target - samples[left][0] <= samples[right][0] - target
        nearest = left if nearer_left else right
        indices.add(nearest)
    if pinned is not None:
        indices.add(pinned)
    return [samples[index] for index in sorted(indices)]


def _least_squares(rows, values, weights):
    """Small weighted QR with reorthogonalization and relative rank checks."""
    roots = [sqrt(weight) for weight in weights]
    target = [value * root for value, root in zip(values, roots, strict=True)]
    columns = [
        [row[index] * root for row, root in zip(rows, roots, strict=True)]
        for index in range(len(rows[0]))
    ]
    count = len(columns)
    upper = [[0.0] * count for _ in range(count)]
    orthogonal = []
    for index, column in enumerate(columns):
        original_norm = sqrt(fsum(map(mul, column, column)))
        for _ in range(2):
            for prior, basis in enumerate(orthogonal):
                projection = fsum(starmap(mul, zip(basis, column, strict=True)))
                upper[prior][index] += projection
                column = [
                    value - projection * basis_value
                    for value, basis_value in zip(column, basis, strict=True)
                ]
        norm = sqrt(fsum(map(mul, column, column)))
        if not isfinite(norm) or norm <= _FLOAT_EPSILON * len(rows) * original_norm:
            return None
        upper[index][index] = norm
        orthogonal.append([value / norm for value in column])
    projected = [
        fsum(starmap(mul, zip(basis, target, strict=True)))
        for basis in orthogonal
    ]
    result = [0.0] * count
    for index in reversed(range(count)):
        result[index] = (
            projected[index] - fsum(
                upper[index][following] * result[following]
                for following in range(index + 1, count)
            )
        ) / upper[index][index]
    return result if all(isfinite(value) for value in result) else None


def _residuals(rows, values, coefficients):
    return [
        value - fsum(starmap(mul, zip(row, coefficients, strict=True)))
        for row, value in zip(rows, values, strict=True)
    ]


def _fit(rows, values, noise, policy):
    weights = [1.0] * len(values)
    coefficients = None
    for iteration in range(policy.robust_iterations + 1):
        coefficients = _least_squares(rows, values, weights)
        if coefficients is None:
            return None
        residuals = _residuals(rows, values, coefficients)
        if iteration != policy.robust_iterations:
            weights = [
                policy.huber_delta / ratio if ratio > policy.huber_delta else 1.0
                for ratio in (abs(residual / noise) for residual in residuals)
            ]
    # Twice the standard Huber loss equals squared normalized residuals in its
    # quadratic part. Its scale is identical for every competing model.
    normalized = [abs(residual / noise) for residual in residuals]
    loss = fsum(
        residual * residual if residual <= policy.huber_delta
        else 2 * policy.huber_delta * residual - policy.huber_delta**2
        for residual in normalized
    )
    return (coefficients, loss) if isfinite(loss) else None


def _models(times, values, noise, knots, policy):
    linear = [[1.0, time] for time in times]
    quadratic = [[1.0, time, time * time] for time in times]
    cubic = [[1.0, time, time * time, time**3] for time in times]
    smooth = []
    for name, rows in (
        ("linear", linear), ("quadratic", quadratic), ("cubic", cubic),
    ):
        fitted = _fit(rows, values, noise, policy)
        if fitted is None:
            return None
        smooth.append((
            fitted[1] + len(rows[0]) * log(len(values)), name,
            _residuals(rows, values, fitted[0]),
        ))
    smooth_score, smooth_model, residuals = min(smooth, key=lambda item: item[:2])
    changes = []
    for index, knot in knots:
        rows = [row + [max(0.0, time - knot)] for row, time in zip(
            quadratic, times, strict=True
        )]
        fitted = _fit(rows, values, noise, policy)
        if fitted is None:
            continue
        # Five model parameters include the fitted hinge time. The additional
        # 2 log(m) cost explicitly accounts for the discrete search over m knots.
        score = fitted[1] + 5 * log(len(values))
        changes.append((score, index, fitted[0]))
    if not changes:
        return None
    return smooth_score, smooth_model, residuals, changes


def _supported_without_point(times, values, noise, index, residuals, penalty, policy):
    """One bounded refit rejects evidence that rests on one influential value."""
    removed = max(range(len(values)), key=lambda point: (abs(residuals[point]), point))
    knot = times[index]
    retained = [point for point in range(len(values)) if point != removed]
    before = sum(times[point] <= knot for point in retained)
    if min(before, len(retained) - before) < policy.min_side_samples:
        return None
    result = _models(
        [times[point] for point in retained],
        [values[point] for point in retained], noise, [(index, knot)], policy,
    )
    if result is None:
        return None
    smooth_score, _, _, changes = result
    score, _, coefficients = changes[0]
    resolution = len(values) * ulp(max(abs(smooth_score), abs(score + penalty)))
    return smooth_score - score - penalty > resolution, coefficients[3]


def _level_models(times, raw_values, policy):
    """Compare a level and smooth trends with one tail-local noise scale."""
    center = median(raw_values)
    values = [value - center for value in raw_values]
    if not all(isfinite(value) for value in values):
        return None
    cubic = [[1.0, time, time * time, time**3] for time in times]
    preliminary = _least_squares(cubic, values, [1.0] * len(values))
    if preliminary is None:
        return None
    residuals = _residuals(cubic, values, preliminary)
    residual_center = median(residuals)
    noise = _MAD_NORMALIZATION * median(
        abs(residual - residual_center) for residual in residuals
    )
    resolution = len(values) * ulp(max(abs(value) for value in raw_values))
    if not isfinite(noise) or noise <= resolution:
        return None
    fitted_models = []
    for name, degree in (
        ("constant", 0), ("linear", 1), ("quadratic", 2), ("cubic", 3),
    ):
        rows = [row[:degree + 1] for row in cubic]
        fitted = _fit(rows, values, noise, policy)
        if fitted is None:
            return None
        fitted_models.append((
            fitted[1] + (degree + 1) * log(len(values)), name, fitted[0],
        ))
    level_score, _, coefficients = fitted_models[0]
    trend_score, trend_model, _ = min(
        fitted_models[1:], key=lambda item: item[:2],
    )
    score_gain = trend_score - level_score
    score_resolution = len(values) * ulp(max(abs(level_score), abs(trend_score)))
    level = center + coefficients[0]
    if not isfinite(level) or abs(score_gain) <= score_resolution:
        return None
    return (
        noise, level, level_score, trend_score, score_gain, trend_model, residuals,
    )


def curve_level(
    samples: Sequence[tuple[datetime, float]], *, policy: CurvePolicy,
) -> CurveLevelFeatures | None:
    """Assess one fixed recent tail without searching for a favorable plateau.

    The caller supplies a coherent post-peak original source segment, chooses
    its duration, and owns the preceding decline and later fresh confirmation.
    All supplied originals are validated before bounded numerical sampling.
    No earlier baseline level or event amplitude enters the comparison.

    Nonconstant tails use a tail-local cubic-residual MAD, shared Huber loss and
    BIC costs for a constant, line, quadratic and cubic. Support must survive
    one influential-point removal with its own re-estimated common noise scale.
    Unresolved nonconstant noise or fragile support returns None; ``rejects``
    means a smooth trend is preferred. Exact equality is checked on every
    supplied original, so downsampling cannot erase an excursion into a level.
    """
    try:
        if not _policy_valid(policy) or len(samples) < 2 * policy.min_side_samples:
            return None
        stamps = [sample[0] for sample in samples]
        raw_values = [sample[1] for sample in samples]
        if any(not isinstance(stamp, datetime) for stamp in stamps):
            return None
        if any(a >= b for a, b in zip(stamps, stamps[1:])):
            return None
        if any(isinstance(value, bool) or not isfinite(value) for value in raw_values):
            return None
        span = (stamps[-1] - stamps[0]).total_seconds()
        if not isfinite(span) or span <= 0:
            return None
        selected = _bounded_samples(samples, policy.max_points, None)
        if len(selected) < 2 * policy.min_side_samples:
            return None
        if all(value == raw_values[0] for value in raw_values):
            return CurveLevelFeatures(
                evidence="supports", start_at=stamps[0], newest_at=stamps[-1],
                count=len(selected), noise_scale=0.0, level=raw_values[0],
                level_score=None, trend_score=None, score_gain=None,
                trend_model=None, exact_constant=True,
            )
        times = [(sample[0] - stamps[0]).total_seconds() / span for sample in selected]
        values = [sample[1] for sample in selected]
        result = _level_models(times, values, policy)
        if result is None:
            return None
        (
            noise, level, level_score, trend_score, score_gain, trend_model, residuals,
        ) = result
        supported = score_gain > 0
        if supported:
            removed = max(range(len(values)), key=lambda point: (
                abs(residuals[point]), point,
            ))
            retained = [point for point in range(len(values)) if point != removed]
            if len(retained) < 2 * policy.min_side_samples:
                return None
            rescue = _level_models(
                [times[point] for point in retained],
                [values[point] for point in retained], policy,
            )
            if rescue is None or rescue[4] <= 0:
                return None
        return CurveLevelFeatures(
            evidence="supports" if supported else "rejects",
            start_at=stamps[0], newest_at=stamps[-1], count=len(selected),
            noise_scale=noise, level=level, level_score=level_score,
            trend_score=trend_score, score_gain=score_gain,
            trend_model=trend_model, exact_constant=False,
        )
    except (
        AttributeError, IndexError, OverflowError, TypeError, ValueError,
        ZeroDivisionError,
    ):
        return None


def _trend_models(samples, policy):
    """Compare a level and line with noise from these originals alone."""
    stamps = [sample[0] for sample in samples]
    raw_values = [sample[1] for sample in samples]
    span = (stamps[-1] - stamps[0]).total_seconds()
    resolution = len(samples) * ulp(max(abs(value) for value in raw_values))
    delta = raw_values[-1] - raw_values[0]
    if all(value == raw_values[0] for value in raw_values):
        return CurveTrendFeatures(
            direction=0, slope=0.0, noise_scale=0.0,
            level_score=None, trend_score=None, score_gain=None,
            exact_constant=True, exact_linear=False,
        ), None, None
    # Check every original before downsampling. An omitted excursion must not
    # turn quantized observations into an exact line or an exact constant.
    collinear = isfinite(delta) and abs(delta) > resolution and all(
        abs((value - raw_values[0]) - delta * (
            (stamp - stamps[0]).total_seconds() / span
        )) <= resolution for stamp, value in samples
    )
    # Removing either endpoint must still leave resolved change. Otherwise a
    # lone value after a long reporting gap could supply the entire direction.
    endpoint_change = min(
        (raw_values[-1] - raw_values[1]) * (1 if delta > 0 else -1),
        (raw_values[-2] - raw_values[0]) * (1 if delta > 0 else -1),
    )
    if collinear and endpoint_change > resolution:
        slope = delta / span
        if not isfinite(slope) or slope == 0:
            return None
        return CurveTrendFeatures(
            direction=1 if delta > 0 else -1, slope=slope, noise_scale=0.0,
            level_score=None, trend_score=None, score_gain=None,
            exact_constant=False, exact_linear=True,
        ), None, None
    selected = _bounded_samples(samples, policy.max_points, None)
    if len(selected) < policy.min_side_samples:
        return None
    values = [sample[1] for sample in selected]
    center = median(values)
    centered = [value - center for value in values]
    if not all(isfinite(value) for value in centered):
        return None
    rows = [[1.0, (stamp - stamps[0]).total_seconds() / span]
            for stamp, _ in selected]
    preliminary = _least_squares(rows, centered, [1.0] * len(centered))
    if preliminary is None:
        return None
    residuals = _residuals(rows, centered, preliminary)
    residual_center = median(residuals)
    noise = _MAD_NORMALIZATION * median(
        abs(residual - residual_center) for residual in residuals
    )
    resolution = len(selected) * ulp(max(abs(value) for value in values))
    if not isfinite(noise) or noise <= resolution:
        return None
    level = _fit([[1.0] for _ in selected], centered, noise, policy)
    trend = _fit(rows, centered, noise, policy)
    if level is None or trend is None:
        return None
    level_score = level[1] + log(len(selected))
    trend_score = trend[1] + 2 * log(len(selected))
    score_gain = level_score - trend_score
    score_resolution = len(selected) * ulp(max(abs(level_score), abs(trend_score)))
    slope = trend[0][1] / span
    if not isfinite(slope) or abs(score_gain) <= score_resolution:
        return None
    if score_gain > 0 and abs(trend[0][1]) <= resolution:
        return None
    times = [row[1] for row in rows]
    time_center = fsum(times) / len(times)
    time_spread = fsum((time - time_center)**2 for time in times)
    if not isfinite(time_spread) or time_spread <= 0:
        return None
    influences = []
    for time, residual in zip(times, residuals, strict=True):
        # A high-leverage endpoint can have a tiny fitted residual although
        # removing it changes the entire direction. External deletion residuals
        # r/(1-h) account for that influence without another fit per point.
        remaining = fsum((
            1.0, -1 / len(times), -(time - time_center)**2 / time_spread,
        ))
        unresolved = remaining <= len(times) * _FLOAT_EPSILON
        influences.append((
            unresolved, 0.0 if unresolved else abs(residual) / remaining,
        ))
    return CurveTrendFeatures(
        direction=(1 if slope > 0 else -1) if score_gain > 0 else 0,
        slope=slope, noise_scale=noise,
        level_score=level_score, trend_score=trend_score, score_gain=score_gain,
        exact_constant=False, exact_linear=False,
    ), selected, influences


def curve_trend(
    samples: Sequence[tuple[datetime, float]], *, policy: CurvePolicy,
) -> CurveTrendFeatures | None:
    """Assess one fixed original segment, without selecting a favorable tail.

    A common linear-residual MAD and Huber loss compare a level and a line.
    The extra line coefficient pays its BIC cost. Directed evidence must survive
    one influential-point removal with newly estimated common noise; exact
    collinearity also retains resolved change after either endpoint is removed.
    The omission uses leverage-corrected linear residuals. No older hinge
    residuals, physical amplitude limits, or additional knots enter here.
    """
    try:
        if not _policy_valid(policy) or len(samples) < policy.min_side_samples:
            return None
        stamps = [sample[0] for sample in samples]
        if any(not isinstance(stamp, datetime) for stamp in stamps):
            return None
        if any(a >= b for a, b in zip(stamps, stamps[1:])):
            return None
        if any(isinstance(value, bool) or not isfinite(value) for _, value in samples):
            return None
        span = (stamps[-1] - stamps[0]).total_seconds()
        if not isfinite(span) or span <= 0:
            return None
        result = _trend_models(samples, policy)
        if result is None:
            return None
        features, selected, influences = result
        if features.direction and not features.exact_linear:
            removed = max(range(len(selected)), key=lambda point: (
                influences[point], point,
            ))
            retained = [
                sample for point, sample in enumerate(selected) if point != removed
            ]
            if len(retained) < policy.min_side_samples:
                return None
            rescue = _trend_models(retained, policy)
            if rescue is None or rescue[0].direction != features.direction:
                return None
        return features
    except (
        AttributeError, IndexError, OverflowError, TypeError, ValueError,
        ZeroDivisionError,
    ):
        return None


def curve_change(
    samples: Sequence[tuple[datetime, float]], *, policy: CurvePolicy,
    onset_at: datetime | None = None,
    direction: int | None = None,
    onset_not_before: datetime | None = None,
) -> CurveFeatures | None:
    """Compare local smooth and changed curves without retaining any state.

    The caller supplies one coherent original source segment in strictly
    increasing time order. Downsampling keeps original observations and bounds
    the numerical work; it never creates interpolated evidence. A supplied onset
    is re-evaluated without moving it. None cannot reject a detector candidate.

    direction restricts the sign of the slope change to -1 or +1. An inclusive
    onset_not_before restricts candidate knots before the search budget is
    applied; earlier original observations still support every fit and its noise
    scale. Fixed-onset and replacement comparisons use the same query limits.
    A supported hinge remains usable when its separate right-hand direction is
    insufficient; the caller must require after_trend for a directed route.
    """
    try:
        if direction is not None and (
            isinstance(direction, bool) or not isinstance(direction, int)
            or direction not in (-1, 1)
        ):
            return None
        if onset_not_before is not None and not isinstance(onset_not_before, datetime):
            return None
        if not _policy_valid(policy) or len(samples) < 2 * policy.min_side_samples:
            return None
        selected = _bounded_samples(samples, policy.max_points, onset_at)
        if selected is None or len(selected) < 2 * policy.min_side_samples:
            return None
        stamps = [sample[0] for sample in selected]
        if any(not isinstance(stamp, datetime) for stamp in stamps):
            return None
        if any(a >= b for a, b in zip(stamps, stamps[1:])):
            return None
        raw_values = [sample[1] for sample in selected]
        if any(isinstance(value, bool) or not isfinite(value) for value in raw_values):
            return None
        span = (stamps[-1] - stamps[0]).total_seconds()
        if not isfinite(span) or span <= 0:
            return None
        times = [(stamp - stamps[0]).total_seconds() / span for stamp in stamps]
        center = median(raw_values)
        values = [value - center for value in raw_values]
        if not all(isfinite(value) for value in values):
            return None
        cubic = [[1.0, time, time * time, time**3] for time in times]
        preliminary = _least_squares(cubic, values, [1.0] * len(values))
        if preliminary is None:
            return None
        residuals = _residuals(cubic, values, preliminary)
        residual_center = median(residuals)
        noise = _MAD_NORMALIZATION * median(
            abs(residual - residual_center) for residual in residuals
        )
        # A zero/numerically unresolved MAD provides no defensible noise ratio.
        # This is a floating-point resolution check, never a physical noise floor.
        resolution = len(values) * ulp(max(abs(value) for value in raw_values))
        if not isfinite(noise) or noise <= resolution:
            return None
        support_boundaries = (
            policy.min_side_samples - 1, len(times) - policy.min_side_samples - 1,
        )
        eligible = [index for index in range(
            policy.min_side_samples - 1, len(times) - policy.min_side_samples
        ) if onset_not_before is None or stamps[index] >= onset_not_before]
        if not eligible:
            return None
        searched = [eligible[index] for index in _indices(
            len(eligible), policy.max_candidates
        )]
        if onset_at is not None:
            index = stamps.index(onset_at)
            if index not in eligible:
                return None
            if index not in searched:
                if len(searched) == policy.max_candidates:
                    nearest = min(range(len(searched)), key=lambda point: (
                        abs(times[searched[point]] - times[index]), point,
                    ))
                    searched[nearest] = index
                else:
                    searched.append(index)
                searched.sort()
        knots = [(index, times[index]) for index in searched]
        result = _models(times, values, noise, knots, policy)
        if result is None:
            return None
        smooth_score, smooth_model, residuals, changes = result
        compatible = [
            item for item in changes
            if direction is None or item[2][3] * direction > 0
        ]
        if onset_at is None:
            if not compatible:
                return None
            change_score, index, coefficients = min(
                compatible, key=lambda item: item[:2],
            )
        else:
            fitted = next((item for item in changes if item[1] == index), None)
            if fitted is None:
                return None
            change_score, index, coefficients = fitted
        search_penalty = 2 * log(len(searched))
        change_score += search_penalty
        score_gain = smooth_score - change_score
        score_resolution = len(values) * ulp(max(abs(smooth_score), abs(change_score)))
        if abs(score_gain) <= score_resolution:
            return None
        supported = score_gain > score_resolution and (
            direction is None or coefficients[3] * direction > 0
        )
        if supported and onset_at is not None:
            best_score = min(item[0] for item in compatible)
            # A newly optimized knot must repay its BIC time-parameter cost and
            # the same discrete-search cost before displacing a frozen onset.
            # Tiny movements of the optimum are not counterevidence, while a
            # distinctly better later change cannot legitimize an old onset.
            replacement_penalty = log(len(values)) + search_penalty
            if change_score - search_penalty - best_score > (
                replacement_penalty + score_resolution
            ):
                supported = False
        # A best knot at an original support boundary is not temporally
        # bracketed; it cannot supply a precise earlier candidate beginning.
        if supported and index in support_boundaries:
            return None
        if supported:
            rescue = _supported_without_point(
                times, values, noise, index, residuals, search_penalty, policy,
            )
            if rescue is None or not rescue[0] or rescue[1] * coefficients[3] <= 0:
                return None
        slope_before = (coefficients[1] + 2 * coefficients[2] * times[index]) / span
        slope_change = coefficients[3] / span
        slope_after = slope_before + slope_change
        strength = coefficients[3] * (times[-1] - times[index]) / noise
        finite = (
            slope_before, slope_after, slope_change, strength,
            smooth_score, change_score,
        )
        if not all(isfinite(value) for value in finite):
            return None
        after_trend = None
        if supported:
            after_trend = curve_trend(
                [sample for sample in samples if sample[0] > stamps[index]],
                policy=policy,
            )
        return CurveFeatures(
            evidence="supports" if supported else "rejects",
            onset_at=stamps[index], newest_at=stamps[-1],
            before_count=index + 1, after_count=len(times) - index - 1,
            noise_scale=noise, slope_before=slope_before,
            slope_after=slope_after, slope_change=slope_change,
            signed_strength=strength, smooth_score=smooth_score,
            change_score=change_score, score_gain=score_gain,
            smooth_model=smooth_model, after_trend=after_trend,
        )
    except (
        AttributeError, IndexError, OverflowError, TypeError, ValueError,
        ZeroDivisionError,
    ):
        # Bad/degenerate local windows must not interrupt the product loop.
        return None
