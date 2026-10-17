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
    VENTILATION_COMPLETE = "ventilation_complete"
    OPERATION_OFF = "operation_off"
    CONFIRMATION_EXPIRED = "confirmation_expired"
    PRESENCE_CONFIRMED = "presence_confirmed"
    PRESENCE_ENDED = "presence_ended"
    PRESENCE_CLOSE_ENDED = "presence_close_ended"


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
class DoorCloseEvidence:
    """Observed candidate and confirmation of one retained closing proof."""

    candidate_at: datetime
    confirmed_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_at", utc(self.candidate_at))
        object.__setattr__(self, "confirmed_at", utc(self.confirmed_at))
        if self.candidate_at > self.confirmed_at:
            raise ValueError("Türschlusskandidat muss vor seiner Bestätigung liegen")


@dataclass(frozen=True)
class Event:
    """Ereignis mit dem ursprünglichen Diagnose-/Bestätigungsraster.

    ``trace_at`` transportiert dessen Zeitpunkt; die reale Erkennungszeit
    bleibt ``detected_at``.
    """

    event_id: str
    session_id: str
    kind: Kind
    effective_at: datetime
    detected_at: datetime
    booking_at: datetime | None = None
    trace_at: datetime | None = None
    door_close_evidence: DoorCloseEvidence | None = None

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
        if self.trace_at is not None:
            object.__setattr__(self, "trace_at", utc(self.trace_at))
            if not self.effective_at <= self.trace_at <= self.booking_at <= self.detected_at:
                raise ValueError(
                    "Bestätigungszeit muss zwischen Ereignis- und Buchungszeit liegen"
                )
        if self.door_close_evidence is not None:
            if (self.kind != Kind.DOOR_CLOSE
                    or not isinstance(self.door_close_evidence, DoorCloseEvidence)
                    or self.door_close_evidence.confirmed_at > self.booking_at):
                raise ValueError("Türschlussbeleg benötigt seine bereits bestätigte Schließung")

    @property
    def confirmation_at(self) -> datetime:
        """Ursprüngliche Bestätigung, ohne Rasterangabe bisheriger Ereigniszeitpunkt."""
        return self.trace_at if self.trace_at is not None else self.effective_at


@dataclass(frozen=True)
class InfusionEnd:
    """Vom Detektor gelieferter Endbeleg eines einzelnen Aufgusses."""

    effective_at: datetime
    confirmed_at: datetime
    trace_at: datetime
    basis: str
    time_basis: str

    def __post_init__(self) -> None:
        if not isinstance(self.basis, str) or not self.basis.strip():
            raise ValueError("Aufgussende benötigt eine nicht leere Beleggrundlage")
        if self.time_basis not in ("physical_effective", "evidence_grid"):
            raise ValueError("Ungültige Zeitbasis des Aufgussendes")
        for name in ("effective_at", "confirmed_at", "trace_at"):
            object.__setattr__(self, name, utc(getattr(self, name)))
        if not self.effective_at <= self.trace_at <= self.confirmed_at:
            raise ValueError(
                "Ende, Auswertungsraster und Bestätigung müssen zeitlich geordnet sein"
            )


@dataclass(frozen=True)
class Infusion:
    """Zusätzliche Metadaten zum unveränderten führenden Aufgussereignis."""

    source_event_id: str
    end: InfusionEnd | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source_event_id, str) or not self.source_event_id.strip():
            raise ValueError("Aufguss benötigt eine nicht leere Quellereignis-ID")
        if self.end is not None and not isinstance(self.end, InfusionEnd):
            raise ValueError("Ungültiger Endbeleg des Aufgusses")


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
    infusions: tuple[Infusion, ...] = ()

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
            or opening.effective_at <= self.active.started_at
        ):
            return None
        return max(utc(absence_at), opening.effective_at, ventilation.effective_at)

    def presence_close_exit_at(self) -> datetime | None:
        """Structural fallback bound; controller supplies the sensor finding."""
        if (self.active is None
                or self.door != Door.CLOSED or self.anchor is None
                or self.anchor.door_close_evidence is None
                or self.closed_opening is None
                or self.closed_opening.effective_at <= self.active.started_at
                or self.exit_ventilation is not None):
            return None
        return self.anchor.booking_at

    @property
    def gang_count(self) -> int:
        return sum(g.confirmation == Confirmation.CONFIRMED for g in self.completed)

    def __post_init__(self) -> None:
        if not self.session_id or not isinstance(self.door, Door):
            raise ValueError("Session-ID und gültiger Türzustand erforderlich")
        object.__setattr__(self, "session_started_at", utc(self.session_started_at))


