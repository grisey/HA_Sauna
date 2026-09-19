"""Laufzeitobjekt einer Sauna-Instanz; keine HA-Serviceaufrufe an Geräte."""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
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
        return cls(Bindings(options[CONF_BINDINGS]), Parameters(options[CONF_PARAMETERS]))

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
        for callback in tuple(self._subscribers):
            callback()

    def check_configuration_change(self):
        self._require_open()
        if self.session is not None:
            raise ValueError("Änderung laufender Fristen ist noch nicht festgelegt")

    async def begin_session(self, session_id: str) -> Session:
        async with self._lock:
            self._require_open()
            return self.controller.begin_session(session_id, self._clock())

    async def receive(self, event: Event) -> Result:
        async with self._lock:
            self._require_open()
            if event.detected_at > self._clock():
                raise ValueError("Erkennungszeit liegt nach der Laufzeituhr")
            return self.controller.process(event)

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
            if failures:
                raise ExceptionGroup("Abmeldung der Sauna-Laufzeit fehlgeschlagen", failures)
