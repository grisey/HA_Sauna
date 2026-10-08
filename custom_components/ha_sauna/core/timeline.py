"""Gangzuordnung: Vorbereitung, vorläufige Erkennung und Aufgussbestätigung.

Der Zustand enthält beobachtete Ereignisse und daraus abgeleitete Gangintervalle.
Ein rückwirkend zugeordneter Beginn ändert weder die Erkennungszeit noch einen
bereits ausgeführten Schaltvorgang. Dieses Modul steuert keine Aktoren.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum


def utc(value: datetime) -> datetime:
    """Einen Zeitpunkt mit Zeitzone verlangen und nach UTC umrechnen."""
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
    OPERATION_OFF = "operation_off"
    CONFIRMATION_EXPIRED = "confirmation_expired"
    PRESENCE_CONFIRMED = "presence_confirmed"
    PRESENCE_ENDED = "presence_ended"


class Door(StrEnum):
    UNKNOWN = "unknown"
    OPEN = "open"
    CLOSED = "closed"


class Confirmation(StrEnum):
    """Bestätigungsstand eines Gangs, unabhängig von dessen Abschluss."""

    PROVISIONAL = "provisional"
    CONFIRMED = "confirmed"


class UnresolvedTransition(ValueError):
    """Für diesen Übergang fehlt noch eine fachliche Festlegung."""


@dataclass(frozen=True)
class Event:
    event_id: str
    session_id: str
    kind: Kind
    effective_at: datetime
    detected_at: datetime
    booking_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.event_id or not self.session_id or not isinstance(self.kind, Kind):
            raise ValueError("Ereignis-ID, Session-ID und gültiger Typ erforderlich")
        object.__setattr__(self, "effective_at", utc(self.effective_at))
        object.__setattr__(self, "detected_at", utc(self.detected_at))
        object.__setattr__(
            self, "booking_at",
            utc(self.booking_at) if self.booking_at is not None else self.detected_at,
        )
        if self.effective_at > self.detected_at:
            raise ValueError("Der Ereigniszeitpunkt darf nicht in der Zukunft liegen")
        if not self.effective_at <= self.booking_at <= self.detected_at:
            raise ValueError("Buchungszeit muss zwischen Ereignis- und Erkennungszeit liegen")


@dataclass(frozen=True)
class Gang:
    gang_id: str
    session_id: str
    started_at: datetime
    detected_at: datetime
    start_source_event_id: str
    recognition_event_id: str
    recognition_kind: Kind
    start_basis: str
    preparation_event_id: str | None = None
    infusion_events: tuple[Event, ...] = ()
    ended_at: datetime | None = None
    end_event_id: str | None = None
    end_reason: str | None = None

    @property
    def confirmation(self) -> Confirmation:
        """Bestätigung aus dem führenden Quellbeleg, ohne zweiten Zustandsmerker."""
        return (
            Confirmation.CONFIRMED
            if self.infusion_events or self.recognition_kind == Kind.PRESENCE_CONFIRMED
            else Confirmation.PROVISIONAL
        )

    @property
    def confirmed_at(self) -> datetime | None:
        """Tatsächliche Erkennungszeit des ersten zugeordneten Aufgusses."""
        if self.recognition_kind == Kind.PRESENCE_CONFIRMED:
            return self.detected_at
        return self.infusion_events[0].detected_at if self.infusion_events else None

    @property
    def confirmation_event_id(self) -> str | None:
        if self.recognition_kind == Kind.PRESENCE_CONFIRMED:
            return self.recognition_event_id
        return self.infusion_events[0].event_id if self.infusion_events else None

    def elapsed_seconds(self, now: datetime) -> float:
        """Dauer ab zugeordnetem Beginn, verfügbar ab vorläufiger Erkennung."""
        now = utc(now)
        if now < self.detected_at:
            raise ValueError("Vor Erkennung existiert kein aktiver Gangzustand")
        end = min(now, self.ended_at) if self.ended_at else now
        return (end - self.started_at).total_seconds()


@dataclass(frozen=True)
class Timeline:
    session_id: str
    session_started_at: datetime
    door: Door = Door.UNKNOWN
    anchor: Event | None = None
    opening: Event | None = None
    closed_opening: Event | None = None
    resolved_presence_close_id: str | None = None
    open_ventilation: Event | None = None
    preparation: Event | None = None
    active: Gang | None = None
    completed: tuple[Gang, ...] = ()
    processed: tuple[Event, ...] = ()
    # Aufgehobene Erkennungen sind Diagnosedaten, keine abgeschlossenen Gänge.
    retracted: tuple[Gang, ...] = ()
    rejected_start_sources: tuple[str, ...] = ()

    @property
    def gang_count(self) -> int:
        return sum(g.confirmation == Confirmation.CONFIRMED for g in self.completed)

    def __post_init__(self) -> None:
        if not self.session_id or not isinstance(self.door, Door):
            raise ValueError("Session-ID und gültiger Türzustand erforderlich")
        object.__setattr__(self, "session_started_at", utc(self.session_started_at))


def apply(state: Timeline, event: Event) -> Timeline:
    """Ein Ereignis verarbeiten, ohne den bisherigen Zustand zu verändern.

    `active` umfasst vorläufige und bestätigte Gänge. Vorbereitung gehört zur
    letzten abgeschlossenen Türöffnungsepisode. Das schwache Startsignal
    benötigt deren tatsächlichen Türschlussanker; der starke Pfad und Aufguss
    bleiben davon unabhängig.
    Ein bestehender Gang behält seine ursprüngliche Zuordnung bei Türbetätigung.
    """
    if event.session_id != state.session_id:
        raise ValueError("Ereignis gehört zu einer anderen Session")
    for previous in state.processed:
        if previous.event_id == event.event_id:
            if previous != event:
                raise ValueError("Ereignis-ID mit abweichendem Inhalt wiederverwendet")
            return state
    if event.effective_at < state.session_started_at:
        raise ValueError("Ereignis liegt vor dem Sessionbeginn")
    if state.processed and event.booking_at < state.processed[-1].booking_at:
        raise ValueError("Ereignisse müssen in Buchungsreihenfolge eintreffen")

    result = state
    if event.kind == Kind.DOOR_OPEN:
        if state.door == Door.OPEN:
            raise ValueError("Doppelte Öffnung mit unterschiedlicher Ereignis-ID")
        result = replace(
            state,
            door=Door.OPEN,
            anchor=None,
            opening=event,
            closed_opening=None,
            open_ventilation=None,
            preparation=None,
            rejected_start_sources=(),
        )
    elif event.kind == Kind.DOOR_CLOSE:
        if state.door == Door.CLOSED:
            raise ValueError("Doppelte Schließung mit unterschiedlicher Ereignis-ID")
        result = replace(
            state,
            door=Door.CLOSED,
            anchor=event,
            opening=None,
            closed_opening=state.opening,
            preparation=state.open_ventilation,
            open_ventilation=None,
        )
    elif event.kind in (Kind.PERSON_STRONG, Kind.PERSON_WEAK, Kind.INFUSION,
                        Kind.PRESENCE_CONFIRMED):
        if state.door != Door.CLOSED:
            raise ValueError("Gangerkennung benötigt einen geschlossenen Türzustand")
        if event.kind == Kind.PRESENCE_CONFIRMED and (
            state.closed_opening is None or state.anchor is None
            or state.anchor.event_id == state.resolved_presence_close_id
        ):
            raise ValueError("Direkte Präsenz benötigt eine vollständige neue Türepisode")
        gang = state.active
        if gang is None:
            source = state.anchor.event_id if state.anchor else "recognition_only"
            if event.kind != Kind.INFUSION and source in state.rejected_start_sources:
                return replace(state, processed=state.processed + (event,))
            if event.kind == Kind.PERSON_WEAK and state.anchor is None:
                raise ValueError("Schwacher Gangstart benötigt einen Türschlussanker")
            anchor = state.anchor
            gang = Gang(
                gang_id=f"{state.session_id}:{event.event_id}",
                session_id=state.session_id,
                started_at=anchor.effective_at if anchor else event.booking_at,
                detected_at=event.detected_at,
                start_source_event_id=anchor.event_id if anchor else event.event_id,
                recognition_event_id=event.event_id,
                recognition_kind=event.kind,
                start_basis="door_close" if anchor else "recognition_only",
                preparation_event_id=(
                    state.preparation.event_id if state.preparation else None
                ),
            )
        if event.kind == Kind.INFUSION:
            gang = replace(gang, infusion_events=gang.infusion_events + (event,))
        result = replace(state, active=gang)
        if event.kind == Kind.PRESENCE_CONFIRMED:
            result = replace(result, resolved_presence_close_id=state.anchor.event_id)
    elif event.kind == Kind.PRESENCE_ENDED:
        if (state.active is None or state.door != Door.CLOSED
                or state.closed_opening is None or state.anchor is None
                or state.anchor.event_id == state.resolved_presence_close_id
                or state.closed_opening.effective_at <= state.active.started_at):
            raise ValueError("Gangende benötigt eine neue vollständige Türepisode")
        finished = replace(state.active, ended_at=event.booking_at,
                           end_event_id=event.event_id, end_reason="presence_exit")
        result = replace(state, active=None, completed=state.completed + (finished,),
                         resolved_presence_close_id=state.anchor.event_id)
    elif event.kind == Kind.VENTILATION:
        if state.door != Door.OPEN:
            raise ValueError("Durchlüftungsbestätigung benötigt eine offene Episode")
        if state.opening is None:
            raise UnresolvedTransition("Durchlüften ohne zugeordnete Öffnung")
        # Mehrere Bestätigungen derselben Episode ändern deren ersten Beleg nicht.
        result = replace(state, open_ventilation=state.open_ventilation or event)
        if (state.active is not None
                and state.active.recognition_kind != Kind.PRESENCE_CONFIRMED):
            if state.active.confirmation == Confirmation.PROVISIONAL:
                result = replace(
                    result,
                    active=None,
                    retracted=state.retracted + (state.active,),
                    rejected_start_sources=state.rejected_start_sources
                    + (
                        state.active.start_source_event_id
                        if state.active.start_basis == "door_close"
                        else "recognition_only",
                    ),
                )
            else:
                finished = replace(
                    state.active,
                    ended_at=event.booking_at,
                    end_event_id=event.event_id,
                    end_reason="ventilation",
                )
                result = replace(
                    result, active=None, completed=state.completed + (finished,)
                )
    elif event.kind == Kind.CONFIRMATION_EXPIRED:
        if state.active is not None and state.active.confirmation == Confirmation.PROVISIONAL:
            result = replace(
                state,
                active=None,
                retracted=state.retracted + (state.active,),
                rejected_start_sources=state.rejected_start_sources
                + (
                    state.active.start_source_event_id
                    if state.active.start_basis == "door_close"
                    else "recognition_only",
                ),
            )
    elif event.kind == Kind.OPERATION_OFF:
        completed = state.completed
        if state.active is not None:
            completed += (
                replace(
                    state.active,
                    ended_at=event.booking_at,
                    end_event_id=event.event_id,
                    end_reason="ausgeschaltet",
                ),
            )
        # Eine spätere neue Erkennung darf nicht den Beginn des ausgeschalteten
        # Gangs erben. Die bekannte Türlage bleibt erhalten.
        result = replace(
            state, active=None, completed=completed, anchor=None, preparation=None
        )
    return replace(result, processed=state.processed + (event,))
