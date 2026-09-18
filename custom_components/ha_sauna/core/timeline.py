"""Zuordnung erkannter Gaenge zu Tuerschliessungen; keine Heizungssteuerung.

Eingaben werden in Erkennungsreihenfolge verarbeitet. Ein fachlicher Beginn
kann davor liegen. Nur der nachtraeglich zugeordnete Beginn wird vorverlegt,
nicht die Entscheidung, ein Aktorbefehl oder ein HA-Zustandswechsel.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum


def utc(value: datetime) -> datetime:
    """Require a timezone-aware instant and normalize it to UTC."""
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Ein Zeitstempel mit Zeitzone ist erforderlich")
    return value.astimezone(UTC)


class Kind(StrEnum):
    DOOR_OPEN = "door_open"
    DOOR_CLOSE = "door_close"
    PERSON_STRONG = "person_strong"
    PERSON_WEAK = "person_weak"
    INFUSION = "infusion"
    VENTILATION = "ventilation_confirmed"


class Door(StrEnum):
    UNKNOWN = "unknown"
    OPEN = "open"
    CLOSED = "closed"


class UnresolvedTransition(ValueError):
    """A policy not yet agreed by the owner must not be silently invented."""


@dataclass(frozen=True)
class Event:
    event_id: str
    session_id: str
    kind: Kind
    effective_at: datetime
    detected_at: datetime

    def __post_init__(self) -> None:
        if not self.event_id or not self.session_id or not isinstance(self.kind, Kind):
            raise ValueError("Ereignis-ID, Session-ID und gueltiger Typ erforderlich")
        object.__setattr__(self, "effective_at", utc(self.effective_at))
        object.__setattr__(self, "detected_at", utc(self.detected_at))
        if self.effective_at > self.detected_at:
            raise ValueError("Der Ereigniszeitpunkt darf nicht in der Zukunft liegen")


@dataclass(frozen=True)
class Gang:
    gang_id: str
    session_id: str
    started_at: datetime
    detected_at: datetime
    start_source_event_id: str
    recognition_event_id: str
    start_basis: str
    infusion_events: tuple[Event, ...] = ()
    ended_at: datetime | None = None
    end_event_id: str | None = None

    def elapsed_seconds(self, now: datetime) -> float:
        """Duration is available only after recognition, from attributed start."""
        now = utc(now)
        if now < self.detected_at:
            raise ValueError("Vor Erkennung existiert kein aktiver Gangzustand")
        return ((min(now, self.ended_at) if self.ended_at else now) - self.started_at).total_seconds()


@dataclass(frozen=True)
class Timeline:
    session_id: str
    session_started_at: datetime
    door: Door = Door.UNKNOWN
    anchor: Event | None = None
    active: Gang | None = None
    completed: tuple[Gang, ...] = ()
    processed: tuple[Event, ...] = ()

    def __post_init__(self) -> None:
        if not self.session_id or not isinstance(self.door, Door):
            raise ValueError("Session-ID und gueltiger Tuerzustand erforderlich")
        object.__setattr__(self, "session_started_at", utc(self.session_started_at))


def apply(state: Timeline, event: Event) -> Timeline:
    """Pure reducer for the agreed recognition/attribution subset.

    `anchor` belongs to the current closed-door episode, never to a previous
    session. An existing gang keeps its original start across short door use.
    Detection source/strength and each infusion remain explicit input events.
    """
    if event.session_id != state.session_id:
        raise ValueError("Ereignis gehoert zu einer anderen Session")
    for previous in state.processed:
        if previous.event_id == event.event_id:
            if previous != event:
                raise ValueError("Ereignis-ID mit abweichendem Inhalt wiederverwendet")
            return state
    if event.effective_at < state.session_started_at:
        raise ValueError("Ereignis liegt vor dem Sessionbeginn")
    if state.processed and event.detected_at < state.processed[-1].detected_at:
        raise ValueError("Ereignisse muessen in Erkennungsreihenfolge eintreffen")

    result = state
    if event.kind == Kind.DOOR_OPEN:
        if state.door == Door.OPEN:
            raise ValueError("Doppelte Oeffnung mit unterschiedlicher Ereignis-ID")
        result = replace(state, door=Door.OPEN, anchor=None)
    elif event.kind == Kind.DOOR_CLOSE:
        if state.door == Door.CLOSED:
            raise ValueError("Doppelte Schliessung mit unterschiedlicher Ereignis-ID")
        result = replace(state, door=Door.CLOSED, anchor=event)
    elif event.kind in (Kind.PERSON_STRONG, Kind.PERSON_WEAK, Kind.INFUSION):
        if state.door != Door.CLOSED:
            raise ValueError("Gangerkennung benoetigt einen geschlossenen Tuerzustand")
        gang = state.active
        if gang is None:
            anchor = state.anchor
            gang = Gang(
                gang_id=f"{state.session_id}:{event.event_id}",
                session_id=state.session_id,
                started_at=anchor.effective_at if anchor else event.detected_at,
                detected_at=event.detected_at,
                start_source_event_id=anchor.event_id if anchor else event.event_id,
                recognition_event_id=event.event_id,
                start_basis="door_close" if anchor else "recognition_only",
            )
        if event.kind == Kind.INFUSION:
            gang = replace(gang, infusion_events=gang.infusion_events + (event,))
        result = replace(state, active=gang)
    elif event.kind == Kind.VENTILATION:
        if state.door != Door.OPEN:
            raise ValueError("Durchlueftungsbestaetigung benoetigt eine offene Episode")
        if state.active is not None:
            if not state.active.infusion_events:
                raise UnresolvedTransition("Durchlueften im Gang ohne Aufguss: Regel noch offen")
            finished = replace(state.active, ended_at=event.detected_at, end_event_id=event.event_id)
            result = replace(state, active=None, completed=state.completed + (finished,))
    return replace(result, processed=state.processed + (event,))
