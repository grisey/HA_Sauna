"""Read-only, complete observation outputs; not controller restore states.

Capture copies mutable containers and reuses existing immutable model values.
No publication, collection, model transition or additional rule occurs here.
Internal replay caches, delivery bookkeeping and historical output queues are not
restore data in this observation contract. Callers choose the capture boundary.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from .contracts import ConsumerEvent, ControlInputs, PresenceReport
from .configuration_model import ConfigurationModel
from .controller import Controller, PresenceEntry
from .model_handoff import FrozenMap, require_immutable
from .models import LightAfterRun, Session, ThermostatState
from .presence import PresenceObservation, PresenceProjection
from .temporary_door_heat import TemporaryDoorHeatState
from .thermostat import Decision
from .timeline import Gang


@dataclass(frozen=True)
class ConfigurationOutput:
    """Complete supplied options and the owner's existing capability model.

    Construct with freeze_data(configuration.as_options()) and the owner's
    already calculated configuration_model, including its effective source.
    Options remain complete without another field allowlist or interpretation.
    """

    options: FrozenMap
    model: ConfigurationModel

    def __post_init__(self) -> None:
        require_immutable(self)


@dataclass(frozen=True)
class ControllerOutput:
    session: Session | None
    completed_sessions: tuple[Session, ...]
    parameters: tuple[tuple[str, float | int | None], ...]
    program_mode: str
    control_mode: str
    temperature_steps: tuple[float, ...] | None
    presence_source: str
    presence_entity: str | None
    direct_presence: PresenceReport | None
    presence_entry: PresenceEntry | None
    last_at: datetime | None
    temperature: float | None
    temperature_valid_until: datetime | None
    recognition_temperature_raster: tuple[float | None, datetime | None] | None
    feedback: bool | None
    contactor: bool | None
    power_w: float | None
    power_valid_until: datetime | None
    fallback_heating: bool | None
    protection: frozenset[str]
    inhibits: frozenset[str]
    automatic_decision: Decision | None
    last_decision: Decision | None
    heater_override: bool | None
    override_binding: tuple[tuple[str | None, str], tuple[bool]] | None
    manual_thermostat: ThermostatState
    heater_override_ends_at: datetime | None
    door_request: TemporaryDoorHeatState
    door_request_pending: bool
    light_after_run: LightAfterRun | None
    phase: str
    phase_since: datetime | None
    automatic_operation_enabled: bool
    control_mode_change_allowed: bool
    target_temperature: float | None
    next_gang_temperature: float | None
    thermostat_target: float | None
    thermostat_restart_temperature: float | None
    regulation_causes: tuple[str, ...]
    regulation_inputs: ControlInputs

    def __post_init__(self) -> None:
        require_immutable(self)

    @classmethod
    def capture(cls, owner: Controller) -> "ControllerOutput":
        """Observe the owner without advancing time or evaluating control."""
        if not isinstance(owner, Controller):
            raise TypeError("Expected Controller")
        return cls(
            session=owner.session,
            completed_sessions=tuple(owner.completed_sessions),
            parameters=tuple(sorted(owner.parameters.as_dict().items())),
            program_mode=owner.program_mode,
            control_mode=owner.control_mode,
            temperature_steps=(tuple(owner.temperature_steps)
                               if owner.temperature_steps is not None else None),
            presence_source=owner.presence_source,
            presence_entity=owner.presence_entity,
            direct_presence=owner.direct_presence,
            presence_entry=owner._presence_entry,
            last_at=owner._last_at,
            temperature=owner.temperature,
            temperature_valid_until=owner._temperature_valid_until,
            recognition_temperature_raster=owner._recognition_temperature_raster,
            feedback=owner.feedback,
            contactor=owner.contactor,
            power_w=owner.power_w,
            power_valid_until=owner.power_valid_until,
            fallback_heating=owner.fallback_heating,
            protection=frozenset(owner.protection),
            inhibits=frozenset(owner.inhibits),
            automatic_decision=owner.automatic_decision,
            last_decision=owner.last_decision,
            heater_override=owner.heater_override,
            override_binding=owner._override_snapshot,
            manual_thermostat=owner._manual_thermostat,
            heater_override_ends_at=owner.heater_override_ends_at,
            door_request=owner.door_request,
            door_request_pending=owner._door_request_pending,
            light_after_run=owner.light_after_run,
            phase=owner.phase,
            phase_since=owner.phase_since,
            automatic_operation_enabled=owner.automatic_operation_enabled,
            control_mode_change_allowed=owner.control_mode_change_allowed,
            target_temperature=owner.target_temperature,
            next_gang_temperature=owner.next_gang_temperature,
            thermostat_target=owner.thermostat_target,
            thermostat_restart_temperature=owner.thermostat_restart_temperature,
            regulation_causes=tuple(owner.regulation_causes),
            regulation_inputs=owner.regulation_inputs,
        )


@dataclass(frozen=True)
class PresenceSourceSelectionSnapshot:
    """Complete source-selection observation; never a controller input.

    Values are supplied by the selection owner in the parallel presence work.
    This contract neither selects a source nor introduces a fallback policy.
    No first observation/change is represented by None in the respective field.
    """

    configured_source: Literal["proxy", "ha_presence"]
    effective_source: Literal["proxy", "ha_presence"]
    available: bool | None
    availability_since: datetime | None
    last_changed_at: datetime | None
    last_processed_at: datetime | None


@dataclass(frozen=True)
class PresenceOutput:
    active_source: str
    current: PresenceReport | None
    external: tuple[tuple[str, PresenceReport], ...]
    reports: tuple[tuple[str, PresenceReport], ...]
    observations: tuple[PresenceObservation, ...]

    def __post_init__(self) -> None:
        require_immutable(self)

    @classmethod
    def capture(cls, owner: PresenceProjection) -> "PresenceOutput":
        """Copy the projection's existing views without accepting a report."""
        if not isinstance(owner, PresenceProjection):
            raise TypeError("Expected PresenceProjection")
        return cls(
            active_source=owner.active_source,
            current=owner.current,
            external=tuple(sorted(owner.external.items())),
            reports=tuple(sorted(owner.reports.items())),
            observations=owner.observations,
        )


