"""Session-eigene Laufzeitdaten und Schnittstellen des ersten Umsetzungspakets."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from math import isfinite

from .timeline import Timeline, utc


class Position(StrEnum):
    UPPER = "upper"
    LOWER = "lower"


class Quantity(StrEnum):
    TEMPERATURE = "temperature"
    HUMIDITY = "humidity"


@dataclass(frozen=True)
class Measurement:
    """Normalisierter Eingang; Originalwert und Herkunft bleiben daneben erhalten.

    Noch kein Live-Messadapter. Sekundenraster oder wiederverwendete Werte dürfen
    später nicht als weitere Originalmessungen an diese Schnittstelle gelangen.
    """

    position: Position
    quantity: Quantity
    value: float | None
    raw_value: str
    source: str
    received_at: datetime
    measured_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.position, Position) or not isinstance(self.quantity, Quantity):
            raise ValueError("Gültige Messrolle und Messgröße erforderlich")
        if not self.source or not isinstance(self.raw_value, str):
            raise ValueError("Messherkunft und Originalwert erforderlich")
        if self.value is not None and (
            isinstance(self.value, bool)
            or not isinstance(self.value, (int, float))
            or not isfinite(self.value)
        ):
            raise ValueError("Messwert muss endlich oder als fehlend gekennzeichnet sein")
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
        if not all(isinstance(v, str) and v for v in (self.session_id, self.purpose, self.token)):
            raise ValueError("Session, Zweck und eindeutiges Fristtoken erforderlich")
        object.__setattr__(self, "due_at", utc(self.due_at))


@dataclass(frozen=True)
class HeatingInterval:
    started_at: datetime
    ended_at: datetime | None = None
    end_reason: str | None = None


@dataclass(frozen=True)
class HeatingTime:
    """Anfangsdaten, noch keine Heizzeit- oder Kühlungsberechnung."""

    elapsed_seconds: float = 0.0
    reported_heating: bool | None = None
    accounted_at: datetime | None = None
    off_since: datetime | None = None
    last_reset_at: datetime | None = None
    intervals: tuple[HeatingInterval, ...] = ()

    def __post_init__(self) -> None:
        if (isinstance(self.elapsed_seconds, bool)
                or not isinstance(self.elapsed_seconds, (int, float))
                or not isfinite(self.elapsed_seconds) or self.elapsed_seconds < 0):
            raise ValueError("Heizzeit muss endlich und nicht negativ sein")


@dataclass(frozen=True)
class TimedPhase:
    phase_id: str
    started_at: datetime
    ends_at: datetime


@dataclass(frozen=True)
class CoolingCycle:
    cycle_id: str
    requested_at: datetime
    duration_seconds: float
    credited_seconds: float = 0
    started_at: datetime | None = None
    ends_at: datetime | None = None
    reason: str = "heating_budget"


@dataclass(frozen=True)
class ThermostatState:
    demand: bool = False
    cooldown_until: datetime | None = None


@dataclass(frozen=True)
class Session:
    """Besitzt Gangmodell, Heizzeit und Fristen; Konfiguration bleibt außerhalb."""

    timeline: Timeline
    heating: HeatingTime = field(default_factory=HeatingTime)
    deadlines: tuple[Deadline, ...] = ()
    operation_enabled: bool = False
    operation_off_at: datetime | None = None
    ended_at: datetime | None = None
    thermostat: ThermostatState = field(default_factory=ThermostatState)
    after_run: TimedPhase | None = None
    after_run_history: tuple[TimedPhase, ...] = ()
    cooling: CoolingCycle | None = None
    cooling_history: tuple[CoolingCycle, ...] = ()
    ready_at: datetime | None = None

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
