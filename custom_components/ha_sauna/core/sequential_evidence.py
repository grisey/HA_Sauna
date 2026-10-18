"""Sequential predictive evidence, with one contribution per original.

Normal-inverse-gamma regression integrates coefficients and variance out of
the likelihood. Alternatives accumulate PRE-update predictive densities, not
fitted scores or repeated tests on overlapping windows. Probabilities are
conditional on supplied priors/models; empirical calibration is a separate
obligation. No recognition defaults, sample gates, timers or interpolation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import exp, fsum, isfinite, lgamma, log, log1p, pi, sqrt

from .probability import student_log_survival


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def _time(value):
    return isinstance(value, datetime) and value.utcoffset() is not None


def _dot(left, right):
    return fsum(a * b for a, b in zip(left, right, strict=True))


def _logsumexp(values):
    values = tuple(values)
    top = max(values)
    return top + log(fsum(exp(value - top) for value in values))


def _positive_definite(matrix):
    """Cholesky validation; no diagonal jitter can invent prior information."""
    size = len(matrix)
    if (any(len(row) != size or any(not _number(v) for v in row) for row in matrix)
            or any(matrix[i][j] != matrix[j][i] for i in range(size) for j in range(i))):
        return False
    lower = [[0.0] * size for _ in range(size)]
    for i in range(size):
        for j in range(i + 1):
            value = matrix[i][j] - _dot(lower[i][:j], lower[j][:j])
            if j == i:
                if value <= 0 or not isfinite(value):
                    return False
                lower[i][j] = sqrt(value)
            else:
                lower[i][j] = value / lower[j][j]
    return True


@dataclass(frozen=True)
class Prediction:
    mean: float
    scale_squared: float
    degrees_of_freedom: float

    def __post_init__(self):
        if (not _number(self.mean) or not _number(self.scale_squared)
                or self.scale_squared <= 0 or not _number(self.degrees_of_freedom)
                or self.degrees_of_freedom <= 0):
            raise ValueError("A Student-t prediction needs finite location and positive scales")

    def log_survival(self, value):
        if not _number(value):
            raise ValueError("A probability boundary must be finite")
        return student_log_survival((value - self.mean) / sqrt(self.scale_squared),
                                    self.degrees_of_freedom)

    def log_density(self, value):
        if not _number(value):
            raise ValueError("A prediction consumes a finite observed value")
        degrees = self.degrees_of_freedom
        residual = (value - self.mean) / sqrt(self.scale_squared)
        # Stable log(1 + r²/nu), including extreme finite residuals.
        ratio = 2 * log(abs(residual)) - log(degrees) if residual else None
        tail = max(0.0, ratio) + log1p(exp(-abs(ratio))) if ratio is not None else 0.0
        return (lgamma((degrees + 1) / 2) - lgamma(degrees / 2)
                - (log(degrees) + log(pi) + log(self.scale_squared)) / 2
                - (degrees + 1) * tail / 2)


@dataclass(frozen=True)
class Regression:
    """b|sigma² ~ N(mean, sigma² covariance); sigma² ~ IG(shape, rate).

    covariance is the coefficient SCALE, not its unconditional covariance.
    Proper priors allow uncertain predictions before a design has full rank.
    """

    mean: tuple[float, ...]
    covariance: tuple[tuple[float, ...], ...]
    shape: float
    rate: float
    count: int = 0

    def __post_init__(self):
        if (not self.mean or any(not _number(v) for v in self.mean)
                or len(self.covariance) != len(self.mean)
                or not _positive_definite(self.covariance)
                or not _number(self.shape) or self.shape <= 0
                or not _number(self.rate) or self.rate <= 0
                or not isinstance(self.count, int) or isinstance(self.count, bool) or self.count < 0):
            raise ValueError("A proper finite normal-inverse-gamma distribution is required")
        object.__setattr__(self, "mean", tuple(self.mean))
        object.__setattr__(self, "covariance", tuple(tuple(row) for row in self.covariance))

    def _projection(self, design):
        if len(design) != len(self.mean) or any(not _number(v) for v in design):
            raise ValueError("Design dimension must match the coefficient prior")
        vector = tuple(_dot(row, design) for row in self.covariance)
        denominator = 1 + _dot(design, vector)
        if not isfinite(denominator) or denominator <= 0:
            raise ValueError("Predictive variance is not numerically representable")
        return vector, denominator

    @property
    def dimension(self):
        return len(self.mean)

    def coefficient_contrast(self, weights):
        """Marginal coefficient distribution, WITHOUT observation noise."""
        vector, _ = self._projection(weights)
        variance = self.rate / self.shape * _dot(weights, vector)
        return Prediction(_dot(weights, self.mean), variance, 2 * self.shape)

    def predict(self, design):
        _, denominator = self._projection(design)
        variance = self.rate / self.shape * denominator
        mean = _dot(design, self.mean)
        if not isfinite(variance) or variance <= 0 or not isfinite(mean):
            raise ValueError("Prediction is not numerically representable")
        return Prediction(mean, variance, 2 * self.shape)

    def observe(self, design, value):
        """Return posterior and the PRE-update predictive log density."""
        predictive = self.predict(design)
        likelihood = predictive.log_density(value)
        vector, denominator = self._projection(design)
        residual = value - predictive.mean
        gain = tuple(v / denominator for v in vector)
        mean = tuple(m + g * residual for m, g in zip(self.mean, gain, strict=True))
        # Joseph form preserves positivity without an invented noise floor.
        size = len(mean)
        transform = tuple(tuple(float(i == j) - gain[i] * design[j] for j in range(size))
                          for i in range(size))
        left = tuple(tuple(_dot(row, column) for column in zip(*self.covariance))
                     for row in transform)
        covariance = [[0.0] * size for _ in range(size)]
        for i in range(size):
            for j in range(i + 1):
                covariance[i][j] = covariance[j][i] = _dot(left[i], transform[j]) + gain[i] * gain[j]
        rate = self.rate + (residual / sqrt(denominator)) ** 2 / 2
        posterior = Regression(mean, tuple(tuple(row) for row in covariance),
                               self.shape + 0.5, rate, self.count + 1)
        return posterior, likelihood


@dataclass(frozen=True)
class HalfSpace:
    """A physical direction is part of the hypothesis, not a later gate.

    The prior is restricted to dot(weights, coefficients) > lower.
    A signed amplitude or a specified slope contrast can use this exactly.
    Multiple constraints cannot be approximated by multiplying their margins.
    """

    weights: tuple[float, ...]
    lower: float = 0.0

    def __post_init__(self):
        if (not self.weights or any(not _number(v) for v in self.weights)
                or not any(self.weights) or not _number(self.lower)):
            raise ValueError("A half-space needs a nonzero finite contrast")
        object.__setattr__(self, "weights", tuple(self.weights))

    def log_mass(self, regression):
        return regression.coefficient_contrast(self.weights).log_survival(self.lower)


@dataclass(frozen=True)
class DirectedRegression:
    """Exact NIG regression conditioned on one physical half-space.

    State retains the enclosing conjugate posterior. The event likelihood
    includes its changing half-space mass. It never treats a wrong-sign fit
    as the named event or clips the posterior coefficient to the boundary.
    """

    enclosing: Regression
    constraint: HalfSpace

    def __post_init__(self):
        if len(self.constraint.weights) != self.enclosing.dimension:
            raise ValueError("Constraint and coefficient dimensions must match")
        if not isfinite(self.constraint.log_mass(self.enclosing)):
            raise ValueError("The directed prior must have representable nonzero mass")

    @property
    def dimension(self):
        return self.enclosing.dimension

    def observe(self, design, value):
        posterior, likelihood = self.enclosing.observe(design, value)
        before = self.constraint.log_mass(self.enclosing)
        after = self.constraint.log_mass(posterior)
        return DirectedRegression(posterior, self.constraint), likelihood + after - before


@dataclass(frozen=True)
class Basis:
    """Fixed physical-time coordinates: longer input never rescales the prior.

    A knot is (absolute time, power); linear hinges and curvature changes are
    explicit model alternatives, not additional confirmation conditions.
    """

    origin: datetime
    time_scale_seconds: float
    powers: tuple[int, ...]
    knots: tuple[tuple[datetime, int], ...] = ()

    def __post_init__(self):
        if (not _time(self.origin) or not _number(self.time_scale_seconds) or self.time_scale_seconds <= 0
                or not self.powers or len(set(self.powers)) != len(self.powers)
                or any(not isinstance(p, int) or isinstance(p, bool) or p < 0 for p in self.powers)
                or any(not _time(at) or not isinstance(p, int) or isinstance(p, bool) or p < 1
                       for at, p in self.knots)):
            raise ValueError("A basis needs fixed finite physical-time coordinates")
        object.__setattr__(self, "powers", tuple(self.powers))
        object.__setattr__(self, "knots", tuple(tuple(knot) for knot in self.knots))

    def design(self, at):
        if not _time(at):
            raise ValueError("An original observation time needs a timezone")
        x = (at - self.origin).total_seconds() / self.time_scale_seconds
        values = tuple(x ** power for power in self.powers) + tuple(
            max(0.0, (at - knot).total_seconds() / self.time_scale_seconds) ** power
            for knot, power in self.knots
        )
        if any(not isfinite(value) for value in values):
            raise ValueError("Design is not numerically representable")
        return values

    @property
    def dimension(self):
        return len(self.powers) + len(self.knots)


@dataclass(frozen=True)
class ChannelModel:
    channel: str
    basis: Basis
    prior: Regression | DirectedRegression

    def __post_init__(self):
        if not self.channel or self.prior.dimension != self.basis.dimension:
            raise ValueError("Channel, basis and coefficient prior must agree")


@dataclass(frozen=True)
class Hypothesis:
    name: str
    event: str
    prior_weight: float
    channels: tuple[ChannelModel, ...]

    def __post_init__(self):
        if (not self.name or not self.event or not _number(self.prior_weight) or self.prior_weight <= 0
                or not self.channels or len({item.channel for item in self.channels}) != len(self.channels)):
            raise ValueError("A hypothesis needs event, positive prior and unique channels")
        object.__setattr__(self, "channels", tuple(self.channels))


@dataclass(frozen=True)
class Observation:
    channel: str
    source: str
    epoch: int
    at: datetime
    received_at: datetime
    value: float | None
    time_basis: str = "measurement"

    def __post_init__(self):
        if (not self.channel or not self.source or not self.time_basis
                or not isinstance(self.epoch, int) or isinstance(self.epoch, bool) or self.epoch < 0
                or not _time(self.at) or not _time(self.received_at) or self.at > self.received_at
                or self.value is not None and not _number(self.value)):
            raise ValueError("An original needs source, causal times and a finite or unknown value")

    @property
    def identity(self):
        return self.channel, self.source, self.epoch, self.time_basis, self.at


@dataclass(frozen=True)
class EventProbability:
    event: str
    probability: float
    log_odds: float
    original_count: int

    def threshold_margin(self, threshold):
        if not _number(threshold) or not 0.5 < threshold < 1:
            raise ValueError("A decision threshold must explicitly lie between one half and one")
        return self.log_odds - (log(threshold) - log1p(-threshold))

    def confirmed(self, threshold):
        margin = self.threshold_margin(threshold)
        return self.original_count > 0 and margin >= 0


class OriginalSequence:
    """Shared receipt/identity owner for fixed and streaming model comparisons."""

    def __init__(self, channels):
        self.channels = frozenset(channels)
        self._observations = {}
        self._sources = {}
        self.last_received_at = None
        self.revisions = {}

    def observe(self, observation):
        if observation.channel not in self.channels:
            raise ValueError("The observation channel is outside this comparison")
        if self.last_received_at is not None and observation.received_at < self.last_received_at:
            raise ValueError("Observations must arrive in causal receipt order")
        if observation.value is None:
            self.last_received_at = observation.received_at
            return False
        source = observation.source, observation.epoch, observation.time_basis
        if observation.channel in self._sources and self._sources[observation.channel] != source:
            raise ValueError("Source identity changed: begin a new comparison")
        old = self._observations.get(observation.identity)
        if old is not None and old.value == observation.value:
            self.last_received_at = observation.received_at
            return False
        if old is not None:
            # Temporary owner makes a failed correction atomic. The receipt of
            # the revision is recorded separately from the original sequence.
            corrected = self._new_comparison()
            for identity, item in self._observations.items():
                corrected.observe(Observation(item.channel, item.source, item.epoch, item.at,
                                              item.received_at, observation.value, item.time_basis)
                                  if identity == observation.identity else item)
            self._observations = corrected._observations
            self._adopt_math_state(corrected)
            self.revisions[observation.identity] = observation.received_at
        else:
            self._assimilate(observation)
            self._observations[observation.identity] = observation
        self._sources[observation.channel] = source
        self.last_received_at = observation.received_at
        return True


class EvidenceSequence(OriginalSequence):
    """One source-bound comparison over unique causal originals.

    Every hypothesis sees exactly the same channels and observations. Channel
    errors are conditionally independent given its model; calibration must
    check this assumption. Upper/lower sensors belong to separate comparisons.
    Corrections rebuild from proper priors; raw archival history is external.
    """

    def __init__(self, hypotheses):
        self.hypotheses = tuple(hypotheses)
        if (len(self.hypotheses) < 2
                or len({h.name for h in self.hypotheses}) != len(self.hypotheses)
                or len({h.event for h in self.hypotheses}) < 2):
            raise ValueError("At least two distinct physical alternatives are required")
        channels = {m.channel for m in self.hypotheses[0].channels}
        if any({m.channel for m in h.channels} != channels for h in self.hypotheses):
            raise ValueError("All hypotheses must explain the same observed channels")
        super().__init__(channels)
        self._models = {h.name: {m.channel: m for m in h.channels} for h in self.hypotheses}
        self._states = {name: {channel: model.prior for channel, model in models.items()}
                        for name, models in self._models.items()}
        normalizer = _logsumexp(log(h.prior_weight) for h in self.hypotheses)
        self._weights = {h.name: log(h.prior_weight) - normalizer for h in self.hypotheses}

    def _new_comparison(self):
        return EvidenceSequence(self.hypotheses)

    def _adopt_math_state(self, other):
        self._states, self._weights = other._states, other._weights

    def _assimilate(self, observation):
        updates = {}
        for name, models in self._models.items():
            model = models[observation.channel]
            posterior, likelihood = self._states[name][observation.channel].observe(
                model.basis.design(observation.at), observation.value,
            )
            updates[name] = posterior, self._weights[name] + likelihood
        normalizer = _logsumexp(weight for _, weight in updates.values())
        for name, (posterior, weight) in updates.items():
            self._states[name][observation.channel] = posterior
            self._weights[name] = weight - normalizer

    def probabilities(self):
        result = []
        for event in dict.fromkeys(h.event for h in self.hypotheses):
            positive = _logsumexp(self._weights[h.name] for h in self.hypotheses if h.event == event)
            negative = _logsumexp(self._weights[h.name] for h in self.hypotheses if h.event != event)
            result.append(EventProbability(event, exp(positive), positive - negative,
                                           len(self._observations)))
        return tuple(result)
