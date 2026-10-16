"""Explicit immutable observation outputs; no collection or live publication.

Callers compose the already obtained results. These forms never evaluate a
model, read HA state, send a service or infer missing observations. Diagnostic
mappings retain their complete supplied content without an event catalogue.
"""

from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import datetime
from math import isfinite

from .detector import Detection
from .light_output import LightOutput, LightPlan, LightQuantizer
from .model_handoff import FrozenMap, freeze_data, require_immutable
from .models import Measurement
from .thermostat import Decision
from .timeline import Kind


def _instance(value, expected):
    if not isinstance(value, expected):
        raise TypeError(f"Expected {expected.__name__}")


def _text(value, *, optional=False):
    if optional and value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Nonempty string required")


def _mapping(value):
    _instance(value, Mapping)
    result = freeze_data(value)
    _instance(result, FrozenMap)
    return result


def _number(value, *, optional=False):
    if value is None and optional:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("Number required")
    if not isfinite(value):
        raise ValueError("Finite number required")


def _time(value, *, optional=False):
    if isinstance(value, datetime) or (value is None and optional):
        return
    _number(value)


def _tuple_of(value, expected):
    if not isinstance(value, (tuple, list)):
        raise TypeError("Tuple or list required")
    result = tuple(value)
    for item in result:
        _instance(item, expected)
        freeze_data(item)
    return result


@dataclass(frozen=True)
class LightQuantizerSnapshot:
    """Complete quantizer memory, including an explicit reset without a plan."""

    previous_value: int | None

    @classmethod
    def capture(cls, owner: LightQuantizer):
        return cls(owner._previous)


@dataclass(frozen=True)
class HeatingObservation:
    """Complete existing observe_heating() result, separate from service output.

    No source is selected and no feedback is read here. The incomplete device
    initialization placeholder is not an evaluated observation.
    """

    source: str
    heating: bool | None
    power_w: float | None
    contactor: bool | None
    estimated: bool

    def __post_init__(self):
        _text(self.source)
        _number(self.power_w, optional=True)
        require_immutable(self)

    @classmethod
    def from_result(cls, result: Mapping):
        _instance(result, Mapping)
        if set(result) != {definition.name for definition in fields(cls)}:
            raise ValueError("A complete heating-observation result is required")
        return cls(**result)


@dataclass(frozen=True)
class DetectionEvaluation:
    """Complete supplied detector diagnostic and its actual Detection results.

    The supplied diagnostic must identify the same raster and complete signal
    tuple. This constructor neither runs the detector nor reconstructs signals.
    """

    diagnostic: FrozenMap
    detections: tuple[Detection, ...]

    def __post_init__(self):
        object.__setattr__(self, "diagnostic", _mapping(self.diagnostic))
        object.__setattr__(self, "detections", _tuple_of(self.detections, Detection))
        required = {
            "at": datetime,
            "original_prefix_overflow": bool,
            "channels": tuple,
            "metrics": FrozenMap,
            "conditions": FrozenMap,
            "checks": FrozenMap,
            "decisions": FrozenMap,
            "holds": FrozenMap,
            "signals": tuple,
            "door_open": bool,
            "ventilation_context": bool,
            "curve_candidates": FrozenMap,
            "curve_proposals": FrozenMap,
            "infusion_episode": FrozenMap,
        }
        for name, expected in required.items():
            if name not in self.diagnostic:
                raise ValueError(f"Detector diagnostic is missing {name}")
            if name == "infusion_episode" and self.diagnostic[name] is None:
                continue
            _instance(self.diagnostic[name], expected)
        for channel in self.diagnostic["channels"]:
            _instance(channel, str)
        for signal in self.diagnostic["signals"]:
            _instance(signal, Kind)
        if self.diagnostic["signals"] != tuple(d.kind for d in self.detections):
            raise ValueError("Diagnostic signals and detection results differ")
        if any(d.trace_at != self.diagnostic["at"] for d in self.detections):
            raise ValueError(
                "Diagnostic and detection results belong to different rasters"
            )
        require_immutable(self)


@dataclass(frozen=True)
class MethodEvaluation:
    """Complete actual diagnostic and detections of a supplied evaluation.

    A method supplies its own full mapping diagnostic shape. No legacy
    checks/holds, episode, signal catalogue, or
    synthetic confirmation time is required. evaluated_at is the actual
    evaluation time, not an inferred beginning or first control usability.
    """

    diagnostic: FrozenMap
    evaluated_at: datetime
    detections: tuple[Detection, ...]

    def __post_init__(self):
        object.__setattr__(self, "diagnostic", _mapping(self.diagnostic))
        object.__setattr__(self, "detections", _tuple_of(self.detections, Detection))