def update_infusion_end(
    state: Timeline, source_event_id: str, end: InfusionEnd | None,
) -> Timeline:
    """Einen gelieferten Endbeleg allein an seiner zugeordneten Quelle ersetzen.

    ``infusion_events`` führt weiterhin Gangzuordnung und Bestätigung. Nur eine
    dort belegte Quelle erhält Metadaten, auch in älteren Gängen ohne bisherigen
    ``Infusion``-Eintrag. ``None`` nimmt ausschließlich ihr vorhandenes Ende
    zurück. Gangzeiten, Quellenereignisse und sämtliche Übergänge bleiben erhalten.
    """
    if end is not None and not isinstance(end, InfusionEnd):
        raise ValueError("Ungültiger Endbeleg des Aufgusses")

    def update(gang: Gang) -> Gang:
        if gang.session_id != state.session_id:
            return gang
        source = next((
            event for event in gang.infusion_events
            if event.event_id == source_event_id and event.kind == Kind.INFUSION
            and event.session_id == state.session_id
        ), None)
        if source is None:
            return gang
        if end is not None and end.effective_at < source.effective_at:
            raise ValueError("Aufgussende liegt vor dem zugehörigen Aufgussbeginn")
        for index, infusion in enumerate(gang.infusions):
            if infusion.source_event_id != source_event_id:
                continue
            if infusion.end == end:
                return gang
            return replace(gang, infusions=(
                *gang.infusions[:index], replace(infusion, end=end),
                *gang.infusions[index + 1:],
            ))
        if end is None:
            return gang
        return replace(gang, infusions=(
            *gang.infusions, Infusion(source_event_id, end),
        ))

    active = update(state.active) if state.active is not None else None
    completed = tuple(update(gang) for gang in state.completed)
    retracted = tuple(update(gang) for gang in state.retracted)
    if (active is state.active and completed == state.completed
            and retracted == state.retracted):
        return state
    return replace(state, active=active, completed=completed, retracted=retracted)


def _projection_value(value, key, default=None):
    return (value.get(key, default) if isinstance(value, Mapping)
            else getattr(value, key, default))


def _projection_time(value):
    try:
        return utc(datetime.fromisoformat(value) if isinstance(value, str) else value)
    except (TypeError, ValueError):
        return None


