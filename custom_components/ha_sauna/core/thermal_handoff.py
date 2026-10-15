"""Read-only handoff forms for existing warm-up and heating-progress owners.

These forms do not accept samples, fit rates, calculate an ETA or issue notices.
The existing device heating-progress notice remains its sole policy owner.
"""

from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import datetime

from .model_handoff import require_immutable
from .models import Measurement, Position, Quantity
from .warmup import HeatingProgress, HeatingProgressEpisode, WarmupEstimate, WarmupTrend


def _owner(value, expected):
    if not isinstance(value, expected):
        raise TypeError(f"Expected {expected.__name__}")


@dataclass(frozen=True)
class WarmupTrendSnapshot:
    """Whole WarmupTrend state, including its accepted fit cache."""

    window_seconds: float
    observations: tuple[tuple[datetime, float], ...]
    accepted_rate: float | None
    accepted_at: datetime | None

    def __post_init__(self):
        require_immutable(self)

    @classmethod
    def capture(cls, trend: WarmupTrend):
        _owner(trend, WarmupTrend)
        return cls(
            trend.window_seconds,
            trend.observations,
            trend._accepted_rate,
            trend._accepted_at,
        )


@dataclass(frozen=True)
class WarmupTrendEvaluation:
    """Supplied rate() result together with its actual read context and state."""

    state: WarmupTrendSnapshot
    evaluated_at: datetime
    maximum_age_seconds: float | None
    rate: float | None

    def __post_init__(self):
        require_immutable(self)


@dataclass(frozen=True)
class HeatingProgressEvidence:
    """All fields actually returned by HeatingProgress.accept()."""

    started_at: datetime
    ended_at: datetime
    start_temperature_c: float
    end_temperature_c: float
    no_rise: bool
    rising: bool

    def __post_init__(self):
        require_immutable(self)

    @classmethod
    def from_result(cls, evidence: Mapping | None):
        if evidence is None:
            return None
        _owner(evidence, Mapping)
        names = {definition.name for definition in fields(cls)}
        if set(evidence) != names:
            raise ValueError(
                "Heating-progress evidence fields differ from its contract"
            )
        return cls(**evidence)


def _bind_evidence(trend, received_at, temperature_c, evidence):
    if evidence is None:
        return
    points = trend.observations
    if not points or (
        points[0] != (evidence.started_at, evidence.start_temperature_c)
        or points[-1] != (evidence.ended_at, evidence.end_temperature_c)
        or points[-1] != (received_at, temperature_c)
    ):
        raise ValueError(
            "Progress evidence does not describe the supplied observation window"
        )


@dataclass(frozen=True)
class HeatingProgressEvaluation:
    """Actual accept() input/result and the owner's complete nested trend.

    HeatingProgress owns only this trend; no second copy of that state is used.
    """

    trend: WarmupTrendSnapshot
    received_at: datetime
    temperature_c: float
    evidence: HeatingProgressEvidence | None

    def __post_init__(self):
        require_immutable(self)
        _bind_evidence(self.trend, self.received_at, self.temperature_c, self.evidence)

    @classmethod
    def capture(cls, progress: HeatingProgress, received_at, temperature_c, evidence):
        _owner(progress, HeatingProgress)
        return cls(
            WarmupTrendSnapshot.capture(progress.trend),
            received_at,
            temperature_c,
            HeatingProgressEvidence.from_result(evidence),
        )


@dataclass(frozen=True)
class HeatingProgressEpisodeSnapshot:
    """Whole episode state, reusing its progress owner's sole trend state."""

    window_seconds: float
    started_at: datetime
    startup_delay_seconds: float | None
    response_at: datetime | None
    analysis_started_at: datetime | None
    trend: WarmupTrendSnapshot

    def __post_init__(self):
        require_immutable(self)

    @classmethod
    def capture(cls, episode: HeatingProgressEpisode):
        _owner(episode, HeatingProgressEpisode)
        return cls(
            episode.window_seconds,
            episode.started_at,
            episode.startup_delay_seconds,
            episode.response_at,
            episode._analysis_started_at,
            WarmupTrendSnapshot.capture(episode.progress.trend),
        )


