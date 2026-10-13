"""Gangzuordnung: Vorbereitung, vorläufige Erkennung und Aufgussbestätigung.

Der Zustand enthält beobachtete Ereignisse und daraus abgeleitete Gangintervalle.
Ein rückwirkend zugeordneter Beginn ändert weder die Erkennungszeit noch einen
bereits ausgeführten Schaltvorgang. Dieses Modul steuert keine Aktoren.
"""

from __future__ import annotations

from collections.abc import Mapping
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
    door: Door = Door.CLOSED
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
    def exit_ventilation(self) -> Event | None:
        """Lüftungsbeleg innerhalb der aktuell zugeordneten Türöffnung."""
        if self.door == Door.OPEN:
            opening, ventilation = self.opening, self.open_ventilation
        elif self.door == Door.CLOSED:
            opening, ventilation = self.closed_opening, self.preparation
            if (self.anchor is None or ventilation is None
                    or ventilation.effective_at > self.anchor.effective_at):
                return None
        else:
            return None
        if (opening is None or ventilation is None
                or ventilation.effective_at < opening.effective_at):
            return None
        return ventilation

    @property
    def entry_cycle_available(self) -> bool:
        """A real, unused opening and closure can establish a new gang."""
        return bool(
            self.door == Door.CLOSED
            and self.anchor is not None and self.closed_opening is not None
            and self.closed_opening.effective_at <= self.anchor.effective_at
            and self.anchor.event_id != self.resolved_presence_close_id
            and self.anchor.event_id not in self.rejected_start_sources
        )

    def presence_exit_at(self, absence_at: datetime) -> datetime | None:
        """Combine current absence with the running gang's own exit episode.

        The presence sensor and thermal door proxy have different delays.
        Closing the door therefore does not expire persistent absence. A new
        opening replaces this episode; an opening from before this gang, or
        one already consumed, cannot finish it.
        """
        opening = self.opening if self.door == Door.OPEN else self.closed_opening
        ventilation = self.exit_ventilation
        if (
            self.active is None
            or opening is None
            or ventilation is None
            or opening.event_id in self.rejected_start_sources
            or opening.effective_at <= self.active.started_at
        ):
            return None
        return max(utc(absence_at), opening.effective_at, ventilation.effective_at)

    @property
    def gang_count(self) -> int:
        return sum(g.confirmation == Confirmation.CONFIRMED for g in self.completed)

    def __post_init__(self) -> None:
        if not self.session_id or not isinstance(self.door, Door):
            raise ValueError("Session-ID und gültiger Türzustand erforderlich")
        object.__setattr__(self, "session_started_at", utc(self.session_started_at))


