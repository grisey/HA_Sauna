"""Sessionrahmen um den geprüften Gangkern, noch ohne Betriebs-/Heizregelung."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from uuid import uuid4

from .models import Deadline, Session
from .parameters import Parameters
from .timeline import Event, Kind, apply, utc


@dataclass(frozen=True)
class Result:
    session: Session
    changed: bool
    reason: str
    event_id: str | None = None


class Controller:
    """Ein führender Zustand. Die äußere Laufzeit ordnet konkurrierende Eingänge."""

    def __init__(self, parameters: Parameters) -> None:
        self.parameters = parameters
        self._session: Session | None = None
        self.completed_sessions: tuple[Session, ...] = ()
        self._last_at: datetime | None = None

    @property
    def session(self) -> Session | None:
        return self._session

    def begin_session(self, session_id: str, at: datetime) -> Session:
        """Expliziter Test-/Kernaufruf, keine automatische Ein-/Ausschaltpolitik."""
        if self._session is not None:
            raise ValueError("Bestehende Session darf nicht beiläufig ersetzt werden")
        self._session = replace(Session.create(session_id, at), operation_enabled=True)
        self._last_at = utc(at)
        return self._session

    def set_operation(self, enabled: bool, at: datetime, *, session_id: str | None = None) -> Session | None:
        """Oberfläche und physischer Eingang verwenden genau diese Entscheidung."""
        at = utc(at)
        self.advance(at)
        if enabled:
            if self._session is None:
                return self.begin_session(session_id or uuid4().hex, at)
            if not self._session.operation_enabled:
                self._session = replace(self._session, operation_enabled=True,
                                        operation_off_at=None,
                                        deadlines=tuple(d for d in self._session.deadlines if d.purpose != "session_gap"))
        elif self._session is not None and self._session.operation_enabled:
            event = Event(uuid4().hex, self._session.session_id, Kind.OPERATION_OFF, at, at)
            self.process(event)
            self._session = replace(self._session, operation_enabled=False, operation_off_at=at)
            self._schedule("session_gap", at + timedelta(seconds=self.parameters.seconds("session_gap_minutes")))
        return self._session

    def _schedule(self, purpose: str, due_at: datetime, token: str | None = None):
        self.register_deadline(Deadline(self._session.session_id, purpose, token or uuid4().hex, due_at))

    def advance(self, at: datetime):
        """Zeit kontrolliert vorwärtsführen; fällige Folgen genau einmal ausführen."""
        at = utc(at)
        if self._last_at is not None and at < self._last_at:
            raise ValueError("Laufzeituhr darf nicht rückwärts laufen")
        self._last_at = at
        while self._session is not None:
            due = sorted((d for d in self._session.deadlines if d.due_at <= at), key=lambda d: (d.due_at, d.purpose))
            if not due:
                break
            self.consume_deadline(due[0], at)
        return self._session

    def process(self, event: Event) -> Result:
        if self._session is None:
            raise ValueError("Ereignis ohne Session")
        previous = self._session
        # Duplikate zuerst prüfen, auch wenn inzwischen die Laufzeituhr weiter ist.
        existing = next((e for e in previous.timeline.processed if e.event_id == event.event_id), None)
        if existing is not None:
            if existing != event:
                raise ValueError("Ereignis-ID mit abweichendem Inhalt wiederverwendet")
            return Result(previous, False, "duplicate", event.event_id)
        if event.session_id != previous.session_id:
            raise ValueError("Ereignis gehört zu einer anderen Session")
        if self._last_at is not None and event.detected_at < self._last_at:
            raise ValueError("Verspätetes Ereignis darf keine aktuelle Betriebsentscheidung ändern")
        # Vor dem Ereignis bereits abgelaufene Bestätigungen erst aufheben.
        # Ein Ereignis genau an der Frist wird noch dieser Frist zugeordnet.
        for deadline in tuple(previous.deadlines):
            if deadline.purpose == "confirmation" and deadline.due_at < event.detected_at:
                self.consume_deadline(deadline, event.detected_at)
                previous = self._session
        if not previous.operation_enabled and event.kind in (Kind.PERSON_STRONG, Kind.PERSON_WEAK, Kind.INFUSION):
            return Result(previous, False, "operation_off", event.event_id)
        timeline = apply(previous.timeline, event)
        if timeline is previous.timeline:
            return Result(previous, False, "duplicate", event.event_id)
        self._session = replace(previous, timeline=timeline)
        self._last_at = event.detected_at
        active = timeline.active
        if active is None or active.infusion_events:
            self._session = replace(self._session, deadlines=tuple(d for d in self._session.deadlines if d.purpose != "confirmation"))
        elif previous.timeline.active is None:
            due_at = active.started_at + timedelta(seconds=self.parameters.seconds("confirmation_minutes"))
            self._schedule("confirmation", due_at, active.gang_id)
            if due_at <= event.detected_at:
                self.advance(event.detected_at)
        return Result(self._session, True, "gang_model_updated", event.event_id)

    def register_deadline(self, deadline: Deadline) -> None:
        """Die Fachmodule liefern später den konfigurationsbasierten Ablaufzeitpunkt."""
        session = self._session
        if session is None or deadline.session_id != session.session_id:
            raise ValueError("Frist ohne passende Session")
        previous = next((d for d in session.deadlines if d.purpose == deadline.purpose), None)
        if previous is not None and previous.token == deadline.token and previous != deadline:
            raise ValueError("Geänderte Frist benötigt ein neues Token")
        kept = tuple(d for d in session.deadlines if d.purpose != deadline.purpose)
        self._session = replace(session, deadlines=kept + (deadline,))

    def consume_deadline(self, deadline: Deadline, now: datetime) -> bool:
        """Alte Aufrufe wirkungslos verwerfen; noch keine fachliche Fristfolge."""
        now = utc(now)
        session = self._session
        if session is None or deadline.session_id != session.session_id:
            return False
        if deadline not in session.deadlines:
            return False
        if now < deadline.due_at:
            raise ValueError("Frist ist noch nicht abgelaufen")
        self._session = replace(
            session, deadlines=tuple(d for d in session.deadlines if d != deadline),
        )
        if deadline.purpose == "confirmation":
            active = session.timeline.active
            if active is not None and active.gang_id == deadline.token and not active.infusion_events:
                # Die Entscheidung wirkt erst jetzt, nie rückwirkend als Heizbefehl.
                event = Event(f"deadline:{deadline.token}", session.session_id,
                              Kind.CONFIRMATION_EXPIRED, deadline.due_at, now)
                self._session = replace(self._session, timeline=apply(session.timeline, event))
        elif deadline.purpose == "session_gap" and not session.operation_enabled:
            finished = replace(self._session, ended_at=deadline.due_at, deadlines=())
            self.completed_sessions += (finished,)
            self._session = None
        return True
