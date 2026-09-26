"""Gemeinsame Verträge für Quellen, Regelung, Projektion und spätere Verbraucher.

Keine Quelle oder Archivkorrektur darf hier einen Aktorbefehl erzeugen.
"""
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class PresenceReport:
    report_id: str
    occupancy: str  # present | absent | unknown
    assertion: str  # provisional_proxy | direct_presence | proxy_retraction
    source: str  # proxy | HA entity_id
    source_ref: str
    effective_at: datetime
    received_at: datetime
    available: bool
    reason: str | None = None


@dataclass(frozen=True)
class ControlInputs:
    door_request: bool = False  # einmaliger Impuls, keine zusätzliche Haltezeit
    gang_veto: bool = False
    cooling: bool = False  # bestehende Ofenkühlung


@dataclass(frozen=True)
class BasePhaseMark:
    at: datetime
    phase: str
    operation_enabled: bool


@dataclass(frozen=True)
class ContactorMark:
    at: datetime
    state: bool | None


@dataclass(frozen=True)
class PhaseInterval:
    started_at: datetime
    ended_at: datetime
    phase: str
    source_id: str | None = None
    complete: bool = True


@dataclass(frozen=True)
class ReadinessPause:
    started_at: datetime
    ended_at: datetime

    @property
    def duration_seconds(self):
        return (self.ended_at - self.started_at).total_seconds()


@dataclass(frozen=True)
class PhaseProjection:
    intervals: tuple[PhaseInterval, ...]
    readiness_pauses: tuple[ReadinessPause, ...]
    corrections: tuple[str, ...] = ()
    complete: bool = True


@dataclass(frozen=True)
class ConsumerEvent:
    event_id: str
    kind: str  # occupancy, gang_started/confirmed/retracted/ended, infusion
    session_id: str | None
    effective_at: datetime
    received_at: datetime
    source_ref: str
    gang_id: str | None = None
    presence: PresenceReport | None = None
    delivery: str = "live"  # live | archive_correction; niemals Wiedergabe im Kern