@dataclass(frozen=True)
class InfusionEpisodeDiagnostics:
    """Complete published episode diagnostic, separate from confirmed events.

    These are the existing episode projection's fields, not a detector restore
    state. The episode owner supplies phase, interruption, and any end, including
    an ended-to-active correction after a late earlier rise. No event or stable
    episode ID is inferred. Confirmed occurrence identity stays with InfusionInput
    and its existing events; binding this observation to one is a future producer
    responsibility. None for an end preserves missing knowledge, not an end.
    """

    phase: str
    onset_at: datetime
    confirmed_at: datetime | None
    level_end_at: datetime | None
    end_basis: str | None
    interrupted: bool
    water_change: FrozenMap
    decays: FrozenMap
    levels: FrozenMap

    def __post_init__(self):
        for name in ("water_change", "decays", "levels"):
            object.__setattr__(self, name, _mapping(getattr(self, name)))

    @classmethod
    def from_result(cls, result: Mapping):
        """Copy an existing complete episode projection, without running its owner."""
        _instance(result, Mapping)
        if set(result) != {definition.name for definition in fields(cls)}:
            raise ValueError("A complete published infusion-episode result is required")
        return cls(**result)


@dataclass(frozen=True)
class LightMotionSnapshot:
    kind: str
    started_at: datetime | float
    start_percent: float
    low_percent: float
    fade_seconds: float
    fixed_percent: float | None

    def __post_init__(self):
        _text(self.kind)
        _time(self.started_at)
        for value in (self.start_percent, self.low_percent, self.fade_seconds):
            _number(value)
        _number(self.fixed_percent, optional=True)
        for name in self.__dataclass_fields__:
            object.__setattr__(self, name, freeze_data(getattr(self, name)))
        require_immutable(self)


@dataclass(frozen=True)
class LightReturnSnapshot:
    started_at: datetime | float
    start_percent: float
    duration_seconds: float

    def __post_init__(self):
        _time(self.started_at)
        _number(self.start_percent)
        _number(self.duration_seconds)
        for name in self.__dataclass_fields__:
            object.__setattr__(self, name, freeze_data(getattr(self, name)))
        require_immutable(self)


@dataclass(frozen=True)
class LightOutputSnapshot:
    """Planner state after an existing call, paired with its supplied plan.

    Parameters remain configuration-owned. Curve, override and phase fields
    mirror LightOutput; capturing them performs no update or expiry operation.
    """

    plan: LightPlan
    phase_key: object
    phase_ends_at: datetime | float | None
    motion: LightMotionSnapshot | None
    manual_brightness: float | None
    manual_phase_key: object
    manual_ends_at: datetime | float | None
    last_automatic: float
    resume_pending: bool
    return_transition: LightReturnSnapshot | None
    phase_paused: bool
    paused_automatic: float | None

    def __post_init__(self):
        _instance(self.plan, LightPlan)
        _number(self.plan.brightness_percent)
        _instance(self.plan.automatic, bool)
        _instance(self.plan.manual_override, bool)
        _instance(self.resume_pending, bool)
        _instance(self.phase_paused, bool)
        _number(self.last_automatic)
        _number(self.manual_brightness, optional=True)
        _number(self.paused_automatic, optional=True)
        _time(self.phase_ends_at, optional=True)
        _time(self.manual_ends_at, optional=True)
        if self.motion is not None:
            _instance(self.motion, LightMotionSnapshot)
        if self.return_transition is not None:
            _instance(self.return_transition, LightReturnSnapshot)
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            object.__setattr__(self, name, freeze_data(value))
        require_immutable(self)

    @classmethod
    def from_output(cls, output: LightOutput, plan: LightPlan):
        """Read existing state only; the supplied plan is never recalculated."""
        _instance(output, LightOutput)
        motion = output._motion
        transition = output._return_transition
        return cls(
            plan=plan,
            phase_key=output._phase_key,
            phase_ends_at=output._phase_ends_at,
            motion=None
            if motion is None
            else LightMotionSnapshot(
                motion.kind,
                motion.started_at,
                motion.start_percent,
                motion.low_percent,
                motion.fade_seconds,
                motion.fixed_percent,
            ),
            manual_brightness=output._manual,
            manual_phase_key=output._manual_phase_key,
            manual_ends_at=output._manual_ends_at,
            last_automatic=output._last_automatic,
            resume_pending=output._resume_pending,
            return_transition=None
            if transition is None
            else LightReturnSnapshot(
                transition.started_at,
                transition.start_percent,
                transition.duration_seconds,
            ),
            phase_paused=output._phase_paused,
            paused_automatic=output._paused_automatic,
        )


@dataclass(frozen=True)
class ButtonEvaluation:
    """Normalized input and every action actually returned by ButtonGestures.

    None denotes a time-only advance, which has no new normalized input. The
    caller supplies () or the returned actions; this form defines no actions.
    """

    normalized_input: str | None
    operation_enabled: bool
    evaluated_at: datetime
    supports_hold_feedback: bool
    actions: tuple[str, ...]

    def __post_init__(self):
        _text(self.normalized_input, optional=True)
        _instance(self.operation_enabled, bool)
        _instance(self.supports_hold_feedback, bool)
        _instance(self.evaluated_at, datetime)
        actions = _tuple_of(self.actions, str)
        for action in actions:
            _text(action)
        object.__setattr__(self, "actions", actions)
        require_immutable(self)


