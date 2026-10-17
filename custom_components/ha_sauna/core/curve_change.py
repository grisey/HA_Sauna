"""Stateless local curve evidence from distinct, causal original measurements.

The detector owns source continuity, candidate identity, and every event decision.
Here a quadratic with an explicitly selected first- or second-order hinge
competes with a line, a smooth quadratic, and a smooth cubic. All share one
normal-consistent MAD noise scale and Huber loss.
The score uses BIC parameter cost and a separate cost for the searched knots;
it is local model evidence, not a calibrated event probability.
A separate fixed-tail comparison assesses a freely fitted level against the
same smooth trends. The detector owns tail selection and event completion.
Direction after a chosen knot is assessed separately on its actual right-hand
originals; a global hinge derivative cannot supply that observed direction.
For the first-order hinge, original sides can supply separate counterevidence
when both prefer lines:
a shared slope or a resolved contrary change can oppose the global hinge.
Curved or unresolved sides cannot supply that additional counterevidence.
A complete smooth fall and recovery is assessed by classifying the same
polynomials and requiring independent directions on their original flanks.
Its fitted minimum is geometry, never a physical event time.
The separate fixed-original helpers expose two disjoint OLS windows, raw
position contrasts and causally available water pairs. Their value scales and
support limits come entirely from the caller; event identity stays outside.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import starmap
from math import ceil, fsum, hypot, isfinite, log, sqrt, ulp
from operator import mul
from statistics import NormalDist, median

from .moisture import absolute_humidity


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


def _profile_number(value):
    try:
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and isfinite(value)
        )
    except OverflowError:
        return False


@dataclass(frozen=True)
class OriginalWindowFeatures:
    """Two disjoint, consecutive original windows and their raw OLS evidence.

    ``current`` and ``previous`` each retain exactly the requested K originals.
    v/b and e/e_minus have input units per second; r/r_minus are maximum
    absolute line residuals divided by the caller's positive value-unit scale.
    Neither the scale nor its derived slope bound is a probability estimate.
    The original times are mathematical support, never physical event times.
    """

    previous: tuple[tuple[datetime, float], ...]
    current: tuple[tuple[datetime, float], ...]
    scale: float
    v: float
    b: float
    e: float
    e_minus: float
    mean: float
    mean_minus: float
    r: float
    r_minus: float

    @property
    def start_at(self) -> datetime:
        """Oldest original in the complete two-window support."""
        return self.previous[0][0]

    @property
    def newest_at(self) -> datetime:
        """Latest original used by the current window."""
        return self.current[-1][0]


@dataclass(frozen=True)
class OriginalFollowupFeatures:
    """Raw follow-up contrasts against one retained original reference.

    Fresh counts include only originals strictly after reference.newest_at.
    An overlapping window remains valid, but does not supply a complete new
    K-original section until its corresponding fresh count reaches K.
    The caller owns the fixed reference, source continuity and every decision.
    current_change and previous_change use the reference's previous slope;
    the *_from_reference_current changes use its current slope instead.
    level_change compares only the two sections of the supplied window.
    reference_level_loss is the dimensionless signed loss against the fixed
    observed mean of the reference's previous section.
    """

    window: OriginalWindowFeatures
    reference: OriginalWindowFeatures
    fresh_previous: int
    fresh_current: int
    current_direction: float
    previous_direction: float
    current_change: float
    previous_change: float
    level_change: float
    residual: float
    reference_level_loss: float
    current_change_from_reference_current: float
    previous_change_from_reference_current: float

    def settled_after_decline(self, significance: float, residual_limit: float) -> bool:
        """A fresh, stable shallower trend after an independently observed fall.

        Both local slopes must be resolved above the retained falling slope.
        Their difference must fit their existing error bounds, with bounded
        line residuals and no resolved renewed rise. A nonzero continuing fall
        is allowed. This compares original windows; it assigns no event time.
        """
        if not (_original_positive(significance) and _original_positive(residual_limit)):
            return False
        window, reference = self.window, self.reference
        return bool(
            reference.v < -significance * reference.e
            and self.fresh_current == len(window.current)
            and self.current_change_from_reference_current > significance
            and self.previous_change_from_reference_current > significance
            and abs(window.v - window.b) <= significance * (window.e + window.e_minus)
            and self.current_direction <= significance
            and self.residual <= residual_limit
        )


@dataclass(frozen=True)
class OriginalEdgeChange:
    """Direction and change of the last two current originals' secant.

    Both contrasts retain their sign. A slowdown can have a negative change
    while direction remains positive or zero. The reference's current OLS
    slope supplies the comparison; its identity and all decisions are owned
    by the caller. window retains both timestamps and their original values.
    """

    window: OriginalWindowFeatures
    reference: OriginalWindowFeatures
    direction: float
    change: float


@dataclass(frozen=True)
class OriginalDeclineEvidence:
    """Fall and level loss on the available originals after a fixed binding."""

    points: tuple[tuple[datetime, float], ...]
    reference: OriginalWindowFeatures
    slope: float
    slope_error: float
    mean: float
    direction: float
    level_loss: float
    level_center: float
    level_scale: float


@dataclass(frozen=True)
class OriginalKinkTolerance:
    """Causal background scale and the remaining signed raw slope change.

    The historical scale is the median absolute change between neighboring
    K-original OLS slopes. It describes observed ordinary slope-change
    strength in input units per second (pp/s for RH), not a measurement-error
    guarantee or an event probability. Its windows may overlap.
    history_count counts complete historical 2K supports; their combined
    original support runs from history_start through history_end. With no
    complete historical window those times and history_scale are None.
    An exactly zero historical scale is valid. Insufficient history retains
    its evidence, while used_scale falls back to the calibrated catalogue.
    The detector owns direction requirements and all admission decisions.
    The same historical blocks provide the median absolute mean change and
    its MAD in value units. These are retained separately from slope changes
    for a later comparison with the observed level loss.
    """

    window: OriginalWindowFeatures
    factor: float
    history_count: int
    history_start: datetime | None
    history_end: datetime | None
    history_scale: float | None
    status: str
    catalog_scale: float
    used_scale: float
    tolerance: float
    delta: float
    effective_change: float
    level_change_median: float | None
    level_change_mad: float | None


@dataclass(frozen=True)
class OriginalPositionWindows:
    """The temperature and RH original windows of one configured position."""

    temperature: OriginalWindowFeatures
    humidity: OriginalWindowFeatures


@dataclass(frozen=True)
class OriginalThermalContrasts:
    """Raw temperature-only B/W contrasts, independent of RH availability.

    opening retains the temperature terms needed by the combined opening
    contrast, so that their formulas have the same owner as B and W.
    """

    braking: float
    warming_min: float
    warming_max: float
    opening: float

    @property
    def closing(self) -> float:
        """The two E2 closing-form branches needing only temperature support."""
        return max(min(self.braking, self.warming_max), self.warming_min)


@dataclass(frozen=True)
class OriginalContrasts:
    """Raw min/max contrasts over exactly the caller's required positions.

    The names correspond to O, B, W_min, W_max, R, F, Z, Z_r, U, C and Q.
    U and C exist only when the caller supplies every group-relative RH
    minimum. A single position uses the same formulas over a smaller set;
    this does not transfer a four-channel physical or empirical proof.
    Retained position windows expose every original anchor to the caller.
    humidity_rise_change (Q) is the minimum normalized RH slope change between
    the previous and current disjoint OLS windows, not a second derivative or
    a probability. The directed humidity onset properties require both the
    current RH direction and its new slope change, independently of T.
    opening_support requires only the continuing joint T/RH fall; opening
    retains the historical contrast including the temperature slope kink.
    """

    positions: tuple[tuple[str, OriginalPositionWindows], ...]
    opening: float
    braking: float
    warming_min: float
    warming_max: float
    rise: float
    fall: float
    background_slope: float
    background_residual: float
    humidity_recovery: float | None
    closing: float | None
    humidity_rise_change: float

    @property
    def humidity_rise_onset(self) -> float:
        """Joint RH rise and new upward slope change at every position."""
        return min(
            min(
                pair.humidity.v / pair.humidity.e,
                (pair.humidity.v - pair.humidity.b)
                / (pair.humidity.e + pair.humidity.e_minus),
            )
            for _, pair in self.positions
        )

    @property
    def humidity_fall_onset(self) -> float:
        """Joint RH fall and new downward slope change at every position."""
        return min(
            min(
                -pair.humidity.v / pair.humidity.e,
                -(pair.humidity.v - pair.humidity.b)
                / (pair.humidity.e + pair.humidity.e_minus),
            )
            for _, pair in self.positions
        )

    @property
    def opening_support(self) -> float:
        """Continuing joint T/RH fall, without requiring another slope kink."""
        return min(
            min(
                -pair.temperature.v / pair.temperature.e,
                -pair.humidity.v / pair.humidity.e,
            )
            for _, pair in self.positions
        )


@dataclass(frozen=True)
class OriginalWaterPair:
    """An RH original with its actual, already available T support and bounds.

    available_at is the right T original's time on the input measurement axis,
    not a device receipt time. For an exact T/RH timestamp match the two T
    supports are the same original, and no later T observation is needed.
    These density bounds alone neither qualify water nor identify an event.
    """

    humidity_sample: tuple[datetime, float]
    temperature_before: tuple[datetime, float]
    temperature_after: tuple[datetime, float]
    temperature: float
    lower_density: float
    upper_density: float
    available_at: datetime


@dataclass(frozen=True)
class OriginalWaterPairSupport:
    """Separate an ordinary missing right T original from invalid support."""

    status: str
    pair: OriginalWaterPair | None = None
    reason: str | None = None


@dataclass(frozen=True)
class OriginalWaterWindowFeatures:
    """Two consecutive water windows with propagated pointwise envelopes.

    Pair identities retain the RH originals, both T partners and availability.
    The slope envelopes are algebraic consequences of those input envelopes,
    never statistical confidence intervals or independent water measurements.
    Current level deviation uses their empirical widths as normalizers, not
    guaranteed individual measurement errors.
    """

    previous: tuple[OriginalWaterPair, ...]
    current: tuple[OriginalWaterPair, ...]
    v: float
    b: float
    e: float
    e_minus: float

    @property
    def start_at(self) -> datetime:
        """First RH original in the complete two-window support."""
        return self.previous[0].humidity_sample[0]

    @property
    def newest_at(self) -> datetime:
        """Last RH original in the current window."""
        return self.current[-1].humidity_sample[0]

    @property
    def available_at(self) -> datetime:
        """Latest right T partner on the accepted input measurement axis."""
        return max(pair.available_at for pair in self.previous + self.current)

    def normalized_level_deviation(self) -> float | None:
        """RMS distance of every current middle to one freely fitted level.

        For positive envelope half-widths a_i, the level minimizes the mean
        squared residual (m_i-level)/a_i, so its weights are 1/a_i**2. Relative
        weights avoid squaring reciprocal small widths. A zero width fixes
        the level exactly; conflicting fixed levels or nonfinite arithmetic
        leave the normalized shape unknown. No numerical noise floor is added.
        The result describes observed dispersion, not physical constancy or a
        statistical confidence bound.
        """
        try:
            if not self.current or any(
                not _original_water_pair_valid(pair) for pair in self.current
            ):
                return None
            radii = tuple(
                (pair.upper_density - pair.lower_density) / 2 for pair in self.current
            )
            middles = tuple(
                pair.lower_density + radius
                for pair, radius in zip(self.current, radii, strict=True)
            )
            fixed = tuple(
                middle
                for middle, radius in zip(middles, radii, strict=True)
                if radius == 0
            )
            if fixed:
                level = fixed[0]
                if any(middle != level for middle in fixed):
                    return None
            else:
                smallest = min(radii)
                weights = tuple((smallest / radius) ** 2 for radius in radii)
                total = fsum(weights)
                anchor = middles[radii.index(smallest)]
                level = anchor + fsum(
                    (weight / total) * (middle - anchor)
                    for weight, middle in zip(weights, middles, strict=True)
                )
            if not _profile_number(level):
                return None
            root_count = sqrt(len(middles))
            normalized = tuple(
                0.0 if radius == 0 else ((middle - level) / root_count) / radius
                for middle, radius in zip(middles, radii, strict=True)
            )
            if not all(_profile_number(value) for value in normalized):
                return None
            deviation = hypot(*normalized)
            return deviation if _profile_number(deviation) else None
        except (AttributeError, IndexError, OverflowError, TypeError, ValueError):
            return None

    def constant_bounds(self, significance: float) -> tuple[float, float]:
        """Diagnose the intersection of scaled current point envelopes.

        A nonempty intersection is compatibility with one constant water
        level under individual point bounds, rather than an RMS form score.
        """
        if not _original_positive(significance):
            raise ValueError("significance must be finite and positive")
        bounds = []
        for pair in self.current:
            if significance == 1:
                bounds.append((pair.lower_density, pair.upper_density))
                continue
            middle = (pair.lower_density + pair.upper_density) / 2
            radius = (pair.upper_density - pair.lower_density) / 2
            bounds.append(
                (
                    middle - significance * radius,
                    middle + significance * radius,
                )
            )
        lower = max(bound[0] for bound in bounds)
        upper = min(bound[1] for bound in bounds)
        if not all(_profile_number(value) for value in (lower, upper)):
            raise ValueError("scaled water envelopes must remain finite")
        return lower, upper

    def constant_compatible(self, significance: float) -> bool:
        """Diagnose whether every current envelope can share one level."""
        lower, upper = self.constant_bounds(significance)
        return lower <= upper

    @property
    def constant_lower(self) -> float:
        """Lower end of the unscaled current constant-level intersection."""
        return max(pair.lower_density for pair in self.current)

    @property
    def constant_upper(self) -> float:
        """Upper end of the unscaled current constant-level intersection."""
        return min(pair.upper_density for pair in self.current)


@dataclass(frozen=True)
class OriginalWaterWindowSupport:
    """Water-window result without silently removing unavailable originals."""

    status: str
    features: OriginalWaterWindowFeatures | None = None
    reason: str | None = None


@dataclass(frozen=True)
class OriginalFlatteningContrast:
    """One direct slope comparison, retaining both operands for diagnosis."""

    value: float
    operator: str
    threshold: float
    result: bool


@dataclass(frozen=True)
class OriginalFlattening:
    """Fall-to-flat evidence in native RH or paired AH value units.

    Humidity comparisons have the supplied humidity window's units per
    second; temperature comparisons have degrees Celsius per second. The
    retained windows expose the disjoint original supports and all raw slope
    operands. These are mathematical forms, without event or stage identity.
    """

    humidity_window: OriginalWindowFeatures | OriginalWaterWindowFeatures
    temperature_window: OriginalWindowFeatures
    significance: float
    previous_fall: OriginalFlatteningContrast
    current_flat: OriginalFlatteningContrast
    braking: OriginalFlatteningContrast
    temperature_falling: OriginalFlatteningContrast
    temperature_not_braking: OriginalFlatteningContrast

    @property
    def current_nonrising(self) -> OriginalFlatteningContrast:
        """Allow a continuing humidity fall, but no resolved humidity rise."""
        window = self.humidity_window
        bound = self.significance * window.e
        return OriginalFlatteningContrast(window.v, "<=", bound, window.v <= bound)

    @property
    def abating(self) -> bool:
        """Resolved slowing of humidity while temperature keeps falling."""
        return (self.previous_fall.result and self.braking.result
                and self.current_nonrising.result and self.temperature_falling.result
                and self.temperature_not_braking.result)

    @property
    def maintained(self) -> bool:
        """Flat humidity while temperature still falls without braking."""
        return (
            self.current_flat.result
            and self.temperature_falling.result
            and self.temperature_not_braking.result
        )

    @property
    def initial(self) -> bool:
        """Maintained form reached by braking a preceding humidity fall."""
        return self.maintained and self.previous_fall.result and self.braking.result


def _original_positive(value: object) -> bool:
    return _profile_number(value) and value > 0


def _original_window_valid(window: object) -> bool:
    return (
        isinstance(window, OriginalWindowFeatures)
        and all(
            _profile_number(value)
            for value in (
                window.v,
                window.b,
                window.e,
                window.e_minus,
                window.mean,
                window.mean_minus,
                window.r,
                window.r_minus,
            )
        )
        and all(
            _original_positive(value)
            for value in (
                window.scale,
                window.e,
                window.e_minus,
            )
        )
        and window.r >= 0
        and window.r_minus >= 0
    )


def _original_comparable_windows_valid(
    window: OriginalWindowFeatures,
    reference: OriginalWindowFeatures,
) -> bool:
    if (
        not _original_window_valid(window)
        or not _original_window_valid(reference)
        or window.scale != reference.scale
    ):
        return False
    count = len(reference.previous)
    if count < 2:
        return False
    for features in (window, reference):
        previous_at = None
        for section in (features.previous, features.current):
            if not isinstance(section, tuple) or len(section) != count:
                return False
            for point in section:
                if not isinstance(point, tuple) or len(point) != 2:
                    return False
                stamp, value = point
                if (
                    not isinstance(stamp, datetime)
                    or not _profile_number(value)
                    or (previous_at is not None and stamp <= previous_at)
                ):
                    return False
                previous_at = stamp
    return True


def _original_line(
    points: tuple[tuple[datetime, float], ...],
    scale: float,
) -> tuple[float, float, float, float] | None:
    """Return slope, slope error bound, mean and max-normalized residual."""
    origin = points[0][0]
    times = tuple((stamp - origin).total_seconds() for stamp, _ in points)
    count = len(points)
    mean_time = fsum(times) / count
    mean_value = fsum(value for _, value in points) / count
    centered_times = tuple(time - mean_time for time in times)
    s_tt = fsum(time * time for time in centered_times)
    if not _original_positive(s_tt):
        return None
    slope = (
        fsum(
            time * (value - mean_value)
            for time, (_, value) in zip(centered_times, points, strict=True)
        )
        / s_tt
    )
    error = scale * (fsum(abs(time) for time in centered_times) / s_tt)
    residual = (
        max(
            abs(value - mean_value - slope * time)
            for time, (_, value) in zip(centered_times, points, strict=True)
        )
        / scale
    )
    result = (slope, error, mean_value, residual)
    if not all(_profile_number(value) for value in result) or error <= 0:
        return None
    return result


def original_window(
    samples: Sequence[tuple[datetime, float]],
    *,
    count: int,
    scale: float,
    max_gap_seconds: float,
    at: datetime,
) -> OriginalWindowFeatures | None:
    """Use the last 2K originals at or before at, without repairing their order.

    Every adjacent gap in that block, including the boundary between windows,
    and the age of its newest original must be within max_gap_seconds. Invalid
    or insufficient support is unknown (None). No interpolation or resampling
    takes place. Work and storage are O(K) for the causal prefix supplied by
    the detector; an optional future suffix costs one visit per skipped point.
    The caller retains source continuity and any earlier history validity.
    """
    try:
        if (
            not isinstance(count, int)
            or isinstance(count, bool)
            or count < 2
            or not _original_positive(scale)
            or not _original_positive(max_gap_seconds)
            or not isinstance(at, datetime)
        ):
            return None
        selected = []
        later = None
        for stamp, value in reversed(samples):
            if not isinstance(stamp, datetime):
                return None
            if later is not None and stamp >= later:
                return None
            later = stamp
            if stamp > at:
                continue
            if not _profile_number(value):
                return None
            if selected and (selected[-1][0] - stamp).total_seconds() > max_gap_seconds:
                return None
            selected.append((stamp, float(value)))
            if len(selected) == 2 * count:
                break
        if len(selected) != 2 * count:
            return None
        if not 0 <= (at - selected[0][0]).total_seconds() <= max_gap_seconds:
            return None
        selected.reverse()
        previous = tuple(selected[:count])
        current = tuple(selected[count:])
        old = _original_line(previous, scale)
        new = _original_line(current, scale)
        if old is None or new is None:
            return None
        return OriginalWindowFeatures(
            previous=previous,
            current=current,
            scale=float(scale),
            v=new[0],
            b=old[0],
            e=new[1],
            e_minus=old[1],
            mean=new[2],
            mean_minus=old[2],
            r=new[3],
            r_minus=old[3],
        )
    except (AttributeError, IndexError, OverflowError, TypeError, ValueError):
        return None


def original_followup(
    window: OriginalWindowFeatures,
    reference: OriginalWindowFeatures,
) -> OriginalFollowupFeatures | None:
    """Compare immutable, ordered 2K support with a fixed earlier reference.

    Both supports retain the same K and value scale. No refit or overlap-based
    evidence accumulation takes place. Equal newest times are valid and have
    no fresh originals; a window older than its reference is unknown (None).
    Invalid support or nonfinite arithmetic is also unknown. Source identity,
    gap limits and the amount of fresh support required belong to the caller.

    current_change_from_reference_current compares window.v with reference.v,
    divided by window.e + reference.e. previous_change_from_reference_current
    compares window.b with reference.v, divided by window.e_minus + reference.e.
    These use the retained current slope without selecting or updating it.

    reference_level_loss = (reference.mean_minus - window.mean) / (2 * scale).
    The common scale is the supplied value-deviation scale; 2 * scale
    is the existing difference normalization, not a new parameter or
    an event rule. The fixed reference's observed level is not extrapolated.
    """
    try:
        if not _original_comparable_windows_valid(window, reference):
            return None
        reference_at = reference.newest_at
        if window.newest_at < reference_at:
            return None
        current_error = window.e + reference.e_minus
        previous_error = window.e_minus + reference.e_minus
        current_reference_error = window.e + reference.e
        previous_reference_error = window.e_minus + reference.e
        level_scale = 2 * window.scale
        if not all(
            _original_positive(value)
            for value in (
                current_error,
                previous_error,
                current_reference_error,
                previous_reference_error,
                level_scale,
            )
        ):
            return None
        current_direction = window.v / window.e
        previous_direction = window.b / window.e_minus
        current_change = (window.v - reference.b) / current_error
        previous_change = (window.b - reference.b) / previous_error
        current_change_from_reference_current = (
            window.v - reference.v
        ) / current_reference_error
        previous_change_from_reference_current = (
            window.b - reference.v
        ) / previous_reference_error
        level_change = (window.mean - window.mean_minus) / level_scale
        residual = max(window.r, window.r_minus)
        reference_level_loss = (reference.mean_minus - window.mean) / level_scale
        if not all(
            _profile_number(value)
            for value in (
                current_direction,
                previous_direction,
                current_change,
                previous_change,
                level_change,
                residual,
                reference_level_loss,
                current_change_from_reference_current,
                previous_change_from_reference_current,
            )
        ):
            return None
        return OriginalFollowupFeatures(
            window=window,
            reference=reference,
            fresh_previous=sum(stamp > reference_at for stamp, _ in window.previous),
            fresh_current=sum(stamp > reference_at for stamp, _ in window.current),
            current_direction=current_direction,
            previous_direction=previous_direction,
            current_change=current_change,
            previous_change=previous_change,
            level_change=level_change,
            residual=residual,
            reference_level_loss=reference_level_loss,
            current_change_from_reference_current=(
                current_change_from_reference_current
            ),
            previous_change_from_reference_current=(
                previous_change_from_reference_current
            ),
        )
    except (AttributeError, IndexError, OverflowError, TypeError, ValueError):
        return None


def original_decline_evidence(
    samples: Sequence[tuple[datetime, float]], reference: OriginalWindowFeatures,
    *, background: OriginalKinkTolerance | None = None,
) -> OriginalDeclineEvidence | None:
    """Assess the growing original suffix, without waiting for full K blocks.

    At least two distinct times are required to determine a slope. The slope
    bound follows from the supplied value-deviation scale and actual time
    support; elapsed time and sample count alone never confirm a fall. The
    The optional bound background supplies ordinary absolute changes between
    adjacent K-original means. Their median is subtracted from the observed
    loss; their normal-consistent MAD enlarges the catalogue difference scale.
    This compares the drop with the already observed background, including
    its ordinary drift. It does not estimate an event probability. Source
    continuity, retained reference identity and admissibility belong to the
    caller.
    """
    try:
        if not _original_window_valid(reference):
            return None
        points = tuple((stamp, value) for stamp, value in samples
                       if stamp > reference.newest_at)
        if len(points) < 2:
            return None
        if any(not isinstance(stamp, datetime) or not _profile_number(value)
               or index and stamp <= points[index - 1][0]
               for index, (stamp, value) in enumerate(points)):
            return None
        line = _original_line(points, reference.scale)
        if line is None:
            return None
        slope, error, mean, _ = line
        direction = slope / error
        level_center, level_scale = 0.0, 2 * reference.scale
        if background is not None:
            if background.window != reference:
                return None
            if background.status == "history":
                if any(not _profile_number(value) or value < 0 for value in (
                    background.level_change_median, background.level_change_mad,
                )):
                    return None
                level_center = background.level_change_median
                level_scale = max(level_scale, _MAD_NORMALIZATION * background.level_change_mad)
            elif background.status != "insufficient":
                return None
        level_loss = (reference.mean_minus - mean - level_center) / level_scale
        if not all(_profile_number(value) for value in (direction, level_loss)):
            return None
        return OriginalDeclineEvidence(
            points, reference, slope, error, mean, direction, level_loss,
            level_center, level_scale,
        )
    except (AttributeError, IndexError, OverflowError, TypeError, ValueError):
        return None


def original_edge_change(
    window: OriginalWindowFeatures,
    reference: OriginalWindowFeatures,
) -> OriginalEdgeChange | None:
    """Compare the newest secant with a retained current OLS reference.

    Only a strictly newer original than reference.newest_at can supply this
    contrast. The secant uses the final two current originals and their actual
    time difference. Its slope bound is exactly 2 * scale / time difference;
    change compares with reference.v using that bound plus reference.e.
    No fit, threshold or reference update takes place. Invalid support and
    nonfinite or unresolved numerical bounds remain unknown (None).
    """
    try:
        if (
            not _original_comparable_windows_valid(window, reference)
            or window.newest_at <= reference.newest_at
        ):
            return None
        previous, current = window.current[-2:]
        duration = (current[0] - previous[0]).total_seconds()
        if not _original_positive(duration):
            return None
        slope = (current[1] - previous[1]) / duration
        error = 2 * window.scale / duration
        change_error = error + reference.e
        if (
            not _profile_number(slope)
            or not _original_positive(error)
            or not _original_positive(change_error)
        ):
            return None
        direction = slope / error
        change = (slope - reference.v) / change_error
        if not all(_profile_number(value) for value in (direction, change)):
            return None
        return OriginalEdgeChange(
            window=window,
            reference=reference,
            direction=direction,
            change=change,
        )
    except (AttributeError, IndexError, OverflowError, TypeError, ValueError):
        return None


def original_kink_tolerance(
    samples: Sequence[tuple[datetime, float]],
    window: OriginalWindowFeatures,
    *,
    factor: float,
    min_history_windows: int,
    max_gap_seconds: float,
) -> OriginalKinkTolerance | None:
    """Shrink one raw slope change by a caller-scaled causal background.

    Only originals strictly before window.previous[0] supply history. Every
    consecutive historical 2K block uses the same K and the actual original
    times with _original_line. Its score is abs(v_j - b_j), so a smooth bend
    may contribute a nonzero scale even without erratic variation.
    A valid gap exceeding max_gap_seconds cuts off older history, including
    at the connection to the current support. Malformed causal originals and
    invalid current support remain unknown (None), never repaired.
    Originals after window.newest_at do not affect this result. The caller
    supplies bounded history from one source and epoch; its source identity
    and the raw current values are not reconstructed or changed here.
    Work is bounded by that supplied history and K. No numerical noise floor
    is added. factor == 0 preserves the raw delta as a control case.
    """
    try:
        if (
            not _original_window_valid(window)
            or not _profile_number(factor)
            or factor < 0
            or not isinstance(min_history_windows, int)
            or isinstance(min_history_windows, bool)
            or min_history_windows <= 0
            or not _original_positive(max_gap_seconds)
        ):
            return None
        count = len(window.previous)
        if count < 2 or len(window.current) != count:
            return None
        previous_at = None
        for stamp, value in window.previous + window.current:
            if not isinstance(stamp, datetime) or not _profile_number(value):
                return None
            if previous_at is not None and not (
                0 < (stamp - previous_at).total_seconds() <= max_gap_seconds
            ):
                return None
            previous_at = stamp
        start_at = window.start_at
        newest_at = window.newest_at
        history = []
        previous_at = None
        for stamp, value in samples:
            if not isinstance(stamp, datetime):
                return None
            if stamp > newest_at:
                continue
            if previous_at is not None and stamp <= previous_at:
                return None
            previous_at = stamp
            if stamp >= start_at:
                continue
            if not _profile_number(value):
                return None
            if history and (stamp - history[-1][0]).total_seconds() > max_gap_seconds:
                history.clear()
            history.append((stamp, float(value)))
        if history and (start_at - history[-1][0]).total_seconds() > max_gap_seconds:
            history.clear()
        historical_changes, historical_levels = [], []
        for end in range(2 * count, len(history) + 1):
            old = _original_line(
                tuple(history[end - 2 * count : end - count]),
                window.scale,
            )
            new = _original_line(tuple(history[end - count : end]), window.scale)
            if old is None or new is None:
                return None
            difference = abs(new[0] - old[0])
            if not _profile_number(difference):
                return None
            historical_changes.append(difference)
            level_difference = abs(new[2] - old[2])
            if not _profile_number(level_difference):
                return None
            historical_levels.append(level_difference)
        history_count = len(historical_changes)
        history_scale = median(historical_changes) if history_count else None
        level_median = median(historical_levels) if history_count else None
        level_mad = (median(abs(value - level_median) for value in historical_levels)
                     if history_count else None)
        catalog_scale = window.e + window.e_minus
        if not _original_positive(catalog_scale) or (
            history_scale is not None and not _profile_number(history_scale)
        ):
            return None
        enough_history = history_count >= min_history_windows
        used_scale = catalog_scale
        if enough_history:
            used_scale = max(catalog_scale, history_scale)
        tolerance = factor * used_scale
        delta = window.v - window.b
        if not all(_profile_number(value) for value in (tolerance, delta)):
            return None
        remaining = max(0.0, abs(delta) - tolerance)
        effective_change = -remaining if delta < 0 else remaining
        return OriginalKinkTolerance(
            window=window,
            factor=float(factor),
            history_count=history_count,
            history_start=history[0][0] if history_count else None,
            history_end=history[-1][0] if history_count else None,
            history_scale=history_scale,
            status="history" if enough_history else "insufficient",
            catalog_scale=catalog_scale,
            used_scale=used_scale,
            tolerance=tolerance,
            delta=delta,
            effective_change=effective_change,
            level_change_median=level_median,
            level_change_mad=level_mad,
        )
    except (AttributeError, IndexError, OverflowError, TypeError, ValueError):
        return None


def original_thermal_contrasts(
    temperatures: Mapping[str, OriginalWindowFeatures],
    *,
    expected_positions: Sequence[str],
) -> OriginalThermalContrasts | None:
    """Aggregate E2 temperature terms over every required configured position.

    Missing required temperature support or an invalid expected set yields
    None. These terms need no humidity observation and make no event decision.
    """
    try:
        expected = tuple(expected_positions)
        if (
            not expected
            or any(not isinstance(position, str) for position in expected)
            or len(set(expected)) != len(expected)
        ):
            return None
        opening_terms = []
        braking_terms = []
        warming_terms = []
        for position in expected:
            temperature = temperatures[position]
            if not _original_window_valid(temperature):
                return None
            slope_difference = temperature.v - temperature.b
            difference_error = temperature.e + temperature.e_minus
            if not _profile_number(slope_difference) or not _original_positive(
                difference_error
            ):
                return None
            braking = slope_difference / difference_error
            warming = temperature.v / temperature.e
            if not all(_profile_number(value) for value in (braking, warming)):
                return None
            opening_terms.extend((-warming, -braking))
            braking_terms.append(braking)
            warming_terms.append(warming)
        return OriginalThermalContrasts(
            braking=min(braking_terms),
            warming_min=min(warming_terms),
            warming_max=max(warming_terms),
            opening=min(opening_terms),
        )
    except (AttributeError, IndexError, KeyError, OverflowError, TypeError, ValueError):
        return None


def original_contrasts(
    windows: Mapping[str, OriginalPositionWindows],
    *,
    expected_positions: Sequence[str],
    humidity_minima: Mapping[str, float] | None = None,
) -> OriginalContrasts | None:
    """Aggregate raw E2/E3 contrasts, retaining every required position.

    Missing required T/RH support or a missing supplied group minimum yields
    None. The caller owns the expected position set and group-relative minima;
    this function never silently reduces that set or manages event state.
    """
    try:
        expected = tuple(expected_positions)
        if (
            not expected
            or any(not isinstance(position, str) for position in expected)
            or len(set(expected)) != len(expected)
        ):
            return None
        positions = tuple((position, windows[position]) for position in expected)
        if any(not isinstance(pair, OriginalPositionWindows) for _, pair in positions):
            return None
        thermal = original_thermal_contrasts(
            {position: pair.temperature for position, pair in positions},
            expected_positions=expected,
        )
        if thermal is None:
            return None
        opening_terms = []
        humidity_terms = []
        humidity_change_terms = []
        residual_terms = []
        recovery_terms = []
        for position, pair in positions:
            humidity = pair.humidity
            if not _original_window_valid(humidity):
                return None
            slope_difference = humidity.v - humidity.b
            difference_error = humidity.e + humidity.e_minus
            if not _profile_number(slope_difference) or not _original_positive(
                difference_error
            ):
                return None
            humidity_change = slope_difference / difference_error
            if not _profile_number(humidity_change):
                return None
            humidity_rise = humidity.v / humidity.e
            opening_terms.append(-humidity_rise)
            humidity_terms.append(humidity_rise)
            humidity_change_terms.append(humidity_change)
            residual_terms.extend((humidity.r, humidity.r_minus))
            if humidity_minima is not None:
                minimum = humidity_minima[position]
                if not _profile_number(minimum):
                    return None
                recovery_difference = humidity.mean - minimum
                recovery_scale = 2 * humidity.scale
                if not _profile_number(recovery_difference) or not _original_positive(
                    recovery_scale
                ):
                    return None
                recovery_terms.append(recovery_difference / recovery_scale)
        opening = min(thermal.opening, min(opening_terms))
        braking = thermal.braking
        warming_min, warming_max = thermal.warming_min, thermal.warming_max
        rise, fall = min(humidity_terms), min(-value for value in humidity_terms)
        humidity_rise_change = min(humidity_change_terms)
        background_slope = max(abs(value) for value in humidity_terms)
        background_residual = max(residual_terms)
        recovery = min(recovery_terms) if humidity_minima is not None else None
        closing = (
            max(min(braking, recovery), thermal.closing)
            if recovery is not None
            else None
        )
        numbers = (
            opening,
            braking,
            warming_min,
            warming_max,
            rise,
            fall,
            background_slope,
            background_residual,
            humidity_rise_change,
        )
        if not all(_profile_number(value) for value in numbers):
            return None
        if recovery is not None and (
            not _profile_number(recovery) or not _profile_number(closing)
        ):
            return None
        return OriginalContrasts(
            positions=positions,
            opening=opening,
            braking=braking,
            warming_min=warming_min,
            warming_max=warming_max,
            rise=rise,
            fall=fall,
            background_slope=background_slope,
            background_residual=background_residual,
            humidity_recovery=recovery,
            closing=closing,
            humidity_rise_change=humidity_rise_change,
        )
    except (AttributeError, IndexError, KeyError, OverflowError, TypeError, ValueError):
        return None


def original_water_pair_support(
    humidity_sample: tuple[datetime, float],
    temperatures: Sequence[tuple[datetime, float]],
    *,
    temperature_scale: float,
    humidity_scale: float,
    max_gap_seconds: float,
    at: datetime,
) -> OriginalWaterPairSupport:
    """Assess one RH original with its immediate causal T neighbors.

    A bracket is usable only after its right original is available at at and
    within the caller's support gap. Historical base pairs do not expire merely
    because at advances; source/candidate continuity belongs to the detector.
    The existing moisture function supplies both density bounds. Invalid error
    envelopes are unknown, never clipped into an invented water observation.
    A valid left neighbor with its right neighbor not yet available is pending,
    not an invalid gap. Receipt-time freshness remains with the caller.
    """
    try:
        humidity_at, humidity = humidity_sample
        if (
            not isinstance(at, datetime)
            or not isinstance(humidity_at, datetime)
            or humidity_at > at
            or not _profile_number(humidity)
            or not 0 <= humidity <= 100
            or not _original_positive(temperature_scale)
            or not _original_positive(humidity_scale)
            or not _original_positive(max_gap_seconds)
        ):
            return OriginalWaterPairSupport("invalid", reason="invalid_input")
        before = None
        after = None
        previous_at = None
        for stamp, value in temperatures:
            if not isinstance(stamp, datetime):
                return OriginalWaterPairSupport(
                    "invalid",
                    reason="invalid_temperature_time",
                )
            if previous_at is not None and stamp <= previous_at:
                return OriginalWaterPairSupport("invalid", reason="temperature_order")
            previous_at = stamp
            if stamp > at:
                break
            if not _profile_number(value):
                return OriginalWaterPairSupport(
                    "invalid",
                    reason="invalid_temperature_value",
                )
            point = (stamp, float(value))
            if stamp == humidity_at:
                before = after = point
                break
            if stamp < humidity_at:
                before = point
            else:
                after = point
                break
        if before is None:
            return OriginalWaterPairSupport(
                "invalid", reason="left_temperature_missing"
            )
        if after is None:
            if (humidity_at - before[0]).total_seconds() > max_gap_seconds:
                return OriginalWaterPairSupport(
                    "invalid", reason="temperature_support_gap"
                )
            return OriginalWaterPairSupport(
                "pending", reason="right_temperature_pending"
            )
        gap = (after[0] - before[0]).total_seconds()
        if not 0 <= gap <= max_gap_seconds:
            return OriginalWaterPairSupport("invalid", reason="temperature_support_gap")
        temperature = before[1]
        if gap:
            fraction = (humidity_at - before[0]).total_seconds() / gap
            temperature += fraction * (after[1] - before[1])
        lower = absolute_humidity(
            temperature - temperature_scale,
            humidity - humidity_scale,
        )
        upper = absolute_humidity(
            temperature + temperature_scale,
            humidity + humidity_scale,
        )
        if not all(_profile_number(value) for value in (temperature, lower, upper)):
            return OriginalWaterPairSupport("invalid", reason="invalid_water_envelope")
        pair = OriginalWaterPair(
            humidity_sample=(humidity_at, float(humidity)),
            temperature_before=before,
            temperature_after=after,
            temperature=temperature,
            lower_density=lower,
            upper_density=upper,
            available_at=after[0],
        )
        return OriginalWaterPairSupport("ready", pair=pair)
    except (AttributeError, IndexError, OverflowError, TypeError, ValueError):
        return OriginalWaterPairSupport("invalid", reason="invalid_support")


def original_water_pair(
    humidity_sample: tuple[datetime, float],
    temperatures: Sequence[tuple[datetime, float]],
    *,
    temperature_scale: float,
    humidity_scale: float,
    max_gap_seconds: float,
    at: datetime,
) -> OriginalWaterPair | None:
    """Compatibility view of the causal water-pair support assessment."""
    return original_water_pair_support(
        humidity_sample,
        temperatures,
        temperature_scale=temperature_scale,
        humidity_scale=humidity_scale,
        max_gap_seconds=max_gap_seconds,
        at=at,
    ).pair


def _original_water_pair_valid(pair: object) -> bool:
    if not isinstance(pair, OriginalWaterPair):
        return False
    humidity_at, humidity = pair.humidity_sample
    before_at, before = pair.temperature_before
    after_at, after = pair.temperature_after
    return (
        all(
            isinstance(stamp, datetime)
            for stamp in (
                humidity_at,
                before_at,
                after_at,
                pair.available_at,
            )
        )
        and before_at <= humidity_at <= after_at
        and pair.available_at == after_at
        and all(
            _profile_number(value)
            for value in (
                humidity,
                before,
                after,
                pair.temperature,
                pair.lower_density,
                pair.upper_density,
            )
        )
        and 0 <= humidity <= 100
        and 0 <= pair.lower_density <= pair.upper_density
    )


def _original_water_line(
    pairs: tuple[OriginalWaterPair, ...],
) -> tuple[float, float] | None:
    """OLS slope and its exact point-envelope bound, in g/m³ per second."""
    origin = pairs[0].humidity_sample[0]
    times = tuple((pair.humidity_sample[0] - origin).total_seconds() for pair in pairs)
    mean_time = fsum(times) / len(times)
    centered_times = tuple(time - mean_time for time in times)
    s_tt = fsum(time * time for time in centered_times)
    if not _original_positive(s_tt):
        return None
    middles = tuple((pair.lower_density + pair.upper_density) / 2 for pair in pairs)
    radii = tuple((pair.upper_density - pair.lower_density) / 2 for pair in pairs)
    mean_value = fsum(middles) / len(middles)
    slope = (
        fsum(
            time * (value - mean_value)
            for time, value in zip(centered_times, middles, strict=True)
        )
        / s_tt
    )
    error = (
        fsum(
            abs(time) * radius
            for time, radius in zip(centered_times, radii, strict=True)
        )
        / s_tt
    )
    if not all(_profile_number(value) for value in (slope, error)) or error < 0:
        return None
    return slope, error


def original_water_window(
    pairs: Sequence[OriginalWaterPair],
    *,
    count: int,
    max_gap_seconds: float,
    at: datetime,
) -> OriginalWaterWindowSupport:
    """Use the last exactly 2K consecutive supplied RH-pair identities.

    No future or invalid entry is skipped to obtain an older usable window.
    The caller supplies every successive RH original, retains pending unpaired
    originals and owns source continuity and raw-channel receipt freshness.
    Adjacent RH-original gaps are checked here; each supplied pair retains
    the T bracket checked with its separate bound by original_water_pair.
    An old complete window is not made invalid merely by ordinary causal
    waiting for a newer pair's right T partner.
    """
    try:
        if (
            not isinstance(count, int)
            or isinstance(count, bool)
            or count < 2
            or not _original_positive(max_gap_seconds)
            or not isinstance(at, datetime)
        ):
            return OriginalWaterWindowSupport("invalid", reason="invalid_input")
        selected = tuple(pairs[-2 * count :])
        if not selected:
            return OriginalWaterWindowSupport(
                "insufficient", reason="insufficient_pairs"
            )
        previous_at = None
        pending = False
        for pair in selected:
            if not _original_water_pair_valid(pair):
                return OriginalWaterWindowSupport(
                    "invalid", reason="invalid_water_pair"
                )
            stamp = pair.humidity_sample[0]
            if previous_at is not None:
                gap = (stamp - previous_at).total_seconds()
                if not 0 < gap <= max_gap_seconds:
                    return OriginalWaterWindowSupport(
                        "invalid",
                        reason="humidity_order_or_gap",
                    )
            previous_at = stamp
            pending |= pair.available_at > at or stamp > at
        if pending:
            return OriginalWaterWindowSupport(
                "pending", reason="pair_not_yet_available"
            )
        if len(selected) != 2 * count:
            return OriginalWaterWindowSupport(
                "insufficient", reason="insufficient_pairs"
            )
        previous, current = selected[:count], selected[count:]
        old, new = _original_water_line(previous), _original_water_line(current)
        if old is None or new is None:
            return OriginalWaterWindowSupport(
                "invalid", reason="invalid_window_geometry"
            )
        return OriginalWaterWindowSupport(
            "ready",
            features=OriginalWaterWindowFeatures(
                previous=previous,
                current=current,
                v=new[0],
                b=old[0],
                e=new[1],
                e_minus=old[1],
            ),
        )
    except (AttributeError, IndexError, OverflowError, TypeError, ValueError):
        return OriginalWaterWindowSupport("invalid", reason="invalid_support")


def original_flattening(
    humidity_window: OriginalWindowFeatures | OriginalWaterWindowFeatures,
    temperature_window: OriginalWindowFeatures, *, significance: float,
    reference_humidity: OriginalWindowFeatures | OriginalWaterWindowFeatures | None = None,
) -> OriginalFlattening | None:
    """Compare a slowing humidity fall with its temperature form.

    The caller supplies significance and owns support continuity, causal
    availability and stage identity. The same product comparisons accept
    native RH bounds and paired AH envelopes, including exact zero-width
    water envelopes. Invalid support or nonfinite arithmetic stays unknown.
    """
    try:
        if not _original_positive(significance) or not _original_window_valid(
            temperature_window
        ):
            return None
        if isinstance(humidity_window, OriginalWindowFeatures):
            if not _original_window_valid(humidity_window):
                return None
        elif isinstance(humidity_window, OriginalWaterWindowFeatures):
            if (
                not humidity_window.previous
                or not humidity_window.current
                or any(
                    not _original_water_pair_valid(pair)
                    for pair in (humidity_window.previous + humidity_window.current)
                )
                or not all(
                    _profile_number(value)
                    for value in (
                        humidity_window.v,
                        humidity_window.b,
                        humidity_window.e,
                        humidity_window.e_minus,
                    )
                )
                or humidity_window.e < 0
                or humidity_window.e_minus < 0
            ):
                return None
        else:
            return None
        humidity, temperature = humidity_window, temperature_window
        reference = humidity if reference_humidity is None else reference_humidity
        if reference_humidity is not None:
            # A retained earlier transition supplies its preceding fall.
            # The current slope always comes from the current original support.
            if (type(reference) is not type(humidity)
                    or reference.newest_at > humidity.newest_at
                    or not all(_profile_number(value) for value in (reference.b, reference.e_minus))
                    or reference.e_minus < 0):
                return None
        humidity_change = humidity.v - reference.b
        humidity_change_error = humidity.e + reference.e_minus
        temperature_change = temperature.v - temperature.b
        temperature_change_error = temperature.e + temperature.e_minus
        humidity_previous_bound = significance * reference.e_minus
        humidity_current_bound = significance * humidity.e
        humidity_change_bound = significance * humidity_change_error
        temperature_current_bound = significance * temperature.e
        temperature_change_bound = significance * temperature_change_error
        if not all(
            _profile_number(value)
            for value in (
                humidity_change,
                humidity_change_error,
                temperature_change,
                temperature_change_error,
                humidity_previous_bound,
                humidity_current_bound,
                humidity_change_bound,
                temperature_current_bound,
                temperature_change_bound,
            )
        ):
            return None
        return OriginalFlattening(
            humidity_window=humidity_window,
            temperature_window=temperature_window,
            significance=significance,
            previous_fall=OriginalFlatteningContrast(
                -reference.b, ">", humidity_previous_bound,
                -reference.b > humidity_previous_bound,
            ),
            current_flat=OriginalFlatteningContrast(
                abs(humidity.v),
                "<=",
                humidity_current_bound,
                abs(humidity.v) <= humidity_current_bound,
            ),
            braking=OriginalFlatteningContrast(
                humidity_change,
                ">",
                humidity_change_bound,
                humidity_change > humidity_change_bound,
            ),
            temperature_falling=OriginalFlatteningContrast(
                -temperature.v,
                ">",
                temperature_current_bound,
                -temperature.v > temperature_current_bound,
            ),
            temperature_not_braking=OriginalFlatteningContrast(
                temperature_change,
                "<=",
                temperature_change_bound,
                temperature_change <= temperature_change_bound,
            ),
        )
    except (AttributeError, IndexError, OverflowError, TypeError, ValueError):
        return None


@dataclass(frozen=True)
class CurveProfile:
    """A mathematical profile in dimensionless x, without event calibration.

    Polynomial coefficients multiply 1, x, x², x³ up to the named degree.
    hinge1/hinge2 use 1, x, x², (x-knot_x)+ or (x-knot_x)+². All
    coefficients have the input value unit. Editing this immutable description
    creates a new profile; no later observation refits it in the evaluator.
    """

    model: str
    coefficients: tuple[float, ...]
    knot_x: float | None = None

    def __post_init__(self):
        counts = {
            "constant": 1,
            "linear": 2,
            "quadratic": 3,
            "cubic": 4,
            "hinge1": 4,
            "hinge2": 4,
        }
        if (
            not isinstance(self.model, str)
            or self.model not in counts
            or not isinstance(self.coefficients, tuple)
            or len(self.coefficients) != counts[self.model]
            or not all(_profile_number(value) for value in self.coefficients)
        ):
            raise ValueError(
                "A profile needs a known basis and finite tuple coefficients",
            )
        if self.model.startswith("hinge"):
            if not _profile_number(self.knot_x):
                raise ValueError("A hinge profile needs a finite normalized knot")
        elif self.knot_x is not None:
            raise ValueError("A polynomial profile has no hinge knot")


@dataclass(frozen=True)
class CurveFitSnapshot:
    """One actual fit, with immutable profile and normalization, never a forecast.

    x=(t-time_origin)/time_scale_seconds; value_center is the original input
    unit. The time scale is a numerical fit coordinate, not an event duration.
    noise_scale retains the initial fit's value-unit MAD; None and zero both
    preclude a normalized error, without precluding a raw curve value/error.
    Sources, binding before new originals and a finite prediction horizon stay
    with the existing consumer. A later fit must not replace a bound snapshot.
    """

    profile: CurveProfile
    time_origin: datetime
    time_scale_seconds: float
    value_center: float
    newest_at: datetime
    noise_scale: float | None

    def __post_init__(self):
        if (
            not isinstance(self.profile, CurveProfile)
            or not isinstance(self.time_origin, datetime)
            or not isinstance(self.newest_at, datetime)
            or not _profile_number(self.time_scale_seconds)
            or self.time_scale_seconds <= 0
            or not _profile_number(self.value_center)
            or self.noise_scale is not None
            and (not _profile_number(self.noise_scale) or self.noise_scale < 0)
        ):
            raise ValueError(
                "A snapshot needs finite normalization and nonnegative noise",
            )
        try:
            valid_span = (self.newest_at - self.time_origin).total_seconds() > 0
        except (TypeError, OverflowError):
            valid_span = False
        if not valid_span:
            raise ValueError("A snapshot needs a positive coherent fitted time span")


def curve_value(
    profile: CurveProfile,
    at: datetime,
    *,
    snapshot: CurveFitSnapshot,
) -> float | None:
    """Evaluate an explicit, possibly manually edited profile without any fit.

    The snapshot's time/value mapping stays fixed. Evaluating outside its fitted
    span is mathematical extrapolation, not evidence of a valid event profile.
    This function neither chooses a profile nor checks source freshness/horizon.
    """
    try:
        if (
            not isinstance(profile, CurveProfile)
            or not isinstance(snapshot, CurveFitSnapshot)
            or not isinstance(at, datetime)
        ):
            return None
        x = (at - snapshot.time_origin).total_seconds() / snapshot.time_scale_seconds
        if not isfinite(x):
            return None
        if profile.model.startswith("hinge"):
            order = 1 if profile.model == "hinge1" else 2
            row = (1.0, x, x * x, max(0.0, x - profile.knot_x) ** order)
        else:
            row = tuple(x**power for power in range(len(profile.coefficients)))
        value = snapshot.value_center + fsum(
            starmap(
                mul,
                zip(row, profile.coefficients, strict=True),
            )
        )
        return value if isfinite(value) else None
    except (AttributeError, OverflowError, TypeError, ValueError, ZeroDivisionError):
        return None


def curve_deviation(
    profile: CurveProfile,
    sample: tuple[datetime, float],
    *,
    snapshot: CurveFitSnapshot,
) -> dict | None:
    """Return observed-minus-expected error in the input unit, without refits.

    normalized_residual uses only the snapshot's unchanged initial noise scale;
    it is not a confidence interval or event probability. In particular, zero
    error stays zero, while missing/zero noise leaves normalization unresolved.
    """
    try:
        at, observed = sample
        if not _profile_number(observed):
            return None
        expected = curve_value(profile, at, snapshot=snapshot)
        if expected is None:
            return None
        residual = observed - expected
        if not isfinite(residual):
            return None
        noise = snapshot.noise_scale
        normalized = residual / noise if noise is not None and noise > 0 else None
        if normalized is not None and not isfinite(normalized):
            normalized = None
        return {
            "expected": expected,
            "observed": observed,
            "residual": residual,
            "noise_scale": noise,
            "normalized_residual": normalized,
        }
    except (AttributeError, OverflowError, TypeError, ValueError, ZeroDivisionError):
        return None


@dataclass(frozen=True)
class CurveFeatures:
    """Local evidence; slope units are input units per second.

    Positive slope_change means a more positive trend, even if both slopes
    remain negative. ``rejects`` is usable counterevidence at a fixed onset;
    None from curve_change means insufficient or degenerate observations.
    The three slopes describe the global hinge fit. hinge_order=2 keeps slope
    continuous at the knot, so slope_change is zero and curvature_change is the
    jump of the second derivative in input units per second squared. Its sign
    is also the queried direction; neither form makes an event decision.
    after_trend independently describes the original observations strictly
    after its fixed onset. An absent after_trend is insufficient direction, while
    direction zero is level evidence; neither provides a direction from the
    global hinge derivative.
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
    hinge_order: int = 1
    curvature_change: float = 0.0
    fit: CurveFitSnapshot | None = None


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
class CurveRecoveryFeatures:
    """A complete observed fall and recovery, without a contact/onset claim.

    valley_at is fitted geometry only. The original flank windows describe
    actual directed support; the caller owns its sources, identity, and fresh
    confirmation. Scores compare the same smooth polynomial family partitioned
    by shape. An unresolved fit or missing original flank returns None instead.
    """

    evidence: str
    start_at: datetime
    newest_at: datetime
    count: int
    noise_scale: float
    model: str
    recovery_score: float | None
    other_score: float | None
    score_gain: float | None
    valley_at: datetime | None = None
    before_count: int = 0
    after_count: int = 0
    decline_start_at: datetime | None = None
    decline_newest_at: datetime | None = None
    recovery_start_at: datetime | None = None
    recovery_newest_at: datetime | None = None
    before_trend: CurveTrendFeatures | None = None
    after_trend: CurveTrendFeatures | None = None


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


