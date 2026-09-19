"""Sessionrahmen um den geprüften Gangkern, noch ohne Betriebs-/Heizregelung."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from .models import Deadline, Session
from .parameters import Parameters
from .timeline import Event, apply, utc


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

    @property
    def session(self) -> Session | None:
        return self._session

    def begin_session(self, session_id: str, at: datetime) -> Session:
        """Expliziter Test-/Kernaufruf, keine automatische Ein-/Ausschaltpolitik."""
        if self._session is not None:
            raise ValueError("Bestehende Session darf nicht beiläufig ersetzt werden")
        self._session = Session.create(session_id, at)
        return self._session

    def process(self, event: Event) -> Result:
        if self._session is None:
            raise ValueError("Ereignis ohne Session")
        previous = self._session
        timeline = apply(previous.timeline, event)
        if timeline is previous.timeline:
            return Result(previous, False, "duplicate", event.event_id)
        self._session = replace(previous, timeline=timeline)
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
        return True