def project_door_cycles(timeline: Timeline | Mapping) -> list[dict]:
    """Historische Türquellen und vorhandene Gangbezüge rein lesend zuordnen.

    ``processed`` behält seine Buchungsreihenfolge. Nur ein echter folgender
    Türschluss schließt eine Öffnung. Rollen stammen aus den Quellverweisen
    bestehender Gänge; Betrieb-AUS belegt keinen Austritt. ``gang_links`` erhält
    auch Austritt und nachfolgenden Eintritt derselben Türfolge. ``role`` und
    ``gang_id`` sind nur bei genau einem Bezug gesetzt. Archivierte Mappings
    werden ebenso gelesen wie Timeline-Objekte, ohne deren Inhalt zu verändern.
    """
    def get(value, key, default=None):
        return (value.get(key, default) if isinstance(value, Mapping)
                else getattr(value, key, default))

    def time(value):
        try:
            return utc(datetime.fromisoformat(value) if isinstance(value, str) else value)
        except (TypeError, ValueError):
            return None

    session_id = get(timeline, "session_id")
    cycles, evidence = [], []
    sources, close_cycles, exit_cycles = {}, {}, {}
    ambiguous_ids = set()
    current = None
    for index, event in enumerate(get(timeline, "processed", ()) or ()):
        if get(event, "session_id", session_id) != session_id:
            continue
        kind, event_id = get(event, "kind"), get(event, "event_id")
        at = time(get(event, "effective_at"))
        door_event = kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE)
        if not isinstance(event_id, str) or not event_id or at is None:
            if door_event:
                if current is not None:
                    cycles[current]["open"] = False
                current = None
            continue
        if event_id in sources:
            if sources[event_id][0] != event:
                ambiguous_ids.add(event_id)
                if door_event:
                    if current is not None:
                        cycles[current]["open"] = False
                    current = None
            continue
        sources[event_id] = (event, index, at)
        if kind == Kind.DOOR_OPEN:
            if current is not None:
                cycles[current]["open"] = False
            current = len(cycles)
            cycles.append({
                "id": event_id,
                "event_ids": [event_id],
                "started_at": at.isoformat(),
                "ended_at": None,
                "open": True,
                "role": None,
                "gang_id": None,
                "gang_links": [],
            })
            evidence.append({
                "started_at": at, "ended_at": None,
                "opening_index": index, "closing_index": None,
                "ventilation_at": None, "ventilation_event_id": None,
            })
        elif kind == Kind.DOOR_CLOSE:
            if (current is not None and cycles[current]["open"]
                    and at >= evidence[current]["started_at"]):
                cycles[current]["event_ids"].append(event_id)
                cycles[current]["ended_at"] = at.isoformat()
                cycles[current]["open"] = False
                evidence[current]["ended_at"] = at
                evidence[current]["closing_index"] = index
                close_cycles[event_id] = current
            else:
                if current is not None:
                    cycles[current]["open"] = False
                current = None
                cycles.append({
                    "id": event_id,
                    "event_ids": [event_id],
                    "started_at": at.isoformat(),
                    "ended_at": at.isoformat(),
                    "open": False,
                    "role": None,
                    "gang_id": None,
                    "gang_links": [],
                })
                evidence.append(None)
        elif current is not None and kind == Kind.VENTILATION:
            episode = evidence[current]
            if cycles[current]["open"]:
                if episode["ventilation_at"] is None:
                    episode["ventilation_at"] = at
                    episode["ventilation_event_id"] = event_id
                if at >= episode["started_at"]:
                    exit_cycles[event_id] = current
        elif current is not None and kind == Kind.PRESENCE_ENDED:
            episode = evidence[current]
            ventilation = episode["ventilation_at"]
            # Präsenzende darf nach dem Schluss wirken. Der damalige aktuelle
            # Öffnungsbezug und dessen Lüftungsbeleg bleiben maßgeblich.
            if (ventilation is not None
                    and episode["started_at"] <= ventilation <= at
                    and (episode["ended_at"] is None
                         or ventilation <= episode["ended_at"])):
                exit_cycles[event_id] = current

    roles = {}
    retracted = {get(gang, "gang_id")
                 for gang in get(timeline, "retracted", ()) or ()
                 if get(gang, "session_id", session_id) == session_id}
    active = get(timeline, "active")
    gangs = [*(get(timeline, "completed", ()) or ()), *([active] if active else [])]
    for gang in gangs:
        gang_id = get(gang, "gang_id")
        if (not isinstance(gang_id, str) or not gang_id or gang_id in retracted
                or get(gang, "session_id", session_id) != session_id):
            continue
        started_at = time(get(gang, "started_at"))
        if started_at is None:
            continue
        start_source = get(gang, "start_source_event_id")
        entry = close_cycles.get(start_source)
        if (entry is not None and start_source not in ambiguous_ids
                and started_at >= evidence[entry]["ended_at"]):
            roles.setdefault(entry, {})[("entry", gang_id)] = None
        end_source = get(gang, "end_event_id")
        exit_cycle = exit_cycles.get(end_source)
        if (exit_cycle is None or end_source in ambiguous_ids
                or time(get(gang, "ended_at")) is None):
            continue
        end_event, _, end_at = sources[end_source]
        episode = evidence[exit_cycle]
        end_kind, end_reason = get(end_event, "kind"), get(gang, "end_reason")
        expected_reason = ("presence_exit" if end_kind == Kind.PRESENCE_ENDED
                           else "ventilation")
        if (end_reason not in (None, expected_reason)
                or episode["started_at"] < started_at
                or (end_kind == Kind.PRESENCE_ENDED
                    and (episode["started_at"] <= started_at
                         or episode["ventilation_event_id"] in ambiguous_ids))
                or (entry is not None
                    and episode["opening_index"] <= evidence[entry]["closing_index"])
                or (end_kind == Kind.VENTILATION
                    and episode["ended_at"] is not None
                    and end_at > episode["ended_at"])):
            continue
        roles.setdefault(exit_cycle, {})[("exit", gang_id)] = None
    for index, candidates in roles.items():
        if ambiguous_ids.intersection(cycles[index]["event_ids"]):
            continue
        cycles[index]["gang_links"] = [
            {"gang_id": gang_id, "role": role} for role, gang_id in candidates
        ]
        if len(candidates) == 1:
            cycles[index]["role"], cycles[index]["gang_id"] = next(iter(candidates))
    return cycles


