"""Session-eigene Laufzeitdaten und unveränderte Messherkunft."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from math import isfinite

from .timeline import Timeline, utc
from .contracts import BasePhaseMark, ContactorMark
from .oven_cooling import OvenCoolingResult


class Position(StrEnum):
    UPPER = "upper"
    LOWER = "lower"


class Quantity(StrEnum):
    TEMPERATURE = "temperature"
    HUMIDITY = "humidity"


@dataclass(frozen=True)
class SampleIdentity:
    """Physical sensor read, independent of transport and wall-clock changes."""

    sensor_id: str
    boot_id: str
    sample_id: int
    monotonic_ns: int

    def __post_init__(self):
        if (not isinstance(self.sensor_id, str) or not self.sensor_id
                or not isinstance(self.boot_id, str) or not self.boot_id
                or type(self.sample_id) is not int or self.sample_id < 1
                or type(self.monotonic_ns) is not int or self.monotonic_ns < 0):
            raise ValueError("Physical sample needs sensor, boot, positive ID and monotonic time")

    @property
    def identity(self):
        return self.sensor_id, self.boot_id, self.sample_id


@dataclass(frozen=True)
class Measurement:
    """Normalisierter Eingang; Originalwert und Herkunft bleiben daneben erhalten.

    Sekundenraster oder wiederverwendete Werte werden getrennt von tatsächlichen
    Originalmessungen geführt und archiviert.
    """

    position: Position
    quantity: Quantity
    value: float | None
    raw_value: str
    source: str
    received_at: datetime
    measured_at: datetime | None = None
    sample: SampleIdentity | None = None
    original_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.position, Position) or not isinstance(
            self.quantity, Quantity
        ):
            raise ValueError("Gültige Messrolle und Messgröße erforderlich")
        if not self.source or not isinstance(self.raw_value, str):
            raise ValueError("Messherkunft und Originalwert erforderlich")
        if self.value is not None and (
            isinstance(self.value, bool)
            or not isinstance(self.value, (int, float))
            or not isfinite(self.value)
        ):
            raise ValueError(
                "Messwert muss endlich oder als fehlend gekennzeichnet sein"
            )
        object.__setattr__(self, "received_at", utc(self.received_at))
        if self.measured_at is not None:
            object.__setattr__(self, "measured_at", utc(self.measured_at))
        if self.sample is not None:
            if not isinstance(self.sample, SampleIdentity):
                raise ValueError("Sample metadata must describe one physical read")
            if self.value is not None and self.measured_at is None:
                raise ValueError("A successful physical sample needs its measurement time")
        if self.original_id is not None and (not isinstance(self.original_id, str) or not self.original_id):
            raise ValueError("An archival original identity must be nonempty text")

    @classmethod
    def from_payload(cls, payload, *, original_id=None):
        """Read current and legacy archive originals through the same contract."""
        return cls(
            Position(payload["position"]), Quantity(payload["quantity"]),
            payload["value"], payload["raw_value"], payload["source"],
            datetime.fromisoformat(payload["received_at"]),
            datetime.fromisoformat(payload["measured_at"]) if payload.get("measured_at") else None,
            SampleIdentity(**payload["sample"]) if payload.get("sample") is not None else None,
            payload.get("original_id") or original_id,
        )

    @property
    def source_identity(self):
        return (self.source, self.sample.sensor_id, self.sample.boot_id) if self.sample else (self.source,)

    @property
    def freshness_at(self):
        """Control freshness; independent of whether an original is evidence."""
        return (min(self.received_at, self.measured_at)
                if self.sample is not None and self.measured_at else self.received_at)


@dataclass(frozen=True)
class Deadline:
    """Fristidentität verhindert die Wirkung alter oder ersetzter Aufrufe."""

    session_id: str
    purpose: str
    token: str
    due_at: datetime

    def __post_init__(self) -> None:
        if not all(
            isinstance(v, str) and v
            for v in (self.session_id, self.purpose, self.token)
        ):
            raise ValueError("Session, Zweck und eindeutiges Fristtoken erforderlich")
        object.__setattr__(self, "due_at", utc(self.due_at))


@dataclass(frozen=True)
class HeatingInterval:
    started_at: datetime
    ended_at: datetime | None = None
    end_reason: str | None = None


@dataclass(frozen=True)
class HeatingStateMark:
    """Vom Heizzeitbesitzer wirksam übernommene Rückmeldung."""

    at: datetime
    heating: bool | None

    def __post_init__(self) -> None:
        if self.heating is not None and not isinstance(self.heating, bool):
            raise ValueError("Heizrückmeldung muss wahr, falsch oder unbekannt sein")
        object.__setattr__(self, "at", utc(self.at))


@dataclass(frozen=True)
class HeatingTime:
    """Tatsächlich rückgemeldete Heizintervalle und lokale Zeitführung."""

    elapsed_seconds: float = 0.0
    reported_heating: bool | None = None
    accounted_at: datetime | None = None
    off_since: datetime | None = None
    last_reset_at: datetime | None = None
    intervals: tuple[HeatingInterval, ...] = ()
    state_history: tuple[HeatingStateMark, ...] = ()

    def __post_init__(self) -> None:
        if (
            isinstance(self.elapsed_seconds, bool)
            or not isinstance(self.elapsed_seconds, (int, float))
            or not isfinite(self.elapsed_seconds)
            or self.elapsed_seconds < 0
        ):
            raise ValueError("Heizzeit muss endlich und nicht negativ sein")
        if not isinstance(self.state_history, tuple):
            raise ValueError("Heizverlauf benötigt Heizrückmeldungen als Tupel")


@dataclass(frozen=True)
class EnergyInterval:
    """Zeitlich begrenzter Abrechnungsbeitrag mit unveränderter Herkunft."""

    started_at: datetime
    ended_at: datetime
    source: str
    power_kw: float | None


@dataclass(frozen=True)
class Energy:
    measured_kwh: float = 0.0
    estimated_kwh: float = 0.0
    measured_seconds: float = 0.0
    estimated_seconds: float = 0.0
    unknown_seconds: float = 0.0
    accounted_at: datetime | None = None
    intervals: tuple[EnergyInterval, ...] = ()

    @property
    def total_kwh(self):
        return self.measured_kwh + self.estimated_kwh

    @property
    def source(self):
        if self.unknown_seconds:
            return "incomplete"
        if self.measured_seconds and self.estimated_seconds:
            return "mixed"
        return "measured" if self.measured_seconds else "estimated"


@dataclass(frozen=True)
class LightAfterRun:
    """Lichtfrist nach Sitzungsende; ohne Wirkung auf Ofen oder Heizfristen."""

    session_id: str
    started_at: datetime
    ends_at: datetime
    brightness_percent: float


@dataclass(frozen=True)
class TimedPhase:
    phase_id: str
    started_at: datetime
    ends_at: datetime | None
    duration_seconds: float | None = None
    elapsed_seconds: float = 0.0
    accounted_at: datetime | None = None
    paused_at: datetime | None = None
    active_intervals: tuple[tuple[datetime, datetime | None], ...] = ()
    # New cooling starts counting only after an actual contactor-OFF report.
    # Existing archived phases have no calculation and retain their old fields.
    pending_start: bool = False
    requested_at: datetime | None = None
    cooling_calculation: OvenCoolingResult | None = None
    # Proxy evidence caps this same phase; original duration and factual OFF
    # accounting stay intact, even when the minimum has already elapsed.
    minimum_duration_requested: bool = False

    def __post_init__(self) -> None:
        # Ältere Aufrufer kannten nur Start und Ende. Die Dauer wird einmal
        # daraus übernommen; nach einer Pause ist sie vom leeren Ende getrennt.
        if self.duration_seconds is None:
            if self.ends_at is None:
                raise ValueError("Pausierte Phase benötigt ihre ursprüngliche Dauer")
            object.__setattr__(
                self,
                "duration_seconds",
                max(0.0, (self.ends_at - self.started_at).total_seconds()),
            )
        if (
            isinstance(self.duration_seconds, bool)
            or not isinstance(self.duration_seconds, (int, float))
            or not isfinite(self.duration_seconds)
            or self.duration_seconds < 0
        ):
            raise ValueError("Phasendauer muss endlich und nicht negativ sein")
        if (
            isinstance(self.elapsed_seconds, bool)
            or not isinstance(self.elapsed_seconds, (int, float))
            or not isfinite(self.elapsed_seconds)
            or self.elapsed_seconds < 0
        ):
            raise ValueError("Phasenzeit muss endlich und nicht negativ sein")
        if self.elapsed_seconds > self.duration_seconds:
            raise ValueError("Phasenzeit darf ihre Dauer nicht überschreiten")

    @property
    def required_seconds(self) -> float:
        if self.minimum_duration_requested and self.cooling_calculation is not None:
            minimum = timedelta(
                minutes=self.cooling_calculation.base_minutes
            ).total_seconds()
            return min(self.duration_seconds, minimum)
        return self.duration_seconds

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.required_seconds - self.elapsed_seconds)


@dataclass(frozen=True)
class CoolingCycle:
    """Historisches Archivformat; wird von der aktiven Steuerung nicht erzeugt."""

    cycle_id: str
    requested_at: datetime
    duration_seconds: float
    credited_seconds: float = 0
    started_at: datetime | None = None
    ends_at: datetime | None = None
    elapsed_seconds: float = 0
    accounted_at: datetime | None = None
    paused_at: datetime | None = None
    reason: str = "heating_budget"

    @property
    def remaining_seconds(self) -> float:
        """Noch echte, nicht durch Nachlauf gedeckte Kühlzeit."""
        return max(
            0.0, self.duration_seconds - self.credited_seconds - self.elapsed_seconds
        )


@dataclass(frozen=True)
class ThermostatState:
    demand: bool = False
    cooldown_until: datetime | None = None


@dataclass(frozen=True)
class TemperatureTargetMark:
    """Zum Zeitpunkt wirksamer Sollwert des bestehenden Sitzungsbesitzers."""

    at: datetime
    target_c: float | None

    def __post_init__(self) -> None:
        if self.target_c is not None and (
            isinstance(self.target_c, bool)
            or not isinstance(self.target_c, (int, float))
            or not isfinite(self.target_c)
        ):
            raise ValueError("Solltemperatur muss endlich oder unbekannt sein")
        object.__setattr__(self, "at", utc(self.at))


@dataclass(frozen=True)
class Session:
    """Besitzt Gangmodell, Heizzeit und Fristen; Konfiguration bleibt außerhalb."""

    timeline: Timeline
    heating: HeatingTime = field(default_factory=HeatingTime)
    energy: Energy = field(default_factory=Energy)
    deadlines: tuple[Deadline, ...] = ()
    operation_enabled: bool = False
    operation_off_at: datetime | None = None
    ended_at: datetime | None = None
    thermostat: ThermostatState = field(default_factory=ThermostatState)
    after_run: TimedPhase | None = None
    after_run_history: tuple[TimedPhase, ...] = ()
    last_completed_oven_cooling_at: datetime | None = None
    # Nur zum Lesen historischer Sitzungen; keine aktive Kühlsteuerung.
    cooling: CoolingCycle | None = None
    cooling_history: tuple[CoolingCycle, ...] = ()
    ready_at: datetime | None = None
    base_phases: tuple[BasePhaseMark, ...] = ()
    contactor_history: tuple[ContactorMark, ...] = ()
    # Anker verweisen in die Timeline; sie zählen keine Gänge selbst.
    temperature_base_c: float | None = None
    temperature_base_gang_count: int = 0
    temperature_program_mode: str | None = None
    temperature_program_gangs: int | None = None
    temperature_program_start_gang_count: int = 0
    temperature_program_steps: tuple[float, ...] | None = None
    next_gang_temperature_c: float | None = None
    next_gang_temperature_blocked_by: str | None = None
    active_gang_temperature_c: float | None = None
    active_gang_temperature_id: str | None = None
    target_history: tuple[TemperatureTargetMark, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.target_history, tuple):
            raise ValueError("Sollverlauf benötigt Sollwertmarken als Tupel")
        purposes = set()
        for deadline in self.deadlines:
            if deadline.session_id != self.session_id:
                raise ValueError("Frist gehört zu einer anderen Session")
            if deadline.due_at < self.started_at or deadline.purpose in purposes:
                raise ValueError("Ungültige oder doppelte Sessionfrist")
            purposes.add(deadline.purpose)

    @classmethod
    def create(cls, session_id: str, at: datetime) -> Session:
        return cls(Timeline(session_id, utc(at)))

    @property
    def session_id(self) -> str:
        # Sessionidentität nicht nochmals parallel zum vorhandenen Gangmodell führen.
        return self.timeline.session_id

    @property
    def started_at(self) -> datetime:
        return self.timeline.session_started_at
