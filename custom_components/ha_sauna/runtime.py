"""Laufzeitobjekt einer Sauna-Instanz; keine HA-Serviceaufrufe an Geräte."""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from .bindings import Bindings
from .const import CONF_BINDINGS, CONF_PARAMETERS
from .core.controller import Controller, Result
from .core.models import Deadline, Session
from .core.parameters import Parameters
from .core.timeline import Event


@dataclass(frozen=True)
class Configuration:
    bindings: Bindings
    parameters: Parameters

    @classmethod
    def from_options(cls, options: Mapping) -> Configuration:
        if not isinstance(options, Mapping) or set(options) != {CONF_BINDINGS, CONF_PARAMETERS}:
            raise ValueError("Vollständige Entitäts- und Parameterkonfiguration erforderlich")
        values = dict(options[CONF_PARAMETERS])
        # Einmalige Übernahme der alten beidseitigen Bandbreite. Danach werden
        # ausschließlich die neuen, gemeinsam gespeicherten Parameter konsumiert.
        if "cold_tolerance_c" in values or "hot_tolerance_c" in values:
            cold = values.pop("cold_tolerance_c", 0)
            hot = values.pop("hot_tolerance_c", 0)
            values.setdefault("readiness_hysteresis_c", cold + hot)
        return cls(Bindings(options[CONF_BINDINGS]), Parameters(values))

    def as_options(self) -> dict:
        return {
            CONF_BINDINGS: self.bindings.as_dict(),
            CONF_PARAMETERS: self.parameters.as_dict(),
        }


class SaunaRuntime:
    """Serialisiert Kernzugriffe; Timer/Listener können kontrolliert abgemeldet werden.

    In Paket 1 werden keine Mess-/Tasterlistener, Servicehandler oder Heizaktionen
    registriert. Die öffentlichen Methoden dienen den Kern- und Adaptertests.
    """

    def __init__(self, configuration: Configuration, clock: Callable[[], datetime] | None = None) -> None:
        self.configuration = configuration
        self.controller = Controller(configuration.parameters)
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)
        self._lock = asyncio.Lock()
        self._cleanup: list[Callable[[], None]] = []
        self._subscribers: set[Callable[[], None]] = set()
        self.closed = False
        self.archive = None
        self._archived_completed = 0

    async def start_archive(self, path, entry_id):
        from .archive import Archive
        self.archive = Archive(path, entry_id)
        await self.archive.start()

    def persist(self):
        if self.archive is None:
            return
        now = self._clock()
        for session in self.controller.completed_sessions[self._archived_completed:]:
            self.archive.save_session(session, now, self.configuration.as_options())
        self._archived_completed = len(self.controller.completed_sessions)
        if self.session is not None:
            self.archive.save_session(self.session, now, self.configuration.as_options())

    @property
    def session(self) -> Session | None:
        return self.controller.session

    def _require_open(self) -> None:
        if self.closed:
            raise RuntimeError("Sauna-Laufzeit wurde entladen")

    def subscribe(self, callback):
        self._subscribers.add(callback)
        return lambda: self._subscribers.discard(callback)

    def notify(self):
        self.persist()
        for callback in tuple(self._subscribers):
            callback()

    def check_configuration_change(self):
        self._require_open()
        if self.session is not None:
            raise ValueError("Grundkonfiguration ist während einer Session gesperrt")

    async def set_operation(self, enabled: bool):
        async with self._lock:
            self._require_open()
            result = self.controller.set_operation(enabled, self._clock())
            self.notify()
            return result

    async def tick(self, _at=None):
        async with self._lock:
            if self.closed:
                return
            self.controller.advance(self._clock())
            self.notify()

    async def begin_session(self, session_id: str) -> Session:
        async with self._lock:
            self._require_open()
            result = self.controller.begin_session(session_id, self._clock())
            self.notify()
            return result

    async def receive(self, event: Event) -> Result:
        async with self._lock:
            self._require_open()
            if event.detected_at > self._clock():
                raise ValueError("Erkennungszeit liegt nach der Laufzeituhr")
            result = self.controller.process(event)
            self.notify()
            return result

    async def deadline_due(self, deadline: Deadline) -> bool:
        async with self._lock:
            self._require_open()
            return self.controller.consume_deadline(deadline, self._clock())

    def on_close(self, unsubscribe: Callable[[], None]) -> None:
        self._require_open()
        self._cleanup.append(unsubscribe)

    async def close(self) -> None:
        async with self._lock:
            if self.closed:
                return
            self.closed = True
            callbacks, self._cleanup = self._cleanup, []
            failures = []
            for unsubscribe in reversed(callbacks):
                try:
                    unsubscribe()
                except Exception as error:
                    failures.append(error)
            if self.archive is not None:
                if self.session is not None:
                    now = self._clock()
                    self.archive.append("interruption", now, {"reason": "integration_unloaded"}, self.session.session_id)
                    self.archive.save_session(replace(self.session, ended_at=now), now, self.configuration.as_options())
                try:
                    await self.archive.close()
                except Exception as error:
                    failures.append(error)
            if failures:
                raise ExceptionGroup("Abmeldung der Sauna-Laufzeit fehlgeschlagen", failures)
