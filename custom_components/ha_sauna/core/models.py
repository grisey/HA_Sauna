"""Session-eigene Laufzeitdaten und unveränderte Messherkunft."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from math import isfinite

from .timeline import Timeline, utc
from .contracts import BasePhaseMark, ContactorMark


class Position(StrEnum):
    UPPER = "upper"
    LOWER = "lower"


class Quantity(StrEnum):
    TEMPERATURE = "temperature"
    HUMIDITY = "humidity"


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
class HeatingTime:
    """Tatsächlich rückgemeldete Heizintervalle und lokale Zeitführung."""

    elapsed_seconds: float = 0.0
    reported_heating: bool | None = None
    accounted_at: datetime | None = None
    off_since: datetime | None = None
    last_reset_at: datetime | None = None
    intervals: tuple[HeatingInterval, ...] = ()

    def __post_init__(self) -> None:
        if (
            isinstance(self.elapsed_seconds, bool)
            or not isinstance(self.elapsed_seconds, (int, float))
            or not isfinite(self.elapsed_seconds)
            or self.elapsed_seconds < 0
        ):
            raise ValueError("Heizzeit muss endlich und nicht negativ sein")


@dataclass(frozen=True)
class Energy:
    measured_kwh: float = 0.0
    estimated_kwh: float = 0.0
    measured_seconds: float = 0.0
    estimated_seconds: float = 0.0
    unknown_seconds: float = 0.0
    accounted_at: datetime | None = None

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
    def remaining_seconds(self) -> float:
        return max(0.0, self.duration_seconds - self.elapsed_seconds)


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

    def __post_init__(self) -> None:
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