@dataclass(frozen=True)
class CurveFormReference:
    """Frozen mathematical geometry, never a physical event or event time.

    parameter_count and search_count retain the original geometry-selection
    cost during a later constrained fit. current_not_before binds the supplied
    current-flank floor; changing it requires a new hypothesis, not a recheck.
    """

    form: str
    model: str
    start_at: datetime
    turn_at: datetime
    turn_kind: str
    knot_at: datetime | None
    hinge_order: int
    parameter_count: int
    search_count: int
    initial_newest_at: datetime
    current_not_before: datetime | None


@dataclass(frozen=True)
class CurveFormModel:
    """One fitted member of the common-support, common-noise comparison."""

    model: str
    score: float
    loss: float
    compatible: bool | None
    turn_at: datetime | None
    turn_kind: str | None
    knot_at: datetime | None
    hinge_order: int
    coefficient_count: int
    parameter_count: int
    search_count: int
    search_cost: float
    geometry_reason: str = ""
    target_turn_count: int = 0
    opposite_turn_count: int = 0


@dataclass(frozen=True)
class CurveFormFeatures:
    """Peak/recovery form evidence with separate identity and deletion checks.

    score_gain compares positive and counter-FORM fits on exactly the same
    originals and noise scale. reference_score_gain compares the frozen branch
    with counterforms. replacement_gain/cost concern only reference identity:
    a better new positive geometry is not negative evidence for the form.
    before_trend/after_trend use their own original flank scales; their scores
    are never added to the common-family scores. No field is a probability.
    """

    evidence: str
    form_evidence: str
    reference_evidence: str
    influence_evidence: str
    reason: str
    form: str
    start_at: datetime
    newest_at: datetime
    count: int
    original_count: int
    noise_scale: float
    positive_score: float | None
    counter_score: float | None
    score_gain: float | None
    positive_model: str | None
    counter_model: str | None
    turn_at: datetime | None
    reference: CurveFormReference | None
    reference_score: float | None
    reference_score_gain: float | None
    replacement_gain: float | None
    replacement_cost: float | None
    fresh_original_count: int
    before_trend: CurveTrendFeatures | None
    after_trend: CurveTrendFeatures | None
    before_count: int
    after_count: int
    removed_at: datetime | None
    influence_score_gain: float | None
    candidate_models: tuple[CurveFormModel, ...]
    score_resolution: float | None = None
    reference_score_resolution: float | None = None
    influence_score_resolution: float | None = None
    observed_form: str | None = None
    reference_model: CurveFormModel | None = None
    replacement_parameter_count: int = 0
    hinge_fits_attempted: int = 0
    hinge_fits_failed: int = 0
    fit: CurveFitSnapshot | None = None