@dataclass(frozen=True)
class HeatingProgressEpisodeEvaluation:
    """Episode accept() result; it does not decide whether to issue a notice."""

    state: HeatingProgressEpisodeSnapshot
    received_at: datetime
    temperature_c: float
    evidence: HeatingProgressEvidence | None

    def __post_init__(self):
        require_immutable(self)
        _bind_evidence(
            self.state.trend, self.received_at, self.temperature_c, self.evidence
        )

    @classmethod
    def capture(cls, episode, received_at, temperature_c, evidence):
        return cls(
            HeatingProgressEpisodeSnapshot.capture(episode),
            received_at,
            temperature_c,
            HeatingProgressEvidence.from_result(evidence),
        )


@dataclass(frozen=True)
class WarmupEstimateSnapshot:
    """Whole cached estimate state; capture performs no readout calculation."""

    window_seconds: float
    trend: WarmupTrendSnapshot
    accepted_at: datetime | None
    estimate_started_at: datetime | None
    temperature_c: float | None
    target_c: float | None
    remaining_seconds: float | None
    fit_started_at: datetime | None
    last_fit_at: datetime | None
    stable_fit_seconds: float
    fit_was_continuous: bool
    slower_since: datetime | None
    reference_live_rate: float | None
    last_live_rate: float | None
    source_invalid: bool

    def __post_init__(self):
        require_immutable(self)

    @classmethod
    def capture(cls, estimate: WarmupEstimate):
        _owner(estimate, WarmupEstimate)
        return cls(
            estimate.window_seconds,
            WarmupTrendSnapshot.capture(estimate.trend),
            estimate._accepted_at,
            estimate._estimate_started_at,
            estimate._temperature_c,
            estimate._target_c,
            estimate._remaining_seconds,
            estimate._fit_started_at,
            estimate._last_fit_at,
            estimate._stable_fit_seconds,
            estimate._fit_was_continuous,
            estimate._slower_since,
            estimate._reference_live_rate,
            estimate._last_live_rate,
            estimate._source_invalid,
        )


@dataclass(frozen=True)
class WarmupOwnerKey:
    """The four existing HADevice._warmup_key components without derivation."""

    session_id: str
    heating_source: str
    position: Position
    measurement_source: str

    def __post_init__(self):
        require_immutable(self)

    @classmethod
    def from_key(cls, key: tuple[str, str, Position, str]):
        if not isinstance(key, tuple) or len(key) != 4:
            raise TypeError("Expected the existing four-component warm-up key")
        return cls(*key)


@dataclass(frozen=True)
class WarmupSourceContext:
    """Explicit existing device owner key, source and historical-rate evidence.

    owner_key is HADevice._warmup_key: session, heating feedback source,
    measurement position and measurement source. No key or fallback is inferred.
    Historical references remain distinct from the current session identity.
    """

    owner_key: WarmupOwnerKey | None
    measurement: Measurement | None
    maximum_age_seconds: float | None
    historical_session_id: str | None
    historical_rate: float | None

    def __post_init__(self):
        require_immutable(self)
        if (
            self.measurement is not None
            and self.measurement.quantity != Quantity.TEMPERATURE
        ):
            raise ValueError(
                "Warm-up source evidence must be a temperature measurement"
            )
        if self.owner_key is not None and self.measurement is not None:
            if (
                self.measurement.position != self.owner_key.position
                or self.measurement.source != self.owner_key.measurement_source
            ):
                raise ValueError("Warm-up owner key and measurement source differ")


@dataclass(frozen=True)
class WarmupEstimateObservation:
    """Actual ETA readout with full owner state and supplied validity context.

    remaining_seconds is supplied from the existing core/device read method,
    including None when its source or live gate is unusable. No method is called
    by this form, and no validity or notice policy is reproduced here.
    """

    state: WarmupEstimateSnapshot
    source: WarmupSourceContext
    evaluated_at: datetime
    remaining_seconds: float | None

    def __post_init__(self):
        require_immutable(self)
