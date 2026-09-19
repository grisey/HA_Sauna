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
class HeatingTime:
    """Anfangsdaten, noch keine Heizzeit- oder Kühlungsberechnung."""

    elapsed_seconds: float = 0.0

    def __post_init__(self) -> None:
        if (isinstance(self.elapsed_seconds, bool)
                or not isinstance(self.elapsed_seconds, (int, float))
                or not isfinite(self.elapsed_seconds) or self.elapsed_seconds < 0):
            raise ValueError("Heizzeit muss endlich und nicht negativ sein")


@dataclass(frozen=True)
class Session:
    """Besitzt Gangmodell, Heizzeit und Fristen; Konfiguration bleibt außerhalb."""

    timeline: Timeline
    heating: HeatingTime = field(default_factory=HeatingTime)
    deadlines: tuple[Deadline, ...] = ()

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