def project_gang_intervals(timeline: Timeline | Mapping, *, door_cycles=None) -> list[dict]:
    """Gangzeiten vom zugeordneten Eintritt bis zum bestätigten Abschluss.

    Der zugeordnete Eintritt mit erfüllten Startbedingungen liefert den Beginn.
    Die Austrittsöffnung allein beendet den Gang nicht. Ihr bestätigtes
    Durchlüften liefert den Abschluss, bei direkter Präsenz zusammen mit
    dem zugehörigen Abwesenheitsnachweis. Im Sensorbetrieb kann außerdem
    der bestätigte Schluss ohne dauerhafte Anwesenheit den Gang beenden.
    Dieser Auffangabschluss, Betrieb-AUS und fehlende Altarchivquellen
    erzeugen keinen Lüftungsnachweis. Alle Türbezüge behalten
    die Mehrdeutigkeitsprüfung der gemeinsamen Türzyklusprojektion.
    """
    get = _projection_value
    cycles = project_door_cycles(timeline) if door_cycles is None else door_cycles
    links = {}
    for cycle in cycles:
        for link in cycle["gang_links"]:
            links.setdefault(link["gang_id"], {}).setdefault(link["role"], []).append(cycle)
    retracted = {get(gang, "gang_id") for gang in get(timeline, "retracted", ()) or ()}
    active = get(timeline, "active")
    result = []
    for gang in (*(get(timeline, "completed", ()) or ()), *([active] if active else [])):
        identity = get(gang, "gang_id")
        if not identity or identity in retracted:
            continue
        roles = links.get(identity, {})
        entries, exits = roles.get("entry", ()), roles.get("exit", ())
        entry = entries[0] if len(entries) == 1 else None
        exit_cycle = exits[0] if len(exits) == 1 else None
        start_at = _projection_time(get(gang, "started_at")) if entry is not None else None
        start = start_at.isoformat() if start_at is not None else None
        end_at = _projection_time(get(gang, "ended_at")) if exit_cycle is not None else None
        end = end_at.isoformat() if end_at is not None else None
        if start is not None and end is not None and _projection_time(end) < _projection_time(start):
            end, exit_cycle = None, None
        result.append({
            "gang_id": identity,
            "started_at": start,
            "ended_at": end,
            "start_source_event_id": entry["event_ids"][-1] if entry is not None else None,
            "end_source_event_id": get(gang, "end_event_id") if end is not None else None,
            "complete": start is not None and end is not None,
        })
    return result


def active_gang_duration_seconds(timeline, now, *, intervals=None) -> float | None:
    """Anzeigedauer des aktiven Gangs ab seinem belegten Beginn.

    Die bereits projizierten Intervalle können vom Verbraucher mitgegeben
    werden. Ohne zugeordneten Beginn bleibt die Dauer unbekannt; die
    Steuerungsdauer im Gangobjekt wird dadurch nicht geändert.
    """
    active = _projection_value(timeline, "active")
    if active is None:
        return None
    at = _projection_time(now)
    detected = _projection_time(_projection_value(active, "detected_at"))
    if at is None or detected is None or at < detected:
        return None
    projected = project_gang_intervals(timeline) if intervals is None else intervals
    identity = _projection_value(active, "gang_id")
    span = next((item for item in projected if item["gang_id"] == identity), None)
    if span is None:
        return None
    start, end = _projection_time(span["started_at"]), _projection_time(span["ended_at"])
    if start is None:
        return None
    end = min(at, end) if end is not None else at
    return (end - start).total_seconds() if end >= start else None


