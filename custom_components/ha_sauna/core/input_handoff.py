"""Prepared, source-free values accepted by the existing control owners.

Construction is explicit: adapters select and admit evidence before constructing
these values. None of these forms reads a sensor, recognizes an event, changes
an owner, or chooses a retention window. Evidence belongs to the observation
handoffs, independently of these normalized control inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from .timeline import Door, Event, Kind


@dataclass(frozen=True)
class InputTiming:
    """Keep the factual beginning, receipt, and causal evaluation separate.

    All three times come from the producer. A historical beginning does not
    backdate when a controller could act, or when actuator feedback took effect.
    The containing ModelHandoff's observation time does not replace these times.
    """

    effective_at: datetime
    received_at: datetime
    evaluated_at: datetime


@dataclass(frozen=True)
class TemperatureInput:
    """The already selected regulation temperature; None remains unavailable."""

    value: float | None
    valid_until: datetime | None
    timing: InputTiming


@dataclass(frozen=True)
class HeatingFeedbackInput:
    """Observed heating state, with unknown distinct from off."""

    heating: bool | None
    timing: InputTiming


@dataclass(frozen=True)
class ContactorFeedbackInput:
    """Observed contactor state, with unknown distinct from open."""

    contactor: bool | None
    timing: InputTiming


@dataclass(frozen=True)
class PowerFeedbackInput:
    """Observed power; unavailable power is not zero power."""

    power_w: float | None
    valid_until: datetime | None
    timing: InputTiming


@dataclass(frozen=True)
class DoorInput:
    """Current door state and the complete currently available transition batch.

    The producer supplies its available ordered findings, including successive
    openings and closings. A return to the same state does not discard their
    identities or factual/detection/booking times. The owner decides transitions
    and the lifetime of retained findings.
    """

    state: Door
    transitions: tuple[Event, ...]
    timing: InputTiming

    def __post_init__(self) -> None:
        if any(
            event.kind not in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE)
            for event in self.transitions
        ):
            raise ValueError("door transitions require DOOR_OPEN or DOOR_CLOSE events")


@dataclass(frozen=True)
class OccupancyInput:
    """Occupancy admitted by its owner, independently of evidence provenance.

    Unknown and unavailable remain explicit. This form does not decide whether
    an external assertion is eligible to become a control input.
    """

    occupancy: Literal["present", "absent", "unknown"]
    available: bool
    timing: InputTiming


@dataclass(frozen=True)
class InfusionInput:
    """The complete currently available ordered INFUSION findings.

    Existing Event identities and all event times are preserved. Repeated
    infusions remain separate occurrences. The owner decides acceptance and
    completion; this form imposes no evidence-strength or retention policy.
    """

    occurrences: tuple[Event, ...]
    timing: InputTiming

    def __post_init__(self) -> None:
        if any(event.kind is not Kind.INFUSION for event in self.occurrences):
            raise ValueError("infusion occurrences require INFUSION events")


@dataclass(frozen=True)
class VentilationInput:
    """The complete currently available ordered VENTILATION findings.

    Existing Event identities and times remain unchanged. The owner decides the
    resulting ventilation state and its end; no duration policy is added here.
    """

    occurrences: tuple[Event, ...]
    timing: InputTiming

    def __post_init__(self) -> None:
        if any(event.kind is not Kind.VENTILATION for event in self.occurrences):
            raise ValueError("ventilation occurrences require VENTILATION events")


@dataclass(frozen=True)
class OperationInput:
    """The existing operation transition and its optional session scope."""

    enabled: bool
    session_id: str | None
    timing: InputTiming


@dataclass(frozen=True)
class ControlModeInput:
    """The existing automatic/manual mode, separately from operation enable."""

    mode: Literal["automatic", "manual"]
    timing: InputTiming


@dataclass(frozen=True)
class HeaterOverrideInput:
    """The existing on/off/no-override choice, separately from control mode."""

    heat: bool | None
    timing: InputTiming
