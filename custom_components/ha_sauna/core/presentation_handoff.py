"""Explicit immutable panel results without querying their runtime owners.

The panel result has named top-level fields. Intentionally extensible maps keep
their complete contents; converting a serialized Session back into a domain
Session is neither needed nor performed. Public projection stays owned by the
existing presentation module.
"""

from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import datetime

from .configuration_model import TemperatureControl
from .contracts import ControlInputs, PhaseProjection, PresenceReport
from .model_handoff import FrozenMap
from .models import LightAfterRun, Measurement, Session
from .observation_handoff import HeatingObservation
from .system_handoff import EnvironmentSnapshot, WireValue, frozen_map
from .thermostat import Decision


@dataclass(frozen=True)
class PanelInstance(WireValue):
    """A visible instance reference, not ownership of the enclosing snapshot."""

    visible_entry_id: str
    title: str

    @property
    def entry_id(self):
        return self.visible_entry_id

    @classmethod
    def from_result(cls, result):
        values = dict(result)
        values["visible_entry_id"] = values.pop("entry_id")
        return cls(**values)

    def _wire_value(self):
        return {"entry_id": self.visible_entry_id, "title": self.title}


@dataclass(frozen=True)
class PanelPermissions(WireValue):
    admin: bool
    control: bool
    temperature: bool
    program: bool
    light: bool
    heater: bool


@dataclass(frozen=True)
class PanelIssue(WireValue):
    key: str
    message: str
    action: str | None


@dataclass(frozen=True)
class PanelPresence(WireValue):
    current: PresenceReport | FrozenMap | None

    def __post_init__(self):
        if isinstance(self.current, Mapping):
            object.__setattr__(self, "current", frozen_map(self.current))


@dataclass(frozen=True)
class HeaterObservation(WireValue):
    available: bool
    on: bool | None


@dataclass(frozen=True)
class ManualHeaterControl(WireValue):
    blocked_on_reason: str | None
    observation: HeaterObservation
    manual: bool | None
    commanded: bool | None
    override_ends_at: str | datetime | None
    automatic: bool | None


@dataclass(frozen=True)
class ManualLightControl(WireValue):
    observation: FrozenMap
    manual: float | None
    manual_feedback_target: float | None
    override_ends_at: str | datetime | None
    automatic: float | None
    normal: float | None

    def __post_init__(self):
        object.__setattr__(self, "observation", frozen_map(self.observation))


@dataclass(frozen=True)
class PanelManualControls(WireValue):
    heater: ManualHeaterControl
    light: ManualLightControl

    @classmethod
    def from_result(cls, result: Mapping):
        values = dict(result)
        heater = dict(values["heater"])
        heater["observation"] = HeaterObservation(**heater["observation"])
        values["heater"] = ManualHeaterControl(**heater)
        values["light"] = ManualLightControl(**values["light"])
        return cls(**values)


@dataclass(frozen=True)
class PhaseTimer(WireValue):
    kind: str
    label: str
    seconds: float | None
    mode: str


@dataclass(frozen=True)
class StartAvailability(WireValue):
    until_ready_seconds: float | None
    ready_estimated: bool
    minimum_wait_seconds: float | None
    message: str
    blocker: FrozenMap | None
    gang_elapsed_seconds: float | None

    def __post_init__(self):
        if self.blocker is not None:
            object.__setattr__(self, "blocker", frozen_map(self.blocker))


