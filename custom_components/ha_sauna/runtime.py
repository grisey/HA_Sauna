"""Laufzeitobjekt einer Sauna-Instanz; keine HA-Serviceaufrufe an Geräte."""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from .archive import encoded, plain
from .core.detector import Detector

from .bindings import Bindings
from .const import CONF_BINDINGS, CONF_PARAMETERS
from .core.controller import Controller, Result
from .core.models import Deadline, Session
from .core.parameters import Parameters
from .core.timeline import Door, Event


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
        self.device = None
        self.detector = None
        self._detector_session = None
        self._archive_signature = None
        self._saved_decisions = 0
        self._saved_phase = None
        self._saved_faults = None

    def _sync_detector(self):
        session_id = self.session.session_id if self.session else None
        if session_id == self._detector_session:
            return
        self._detector_session = session_id
        def observed(trace):
            if self.archive:
                self.archive.append("detector_trace", self._clock(), trace, session_id)
        self.detector = Detector(self.configuration.parameters, self.session.started_at,
            observer=observed) if self.session else None
        if self.detector and self.device:
            for measurement in sorted(self.device.measurements.values(), key=lambda m: m.received_at):
                self.detector.accept(measurement)
                if self.archive:
                    self.archive.append("source_snapshot", self._clock(), measurement, session_id)

    async def _cycle(self, *, sample=False):
        now = self._clock()
        self._sync_detector()
        if sample and self.detector:
            detections = self.detector.advance(now, enabled=self.session.operation_enabled)
            if self.session.timeline.door == Door.UNKNOWN and self.detector.active_positions:
                # Dokumentierte Anfangsannahme des Kandidaten, keine erfundene
                # Türschließung und kein rückdatierter Startanker.
                self.controller._session = replace(self.session,
                    timeline=replace(self.session.timeline, door=Door.CLOSED))
            for i, detection in enumerate(detections):
                event = Event(f"detector:{self.session.session_id}:{detection.effective_at.isoformat()}:{i}:{detection.kind}",
                    self.session.session_id, detection.kind, detection.effective_at, now)
                self.controller.process(event)
                if self.archive:
                    self.archive.append("detection", now, {"event": event, "channels": detection.channels}, self.session.session_id)
        if self.device:
            self.device.refresh(now)
        else:
            self.controller.advance(now)
        if self.device:
            await self.device.apply(now)
        self.notify()

    async def device_input(self, event):
        received_at = self._clock()
        async with self._lock:
            if self.closed:
                return
            entity_id = event.data["entity_id"]
            for role, source in self.configuration.bindings.values.items():
                if source == entity_id:
                    self.device.ingest(role, event.data.get("new_state"), received_at)
            action = self.device.physical_action(event)
            if action is not None:
                self.controller.set_operation(action, self._clock())
            await self._cycle()

    async def reset_protection(self):
        async with self._lock:
            self._require_open()
            if self.session and self.session.operation_enabled:
                raise ValueError("Betrieb vor Quittierung ausschalten")
            if self.device and (self.device.feedback() is not False or self.device.command_error):
                raise ValueError("Quittierung benötigt bestätigten Ofen-Aus-Zustand")
            self.controller.protection.clear()
            await self._cycle()

    async def start_archive(self, path, entry_id):
        from .archive import Archive
        self.archive = Archive(path, entry_id)
        await self.archive.start()

    def persist(self):
        if self.archive is None:
            return
        now = self._clock()
        phase_key = (self.session.session_id if self.session else None, self.controller.phase)
        if phase_key != self._saved_phase:
            self.archive.append("phase", now, {"phase": self.controller.phase}, phase_key[0])
            self._saved_phase = phase_key
        faults = dict(self.device.faults) if self.device else {}
        fault_key = (phase_key[0], encoded(faults))
        if fault_key != self._saved_faults:
            self.archive.append("diagnostic", now, {"faults": faults}, phase_key[0])
            self._saved_faults = fault_key
        for session in self.controller.completed_sessions[self._archived_completed:]:
            self.archive.save_session(session, now, self.configuration.as_options())
        self._archived_completed = len(self.controller.completed_sessions)
        if self.session is not None:
            signature = plain(self.session)
            signature["heating"].pop("accounted_at")
            signature["heating"].pop("elapsed_seconds")
            signature = encoded(signature)
            if signature != self._archive_signature:
                self.archive.save_session(self.session, now, self.configuration.as_options())
                self._archive_signature = signature
        for decision in self.controller.decisions[self._saved_decisions:]:
            self.archive.append("decision", decision.at, decision,
                self.session.session_id if self.session else None)
        self._saved_decisions = len(self.controller.decisions)

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
            await self._cycle()
            return result

    async def tick(self, _at=None):
        async with self._lock:
            if self.closed:
                return
            await self._cycle(sample=True)

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
            await self._cycle()
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
            if self.device:
                try:
                    self.controller.set_operation(False, self._clock())
                    await self.device.close()
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