@dataclass(frozen=True)
class ConsumerEventOutput:
    """Original event with its complete source/owner observation enclosed.

    Gang details are read from the enclosed immutable session, never a live
    controller. Source references and source times remain in the original
    event and models; this form does not reinterpret correction references.
    """

    event: ConsumerEvent
    session: Session | None = None

    def __post_init__(self) -> None:
        require_immutable(self)
        if self.event.session_id is not None:
            if self.session is None or self.session.session_id != self.event.session_id:
                raise ValueError("Event requires its matching session observation")
        elif self.session is not None:
            raise ValueError("Unscoped event cannot claim a session owner")
        if self.event.gang_id is not None:
            gang = self.gang
            if gang is None or gang.session_id != self.event.session_id:
                raise ValueError("Event requires its actual gang in the session")
        elif self.event.presence is None:
            raise ValueError("Event requires a gang owner or complete presence report")
        if self.event.kind == "occupancy" and self.event.presence is None:
            raise ValueError("Occupancy requires its complete presence report")
        report = self.event.presence
        if report is not None and (
            self.event.event_id != report.report_id
            or self.event.source_ref != report.source_ref
            or self.event.effective_at != report.effective_at
            or self.event.received_at != report.received_at
        ):
            raise ValueError("Event and presence source observation differ")

    @property
    def gang(self) -> Gang | None:
        if self.session is None or self.event.gang_id is None:
            return None
        timeline = self.session.timeline
        gangs = (*timeline.completed, *timeline.retracted)
        if timeline.active is not None:
            gangs = (*gangs, timeline.active)
        matches = tuple(gang for gang in gangs if gang.gang_id == self.event.gang_id)
        if len(matches) != 1:
            raise ValueError("Event gang must occur exactly once in its owner")
        return matches[0]