@dataclass(frozen=True)
class CommonPanelFields(WireValue):
    """Named fields shared by the internal and projected public contracts.

    These declarations describe complete results; they do not select fields or
    redact values. The existing presentation.public_state owns that projection.
    """

    title: str
    instances: tuple[PanelInstance, ...]
    now: str | datetime
    phase: str
    environment: EnvironmentSnapshot
    phase_projection: PhaseProjection | FrozenMap
    session: Session | FrozenMap | None
    configuration: FrozenMap
    configuration_model: FrozenMap
    button_session_gestures: tuple[str, ...]
    measurement_ttl_seconds: float
    appearance: FrozenMap
    appearance_catalog: FrozenMap
    frontend_defaults: FrozenMap
    last_session: Session | FrozenMap | None
    archive_revision: int
    parameters: tuple[FrozenMap, ...]
    configuration_locked: bool
    control_mode_change_allowed: bool
    operation_enabled: bool
    heating_feedback: bool | None
    energy_kwh: float
    energy_source: str
    thermostat_target: float | None
    thermostat_restart_temperature: float | None
    target_temperature: float | None
    next_gang_temperature: float | None
    temperature_control: TemperatureControl
    phase_timer: PhaseTimer | None
    start_availability: StartAvailability
    light_after_run: LightAfterRun | FrozenMap | None
    start_errors: tuple[str, ...]
    issues: tuple[PanelIssue, ...]
    decision_text: str
    measurement_status: FrozenMap
    regulation_temperature_position: str | None
    measurement_positions: tuple[str, ...]
    gang_count: int
    gang_confirmation: str | None
    gang_duration_seconds: float | None
    measurements: tuple[Measurement | FrozenMap, ...]
    manual_controls: PanelManualControls
    permissions: PanelPermissions

    def __post_init__(self):
        # Only foreign mutable containers need copying. Existing frozen domain
        # values and the typed child forms above remain the supplied values.
        for definition in fields(self):
            value = getattr(self, definition.name)
            if isinstance(value, Mapping):
                object.__setattr__(self, definition.name, frozen_map(value))
            elif isinstance(value, (list, tuple)):
                object.__setattr__(
                    self,
                    definition.name,
                    tuple(
                        frozen_map(item) if isinstance(item, Mapping) else item
                        for item in value
                    ),
                )

    @classmethod
    def _result_values(cls, result: Mapping):
        values = dict(result)
        values["instances"] = tuple(
            PanelInstance.from_result(item) for item in values["instances"]
        )
        values["environment"] = EnvironmentSnapshot.from_result(values["environment"])
        values["temperature_control"] = TemperatureControl(
            **values["temperature_control"]
        )
        values["phase_timer"] = (
            None
            if values["phase_timer"] is None
            else PhaseTimer(**values["phase_timer"])
        )
        values["start_availability"] = StartAvailability(**values["start_availability"])
        values["issues"] = tuple(PanelIssue(**item) for item in values["issues"])
        values["permissions"] = PanelPermissions(**values["permissions"])
        values["manual_controls"] = PanelManualControls.from_result(
            values["manual_controls"]
        )
        return values

    @classmethod
    def from_result(cls, result: Mapping):
        """Copy an existing complete result; missing/extra fields fail closed."""
        return cls(**cls._result_values(result))


@dataclass(frozen=True)
class PanelSnapshot(CommonPanelFields):
    """The complete internal StateView.get result before public projection.

    Existing frozen domain values may be supplied directly before serialization.
    Serialized domain values retain their complete JSON maps; no domain state
    is rebuilt or evaluated here.
    """

    presence: PanelPresence
    rule_inputs: ControlInputs | FrozenMap
    heating_observation: HeatingObservation | FrozenMap | None
    decision: Decision | FrozenMap | None
    faults: FrozenMap
    protection: tuple[str, ...]
    inhibits: tuple[str, ...]
    archive_error: str | None
    detection_channels: tuple[str, ...]
    detector_trace: FrozenMap | None

    @classmethod
    def from_result(cls, result: Mapping):
        values = cls._result_values(result)
        values["presence"] = PanelPresence(**values["presence"])
        return cls(**values)


@dataclass(frozen=True)
class PublicPanelSnapshot(CommonPanelFields):
    """The complete named public result, with diagnostic fields absent.

    ``from_result`` consumes an already projected complete result and rejects
    additional or missing top-level keys through its concrete constructor.
    ``from_snapshot`` delegates all projection to presentation.public_state.
    """

    @classmethod
    def from_snapshot(cls, snapshot: PanelSnapshot):
        from ..presentation import public_state

        return cls.from_result(public_state(snapshot.to_wire()))
