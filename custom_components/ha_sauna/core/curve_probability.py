"""Causal curve-onset comparison with explicit, calibratable priors.

This is a finite Bayesian model of the first change in one context. It shares
the original-identity owner with fixed comparisons. It has no sample blocks,
expiry timer, repeated-window likelihood, implicit threshold or legacy gate.
The caller supplies the physical profile catalogue and owns event transitions.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from math import exp, expm1, fsum, log, log1p

from .sequential_evidence import (
    Basis, ChannelModel, DirectedRegression, EventProbability, HalfSpace,
    OriginalSequence, Regression, _logsumexp, _number,
)


@dataclass(frozen=True)
class Response:
    """A calibrated causal curve times one positive uncertain amplitude.

    duration_seconds scales the curve, never an acceptance deadline. amplitude
    is half-normal given noise variance: |N(0, sigma² amplitude_variance)|.
    The scale is dimensionless relative to that channel's noise distribution.
    """

    channel: str
    form: str
    duration_seconds: float
    amplitude_variance: float
    direction: int

    def __post_init__(self):
        if (not self.channel or self.form not in (
                "slope_change", "curvature_change", "relaxation", "pulse", "smooth_braking")
                or not _number(self.duration_seconds) or self.duration_seconds <= 0
                or not _number(self.amplitude_variance) or self.amplitude_variance <= 0
                or isinstance(self.direction, bool) or self.direction not in (-1, 1)):
            raise ValueError("A response needs an explicit causal shape, scale and direction")

    def value(self, elapsed_seconds):
        if not _number(elapsed_seconds):
            raise ValueError("Response time must be finite")
        if elapsed_seconds <= 0:
            return 0.0
        x = elapsed_seconds / self.duration_seconds
        if self.form == "slope_change":
            value = x
        elif self.form == "curvature_change":
            value = x * x
        elif self.form == "relaxation":
            value = -expm1(-x)
        elif self.form == "pulse":
            # Unit maximum at x=1. Log form avoids inf*0 at large times.
            value = exp(log(x) + 1 - x)
        else:
            value = x + expm1(-x)
        if not _number(value):
            raise ArithmeticError("Response is not numerically representable")
        return self.direction * value


@dataclass(frozen=True)
class ResponseBasis:
    background: Basis
    response: Response
    onset_at: datetime

    @property
    def dimension(self):
        return self.background.dimension + 1

    def design(self, at):
        return self.background.design(at) + (
            self.response.value((at - self.onset_at).total_seconds()),
        )


@dataclass(frozen=True)
class CurveProfile:
    name: str
    event: str
    prior_weight: float
    responses: tuple[Response, ...]

    def __post_init__(self):
        if (not self.name or not self.event or self.event == "background"
                or not _number(self.prior_weight) or self.prior_weight <= 0
                or not self.responses
                or len({r.channel for r in self.responses}) != len(self.responses)):
            raise ValueError("A profile needs a distinct event and explicit prior weight")
        object.__setattr__(self, "responses", tuple(self.responses))

    def bind(self, background, onset_at):
        """Spawn from the PRE-observation background posterior.

        The new column is zero before its onset. Its symmetric enclosing prior
        and positive-amplitude restriction have exactly 1/2 mass independent
        of sigma². Birth therefore cannot reweight past background evidence.
        """
        responses = {r.channel: r for r in self.responses}
        output = []
        for model in background:
            response = responses.get(model.channel)
            if response is None:
                output.append(model)
                continue
            state = model.prior
            size = state.dimension
            covariance = tuple(row + (0.0,) for row in state.covariance) + (
                (0.0,) * size + (response.amplitude_variance,),
            )
            enclosing = Regression(state.mean + (0.0,), covariance,
                                   state.shape, state.rate, state.count)
            directed = DirectedRegression(enclosing, HalfSpace((0.0,) * size + (1.0,)))
            output.append(ChannelModel(model.channel,
                                       ResponseBasis(model.basis, response, onset_at), directed))
        return tuple(output)


@dataclass(frozen=True)
class OnsetPrior:
    """Physical-time hazard and an explicit finite distribution within a gap.

    Locations are (conditional-quantile, weight). They discretize unknown onset
    time, not observations or confirmation. More nodes divide the same birth
    mass. The posterior is exact for this declared finite model, not an exact
    integral over all continuous onset times. Refinement requires validation.
    """

    rate_per_second: float
    locations: tuple[tuple[float, float], ...]

    def __post_init__(self):
        if (not _number(self.rate_per_second) or self.rate_per_second <= 0
                or not self.locations
                or any(not _number(q) or not 0 < q < 1 or not _number(w) or w <= 0
                       for q, w in self.locations)):
            raise ValueError("An onset prior needs a positive rate and interior weighted locations")
        total = fsum(w for _, w in self.locations)
        object.__setattr__(self, "locations", tuple((q, w / total) for q, w in self.locations))

    def interval(self, start, end):
        seconds = (end - start).total_seconds()
        if seconds <= 0:
            return ()
        hazard = self.rate_per_second * seconds
        birth_mass = -expm1(-hazard)
        return tuple((start + timedelta(seconds=-log1p(-q * birth_mass) / self.rate_per_second),
                      log(birth_mass) + log(weight)) for q, weight in self.locations)


@dataclass(frozen=True)
class OnsetProbability:
    profile: str
    event: str
    probability: float
    onset_at: datetime
    interval_start: datetime
    interval_end: datetime


@dataclass(frozen=True)
class _Component:
    profile: str
    event: str
    log_weight: float
    channels: tuple[ChannelModel, ...]
    onset_at: datetime | None = None
    interval_start: datetime | None = None
    interval_end: datetime | None = None

    def observe(self, observation):
        channels = []
        weight = self.log_weight
        for model in self.channels:
            if model.channel == observation.channel:
                state, density = model.prior.observe(model.basis.design(observation.at),
                                                     observation.value)
                channels.append(replace(model, prior=state))
                weight += density
            else:
                channels.append(model)
        return replace(self, log_weight=weight, channels=tuple(channels))


class CurveProbability(OriginalSequence):
    """One physical sensor's first change, consumed continuously per original.

    All alternatives see the same originals. No event/position coupling is
    inferred here. Upper and lower sensors use separate instances. There is
    deliberately no hidden probability pruning or storage-age reset; a caller
    must establish explicit context transitions and qualify workload bounds.
    """

    def __init__(self, background, profiles, onset_prior):
        self.background = tuple(background)
        self.profiles = tuple(profiles)
        channels = {m.channel for m in self.background}
        if (not self.background or len(channels) != len(self.background)
                or any(not isinstance(m.prior, Regression) for m in self.background)
                or not self.profiles or len({p.name for p in self.profiles}) != len(self.profiles)
                or any({r.channel for r in p.responses} - channels for p in self.profiles)):
            raise ValueError("Unique background channels and comparable named profiles are required")
        super().__init__(channels)
        self.onset_prior = onset_prior
        total = _logsumexp(log(p.prior_weight) for p in self.profiles)
        self._profile_weights = tuple(log(p.prior_weight) - total for p in self.profiles)
        self._components = (_Component("background", "background", 0.0, self.background),)
        self.frontier = None

    def _new_comparison(self):
        return CurveProbability(self.background, self.profiles, self.onset_prior)

    def _adopt_math_state(self, other):
        self._components, self.frontier = other._components, other.frontier

    def _assimilate(self, observation):
        components = list(self._components)
        if self.frontier is not None and observation.at > self.frontier:
            background = components[0]
            nodes = self.onset_prior.interval(self.frontier, observation.at)
            for onset_at, log_mass in nodes:
                for profile, log_prior in zip(self.profiles, self._profile_weights, strict=True):
                    components.append(_Component(
                        profile.name, profile.event, background.log_weight + log_mass + log_prior,
                        profile.bind(background.channels, onset_at), onset_at,
                        self.frontier, observation.at,
                    ))
            elapsed = (observation.at - self.frontier).total_seconds()
            components[0] = replace(background, log_weight=(
                background.log_weight - self.onset_prior.rate_per_second * elapsed))
        updated = tuple(c.observe(observation) for c in components)
        normalizer = _logsumexp(c.log_weight for c in updated)
        self._components = tuple(replace(c, log_weight=c.log_weight - normalizer) for c in updated)
        self.frontier = max(self.frontier, observation.at) if self.frontier else observation.at

    def probabilities(self):
        if len(self._components) == 1:
            return (EventProbability("background", 1.0, float("inf"), len(self._observations)),)
        result = []
        for event in dict.fromkeys(c.event for c in self._components):
            positive = _logsumexp(c.log_weight for c in self._components if c.event == event)
            negative = _logsumexp(c.log_weight for c in self._components if c.event != event)
            result.append(EventProbability(event, exp(positive), positive - negative,
                                           len(self._observations)))
        return tuple(result)

    def onsets(self):
        """Unconditional onset masses; uncertainty is retained, not relabelled exact."""
        return tuple(OnsetProbability(c.profile, c.event, exp(c.log_weight), c.onset_at,
                                      c.interval_start, c.interval_end)
                     for c in self._components if c.onset_at is not None)
