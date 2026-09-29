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


@dataclass(frozen=True, init=False)
class ControlInputs:
    """Live demands that the thermostat may honour after safety checks.

    ``gang_heat_demand`` and ``temporary_door_heat`` are levels, not events.
    Their owner keeps them asserted until its own completion condition is met.
    The old names remain readable and constructible during the controller
    migration, but are compatibility aliases only.
    """

    gang_heat_demand: bool
    temporary_door_heat: bool
    cooling: bool

    def __init__(
        self,
        gang_heat_demand: bool | None = None,
        temporary_door_heat: bool | None = None,
        cooling: bool = False,
        *,
        gang_veto: bool | None = None,
        door_request: bool | None = None,
    ) -> None:
        if gang_heat_demand is None:
            gang_heat_demand = bool(gang_veto)
        if temporary_door_heat is None:
            temporary_door_heat = bool(door_request)
        object.__setattr__(self, "gang_heat_demand", bool(gang_heat_demand))
        object.__setattr__(self, "temporary_door_heat", bool(temporary_door_heat))
        object.__setattr__(self, "cooling", bool(cooling))

    @property
    def gang_veto(self) -> bool:
        """Compatibility alias; new control code must use gang_heat_demand."""
        return self.gang_heat_demand

    @property
    def door_request(self) -> bool:
        """Compatibility alias; new control code must use temporary_door_heat."""
        return self.temporary_door_heat


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