def apply(state: Timeline, event: Event, *, gang_start_at: datetime | None = None) -> Timeline:
    """Ein Ereignis verarbeiten, ohne den bisherigen Zustand zu verändern.

    `active` umfasst vorläufige und bestätigte Gänge. Vorbereitung gehört zur
    letzten abgeschlossenen Türöffnungsepisode. Jeder neue Gang benötigt eine
    ungenutzte vollständige Öffnungs- und Schließfolge.
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
            rejected_start_sources=(
                (event.event_id,) if state.active is not None
                and state.active.recognition_kind != Kind.PRESENCE_CONFIRMED else ()
            ),
        )
    elif event.kind == Kind.DOOR_CLOSE:
        if state.door == Door.CLOSED:
            raise ValueError("Doppelte Schließung mit unterschiedlicher Ereignis-ID")
        if state.opening is not None and event.effective_at < state.opening.effective_at:
            raise ValueError("Türschluss liegt vor der zugehörigen Öffnung")
        result = replace(
            state,
            door=Door.CLOSED,
            anchor=event,
            opening=None,
            closed_opening=state.opening,
            preparation=state.open_ventilation,
            open_ventilation=None,
        )
        if (state.active is not None
                and state.active.confirmation == Confirmation.CONFIRMED):
            # A confirmed gang consumes door cycles within that same stay.
            # A provisional gang keeps its own start anchor; a newer cycle
            # remains available if that earlier conjecture later expires.
            result = replace(result, rejected_start_sources=(
                *state.rejected_start_sources, event.event_id,
            ))
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
            if not state.entry_cycle_available:
                raise ValueError("Gangstart benötigt eine vollständige neue Türepisode")
            anchor = state.anchor
            if event.effective_at < anchor.effective_at:
                raise ValueError("Gangerkennung liegt vor dem zugehörigen Türschluss")
            start = utc(gang_start_at) if gang_start_at is not None else anchor.effective_at
            if not anchor.effective_at <= start <= event.effective_at:
                raise ValueError("Gangbeginn benötigt Türschluss und erfüllte Startbedingungen")
            gang = Gang(
                gang_id=f"{state.session_id}:{event.event_id}",
                session_id=state.session_id,
                started_at=start,
                detected_at=event.detected_at,
                start_source_event_id=anchor.event_id,
                recognition_event_id=event.event_id,
                recognition_kind=event.kind,
                start_basis=("door_close" if start == anchor.effective_at
                             else "minimum_temperature"),
                preparation_event_id=(
                    state.preparation.event_id if state.preparation else None
                ),
            )
            result = replace(result, rejected_start_sources=(
                *state.rejected_start_sources, anchor.event_id,
            ))
        if event.kind == Kind.INFUSION:
            gang = replace(gang, infusion_events=gang.infusion_events + (event,))
        result = replace(result, active=gang)
        if event.kind == Kind.PRESENCE_CONFIRMED:
            result = replace(result, resolved_presence_close_id=state.anchor.event_id)
    elif event.kind == Kind.PRESENCE_ENDED:
        opening = state.opening if state.door == Door.OPEN else state.closed_opening
        if state.presence_exit_at(event.effective_at) != event.effective_at:
            raise ValueError(
                "Gangende benötigt Abwesenheit und bestätigtes Lüften einer neuen Austrittsöffnung"
            )
        finished = replace(state.active, ended_at=event.booking_at,
                           end_event_id=event.event_id, end_reason="presence_exit")
        result = replace(state, active=None, completed=state.completed + (finished,),
                         rejected_start_sources=(*state.rejected_start_sources,
                                                 opening.event_id),
                         resolved_presence_close_id=(
                             state.anchor.event_id if state.anchor
                             else state.resolved_presence_close_id
                         ))
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
                result = replace(result, active=None,
                                 retracted=state.retracted + (state.active,))
            else:
                finished = replace(state.active, ended_at=event.booking_at,
                                   end_event_id=event.event_id, end_reason="ventilation")
                result = replace(result, active=None,
                                 completed=state.completed + (finished,))
    elif event.kind == Kind.CONFIRMATION_EXPIRED:
        if state.active is not None and state.active.confirmation == Confirmation.PROVISIONAL:
            result = replace(
                state,
                active=None,
                retracted=state.retracted + (state.active,),
                rejected_start_sources=state.rejected_start_sources
                + (state.active.start_source_event_id,),
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