def project_door_cycles(timeline: Timeline | Mapping) -> list[dict]:
    """Historische Türquellen und vorhandene Gangbezüge rein lesend zuordnen.

    ``processed`` behält seine Buchungsreihenfolge. Nur ein echter folgender
    Türschluss schließt eine Öffnung. Rollen stammen aus den Quellverweisen
    bestehender Gänge; Betrieb-AUS belegt keinen Austritt. ``gang_links`` erhält
    auch Austritt und nachfolgenden Eintritt derselben Türfolge. ``role`` und
    ``gang_id`` sind nur bei genau einem Bezug gesetzt. Archivierte Mappings
    werden ebenso gelesen wie Timeline-Objekte, ohne deren Inhalt zu verändern.
    Die beiden Lüftungsstufen erhalten eigene Zeit- und Quellfelder; sie ändern
    weder ``event_ids`` der Türpaarung noch die vorhandenen Gangbezüge.
    """
    get, time = _projection_value, _projection_time

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
                "ventilation_at": None,
                "ventilation_event_id": None,
                "ventilation_complete_at": None,
                "ventilation_complete_event_id": None,
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
                    "ventilation_at": None,
                    "ventilation_event_id": None,
                    "ventilation_complete_at": None,
                    "ventilation_complete_event_id": None,
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
                    if episode["ventilation_event_id"] == event_id:
                        cycles[current]["ventilation_at"] = at.isoformat()
                        cycles[current]["ventilation_event_id"] = event_id
        elif current is not None and kind == Kind.VENTILATION_COMPLETE:
            cycle = cycles[current]
            ventilation_id = cycle["ventilation_event_id"]
            if (cycle["open"] and ventilation_id is not None
                    and cycle["ventilation_complete_at"] is None):
                ventilation, ventilation_index, ventilation_at = sources[ventilation_id]
                confirmed_at = time(get(event, "trace_at") or at)
                ventilation_confirmed_at = time(get(ventilation, "trace_at") or ventilation_at)
                booked_at = time(get(event, "booking_at") or get(event, "detected_at") or at)
                ventilation_booked_at = time(
                    get(ventilation, "booking_at") or get(ventilation, "detected_at")
                    or ventilation_at
                )
                detected_at = time(get(event, "detected_at") or booked_at)
                ventilation_detected_at = time(
                    get(ventilation, "detected_at") or ventilation_booked_at
                )
                if (ventilation_index < index and at >= ventilation_at
                        and confirmed_at is not None and ventilation_confirmed_at is not None
                        and confirmed_at >= ventilation_confirmed_at
                        and booked_at is not None and ventilation_booked_at is not None
                        and booked_at >= ventilation_booked_at
                        and detected_at is not None and ventilation_detected_at is not None
                        and detected_at >= ventilation_detected_at):
                    cycle["ventilation_complete_at"] = at.isoformat()
                    cycle["ventilation_complete_event_id"] = event_id
        elif current is not None and kind == Kind.PRESENCE_CLOSE_ENDED:
            episode = evidence[current]
            if (episode["ended_at"] is not None
                    and episode["ended_at"] <= at
                    and episode["ventilation_at"] is None):
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

    for cycle, episode in zip(cycles, evidence, strict=True):
        for prefix in ("ventilation", "ventilation_complete"):
            source_id = cycle[f"{prefix}_event_id"]
            if source_id is None:
                continue
            source_at = sources[source_id][2]
            if (cycle["id"] in ambiguous_ids or source_id in ambiguous_ids
                    # Stufe 2 gehört zur bei ihrer Verarbeitung offenen Episode;
                    # ein später rückzugeordneter Schluss hebt den Beleg nicht auf.
                    or (prefix == "ventilation" and episode["ended_at"] is not None
                        and source_at > episode["ended_at"])):
                cycle[f"{prefix}_at"] = None
                cycle[f"{prefix}_event_id"] = None
        if cycle["ventilation_event_id"] is None:
            cycle["ventilation_complete_at"] = None
            cycle["ventilation_complete_event_id"] = None

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
        expected_reason = (
            "presence_close" if end_kind == Kind.PRESENCE_CLOSE_ENDED else
            "presence_exit" if end_kind == Kind.PRESENCE_ENDED else "ventilation"
        )
        if (end_reason not in (None, expected_reason)
                or episode["started_at"] < started_at
                or (end_kind in (Kind.PRESENCE_ENDED, Kind.PRESENCE_CLOSE_ENDED)
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


def apply(
    state: Timeline, event: Event, *, gang_start_at: datetime | None = None,
    qualified_presence: bool | None = None,
) -> Timeline:
    """Ein Ereignis verarbeiten, ohne den bisherigen Zustand zu verändern.

    `active` umfasst vorläufige und bestätigte Gänge. Vorbereitung gehört zur
    letzten abgeschlossenen Türöffnungsepisode. Jeder neue Gang benötigt eine
    ungenutzte vollständige Öffnungs- und Schließfolge.
    Ein bestehender Gang behält seine ursprüngliche Zuordnung bei Türbetätigung.
    Die aktuelle Belegungsquelle bestimmt den Austrittsbeleg. Ohne explizite
    Quellenübergabe bleibt die Bestätigungsart des aktiven Gangs maßgeblich.
    Die vollständige Lüftung ergänzt allein ``processed``; ihr vorangehender
    Lüftungsbeleg derselben Öffnung führt weiterhin das Gangende.
    """
    proxy_presence = (
        state.active is not None
        and (not qualified_presence if qualified_presence is not None
             else state.active.recognition_kind != Kind.PRESENCE_CONFIRMED)
    )
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
                and proxy_presence else ()
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
        if (state.active is not None and proxy_presence
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
                start_basis=(
                    "door_close" if start == anchor.effective_at else
                    "previous_gang_end" if any(previous.ended_at == start
                                               for previous in state.completed) else
                    "minimum_temperature"
                ),
                preparation_event_id=(
                    state.preparation.event_id if state.preparation else None
                ),
            )
            result = replace(result, rejected_start_sources=(
                *state.rejected_start_sources, anchor.event_id,
            ))
        if event.kind == Kind.INFUSION:
            gang = replace(
                gang, infusion_events=gang.infusion_events + (event,),
                infusions=gang.infusions + (Infusion(event.event_id),),
            )
        result = replace(result, active=gang)
        if event.kind == Kind.PRESENCE_CONFIRMED:
            result = replace(result, resolved_presence_close_id=state.anchor.event_id)
    elif event.kind == Kind.PRESENCE_CLOSE_ENDED:
        close_at = state.presence_close_exit_at()
        if close_at is None or event.effective_at < close_at:
            raise ValueError("Sensor-Auffangende benötigt einen neuen bestätigten Türschluss")
        finished = replace(state.active, ended_at=event.booking_at,
                           end_event_id=event.event_id, end_reason="presence_close")
        result = replace(state, active=None, completed=state.completed + (finished,),
                         rejected_start_sources=(*state.rejected_start_sources,
                                                 state.closed_opening.event_id,
                                                 state.anchor.event_id),
                         resolved_presence_close_id=state.anchor.event_id)
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
                         resolved_presence_close_id=state.resolved_presence_close_id)
    elif event.kind == Kind.VENTILATION:
        if state.door != Door.OPEN:
            raise ValueError("Durchlüftungsbestätigung benötigt eine offene Episode")
        if state.opening is None:
            raise UnresolvedTransition("Durchlüften ohne zugeordnete Öffnung")
        # Mehrere Bestätigungen derselben Episode ändern deren ersten Beleg nicht.
        result = replace(state, open_ventilation=state.open_ventilation or event)
        if state.active is not None and proxy_presence:
            if state.active.confirmation == Confirmation.PROVISIONAL:
                result = replace(result, active=None,
                                 retracted=state.retracted + (state.active,))
            else:
                finished = replace(state.active, ended_at=event.booking_at,
                                   end_event_id=event.event_id, end_reason="ventilation")
                result = replace(result, active=None,
                                 completed=state.completed + (finished,))
    elif event.kind == Kind.VENTILATION_COMPLETE:
        if state.door != Door.OPEN:
            raise ValueError("Vollständiges Lüften benötigt eine offene Episode")
        if state.opening is None:
            raise UnresolvedTransition("Vollständiges Lüften ohne zugeordnete Öffnung")
        episode = []
        for previous in reversed(state.processed):
            if previous.kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE):
                if previous.kind != Kind.DOOR_OPEN or previous != state.opening:
                    raise ValueError("Vollständiges Lüften gehört nicht zur aktuellen Öffnung")
                break
            episode.append(previous)
        else:
            raise ValueError("Vollständiges Lüften benötigt die verarbeitete Türöffnung")
        ventilation = state.open_ventilation
        if (ventilation is None or ventilation.kind != Kind.VENTILATION
                or ventilation not in episode):
            raise ValueError("Vollständiges Lüften benötigt den vorherigen Lüftungsbeleg")
        if (ventilation.effective_at < state.opening.effective_at
                or event.effective_at < ventilation.effective_at
                or event.confirmation_at < ventilation.confirmation_at
                or event.detected_at < ventilation.detected_at):
            raise ValueError("Lüftungsstufen müssen in zeitlicher Reihenfolge liegen")
        # Stufe 1 führt das Gangende. Stufe 2 bleibt ausschließlich Quellbeleg
        # derselben Öffnung und wird unten nur an processed angehängt.
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
