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
    """Keep factual beginning, receipt, first usability, and evaluation separate.

    All times come from the producer. First usability records when the required
    evidence actually admitted this assertion, including an unknown assertion.
    It is not inferred from the factual beginning or Event.confirmation_at.
    A historical beginning does not backdate a controller or actuator effect.
    The containing ModelHandoff's observation time replaces none of these times.
    """

    effective_at: datetime
    received_at: datetime
    first_usable_at: datetime
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
class DoorOperation:
    """Existing opening and its owner-associated closing and ventilation evidence.

    The opening's existing event_id identifies this operation. Timeline supplies
    the association; this form neither assigns an event nor creates a group ID.
    ventilation_known is supplied explicitly: true with no ventilation event is
    an evaluated, unconfirmed finding; false preserves unknown knowledge. A
    retained event is evidence, not an automatic choice of current knowledge.
    """

    opening: Event
    closing: Event | None
    ventilation: Event | None
    ventilation_known: bool

    def __post_init__(self) -> None:
        if self.opening.kind is not Kind.DOOR_OPEN:
            raise ValueError("door operation requires a DOOR_OPEN event")
        if self.closing is not None and self.closing.kind is not Kind.DOOR_CLOSE:
            raise ValueError("door operation closing requires a DOOR_CLOSE event")
        if self.ventilation is not None and self.ventilation.kind is not Kind.VENTILATION:
            raise ValueError("door operation ventilation requires a VENTILATION event")
        if any(
            event is not None and event.session_id != self.opening.session_id
            for event in (self.closing, self.ventilation)
        ):
            raise ValueError("door operation events require the same session")


@dataclass(frozen=True)
class DoorInput:
    """Door state, ordered findings, and the existing owner's operation grouping.

    state and operations are the complete observed domain state. events is this
    evaluation boundary's ordered input batch, not a history to replay. One batch
    preserves door and ventilation order, including ties in booking time.
    Initial/unknown state and an unmatched closing need no fabricated opening.
    The owner supplies operation membership and decides transitions and retention;
    ModelHandoff.before/after continue to observe complete supplied values.
    """

    state: Door
    events: tuple[Event, ...]
    operations: tuple[DoorOperation, ...]
    timing: InputTiming

    def __post_init__(self) -> None:
        if any(
            event.kind not in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE, Kind.VENTILATION)
            for event in self.events
        ):
            raise ValueError("door events require DOOR_OPEN, DOOR_CLOSE or VENTILATION")


@dataclass(frozen=True)
class OccupancyInput:
    """Occupancy admitted by its owner, independently of evidence provenance.

    person_evidence is an already admitted person finding, including the existing
    cooling-minimum evidence use. qualified_presence says that the occupancy
    assertion itself is qualified, including a qualified absent assertion. It
    does not decide gang confirmation, entry, exit, or the relevant door context.
    The gang/controller owner retains those decisions.

    The ordered events batch at this evaluation boundary preserves identities,
    times, and recognition kinds, and is not a replayable history;
    no PERSON_STRONG/WEAK event is required or reconstructed from these flags.
    report_id retains an existing state-report identity when one is present;
    None means no report, never a generated observation or presence-event ID.
    Unknown/unavailable is not absence. Evidence loss may remove qualifications
    or leave an unchanged state as decided upstream; it creates no retraction
    target or new control action here.
    """

    occupancy: Literal["present", "absent", "unknown"]
    available: bool
    person_evidence: bool
    qualified_presence: bool
    report_id: str | None
    events: tuple[Event, ...]
    timing: InputTiming


@dataclass(frozen=True)
class InfusionInput:
    """The ordered confirmed INFUSION input batch at this evaluation boundary.

    This is not a complete history to replay. Existing Event identities and all
    event times are preserved; repeated infusions remain separate occurrences.
    These are confirmed control findings;
    continuation, interruption, and completion belong to the real episode owner's
    separate observation, and are not reconstructed from events here.
    The owner decides acceptance; no evidence-strength or retention policy is added.
    """

    occurrences: tuple[Event, ...]
    timing: InputTiming

    def __post_init__(self) -> None:
        if any(event.kind is not Kind.INFUSION for event in self.occurrences):
            raise ValueError("infusion occurrences require INFUSION events")


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