@dataclass(frozen=True)
class ObservedSource:
    """Explicit adapter DTO for an observed source, without HA State objects.

    Context and attributes are supplied observations, not inferred identities.
    None for state or timestamps preserves missing information.
    """

    entity_id: str
    state: str | None
    attributes: FrozenMap
    received_at: datetime
    last_changed: datetime | None
    last_updated: datetime | None
    context: FrozenMap

    def __post_init__(self):
        _text(self.entity_id)
        if self.state is not None:
            _instance(self.state, str)
        _instance(self.received_at, datetime)
        for value in (self.last_changed, self.last_updated):
            if value is not None:
                _instance(value, datetime)
        object.__setattr__(self, "attributes", _mapping(self.attributes))
        object.__setattr__(self, "context", _mapping(self.context))
        require_immutable(self)


@dataclass(frozen=True)
class DeviceObservation:
    """Supplied source observations and existing normalized measurements."""

    sources: tuple[ObservedSource, ...]
    measurements: tuple[Measurement, ...]

    def __post_init__(self):
        object.__setattr__(self, "sources", _tuple_of(self.sources, ObservedSource))
        object.__setattr__(
            self, "measurements", _tuple_of(self.measurements, Measurement)
        )
        require_immutable(self)


@dataclass(frozen=True)
class DeviceDiagnostics:
    """Current device fault map, onset evidence and existing service-error state."""

    faults: FrozenMap
    fault_since: FrozenMap
    command_error: bool
    command_error_at: datetime | None

    def __post_init__(self):
        object.__setattr__(self, "faults", _mapping(self.faults))
        object.__setattr__(self, "fault_since", _mapping(self.fault_since))
        _instance(self.command_error, bool)
        if self.command_error_at is not None:
            _instance(self.command_error_at, datetime)
        require_immutable(self)


@dataclass(frozen=True)
class HeaterServiceResult:
    """Existing heater-service result; distinct from physical feedback.

    Fields follow HADevice._run_heater_service. The existing Decision remains
    typed rather than duplicating its fields through an archive serialization.
    Instance/session association belongs to the enclosing handoff context.
    """

    heater: str
    heat: bool
    decision: Decision | None
    service_error: str | None
    planned_at: datetime
    sent_at: datetime
    feedback_proves_heating: bool = False

    def __post_init__(self):
        _text(self.heater)
        _instance(self.heat, bool)
        _text(self.service_error, optional=True)
        _instance(self.planned_at, datetime)
        _instance(self.sent_at, datetime)
        _instance(self.feedback_proves_heating, bool)
        if self.feedback_proves_heating:
            raise ValueError("Service completion is not physical heating evidence")
        if self.decision is not None:
            _instance(self.decision, Decision)
            freeze_data(self.decision)
        require_immutable(self)


@dataclass(frozen=True)
class LightServiceResult:
    """Actual light-service result, following its existing completion payload.

    ``light`` is the original service data's entity_id, which is available at
    the same completion boundary. This observation neither claims a changed
    device state nor acknowledges a later state report. Instance/session
    association belongs to handoff context.
    """

    light: str
    planned_at: datetime
    purpose: str
    phase: str
    service: str
    brightness_pct: float | None
    transition_seconds: float | None
    ends_at: datetime | None
    sent_at: datetime
    completed_at: datetime
    service_error: str | None

    def __post_init__(self):
        for value in (self.light, self.purpose, self.phase, self.service):
            _text(value)
        _number(self.brightness_pct, optional=True)
        _number(self.transition_seconds, optional=True)
        _text(self.service_error, optional=True)
        for value in (self.planned_at, self.sent_at, self.completed_at):
            _instance(value, datetime)
        if self.ends_at is not None:
            _instance(self.ends_at, datetime)
        for name in self.__dataclass_fields__:
            freeze_data(getattr(self, name))
        require_immutable(self)


@dataclass(frozen=True)
class LightQuantization:
    """One actual LightQuantizer call with supplied input, memory and result.

    The caller reads previous_value before quantize and next_value afterwards.
    This form neither invokes the quantizer nor checks its calculation again;
    next_value can be None after its existing reset behavior.
    """

    plan: LightPlan
    previous_value: int | None
    next_value: int | None
    hysteresis_percent: float
    result: int

    def __post_init__(self):
        _instance(self.plan, LightPlan)
        _number(self.hysteresis_percent)
        for value in (self.previous_value, self.next_value, self.result):
            if value is not None and type(value) is not int:
                raise TypeError("Quantizer values must be integers")
        if self.result is None:
            raise TypeError("A quantizer result is required")
        require_immutable(self)