def _policy_valid(policy):
    counts = (
        policy.min_side_samples,
        policy.max_points,
        policy.max_candidates,
        policy.robust_iterations,
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
        fsum(starmap(mul, zip(basis, target, strict=True))) for basis in orthogonal
    ]
    result = [0.0] * count
    for index in reversed(range(count)):
        result[index] = (
            projected[index]
            - fsum(
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
        residual * residual
        if residual <= policy.huber_delta
        else 2 * policy.huber_delta * residual - policy.huber_delta**2
        for residual in normalized
    )
    return (coefficients, loss) if isfinite(loss) else None


def _smooth_models(times, values, noise, policy):
    linear = [[1.0, time] for time in times]
    quadratic = [[1.0, time, time * time] for time in times]
    cubic = [[1.0, time, time * time, time**3] for time in times]
    smooth = []
    for name, rows in (
        ("linear", linear),
        ("quadratic", quadratic),
        ("cubic", cubic),
    ):
        fitted = _fit(rows, values, noise, policy)
        if fitted is None:
            return None
        smooth.append(
            (
                fitted[1] + len(rows[0]) * log(len(values)),
                name,
                _residuals(rows, values, fitted[0]),
                fitted[0],
            )
        )
    return quadratic, smooth


def _models(times, values, noise, knots, policy, *, hinge_order=1, smooth_result=None):
    result = (
        smooth_result
        if smooth_result is not None
        else _smooth_models(times, values, noise, policy)
    )
    if result is None:
        return None
    quadratic, smooth = result
    smooth_score, smooth_model, residuals, _ = min(
        smooth,
        key=lambda item: item[:2],
    )
    changes = []
    for index, knot in knots:
        rows = [
            row
            + [
                max(0.0, time - knot)
                if hinge_order == 1
                else max(0.0, time - knot) ** 2,
            ]
            for row, time in zip(quadratic, times, strict=True)
        ]
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


def _side_change_rejects(
    times,
    values,
    noise,
    knot,
    start,
    change,
    value_resolution,
    policy,
):
    """Assess relative counterevidence only on independently linear sides.

    The existing onset floor excludes earlier state shapes from this additional
    consistency check. If its left side is too short, only the nearest earlier
    originals needed for the existing side minimum are retained. This also
    permits a change exactly at the inclusive floor. The global fit still owns
    noise and primary model evidence. None means auxiliary uncertainty and
    cannot suppress that primary support.

    Each side must prefer a line to both a quadratic and cubic before its mean
    slope is usable here. Those linear sides then compare one shared slope
    against separate slopes, with independent intercepts in both models. The
    fixed split pays no knot-search cost. A shared slope is relative evidence
    for continuation; separate slopes oppose the hinge only with a resolved
    contrary difference. This is no independent positive change confirmation.
    """
    before, after = [], []
    for time, value in zip(times, values, strict=True):
        if time <= knot:
            before.append((time, value))
        else:
            after.append((time, value))
    first = sum(time < start for time, _ in before)
    first = min(first, max(0, len(before) - policy.min_side_samples))
    before = before[first:]
    slopes, losses, resolutions = [], [], []
    for points in (before, after):
        if len(points) < policy.min_side_samples:
            return None
        span = points[-1][0] - points[0][0]
        local_times = [(time - points[0][0]) / span for time, _ in points]
        side_values = [value for _, value in points]
        rows = [[1.0, time] for time in local_times]
        linear = _fit(rows, side_values, noise, policy)
        if linear is None:
            return None
        linear_score = linear[1] + 2 * log(len(points))
        for degree in (2, 3):
            rows = [
                [time**power for power in range(degree + 1)] for time in local_times
            ]
            smooth = _fit(rows, side_values, noise, policy)
            if smooth is None:
                return None
            smooth_score = smooth[1] + (degree + 1) * log(len(points))
            score_resolution = len(points) * ulp(
                max(
                    abs(linear_score),
                    abs(smooth_score),
                )
            )
            if smooth_score - linear_score <= score_resolution:
                return None
        slopes.append(linear[0][1] / span)
        losses.append(linear[1])
        resolutions.append(value_resolution / span)
    points = before + after
    rows = [[1.0, float(time > knot), time] for time, _ in points]
    common = _fit(rows, [value for _, value in points], noise, policy)
    if common is None:
        return None
    common_score = common[1] + 3 * log(len(points))
    separate_score = fsum(losses) + 4 * log(len(points))
    score_gain = common_score - separate_score
    score_resolution = len(points) * ulp(
        max(
            abs(common_score),
            abs(separate_score),
        )
    )
    if not isfinite(score_gain) or abs(score_gain) <= score_resolution:
        return None
    if score_gain < 0:
        return True
    difference = slopes[1] - slopes[0]
    resolution = fsum(resolutions)
    if (
        not isfinite(difference)
        or not isfinite(resolution)
        or abs(difference) <= resolution
    ):
        return None
    return difference * change < 0


def _supported_without_point(
    times,
    values,
    noise,
    index,
    residuals,
    penalty,
    policy,
    *,
    shape_start,
    value_resolution,
    hinge_order=1,
):
    """One bounded refit rejects evidence that rests on one influential value."""
    removed = max(range(len(values)), key=lambda point: (abs(residuals[point]), point))
    knot = times[index]
    retained = [point for point in range(len(values)) if point != removed]
    before = sum(times[point] <= knot for point in retained)
    if min(before, len(retained) - before) < policy.min_side_samples:
        return None
    retained_times = [times[point] for point in retained]
    retained_values = [values[point] for point in retained]
    model_options = {"hinge_order": hinge_order} if hinge_order != 1 else {}
    result = _models(
        retained_times,
        retained_values,
        noise,
        [(index, knot)],
        policy,
        **model_options,
    )
    if result is None:
        return None
    smooth_score, _, _, changes = result
    score, _, coefficients = changes[0]
    resolution = len(values) * ulp(max(abs(smooth_score), abs(score + penalty)))
    if smooth_score - score - penalty <= resolution:
        return False, coefficients[3]
    side_rejects = (
        _side_change_rejects(
            retained_times,
            retained_values,
            noise,
            knot,
            shape_start,
            coefficients[3],
            value_resolution,
            policy,
        )
        if hinge_order == 1
        else None
    )
    return (
        smooth_score - score - penalty > resolution and side_rejects is not True,
        coefficients[3],
    )


def _common_noise(times, raw_values):
    """Existing cubic-residual MAD, shared by every fit in one comparison."""
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
    return noise, center, values, residuals


def _level_models(times, raw_values, policy):
    """Compare a level and smooth trends with one tail-local noise scale."""
    common = _common_noise(times, raw_values)
    if common is None:
        return None
    noise, center, values, residuals = common
    cubic = [[1.0, time, time * time, time**3] for time in times]
    fitted_models = []
    for name, degree in (
        ("constant", 0),
        ("linear", 1),
        ("quadratic", 2),
        ("cubic", 3),
    ):
        rows = [row[: degree + 1] for row in cubic]
        fitted = _fit(rows, values, noise, policy)
        if fitted is None:
            return None
        fitted_models.append(
            (
                fitted[1] + (degree + 1) * log(len(values)),
                name,
                fitted[0],
            )
        )
    level_score, _, coefficients = fitted_models[0]
    trend_score, trend_model, _ = min(
        fitted_models[1:],
        key=lambda item: item[:2],
    )
    score_gain = trend_score - level_score
    score_resolution = len(values) * ulp(max(abs(level_score), abs(trend_score)))
    level = center + coefficients[0]
    if not isfinite(level) or abs(score_gain) <= score_resolution:
        return None
    return (
        noise,
        level,
        level_score,
        trend_score,
        score_gain,
        trend_model,
        residuals,
    )


def curve_level(
    samples: Sequence[tuple[datetime, float]],
    *,
    policy: CurvePolicy,
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
                evidence="supports",
                start_at=stamps[0],
                newest_at=stamps[-1],
                count=len(selected),
                noise_scale=0.0,
                level=raw_values[0],
                level_score=None,
                trend_score=None,
                score_gain=None,
                trend_model=None,
                exact_constant=True,
            )
        times = [(sample[0] - stamps[0]).total_seconds() / span for sample in selected]
        values = [sample[1] for sample in selected]
        result = _level_models(times, values, policy)
        if result is None:
            return None
        (
            noise,
            level,
            level_score,
            trend_score,
            score_gain,
            trend_model,
            residuals,
        ) = result
        supported = score_gain > 0
        if supported:
            removed = max(
                range(len(values)),
                key=lambda point: (
                    abs(residuals[point]),
                    point,
                ),
            )
            retained = [point for point in range(len(values)) if point != removed]
            if len(retained) < 2 * policy.min_side_samples:
                return None
            rescue = _level_models(
                [times[point] for point in retained],
                [values[point] for point in retained],
                policy,
            )
            if rescue is None or rescue[4] <= 0:
                return None
        return CurveLevelFeatures(
            evidence="supports" if supported else "rejects",
            start_at=stamps[0],
            newest_at=stamps[-1],
            count=len(selected),
            noise_scale=noise,
            level=level,
            level_score=level_score,
            trend_score=trend_score,
            score_gain=score_gain,
            trend_model=trend_model,
            exact_constant=False,
        )
    except (
        AttributeError,
        IndexError,
        OverflowError,
        TypeError,
        ValueError,
        ZeroDivisionError,
    ):
        return None


def _calibration_originals(channels, origin):
    """Validate four immutable original streams without merging their clocks."""
    if (
        not isinstance(channels, tuple)
        or len(channels) != 4
        or not isinstance(origin, datetime)
    ):
        return False
    names = []
    for item in channels:
        if not isinstance(item, tuple) or len(item) != 2:
            return False
        name, samples = item
        if (
            not isinstance(name, str)
            or not name
            or name in names
            or not isinstance(samples, tuple)
            or not samples
        ):
            return False
        names.append(name)
        previous = None
        for sample in samples:
            if (
                not isinstance(sample, tuple)
                or len(sample) != 2
                or not isinstance(sample[0], datetime)
                or not _profile_number(sample[1])
            ):
                return False
            try:
                elapsed = (sample[0] - origin).total_seconds()
                if (
                    not isfinite(elapsed)
                    or previous is not None
                    and sample[0] <= previous
                ):
                    return False
            except (TypeError, OverflowError):
                return False
            previous = sample[0]
    return True


@dataclass(frozen=True)
class CurveCalibrationPolicy:
    """Declared numerical limits; no event thresholds or probabilities.

    The four noise floors use their respective channel units. Residual limits
    are nearest-rank empirical quantiles of training continuation mean Huber
    losses, bounded below by residual_loss_floor and multiplied by the declared
    residual_limit_multiplier. They are training bands, not coverage claims.
    max_phase_x is the finite evaluation horizon in the fixed coordinate t/D.
    """

    curve: CurvePolicy
    channels: tuple[str, ...]
    noise_floors: tuple[float, ...]
    min_examples: int
    min_continuation_samples: int
    residual_quantile: float
    residual_limit_multiplier: float
    residual_loss_floor: float
    minimum_gain: float
    max_phase_x: float

    def __post_init__(self):
        try:
            valid_curve = isinstance(self.curve, CurvePolicy) and _policy_valid(
                self.curve
            )
        except (AttributeError, TypeError, ValueError, OverflowError):
            valid_curve = False
        counts = (self.min_examples, self.min_continuation_samples)
        numbers = (
            self.residual_quantile,
            self.residual_limit_multiplier,
            self.residual_loss_floor,
            self.minimum_gain,
            self.max_phase_x,
        )
        if (
            not valid_curve
            or not isinstance(self.channels, tuple)
            or len(self.channels) != 4
            or not all(isinstance(name, str) and name for name in self.channels)
            or len(set(self.channels)) != len(self.channels)
            or not isinstance(self.noise_floors, tuple)
            or len(self.noise_floors) != len(self.channels)
            or not all(
                _profile_number(value) and value > 0 for value in self.noise_floors
            )
            or not all(
                isinstance(value, int) and not isinstance(value, bool) and value > 0
                for value in counts
            )
            or self.min_continuation_samples > self.curve.max_points
            or not all(_profile_number(value) for value in numbers)
            or not 0 < self.residual_quantile <= 1
            or self.residual_limit_multiplier < 1
            or self.residual_loss_floor <= 0
            or self.minimum_gain < 0
            or self.max_phase_x < 1
        ):
            raise ValueError(
                "Calibration needs four channels and finite declared limits"
            )


@dataclass(frozen=True)
class CurveCalibrationExample:
    """Caller-selected originals, before and after one mathematical origin.

    There is no event label in this type. binding_until separates the amplitude
    prefix from later observations used to learn residual bands. The caller
    selects the training end by supplying only that original phase window.
    """

    onset_at: datetime
    binding_until: datetime
    channels: tuple[tuple[str, tuple[tuple[datetime, float], ...]], ...]

    def __post_init__(self):
        try:
            valid_span = (
                isinstance(self.onset_at, datetime)
                and isinstance(self.binding_until, datetime)
                and self.binding_until > self.onset_at
            )
        except TypeError:
            valid_span = False
        if not valid_span or not _calibration_originals(self.channels, self.onset_at):
            raise ValueError("A calibration example needs coherent original streams")


@dataclass(frozen=True)
class CurveTemplateChannel:
    """A relative channel shape; all channels share one bound amplitude."""

    name: str
    profile: CurveProfile
    residual_limit: float

    def __post_init__(self):
        if (
            not isinstance(self.name, str)
            or not self.name
            or not isinstance(self.profile, CurveProfile)
            or self.profile.model not in ("quadratic", "cubic", "hinge1", "hinge2")
            or self.profile.coefficients[0] != 0
            or self.profile.knot_x is not None
            and self.profile.knot_x <= 0
            or not _profile_number(self.residual_limit)
            or self.residual_limit <= 0
        ):
            raise ValueError(
                "A channel needs a zero-origin shape and finite residual band"
            )


@dataclass(frozen=True)
class CurveCalibrationProfile:
    """Learned four-channel geometry in an explicitly fixed time coordinate.

    g_ref(1)=reference_sign fixes the scale, while every relative channel gain
    and delay remains in its coefficients. Learning never changes D to the
    latest observed duration. These are mathematical shapes without labels.
    """

    policy: CurveCalibrationPolicy
    duration_seconds: float
    reference_channel: str
    reference_sign: int
    channels: tuple[CurveTemplateChannel, ...]
    residual_limit: float
    calibration_count: int

    def __post_init__(self):
        if (
            not isinstance(self.policy, CurveCalibrationPolicy)
            or not _profile_number(self.duration_seconds)
            or self.duration_seconds <= 0
            or not isfinite(self.duration_seconds * self.policy.max_phase_x)
            or not isinstance(self.reference_channel, str)
            or self.reference_channel not in self.policy.channels
            or not isinstance(self.reference_sign, int)
            or isinstance(self.reference_sign, bool)
            or self.reference_sign not in (-1, 1)
            or not isinstance(self.channels, tuple)
            or not all(
                isinstance(channel, CurveTemplateChannel) for channel in self.channels
            )
            or tuple(channel.name for channel in self.channels) != self.policy.channels
            or not _profile_number(self.residual_limit)
            or self.residual_limit <= 0
            or not isinstance(self.calibration_count, int)
            or isinstance(self.calibration_count, bool)
            or self.calibration_count < self.policy.min_examples
        ):
            raise ValueError("A calibrated profile needs finite four-channel geometry")
        reference = self.channels[
            self.policy.channels.index(self.reference_channel)
        ].profile
        expected = _calibration_shape(reference, 1.0)
        resolution = (
            _FLOAT_EPSILON
            * len(reference.coefficients)
            * fsum(abs(value) for value in reference.coefficients)
        )
        if (
            expected is None
            or abs(expected - self.reference_sign) > resolution
            or any(
                channel.profile.knot_x is not None
                and channel.profile.knot_x >= self.policy.max_phase_x
                for channel in self.channels
            )
        ):
            raise ValueError("The reference shape must retain g_ref(1)=reference_sign")


@dataclass(frozen=True)
class CurveChannelBaseline:
    """Immutable pre-origin level, slope/second and pre-origin noise scale."""

    name: str
    level: float
    slope: float
    noise_scale: float
    before_count: int
    binding_count: int

    def __post_init__(self):
        if (
            not isinstance(self.name, str)
            or not self.name
            or not all(
                _profile_number(value)
                for value in (self.level, self.slope, self.noise_scale)
            )
            or self.noise_scale <= 0
            or not all(
                isinstance(value, int) and not isinstance(value, bool) and value > 0
                for value in (self.before_count, self.binding_count)
            )
        ):
            raise ValueError(
                "A baseline needs finite values and original sample counts"
            )


@dataclass(frozen=True)
class CurveTemplateBinding:
    """yhat_c=B_c+V_c*(t-tau)+A*g_c((t-tau)/D), fixed once.

    The corresponding null uses exactly the same B,V and A=0. Source identity,
    continuity and event lifecycle remain with the caller. A later correction
    or additional prefix is a new hypothesis, never a mutation of this binding.
    """

    profile: CurveCalibrationProfile
    onset_at: datetime
    binding_until: datetime
    amplitude: float
    channels: tuple[CurveChannelBaseline, ...]

    def __post_init__(self):
        if (
            not isinstance(self.profile, CurveCalibrationProfile)
            or not isinstance(self.onset_at, datetime)
            or not isinstance(self.binding_until, datetime)
            or not _profile_number(self.amplitude)
            or self.amplitude <= 0
            or not isinstance(self.channels, tuple)
            or not all(
                isinstance(channel, CurveChannelBaseline) for channel in self.channels
            )
            or tuple(channel.name for channel in self.channels)
            != self.profile.policy.channels
            or any(
                not self.profile.policy.curve.min_side_samples
                <= count
                <= self.profile.policy.curve.max_points
                for channel in self.channels
                for count in (channel.before_count, channel.binding_count)
            )
            or any(
                channel.noise_scale < floor
                for channel, floor in zip(
                    self.channels,
                    self.profile.policy.noise_floors,
                    strict=True,
                )
            )
        ):
            raise ValueError(
                "A binding needs one finite shared amplitude and four baselines"
            )
        try:
            elapsed = (self.binding_until - self.onset_at).total_seconds()
            valid_span = (
                0
                < elapsed
                <= (self.profile.duration_seconds * self.profile.policy.max_phase_x)
            )
        except (TypeError, OverflowError):
            valid_span = False
        if not valid_span:
            raise ValueError(
                "A binding prefix must lie within the declared phase horizon"
            )


@dataclass(frozen=True)
class CurveChannelScore:
    """Mean twice-Huber losses on later originals of one channel."""

    name: str
    count: int
    original_count: int
    template_loss: float
    null_loss: float
    score_gain: float
    residual_limit: float
    within_residual_limit: bool

    def __post_init__(self):
        if (
            not isinstance(self.name, str)
            or not self.name
            or not _calibration_score_fields(self)
        ):
            raise ValueError("A channel score needs finite losses and original counts")


@dataclass(frozen=True)
class CurveContinuationScore:
    """Frozen continuation evidence, never a probability or event decision.

    Overall losses give each of the four channel means equal weight. Support
    needs gain over the identical baseline null AND the learned overall band
    AND every learned channel band. Old/prefix originals are never scored.
    """

    status: str
    count: int
    original_count: int
    template_loss: float
    null_loss: float
    score_gain: float
    residual_limit: float
    channels: tuple[CurveChannelScore, ...]
    within_residual_limit: bool

    def __post_init__(self):
        if (
            self.status not in ("supports", "rejects")
            or not _calibration_score_fields(self)
            or not isinstance(self.channels, tuple)
            or len(self.channels) != 4
            or not all(
                isinstance(channel, CurveChannelScore) for channel in self.channels
            )
            or len({channel.name for channel in self.channels}) != len(self.channels)
            or self.count != sum(channel.count for channel in self.channels)
            or self.original_count
            != sum(channel.original_count for channel in self.channels)
        ):
            raise ValueError(
                "A continuation score needs four finite channel comparisons"
            )


def _calibration_score_fields(score):
    return (
        all(
            isinstance(value, int) and not isinstance(value, bool) and value > 0
            for value in (score.count, score.original_count)
        )
        and score.count <= score.original_count
        and all(
            _profile_number(value)
            for value in (
                score.template_loss,
                score.null_loss,
                score.score_gain,
                score.residual_limit,
            )
        )
        and score.template_loss >= 0
        and score.null_loss >= 0
        and score.residual_limit > 0
        and isinstance(score.within_residual_limit, bool)
    )


def _calibration_row(model, x, knot):
    if model.startswith("hinge"):
        return (x, x * x, max(0.0, x - knot) ** (1 if model == "hinge1" else 2))
    return tuple(x**power for power in range(1, 4 if model == "cubic" else 3))


def _calibration_shape(profile, x):
    try:
        value = fsum(
            starmap(
                mul,
                zip(
                    _calibration_row(profile.model, x, profile.knot_x),
                    profile.coefficients[1:],
                    strict=True,
                ),
            )
        )
        return value if isfinite(value) else None
    except (OverflowError, TypeError, ValueError):
        return None


def _calibration_series(channels, policy, origin):
    if not _calibration_originals(channels, origin) or {
        item[0] for item in channels
    } != set(policy.channels):
        return None
    by_name = dict(channels)
    return tuple(by_name[name] for name in policy.channels)


def _calibration_baseline(samples, origin, floor, policy):
    if len(samples) < policy.min_side_samples or samples[-1][0] >= origin:
        return None
    selected = _bounded_samples(samples, policy.max_points, None)
    if len(selected) < policy.min_side_samples:
        return None
    span = (selected[-1][0] - selected[0][0]).total_seconds()
    if span <= 0:
        return None
    rows = [[1.0, (at - origin).total_seconds() / span] for at, _ in selected]
    center = median(value for _, value in selected)
    values = [value - center for _, value in selected]
    initial = _least_squares(rows, values, [1.0] * len(values))
    if initial is None:
        return None
    residuals = _residuals(rows, values, initial)
    residual_center = median(residuals)
    noise = max(
        floor,
        _MAD_NORMALIZATION
        * median(abs(value - residual_center) for value in residuals),
    )
    fitted = _fit(rows, values, noise, policy)
    if fitted is None:
        return None
    level, slope = center + fitted[0][0], fitted[0][1] / span
    if not all(isfinite(value) for value in (level, slope, noise)):
        return None
    return level, slope, noise, len(selected)


def curve_template_value(
    binding: CurveTemplateBinding,
    channel: str,
    at: datetime,
    *,
    null: bool = False,
) -> float | None:
    """Evaluate the unchanged bound template/null within its declared horizon."""
    try:
        if (
            not isinstance(binding, CurveTemplateBinding)
            or not isinstance(null, bool)
            or not isinstance(at, datetime)
        ):
            return None
        index = binding.profile.policy.channels.index(channel)
        elapsed = (at - binding.onset_at).total_seconds()
        x = elapsed / binding.profile.duration_seconds
        if not isfinite(x) or not 0 <= x <= binding.profile.policy.max_phase_x:
            return None
        baseline = binding.channels[index]
        value = baseline.level + baseline.slope * elapsed
        if not null:
            shape = _calibration_shape(binding.profile.channels[index].profile, x)
            if shape is None:
                return None
            value += binding.amplitude * shape
        return value if isfinite(value) else None
    except (AttributeError, OverflowError, TypeError, ValueError, ZeroDivisionError):
        return None


def bind_curve_template(
    profile: CurveCalibrationProfile,
    before,
    prefix,
    *,
    onset_at: datetime,
    binding_until: datetime,
) -> CurveTemplateBinding | None:
    """Fit B,V on strictly pre-origin originals, then one A on the prefix.

    Channels are immutable (name, originals) tuples. Prefix originals must lie
    in [onset_at,binding_until]; no later point can enter baseline, noise or A.
    Time/value centering and original-point bounding reuse the curve owner.
    """
    try:
        if not isinstance(profile, CurveCalibrationProfile):
            return None
        policy = profile.policy
        elapsed = (binding_until - onset_at).total_seconds()
        if not 0 < elapsed <= profile.duration_seconds * policy.max_phase_x:
            return None
        pre = _calibration_series(before, policy, onset_at)
        initial = _calibration_series(prefix, policy, onset_at)
        if pre is None or initial is None:
            return None
        baselines, rows, values = [], [], []
        for index, name in enumerate(policy.channels):
            originals = initial[index]
            if (
                len(originals) < policy.curve.min_side_samples
                or originals[0][0] < onset_at
                or originals[-1][0] > binding_until
            ):
                return None
            baseline = _calibration_baseline(
                pre[index],
                onset_at,
                policy.noise_floors[index],
                policy.curve,
            )
            if baseline is None:
                return None
            level, slope, noise, before_count = baseline
            selected = _bounded_samples(originals, policy.curve.max_points, None)
            if len(selected) < policy.curve.min_side_samples:
                return None
            baselines.append(
                CurveChannelBaseline(
                    name,
                    level,
                    slope,
                    noise,
                    before_count,
                    len(selected),
                )
            )
            for at, value in selected:
                seconds = (at - onset_at).total_seconds()
                shape = _calibration_shape(
                    profile.channels[index].profile,
                    seconds / profile.duration_seconds,
                )
                if shape is None:
                    return None
                rows.append([shape / noise])
                values.append((value - level - slope * seconds) / noise)
        fitted = _fit(rows, values, 1.0, policy.curve)
        if fitted is None or fitted[0][0] <= 0:
            return None
        return CurveTemplateBinding(
            profile,
            onset_at,
            binding_until,
            fitted[0][0],
            tuple(baselines),
        )
    except (
        AttributeError,
        IndexError,
        OverflowError,
        TypeError,
        ValueError,
        ZeroDivisionError,
    ):
        return None


def _calibration_loss(residuals, delta):
    return fsum(
        value * value if abs(value) <= delta else delta * (2 * abs(value) - delta)
        for value in residuals
    ) / len(residuals)


def score_curve_continuation(
    binding: CurveTemplateBinding,
    later,
) -> CurveContinuationScore | None:
    """Compare only later own originals; this function never performs a fit."""
    try:
        if not isinstance(binding, CurveTemplateBinding):
            return None
        policy = binding.profile.policy
        originals = _calibration_series(later, policy, binding.onset_at)
        if originals is None:
            return None
        channels = []
        for index, name in enumerate(policy.channels):
            samples = originals[index]
            if (
                len(samples) < policy.min_continuation_samples
                or samples[0][0] <= binding.binding_until
            ):
                return None
            selected = _bounded_samples(samples, policy.curve.max_points, None)
            if len(selected) < policy.min_continuation_samples:
                return None
            residuals, null_residuals = [], []
            noise = binding.channels[index].noise_scale
            for at, observed in selected:
                expected = curve_template_value(binding, name, at)
                null_value = curve_template_value(binding, name, at, null=True)
                if expected is None or null_value is None:
                    return None
                residuals.append((observed - expected) / noise)
                null_residuals.append((observed - null_value) / noise)
            loss = _calibration_loss(residuals, policy.curve.huber_delta)
            null_loss = _calibration_loss(null_residuals, policy.curve.huber_delta)
            limit = binding.profile.channels[index].residual_limit
            channels.append(
                CurveChannelScore(
                    name,
                    len(selected),
                    len(samples),
                    loss,
                    null_loss,
                    null_loss - loss,
                    limit,
                    loss <= limit,
                )
            )
        loss = fsum(channel.template_loss for channel in channels) / len(channels)
        null_loss = fsum(channel.null_loss for channel in channels) / len(channels)
        within = loss <= binding.profile.residual_limit and all(
            channel.within_residual_limit for channel in channels
        )
        gain = null_loss - loss
        return CurveContinuationScore(
            "supports" if within and gain > policy.minimum_gain else "rejects",
            sum(channel.count for channel in channels),
            sum(channel.original_count for channel in channels),
            loss,
            null_loss,
            gain,
            binding.profile.residual_limit,
            tuple(channels),
            within,
        )
    except (
        AttributeError,
        IndexError,
        OverflowError,
        TypeError,
        ValueError,
        ZeroDivisionError,
    ):
        return None


def fit_curve_calibration(
    examples: Sequence[CurveCalibrationExample],
    *,
    policy: CurveCalibrationPolicy,
    duration_seconds: float,
    reference_channel: str,
    reference_sign: int,
    basis: str,
    knot_x: float | None = None,
) -> CurveCalibrationProfile | None:
    """Learn one declared P2/P3/M1/M2 candidate and its residual bands.

    P2/P3 use x,x²[,x³]; M1/M2 use x,x²,(x-k)+[²], with zero intercept.
    Basis and knot are supplied, never searched here. Full training phases
    determine shape coefficients and reference-channel normalization units.
    These training units are not a detection amplitude: final bands always
    use a fresh binding whose sole shared A sees only its declared prefix.
    The resulting bands are in-sample training residuals; held-out recordings
    and event interpretations belong to the caller's quality assessment.
    """
    try:
        models = {"P2": "quadratic", "P3": "cubic", "M1": "hinge1", "M2": "hinge2"}
        if (
            not isinstance(policy, CurveCalibrationPolicy)
            or not _profile_number(duration_seconds)
            or duration_seconds <= 0
            or reference_channel not in policy.channels
            or not isinstance(reference_sign, int)
            or isinstance(reference_sign, bool)
            or reference_sign not in (-1, 1)
            or not isinstance(basis, str)
            or basis not in models
            or len(examples) < policy.min_examples
            or not all(
                isinstance(example, CurveCalibrationExample) for example in examples
            )
        ):
            return None
        model = models[basis]
        if model.startswith("hinge"):
            if not _profile_number(knot_x) or not 0 < knot_x < policy.max_phase_x:
                return None
        elif knot_x is not None:
            return None
        reference_index = policy.channels.index(reference_channel)
        training = []
        for example in examples:
            originals = _calibration_series(example.channels, policy, example.onset_at)
            if originals is None:
                return None
            before = tuple(
                tuple(sample for sample in samples if sample[0] < example.onset_at)
                for samples in originals
            )
            phase = tuple(
                tuple(sample for sample in samples if sample[0] >= example.onset_at)
                for samples in originals
            )
            baselines = []
            for index, samples in enumerate(phase):
                if (
                    len(samples) < policy.curve.min_side_samples
                    or (samples[-1][0] - example.onset_at).total_seconds()
                    > duration_seconds * policy.max_phase_x
                ):
                    return None
                baseline = _calibration_baseline(
                    before[index],
                    example.onset_at,
                    policy.noise_floors[index],
                    policy.curve,
                )
                if baseline is None:
                    return None
                baselines.append(baseline)
            selected = _bounded_samples(
                phase[reference_index],
                policy.curve.max_points,
                None,
            )
            level, slope, noise, _ = baselines[reference_index]
            rows, values = [], []
            for at, value in selected:
                seconds = (at - example.onset_at).total_seconds()
                rows.append(_calibration_row(model, seconds / duration_seconds, knot_x))
                values.append(value - level - slope * seconds)
            fitted = _fit(rows, values, noise, policy.curve)
            if fitted is None:
                return None
            unit = reference_sign * fsum(
                starmap(
                    mul,
                    zip(
                        _calibration_row(model, 1.0, knot_x),
                        fitted[0],
                        strict=True,
                    ),
                )
            )
            if not isfinite(unit) or unit <= 0:
                return None
            training.append((example, before, phase, baselines, unit))
        shapes = []
        for index in range(len(policy.channels)):
            rows, values = [], []
            for example, _, phase, baselines, unit in training:
                level, slope, noise, _ = baselines[index]
                for at, value in _bounded_samples(
                    phase[index], policy.curve.max_points, None
                ):
                    seconds = (at - example.onset_at).total_seconds()
                    rows.append(
                        tuple(
                            coefficient * unit / noise
                            for coefficient in _calibration_row(
                                model, seconds / duration_seconds, knot_x
                            )
                        )
                    )
                    values.append((value - level - slope * seconds) / noise)
            fitted = _fit(rows, values, 1.0, policy.curve)
            if fitted is None:
                return None
            shapes.append(CurveProfile(model, (0.0, *fitted[0]), knot_x))
        reference_value = _calibration_shape(shapes[reference_index], 1.0)
        if reference_value is None or reference_value * reference_sign <= 0:
            return None
        factor = reference_sign / reference_value
        initial_limit = policy.residual_loss_floor * policy.residual_limit_multiplier
        channels = tuple(
            CurveTemplateChannel(
                name,
                CurveProfile(
                    model, tuple(value * factor for value in shape.coefficients), knot_x
                ),
                initial_limit,
            )
            for name, shape in zip(policy.channels, shapes, strict=True)
        )
        profile = CurveCalibrationProfile(
            policy,
            duration_seconds,
            reference_channel,
            reference_sign,
            channels,
            initial_limit,
            len(training),
        )
        scores = []
        for example, before, phase, _, _ in training:
            pre = tuple(zip(policy.channels, before, strict=True))
            prefix = tuple(
                (
                    name,
                    tuple(
                        sample
                        for sample in samples
                        if sample[0] <= example.binding_until
                    ),
                )
                for name, samples in zip(policy.channels, phase, strict=True)
            )
            later = tuple(
                (
                    name,
                    tuple(
                        sample
                        for sample in samples
                        if sample[0] > example.binding_until
                    ),
                )
                for name, samples in zip(policy.channels, phase, strict=True)
            )
            binding = bind_curve_template(
                profile,
                pre,
                prefix,
                onset_at=example.onset_at,
                binding_until=example.binding_until,
            )
            score = score_curve_continuation(binding, later)
            if score is None:
                return None
            scores.append(score)

        def learned_limit(losses):
            ordered = sorted(losses)
            quantile = ordered[ceil(policy.residual_quantile * len(ordered)) - 1]
            return (
                max(policy.residual_loss_floor, quantile)
                * policy.residual_limit_multiplier
            )

        return CurveCalibrationProfile(
            policy,
            duration_seconds,
            reference_channel,
            reference_sign,
            tuple(
                CurveTemplateChannel(
                    channel.name,
                    channel.profile,
                    learned_limit(
                        score.channels[index].template_loss for score in scores
                    ),
                )
                for index, channel in enumerate(channels)
            ),
            learned_limit(score.template_loss for score in scores),
            len(training),
        )
    except (
        AttributeError,
        IndexError,
        KeyError,
        OverflowError,
        TypeError,
        ValueError,
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
        return (
            CurveTrendFeatures(
                direction=0,
                slope=0.0,
                noise_scale=0.0,
                level_score=None,
                trend_score=None,
                score_gain=None,
                exact_constant=True,
                exact_linear=False,
            ),
            None,
            None,
        )
    # Check every original before downsampling. An omitted excursion must not
    # turn quantized observations into an exact line or an exact constant.
    collinear = (
        isfinite(delta)
        and abs(delta) > resolution
        and all(
            abs(
                (value - raw_values[0])
                - delta * ((stamp - stamps[0]).total_seconds() / span)
            )
            <= resolution
            for stamp, value in samples
        )
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
        return (
            CurveTrendFeatures(
                direction=1 if delta > 0 else -1,
                slope=slope,
                noise_scale=0.0,
                level_score=None,
                trend_score=None,
                score_gain=None,
                exact_constant=False,
                exact_linear=True,
            ),
            None,
            None,
        )
    selected = _bounded_samples(samples, policy.max_points, None)
    if len(selected) < policy.min_side_samples:
        return None
    values = [sample[1] for sample in selected]
    center = median(values)
    centered = [value - center for value in values]
    if not all(isfinite(value) for value in centered):
        return None
    rows = [[1.0, (stamp - stamps[0]).total_seconds() / span] for stamp, _ in selected]
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
    time_spread = fsum((time - time_center) ** 2 for time in times)
    if not isfinite(time_spread) or time_spread <= 0:
        return None
    influences = []
    for time, residual in zip(times, residuals, strict=True):
        # A high-leverage endpoint can have a tiny fitted residual although
        # removing it changes the entire direction. External deletion residuals
        # r/(1-h) account for that influence without another fit per point.
        remaining = fsum(
            (
                1.0,
                -1 / len(times),
                -((time - time_center) ** 2) / time_spread,
            )
        )
        unresolved = remaining <= len(times) * _FLOAT_EPSILON
        influences.append(
            (
                unresolved,
                0.0 if unresolved else abs(residual) / remaining,
            )
        )
    return (
        CurveTrendFeatures(
            direction=(1 if slope > 0 else -1) if score_gain > 0 else 0,
            slope=slope,
            noise_scale=noise,
            level_score=level_score,
            trend_score=trend_score,
            score_gain=score_gain,
            exact_constant=False,
            exact_linear=False,
        ),
        selected,
        influences,
    )


def curve_trend(
    samples: Sequence[tuple[datetime, float]],
    *,
    policy: CurvePolicy,
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
            removed = max(
                range(len(selected)),
                key=lambda point: (
                    influences[point],
                    point,
                ),
            )
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
        AttributeError,
        IndexError,
        OverflowError,
        TypeError,
        ValueError,
        ZeroDivisionError,
    ):
        return None


def _smooth_valley(coefficients, value_resolution, count):
    """Return a normalized minimum, False for another shape, or None if unclear."""
    if len(coefficients) == 2:
        return False
    scale = max(abs(value) for value in coefficients[1:])
    if scale == 0:
        return False
    first = coefficients[1] / scale
    linear = 2 * (coefficients[2] / scale)
    quadratic = 3 * (coefficients[3] / scale) if len(coefficients) == 4 else 0.0
    last = fsum((first, linear, quadratic))
    resolution = max(
        value_resolution / scale,
        count * _FLOAT_EPSILON * fsum(map(abs, (first, linear, quadratic))),
    )
    if not isfinite(resolution) or min(abs(first), abs(last)) <= resolution:
        return None
    if first >= 0 or last <= 0:
        return False
    if quadratic == 0:
        roots = [-first / linear]
    else:
        discriminant = linear * linear - 4 * quadratic * first
        discriminant_resolution = count * ulp(
            max(
                linear * linear,
                abs(4 * quadratic * first),
            )
        )
        if discriminant <= discriminant_resolution:
            return None
        root = sqrt(discriminant)
        numerator = -0.5 * (linear + (root if linear >= 0 else -root))
        if numerator == 0:
            return None
        roots = [numerator / quadratic, first / numerator]
    minima = [
        root
        for root in roots
        if (
            isfinite(root)
            and 0 < root < 1
            and linear + 2 * quadratic * root > resolution
        )
    ]
    if len(minima) != 1:
        return None
    root = minima[0]
    if min(root, 1 - root) <= count * _FLOAT_EPSILON:
        return None
    return root


def _recovery_assessment(samples, selected, noise, current_not_before, policy):
    start, newest = samples[0][0], samples[-1][0]
    span = (newest - start).total_seconds()
    times = [(stamp - start).total_seconds() / span for stamp, _ in selected]
    raw_values = [value for _, value in selected]
    center = median(raw_values)
    values = [value - center for value in raw_values]
    if not all(isfinite(value) for value in values):
        return None
    result = _smooth_models(times, values, noise, policy)
    if result is None:
        return None
    _, smooth = result
    value_resolution = len(selected) * ulp(max(abs(value) for value in raw_values))
    recovery, other = [], []
    for model in smooth:
        valley = _smooth_valley(model[3], value_resolution, len(selected))
        if valley is None:
            return None
        (other if valley is False else recovery).append((*model, valley))
    other_score, other_name, residuals, _, _ = min(other, key=lambda item: item[:2])
    if not recovery:
        return CurveRecoveryFeatures(
            evidence="rejects",
            start_at=start,
            newest_at=newest,
            count=len(samples),
            noise_scale=noise,
            model=other_name,
            recovery_score=None,
            other_score=other_score,
            score_gain=None,
        ), residuals
    recovery_score, name, _, _, valley = min(recovery, key=lambda item: item[:2])
    score_gain = other_score - recovery_score
    score_resolution = len(selected) * ulp(max(abs(other_score), abs(recovery_score)))
    if abs(score_gain) <= score_resolution:
        return None
    if score_gain < 0:
        return CurveRecoveryFeatures(
            evidence="rejects",
            start_at=start,
            newest_at=newest,
            count=len(samples),
            noise_scale=noise,
            model=other_name,
            recovery_score=recovery_score,
            other_score=other_score,
            score_gain=score_gain,
        ), residuals
    valley_at = start + timedelta(seconds=valley * span)
    before = [point for point in samples if point[0] < valley_at]
    after = [
        point
        for point in samples
        if (
            point[0] > valley_at
            and (current_not_before is None or point[0] >= current_not_before)
        )
    ]
    if min(len(before), len(after)) < policy.min_side_samples:
        return None
    before_trend = curve_trend(before, policy=policy)
    after_trend = curve_trend(after, policy=policy)
    if before_trend is None or after_trend is None:
        return None
    supported = before_trend.direction == -1 and after_trend.direction == 1
    return CurveRecoveryFeatures(
        evidence="supports" if supported else "rejects",
        start_at=start,
        newest_at=newest,
        count=len(samples),
        noise_scale=noise,
        model=name,
        recovery_score=recovery_score,
        other_score=other_score,
        score_gain=score_gain,
        valley_at=valley_at,
        before_count=len(before),
        after_count=len(after),
        decline_start_at=before[0][0],
        decline_newest_at=before[-1][0],
        recovery_start_at=after[0][0],
        recovery_newest_at=after[-1][0],
        before_trend=before_trend,
        after_trend=after_trend,
    ), residuals


def curve_recovery(
    samples: Sequence[tuple[datetime, float]],
    *,
    policy: CurvePolicy,
    current_not_before: datetime | None = None,
) -> CurveRecoveryFeatures | None:
    """Assess a complete smooth fall and rise without assigning an event onset.

    The caller supplies one coherent original segment in its permitted context.
    The existing linear, quadratic and cubic models share cubic-residual MAD,
    Huber loss and their usual BIC costs. A resolved single interior valley
    belongs to the recovery class; the same fit cannot also oppose that class.
    Its stationary time is derived geometry, with no searched-knot parameter.

    Actual originals strictly before the valley must support a falling trend.
    Originals strictly after it, additionally restricted by the inclusive
    current_not_before, must support a rising current trend. Neither direction
    comes from extrapolating the fitted derivatives. The entire form and both
    original flanks must survive removal of an influential competing-fit value.
    None means unresolved or insufficient evidence; a resolved counterform
    returns rejects. Sources, identity and fresh confirmation remain the caller's.
    """
    try:
        if not _policy_valid(policy) or len(samples) < 2 * policy.min_side_samples:
            return None
        if current_not_before is not None and not isinstance(
            current_not_before,
            datetime,
        ):
            return None
        stamps = [stamp for stamp, _ in samples]
        raw_values = [value for _, value in samples]
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
        times = [(stamp - stamps[0]).total_seconds() / span for stamp, _ in selected]
        values = [value for _, value in selected]
        center = median(values)
        centered = [value - center for value in values]
        if not all(isfinite(value) for value in centered):
            return None
        cubic = [[1.0, time, time * time, time**3] for time in times]
        preliminary = _least_squares(cubic, centered, [1.0] * len(centered))
        if preliminary is None:
            return None
        residuals = _residuals(cubic, centered, preliminary)
        residual_center = median(residuals)
        noise = _MAD_NORMALIZATION * median(
            abs(residual - residual_center) for residual in residuals
        )
        resolution = len(values) * ulp(max(abs(value) for value in raw_values))
        if not isfinite(noise) or noise <= resolution:
            exact = curve_trend(samples, policy=policy)
            if exact is None or not (exact.exact_constant or exact.exact_linear):
                return None
            return CurveRecoveryFeatures(
                evidence="rejects",
                start_at=stamps[0],
                newest_at=stamps[-1],
                count=len(samples),
                noise_scale=0.0,
                model="constant" if exact.exact_constant else "linear",
                recovery_score=None,
                other_score=None,
                score_gain=None,
            )
        result = _recovery_assessment(
            samples,
            selected,
            noise,
            current_not_before,
            policy,
        )
        if result is None:
            return None
        features, residuals = result
        if features.evidence == "supports":
            removed = max(
                range(len(selected)),
                key=lambda point: (
                    abs(residuals[point]),
                    point,
                ),
            )
            removed_at = selected[removed][0]
            retained = [point for point in samples if point[0] != removed_at]
            retained_selected = [
                point for index, point in enumerate(selected) if index != removed
            ]
            if len(retained_selected) < 2 * policy.min_side_samples:
                return None
            rescue = _recovery_assessment(
                retained,
                retained_selected,
                noise,
                current_not_before,
                policy,
            )
            if rescue is None or rescue[0].evidence != "supports":
                return None
        return features
    except (
        AttributeError,
        IndexError,
        OverflowError,
        TypeError,
        ValueError,
        ZeroDivisionError,
    ):
        return None


def curve_change(
    samples: Sequence[tuple[datetime, float]],
    *,
    policy: CurvePolicy,
    onset_at: datetime | None = None,
    direction: int | None = None,
    onset_not_before: datetime | None = None,
    onset_bounds: tuple[datetime, datetime] | None = None,
    hinge_order: int = 1,
) -> CurveFeatures | None:
    """Compare local smooth and changed curves without retaining any state.

    The caller supplies one coherent original source segment in strictly
    increasing time order. Downsampling keeps original observations and bounds
    the numerical work; it never creates interpolated evidence. A supplied onset
    is re-evaluated without moving it. None cannot reject a detector candidate.

    hinge_order explicitly chooses a slope jump (1) or a curvature jump with
    continuous slope (2). This is part of the fixed hypothesis and must be
    supplied unchanged for later onset rechecks; no automatic form search occurs.
    direction restricts the sign of that selected change to -1 or +1. An inclusive
    onset_not_before restricts candidate knots before the search budget is
    applied; earlier original observations still support every fit and its noise
    scale. Fixed-onset and replacement comparisons use the same query limits.
    Optional inclusive onset_bounds narrow only the searched knot positions;
    they do not trim observations or change the before/after context. The
    caller supplies these bounds from an already bound original transition.
    A supported hinge remains usable when its separate right-hand direction is
    insufficient; the caller must require after_trend for a directed route.
    Independent lines on the original sides use onset_not_before, retaining
    nearest earlier originals only when needed for the existing side minimum.
    For order 1, when both sides prefer lines, a shared slope or a resolved
    contrary change supplies additional counterevidence, including after point
    removal. This slope comparison does not assess a curvature change and is
    unused for order 2. Curved or unresolved sides leave the primary comparison
    unchanged.
    """
    try:
        if (
            isinstance(hinge_order, bool)
            or not isinstance(hinge_order, int)
            or hinge_order not in (1, 2)
        ):
            return None
        if direction is not None and (
            isinstance(direction, bool)
            or not isinstance(direction, int)
            or direction not in (-1, 1)
        ):
            return None
        if onset_not_before is not None and not isinstance(onset_not_before, datetime):
            return None
        if onset_bounds is not None and (
            not isinstance(onset_bounds, tuple) or len(onset_bounds) != 2
            or not all(isinstance(bound, datetime) for bound in onset_bounds)
            or onset_bounds[0] > onset_bounds[1]
        ):
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
            policy.min_side_samples - 1,
            len(times) - policy.min_side_samples - 1,
        )
        eligible = [
            index
            for index in range(
                policy.min_side_samples - 1, len(times) - policy.min_side_samples
            )
            if onset_not_before is None or stamps[index] >= onset_not_before
            if onset_bounds is None or onset_bounds[0] <= stamps[index] <= onset_bounds[1]
        ]
        if not eligible:
            return None
        searched = [
            eligible[index] for index in _indices(len(eligible), policy.max_candidates)
        ]
        if onset_at is not None:
            index = stamps.index(onset_at)
            if index not in eligible:
                return None
            if index not in searched:
                if len(searched) == policy.max_candidates:
                    nearest = min(
                        range(len(searched)),
                        key=lambda point: (
                            abs(times[searched[point]] - times[index]),
                            point,
                        ),
                    )
                    searched[nearest] = index
                else:
                    searched.append(index)
                searched.sort()
        knots = [(index, times[index]) for index in searched]
        model_options = {"hinge_order": hinge_order} if hinge_order != 1 else {}
        result = _models(times, values, noise, knots, policy, **model_options)
        if result is None:
            return None
        smooth_score, smooth_model, residuals, changes = result
        compatible = [
            item for item in changes if direction is None or item[2][3] * direction > 0
        ]
        if onset_at is None:
            if not compatible:
                return None
            change_score, index, coefficients = min(
                compatible,
                key=lambda item: item[:2],
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
        shape_start = (
            (onset_not_before - stamps[0]).total_seconds() / span
            if onset_not_before is not None
            else times[0]
        )
        if supported and hinge_order == 1:
            side_rejects = _side_change_rejects(
                times,
                values,
                noise,
                times[index],
                shape_start,
                coefficients[3],
                resolution,
                policy,
            )
            supported = side_rejects is not True
        if supported:
            rescue = _supported_without_point(
                times,
                values,
                noise,
                index,
                residuals,
                search_penalty,
                policy,
                shape_start=shape_start,
                value_resolution=resolution,
                hinge_order=hinge_order,
            )
            if rescue is None or not rescue[0] or rescue[1] * coefficients[3] <= 0:
                return None
        slope_before = (coefficients[1] + 2 * coefficients[2] * times[index]) / span
        slope_change = coefficients[3] / span if hinge_order == 1 else 0.0
        slope_after = slope_before + slope_change if hinge_order == 1 else slope_before
        curvature_change = 2 * coefficients[3] / span**2 if hinge_order == 2 else 0.0
        strength = (
            coefficients[3] * (times[-1] - times[index]) / noise
            if hinge_order == 1
            else coefficients[3] * (times[-1] - times[index]) ** 2 / noise
        )
        finite = (
            slope_before,
            slope_after,
            slope_change,
            strength,
            smooth_score,
            change_score,
            curvature_change,
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
            onset_at=stamps[index],
            newest_at=stamps[-1],
            before_count=index + 1,
            after_count=len(times) - index - 1,
            noise_scale=noise,
            slope_before=slope_before,
            slope_after=slope_after,
            slope_change=slope_change,
            signed_strength=strength,
            smooth_score=smooth_score,
            change_score=change_score,
            score_gain=score_gain,
            smooth_model=smooth_model,
            after_trend=after_trend,
            hinge_order=hinge_order,
            curvature_change=curvature_change,
            fit=CurveFitSnapshot(
                profile=CurveProfile(
                    f"hinge{hinge_order}",
                    tuple(coefficients),
                    times[index],
                ),
                time_origin=stamps[0],
                time_scale_seconds=span,
                value_center=center,
                newest_at=stamps[-1],
                noise_scale=noise,
            ),
        )
    except (
        AttributeError,
        IndexError,
        OverflowError,
        TypeError,
        ValueError,
        ZeroDivisionError,
    ):
        # Bad/degenerate local windows must not interrupt the product loop.
        return None


def _form_turn(coefficients, knot, order, form, value_resolution, count):
    """Classify fitted geometry, independently of its parameterization.

    A reflected peak is a minimum. Derivative roots and the hinge knot divide
    the *whole fitted support* into directed intervals. A unique target turn
    can coexist with an opposite background turn; global endpoint directions
    do not define the target. Multiple target turns are ambiguous. The chosen
    geometry still needs independent, ordered original flanks and deletion
    support; a mathematical maximum alone is not positive form evidence.
    """
    orientation = -1 if form == "peak" else 1
    coefficients = [orientation * value for value in coefficients]
    if len(coefficients) < 3:
        return False, None, None, "no_interior_target_turn", 0, 0
    scale = max(abs(value) for value in coefficients[1:])
    if scale == 0:
        return False, None, None, "no_interior_target_turn", 0, 0
    normalized = [value / scale for value in coefficients]
    resolution = max(
        value_resolution / scale,
        count
        * _FLOAT_EPSILON
        * fsum(power * abs(value) for power, value in enumerate(normalized)),
    )
    nodes = {0.0, 1.0}
    if order:
        _, first, quadratic, change = normalized
        right_first = first + change if order == 1 else first - 2 * change * knot
        right_quadratic = quadratic if order == 1 else quadratic + change
        nodes.add(knot)
        for intercept, curvature, lo, hi in (
            (first, quadratic, 0.0, knot),
            (right_first, right_quadratic, knot, 1.0),
        ):
            if curvature:
                root = -intercept / (2 * curvature)
                if isfinite(root) and lo < root < hi:
                    nodes.add(root)

        def derivative(at):
            return (
                first + 2 * quadratic * at
                if at < knot
                else right_first + 2 * right_quadratic * at
            )
    else:
        first = normalized[1]
        linear = 2 * normalized[2]
        quadratic = 3 * normalized[3] if len(normalized) == 4 else 0.0
        roots = []
        if quadratic == 0:
            if linear:
                roots = [-first / linear]
        else:
            discriminant = linear * linear - 4 * quadratic * first
            discriminant_resolution = count * ulp(
                max(
                    linear * linear,
                    abs(4 * quadratic * first),
                )
            )
            if abs(discriminant) <= discriminant_resolution:
                return None, None, None, "stationary_discriminant_unresolved", 0, 0
            if discriminant > discriminant_resolution:
                root = sqrt(discriminant)
                numerator = -0.5 * (linear + (root if linear >= 0 else -root))
                if numerator == 0:
                    return None, None, None, "stationary_root_unresolved", 0, 0
                roots = [numerator / quadratic, first / numerator]
        nodes.update(root for root in roots if isfinite(root) and 0 < root < 1)

        def derivative(at):
            return fsum((first, linear * at, quadratic * at * at))

    nodes = sorted(nodes)
    directions = []
    for left, right in zip(nodes, nodes[1:]):
        slope = derivative((left + right) / 2)
        directions.append(0 if abs(slope) <= resolution else 1 if slope > 0 else -1)
    minima, maxima = [], []
    for index, (before, after) in enumerate(zip(directions, directions[1:]), 1):
        if before < 0 < after:
            minima.append(nodes[index])
        elif before > 0 > after:
            maxima.append(nodes[index])
    if len(minima) > 1:
        return None, None, None, "multiple_target_turns", len(minima), len(maxima)
    if not minima:
        nonzero = [direction for direction in directions if direction]
        if 0 in directions and any(a < 0 < b for a, b in zip(nonzero, nonzero[1:])):
            return None, None, None, "flat_target_interval_unresolved", 0, len(maxima)
        return False, None, None, "no_interior_target_turn", 0, len(maxima)
    turn = minima[0]
    if min(turn, 1 - turn) <= count * _FLOAT_EPSILON:
        return None, None, None, "target_at_support_resolution", 1, len(maxima)
    # At an M1 knot, one one-sided derivative may be zero while the adjacent
    # observed branches still reverse direction. Bind the searched knot time;
    # a stationary constraint would incorrectly force the *other* derivative
    # to zero. The original flank and deletion checks remain mandatory.
    kind = "knot" if order == 1 and turn == knot else "stationary"
    reason = (
        "unique_target_with_opposite_background_turn"
        if maxima
        else "unique_interior_target_turn"
    )
    return True, turn, kind, reason, 1, len(maxima)


def _form_record(
    name,
    fitted,
    times,
    values,
    noise,
    start,
    span,
    form,
    value_resolution,
    *,
    knot=None,
    order=0,
    parameter_count=None,
    coefficient_count=None,
    search_count=1,
):
    coefficients, loss = fitted
    compatible, turn, turn_kind, geometry_reason, target_count, opposite_count = (
        _form_turn(
            coefficients,
            knot,
            order,
            form,
            value_resolution,
            len(values),
        )
    )
    rows = [[1.0, at, at * at, at**3][: len(coefficients)] for at in times]
    if order:
        rows = [[1.0, at, at * at, max(0.0, at - knot) ** order] for at in times]
    count = len(coefficients) if coefficient_count is None else coefficient_count
    parameters = count if parameter_count is None else parameter_count
    search_cost = 2 * log(search_count) if search_count > 1 else 0.0
    score = loss + parameters * log(len(values)) + search_cost
    model = CurveFormModel(
        model=name,
        score=score,
        loss=loss,
        compatible=compatible,
        turn_at=start + timedelta(seconds=turn * span) if turn is not None else None,
        turn_kind=turn_kind,
        knot_at=start + timedelta(seconds=knot * span) if knot is not None else None,
        hinge_order=order,
        coefficient_count=count,
        parameter_count=parameters,
        search_count=search_count,
        search_cost=search_cost,
        geometry_reason=geometry_reason,
        target_turn_count=target_count,
        opposite_turn_count=opposite_count,
    )
    return model, coefficients, _residuals(rows, values, coefficients)


def _form_models(
    selected,
    noise,
    policy,
    form,
    *,
    knot_times=None,
    retained_search_count=None,
    include_knot=None,
):
    """Fit each existing family once on the same originals and noise scale."""
    start, newest = selected[0][0], selected[-1][0]
    span = (newest - start).total_seconds()
    times = [(stamp - start).total_seconds() / span for stamp, _ in selected]
    raw_values = [value for _, value in selected]
    center = median(raw_values)
    values = [value - center for value in raw_values]
    resolution = len(values) * ulp(max(abs(value) for value in raw_values))
    smooth_result = _smooth_models(times, values, noise, policy)
    if smooth_result is None:
        return None
    _, smooth = smooth_result
    level = _fit([[1.0] for _ in times], values, noise, policy)
    if level is None:
        return None
    records = [
        _form_record(
            "constant", level, times, values, noise, start, span, form, resolution
        )
    ]
    for score, name, _, coefficients in smooth:
        # Reuse the owner's already computed loss, without another robust fit.
        loss = score - len(coefficients) * log(len(values))
        records.append(
            _form_record(
                name,
                (coefficients, loss),
                times,
                values,
                noise,
                start,
                span,
                form,
                resolution,
            )
        )
    if knot_times is None:
        eligible = list(
            range(policy.min_side_samples, len(times) - policy.min_side_samples)
        )
        indices = [
            eligible[index] for index in _indices(len(eligible), policy.max_candidates)
        ]
        if include_knot is not None:
            pinned = next(
                (
                    index
                    for index, (stamp, _) in enumerate(selected)
                    if stamp == include_knot
                ),
                None,
            )
            if pinned in eligible and pinned not in indices:
                if len(indices) == policy.max_candidates:
                    nearest = min(
                        range(len(indices)),
                        key=lambda i: (
                            abs(times[indices[i]] - times[pinned]),
                            i,
                        ),
                    )
                    indices[nearest] = pinned
                else:
                    indices.append(pinned)
                indices.sort()
        knots = [(index, times[index]) for index in indices]
        knot_times = tuple(selected[index][0] for index in indices)
    else:
        knots = [
            (index, (stamp - start).total_seconds() / span)
            for index, stamp in enumerate(knot_times)
            if start < stamp < newest
        ]
    changes = []
    for order in (1, 2):
        if not knots:
            continue
        result = _models(
            times,
            values,
            noise,
            knots,
            policy,
            hinge_order=order,
            smooth_result=smooth_result,
        )
        if result is not None:
            changes.extend(
                (order, index, coefficients, score)
                for score, index, coefficients in result[3]
            )
    # Every attempted knot/order was searched, including a failed/rank-deficient
    # fit. Such failures must not discount the surviving hypotheses' search.
    search_count = (
        2 * len(knots) if retained_search_count is None else retained_search_count
    )
    for order, index, coefficients, score in changes:
        knot = next(value for candidate, value in knots if candidate == index)
        loss = score - 5 * log(len(values))
        records.append(
            _form_record(
                f"hinge{order}",
                (coefficients, loss),
                times,
                values,
                noise,
                start,
                span,
                form,
                resolution,
                knot=knot,
                order=order,
                parameter_count=5,
                search_count=search_count,
            )
        )
    return records, knot_times, search_count


def _fixed_form_model(selected, noise, policy, reference, records):
    """Refit the bound branch; a stationary turn is a linear constraint."""
    start, newest = selected[0][0], selected[-1][0]
    span = (newest - start).total_seconds()
    turn = (reference.turn_at - start).total_seconds() / span
    if not 0 < turn < 1:
        return None
    if reference.turn_kind == "knot":
        for record in records:
            model = record[0]
            if model.model == reference.model and model.knot_at == reference.knot_at:
                if model.compatible is True and (
                    model.turn_kind != reference.turn_kind
                    or abs(model.turn_at - reference.turn_at) > datetime.resolution
                ):
                    return None
                # Search costs belong to the first proposal, even if a later
                # prefix permits a different number of competing knots.
                return (
                    CurveFormModel(
                        **{
                            **model.__dict__,
                            "score": model.loss
                            + reference.parameter_count * log(len(selected))
                            + 2 * log(reference.search_count),
                            "parameter_count": reference.parameter_count,
                            "search_count": reference.search_count,
                            "search_cost": 2 * log(reference.search_count),
                        },
                    ),
                    record[1],
                    record[2],
                )
    raw_values = [value for _, value in selected]
    center = median(raw_values)
    values = [value - center for value in raw_values]
    times = [(stamp - start).total_seconds() / span for stamp, _ in selected]
    knot = (
        (reference.knot_at - start).total_seconds() / span
        if reference.knot_at is not None
        else None
    )
    order = reference.hinge_order
    if reference.turn_kind == "knot":
        rows = [[1.0, at, at * at, max(0.0, at - knot) ** order] for at in times]
        fitted = _fit(rows, values, noise, policy)
        count = 4
    elif order:
        derivative = (
            (1.0 if turn > knot else 0.0) if order == 1 else 2 * max(0.0, turn - knot)
        )
        rows = [
            [
                1.0,
                at * at - 2 * turn * at,
                max(0.0, at - knot) ** order - derivative * at,
            ]
            for at in times
        ]
        fitted = _fit(rows, values, noise, policy)
        if fitted is not None:
            offset, quadratic, change = fitted[0]
            fitted = (
                [
                    offset,
                    -2 * turn * quadratic - derivative * change,
                    quadratic,
                    change,
                ],
                fitted[1],
            )
        count = 3
    else:
        degree = {"quadratic": 2, "cubic": 3}[reference.model]
        rows = [
            [1.0, *((at - turn) ** power for power in range(2, degree + 1))]
            for at in times
        ]
        fitted = _fit(rows, values, noise, policy)
        if fitted is not None:
            local = fitted[0]
            cubic = local[2] if degree == 3 else 0.0
            coefficients = [
                local[0] + local[1] * turn**2 - cubic * turn**3,
                -2 * local[1] * turn + 3 * cubic * turn**2,
                local[1] - 3 * cubic * turn,
            ]
            if degree == 3:
                coefficients.append(cubic)
            fitted = coefficients, fitted[1]
        count = degree
    if fitted is None:
        return None
    resolution = len(selected) * ulp(max(abs(value) for value in raw_values))
    record = _form_record(
        reference.model,
        fitted,
        times,
        values,
        noise,
        start,
        span,
        reference.form,
        resolution,
        knot=knot,
        order=order,
        parameter_count=reference.parameter_count,
        coefficient_count=count,
        search_count=reference.search_count,
    )
    model = record[0]
    if (
        model.compatible is True
        and model.turn_at is not None
        and abs(model.turn_at - reference.turn_at) > datetime.resolution
    ):
        return None
    return record


def _form_flanks(samples, turn_at, form, current_not_before, policy, cache):
    before = [point for point in samples if point[0] < turn_at]
    after = [
        point
        for point in samples
        if point[0] > turn_at
        and (current_not_before is None or point[0] >= current_not_before)
    ]
    results = []
    for points in (before, after):
        key = tuple(points)
        if key not in cache:
            cache[key] = curve_trend(points, policy=policy)
        results.append(cache[key])
    before_trend, after_trend = results
    if before_trend is None or after_trend is None:
        evidence = "unresolved"
    else:
        compatible = (
            before_trend.direction == 1 and after_trend.direction == -1
            if form == "peak"
            else before_trend.direction == -1 and after_trend.direction in (0, 1)
        )
        evidence = "supports" if compatible else "rejects"
    return before_trend, after_trend, len(before), len(after), evidence


def _form_comparison(records, count):
    positive = [record for record in records if record[0].compatible is True]
    counter = [record for record in records if record[0].compatible is False]
    unclear = [record for record in records if record[0].compatible is None]
    positive = (
        min(positive, key=lambda item: (item[0].score, item[0].model))
        if positive
        else None
    )
    counter = (
        min(counter, key=lambda item: (item[0].score, item[0].model))
        if counter
        else None
    )
    scores = [record[0].score for record in (positive, counter) if record is not None]
    resolution = count * ulp(max(map(abs, scores))) if scores else None
    gain = counter[0].score - positive[0].score if positive and counter else None
    if (
        unclear
        and scores
        and min(item[0].score for item in unclear) <= min(scores) + resolution
    ):
        evidence = "unresolved"
    elif not positive:
        evidence = "rejects" if counter else "unresolved"
    elif counter is None or abs(gain) <= resolution:
        evidence = "unresolved"
    else:
        evidence = "supports" if gain > resolution else "rejects"
    return positive, counter, gain, resolution, evidence


def curve_form(
    samples: Sequence[tuple[datetime, float]],
    *,
    policy: CurvePolicy,
    form: str = "peak",
    reference: CurveFormReference | None = None,
    current_not_before: datetime | None = None,
) -> CurveFormFeatures | None:
    """Assess an observed peak or recovery across the existing fit families.

    L/P1/P2/P3/M1/M2 share actual bounded originals and one cubic-residual MAD.
    Each concrete fit belongs to the compatible or counterform class by its
    geometry; a curved or hinged peak is never automatically a counterform.
    Both hinge orders share one declared search over all attempted knot fits,
    including unresolved fits. score_gain is counterform score minus
    compatible-form score.

    A reference freezes the initial context, model branch, turn, hinge knot,
    current-flank floor and geometry-search history. A smooth stationary turn
    is held by a linear derivative-zero constraint within the same polynomial
    family; a first-order kink keeps its original knot. Reduced current free
    coefficients do not erase the originally estimated geometry costs.
    Better *positive* geometry can contradict this reference's identity;
    replacement_gain/cost report that separately from form competition.

    Recovery requires an observed fall followed by an observed level or rise.
    A merely less-steep fall is not recovery here, nor does recovery decide an
    infusion's end. Sources, coherent input selection, freshness requirements,
    onset/event times and episode identity remain the detector's responsibility.
    """
    try:
        if not _policy_valid(policy) or form not in ("peak", "recovery"):
            return None
        if len(samples) < 2 * policy.min_side_samples:
            return None
        stamps = [stamp for stamp, _ in samples]
        if any(not isinstance(stamp, datetime) for stamp in stamps):
            return None
        if any(a >= b for a, b in zip(stamps, stamps[1:])):
            return None
        if any(isinstance(value, bool) or not isfinite(value) for _, value in samples):
            return None
        if current_not_before is not None and not isinstance(
            current_not_before, datetime
        ):
            return None
        supplied_reference = reference is not None
        if supplied_reference:
            if not isinstance(reference, CurveFormReference) or reference.form != form:
                return None
            parameters = {"quadratic": 3, "cubic": 4, "hinge1": 5, "hinge2": 5}
            if (
                reference.model not in parameters
                or reference.parameter_count != parameters[reference.model]
                or not isinstance(reference.search_count, int)
                or isinstance(reference.search_count, bool)
                or reference.search_count < 1
                or reference.start_at != stamps[0]
                or reference.initial_newest_at > stamps[-1]
                or not stamps[0] < reference.turn_at < stamps[-1]
                or reference.turn_kind not in ("knot", "stationary")
                or reference.hinge_order
                != {"hinge1": 1, "hinge2": 2}.get(reference.model, 0)
                or (reference.hinge_order != 0) != (reference.knot_at is not None)
                or reference.turn_kind == "knot"
                and (
                    reference.hinge_order != 1 or reference.turn_at != reference.knot_at
                )
            ):
                return None
            if current_not_before is None:
                current_not_before = reference.current_not_before
            elif current_not_before != reference.current_not_before:
                return None
        knot_at = reference.knot_at if reference is not None else None
        selected = _bounded_samples(samples, policy.max_points, knot_at)
        if selected is None or len(selected) < 2 * policy.min_side_samples:
            return None
        span = (selected[-1][0] - selected[0][0]).total_seconds()
        if not isfinite(span) or span <= 0:
            return None
        times = [
            (stamp - selected[0][0]).total_seconds() / span for stamp, _ in selected
        ]
        common = _common_noise(times, [value for _, value in selected])
        if common is None:
            return None
        noise, center, _, _ = common
        fitted = _form_models(selected, noise, policy, form, include_knot=knot_at)
        if fitted is None:
            return None
        records, knot_times, search_count = fitted
        positive, counter, gain, resolution, form_evidence = _form_comparison(
            records, len(selected)
        )
        if reference is None and positive is not None:
            model = positive[0]
            reference = CurveFormReference(
                form=form,
                model=model.model,
                start_at=stamps[0],
                turn_at=model.turn_at,
                turn_kind=model.turn_kind,
                knot_at=model.knot_at,
                hinge_order=model.hinge_order,
                parameter_count=model.parameter_count,
                search_count=model.search_count,
                initial_newest_at=stamps[-1],
                current_not_before=current_not_before,
            )
        fixed = (
            positive
            if not supplied_reference
            else _fixed_form_model(selected, noise, policy, reference, records)
        )
        reference_gain = reference_resolution = None
        reference_evidence = "unresolved"
        replacement_gain = replacement_cost = None
        replacement_parameters = 0
        displaced = False
        if fixed is not None and fixed[0].compatible is False:
            reference_evidence = "rejects"
        elif fixed is not None and fixed[0].compatible is True and counter is not None:
            reference_gain = counter[0].score - fixed[0].score
            reference_resolution = len(selected) * ulp(
                max(
                    abs(counter[0].score),
                    abs(fixed[0].score),
                )
            )
            if abs(reference_gain) > reference_resolution:
                reference_evidence = "supports" if reference_gain > 0 else "rejects"
        if supplied_reference and fixed is not None and positive is not None:
            best = positive[0]
            # A first-order kink maximum has one searched time: turn and knot
            # are two names for it. Count distinct new geometry coordinates,
            # rather than charging this same estimated time twice.
            moved_times = set()
            if best.turn_at != reference.turn_at:
                moved_times.add(best.turn_at)
            if best.knot_at is not None and best.knot_at != reference.knot_at:
                moved_times.add(best.knot_at)
            replacement_parameters = len(moved_times)
            if replacement_parameters:
                replacement_gain = fixed[0].score - best.score
                replacement_cost = (
                    replacement_parameters * log(len(selected)) + best.search_cost
                )
                displaced = replacement_gain > replacement_cost + (resolution or 0.0)
                if displaced:
                    reference_evidence = "rejects"
        flank_cache = {}
        before_trend = after_trend = None
        before_count = after_count = 0
        flank_evidence = "unresolved"
        if reference is not None:
            (before_trend, after_trend, before_count, after_count, flank_evidence) = (
                _form_flanks(
                    samples,
                    reference.turn_at,
                    form,
                    current_not_before,
                    policy,
                    flank_cache,
                )
            )
        influence_evidence = "not_assessed"
        influence_gain = influence_resolution = None
        removed_at = None
        if (
            form_evidence == reference_evidence == flank_evidence == "supports"
            and fixed is not None
            and counter is not None
        ):
            removed = max(
                range(len(selected)),
                key=lambda index: (
                    abs(counter[2][index]),
                    index,
                ),
            )
            removed_at = selected[removed][0]
            retained = [point for point in samples if point[0] != removed_at]
            retained_selected = [
                point for index, point in enumerate(selected) if index != removed
            ]
            influence_evidence = "unresolved"
            if len(retained_selected) >= 2 * policy.min_side_samples:
                rescue = _form_models(
                    retained_selected,
                    noise,
                    policy,
                    form,
                    knot_times=knot_times,
                    retained_search_count=search_count,
                )
                if rescue is not None:
                    rescue_records, _, _ = rescue
                    _, rescue_counter, _, _, rescue_form = _form_comparison(
                        rescue_records,
                        len(retained_selected),
                    )
                    rescue_fixed = _fixed_form_model(
                        retained_selected,
                        noise,
                        policy,
                        reference,
                        rescue_records,
                    )
                    if rescue_fixed is not None and rescue_fixed[0].compatible is False:
                        influence_evidence = "rejects"
                    elif (
                        rescue_fixed is not None
                        and rescue_fixed[0].compatible is True
                        and rescue_counter is not None
                    ):
                        influence_gain = rescue_counter[0].score - rescue_fixed[0].score
                        influence_resolution = len(retained_selected) * ulp(
                            max(
                                abs(rescue_counter[0].score),
                                abs(rescue_fixed[0].score),
                            )
                        )
                        _, _, _, _, rescue_flanks = _form_flanks(
                            retained,
                            reference.turn_at,
                            form,
                            current_not_before,
                            policy,
                            flank_cache,
                        )
                        if rescue_form == "rejects" or rescue_flanks == "rejects":
                            influence_evidence = "rejects"
                        elif abs(influence_gain) > influence_resolution:
                            if influence_gain < 0:
                                influence_evidence = "rejects"
                            elif rescue_form == rescue_flanks == "supports":
                                influence_evidence = "supports"
        if form_evidence != "supports":
            evidence, reason = form_evidence, "form_comparison_" + form_evidence
        elif reference_evidence != "supports":
            evidence = reference_evidence
            reason = (
                "positive_geometry_displaces_reference"
                if displaced
                else "frozen_geometry_" + reference_evidence
            )
        elif flank_evidence != "supports":
            evidence, reason = flank_evidence, "original_flanks_" + flank_evidence
        elif influence_evidence != "supports":
            evidence, reason = (
                influence_evidence,
                "one_point_removal_" + influence_evidence,
            )
        else:
            evidence, reason = "supports", "form_and_frozen_geometry_supported"
        observed_form = None
        if flank_evidence == "supports":
            observed_form = (
                "rise_to_fall"
                if form == "peak"
                else "fall_to_level"
                if after_trend.direction == 0
                else "fall_to_rise"
            )
        return CurveFormFeatures(
            evidence=evidence,
            form_evidence=form_evidence,
            reference_evidence=reference_evidence,
            influence_evidence=influence_evidence,
            reason=reason,
            form=form,
            start_at=stamps[0],
            newest_at=stamps[-1],
            count=len(selected),
            original_count=len(samples),
            noise_scale=noise,
            positive_score=positive[0].score if positive is not None else None,
            counter_score=counter[0].score if counter is not None else None,
            score_gain=gain,
            positive_model=positive[0].model if positive is not None else None,
            counter_model=counter[0].model if counter is not None else None,
            turn_at=reference.turn_at if reference is not None else None,
            reference=reference,
            reference_score=fixed[0].score if fixed is not None else None,
            reference_score_gain=reference_gain,
            replacement_gain=replacement_gain,
            replacement_cost=replacement_cost,
            fresh_original_count=(
                sum(stamp > reference.initial_newest_at for stamp in stamps)
                if supplied_reference
                else 0
            ),
            before_trend=before_trend,
            after_trend=after_trend,
            before_count=before_count,
            after_count=after_count,
            removed_at=removed_at,
            influence_score_gain=influence_gain,
            candidate_models=tuple(record[0] for record in records),
            score_resolution=resolution,
            reference_score_resolution=reference_resolution,
            influence_score_resolution=influence_resolution,
            observed_form=observed_form,
            reference_model=fixed[0] if fixed is not None else None,
            replacement_parameter_count=replacement_parameters,
            hinge_fits_attempted=search_count,
            hinge_fits_failed=search_count
            - sum(record[0].hinge_order != 0 for record in records),
            fit=CurveFitSnapshot(
                profile=CurveProfile(
                    fixed[0].model,
                    tuple(fixed[1]),
                    (
                        (fixed[0].knot_at - selected[0][0]).total_seconds() / span
                        if fixed[0].knot_at is not None
                        else None
                    ),
                ),
                time_origin=selected[0][0],
                time_scale_seconds=span,
                value_center=center,
                newest_at=selected[-1][0],
                noise_scale=noise,
            )
            if fixed is not None
            else None,
        )
    except (
        AttributeError,
        IndexError,
        KeyError,
        OverflowError,
        TypeError,
        ValueError,
        ZeroDivisionError,
    ):
        return None
