"""Laufzeitobjekt einer Sauna-Instanz; keine HA-Serviceaufrufe an Geräte."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from math import isfinite
from types import MappingProxyType

from .appearance import (
    APPEARANCE_CATALOG,
    appearance_options,
    default_appearance,
    validate_appearance,
)
from .archive import encoded, encoded_plain, plain
from .bindings import Bindings
from .const import CONF_BINDINGS, CONF_PARAMETERS
from .core.button import (
    END_HOLD,
    END_RELEASE,
    HEATER_TOGGLE_OVERRIDE,
    START_HOLD,
    START_RELEASE,
    START_STANDARD_PROGRAM,
    ButtonGestures,
    validate_session_gesture,
)
from .core.configuration_model import ConfigurationModel
from .core.contracts import ConsumerEvent, PresenceReport
from .core.controller import Controller, Result
from .core.defaults import instance_default, preference_values
from .core.detector import Detection, Detector
from .core.input_handoff import (
    ControlModeInput,
    HeaterOverrideInput,
    InputTiming,
    OccupancyInput,
    OperationInput,
    TemperatureInput,
)
from .core.models import Position, Session
from .core.sensor_sample import ClimatePacket, SampleStream
from .core.notification_recipients import ActorRef, RecipientAssignment, SessionRecipients
from .notification_options import (
    CONF_NOTIFICATION_ASSIGNMENTS, decode_notification_assignments, encode_notification_assignments,
)
from .core.parameters import (
    BY_KEY,
    CATALOG_PARAMETER_KEYS,
    EDITABLE_DEFINITIONS,
    Parameters,
)
from .core.presence import PresenceProjection, PresenceSourceSelection, ProxyPresenceSource
from .core.program_catalog import (
    DEFAULT_PROGRAMS,
    NamedTemperatureProgram,
    load_programs,
    migrate_legacy_programs,
    validate_programs,
)
from .core.temperature_program import temperature_steps as validate_temperature_steps
from .core.timeline import Event, Kind
from .log import LEVELS, SaunaLog
from .presentation import (
    EVENTS,
    PHASES,
    decision_message,
    fault_message,
    fault_resolved,
)
from .settings import program_parameters


class HeaterOverrideRejected(ValueError):
    """A current operating condition declines an otherwise valid command."""


@dataclass(frozen=True)
class Configuration:
    bindings: Bindings
    parameters: Parameters
    log_level: str = instance_default("log_level")
    control_input_mode: str = instance_default("control_input_mode")
    button_event_type: str = instance_default("button_event_type")
    program_mode: str = instance_default("program_mode")
    button_program: str = instance_default("button_program")
    temperature_programs: tuple[NamedTemperatureProgram, ...] = DEFAULT_PROGRAMS
    selected_program_id: str | None = None
    control_mode: str = instance_default("control_mode")
    presence_source: str = instance_default("presence_source")
    temperature_steps: tuple[float, ...] | None = None
    appearance: dict = field(default_factory=default_appearance)
    button_session_gesture: str = instance_default("button_session_gesture")
    field_standards: Mapping[str, object] = field(default_factory=dict)

    notification_assignments: tuple[RecipientAssignment, ...] = ()

    def __post_init__(self) -> None:
        """Keep a direct legacy-button construction serializable as options."""
        validate_session_gesture(self.button_session_gesture)
        if (self.button_session_gesture != "long"
                and not self.bindings.values["control_input"].startswith("event.")):
            raise ValueError(
                "Der Taster muss zweimaliges und dreimaliges Drücken selbst erkennen."
            )
        if self.presence_source not in {"proxy", "ha_presence"}:
            raise ValueError("Ungültige Präsenzquelle")
        object.__setattr__(self, "appearance", validate_appearance(self.appearance))
        standards = {}
        if isinstance(self.field_standards, Mapping):
            for name, value in self.field_standards.items():
                try:
                    standards[name] = self._validated_field_standard(name, value)
                except ValueError:
                    # An obsolete or newly invalid standard must not replace a
                    # valid active setting. Its standard fallback remains W.
                    continue
        object.__setattr__(self, "field_standards", MappingProxyType(standards))
        minimum = self.parameters.minimum_for("target_temperature_c")
        maximum = BY_KEY["target_temperature_c"].maximum
        if self.temperature_steps is not None:
            steps = validate_temperature_steps(
                self.temperature_steps, minimum_c=minimum, maximum_c=maximum,
            )
            if len(steps) > BY_KEY["temperature_gangs"].maximum:
                raise ValueError("Ungültige manuelle Temperaturstufen")
            object.__setattr__(self, "temperature_steps", steps)
        if self.button_program not in {"program_1", "program_2"} or any(
            program.id == self.button_program for program in self.temperature_programs
        ):
            return
        legacy_programs = migrate_legacy_programs(
            {
                "parameters": self.parameters.as_dict(),
                "button_program": self.button_program,
            },
            minimum_c=self.parameters.minimum_for("target_temperature_c"),
            maximum_c=BY_KEY["target_temperature_c"].maximum,
            maximum_gangs=int(BY_KEY["temperature_gangs"].maximum),
        )
        missing = tuple(
            program
            for program in legacy_programs
            if program.id in {"program_1", "program_2"}
            and program.id not in {current.id for current in self.temperature_programs}
        )
        object.__setattr__(
            self, "temperature_programs", (*self.temperature_programs, *missing)
        )

    @property
    def available_button_session_gestures(self):
        return ConfigurationModel.from_configuration(self).button.session_gestures

    @classmethod
    def standard_field_groups(cls) -> dict[str, str]:
        """Derive the permanent field scope from the existing catalog metadata."""
        return {
            f"parameters.{definition.key}": definition.settings_group
            for definition in EDITABLE_DEFINITIONS
            if definition.configuration_surface != "catalog"
            and definition.settings_group != "programs"
        } | {
            f"appearance.colors.{definition['id']}": "appearance"
            for definition in APPEARANCE_CATALOG["colors"]
        } | {
            f"appearance.scales.{name}.{bound}": "appearance"
            for name, spec in APPEARANCE_CATALOG["scales"].items()
            for bound in spec["default"]
        } | {
            f"appearance.instruments.{name}": "appearance"
            for name in APPEARANCE_CATALOG["instruments"]
        } | {
            f"appearance.preferences.{name}": "appearance"
            for name, definition in APPEARANCE_CATALOG["preferences"].items()
            if definition["configuration_surface"] == "panel"
        }

    @classmethod
    def standard_fields(cls, *, field=None, group=None) -> tuple[str, ...]:
        """Resolve a field, settings group or global scope, including hidden fields."""
        groups = cls.standard_field_groups()
        if field is not None:
            if group is not None or not isinstance(field, str) or field not in groups:
                raise ValueError("Ungültiges Standardfeld")
            return (field,)
        if group is not None:
            if not isinstance(group, str) or group not in groups.values():
                raise ValueError("Ungültiger Standardbereich")
            return tuple(name for name, owner in groups.items() if owner == group)
        return tuple(groups)

    def standard_field_metadata(self) -> dict[str, dict]:
        """Project existing labels and applicability without shrinking reset scopes."""
        model = ConfigurationModel.from_configuration(self)
        result = {}
        for name, group in self.standard_field_groups().items():
            parts = name.split(".")
            if parts[0] == "parameters":
                definition = BY_KEY[parts[1]]
                label, unit = definition.label, definition.unit
                applicable = model.parameter_applicable(definition)
            else:
                category, key = parts[1:3]
                if category == "colors":
                    definition = next(
                        spec for spec in APPEARANCE_CATALOG[category]
                        if spec["id"] == key
                    )
                else:
                    definition = APPEARANCE_CATALOG[category][key]
                label, unit = definition.get("label", key), definition.get("unit", "")
                applicable = True
            result[name] = {
                "group": group,
                "label": label,
                "unit": unit,
                "applicable": applicable,
            }
            if parts[0] == "appearance" and parts[1] == "scales":
                result[name]["bound"] = parts[3]
        return result

    @classmethod
    def _validated_field_standard(cls, name, value):
        """Check one stored scalar independently of the current active configuration."""
        cls.standard_fields(field=name)
        parts = name.split(".")
        if parts[0] == "parameters":
            return BY_KEY[parts[1]].validate(value)
        category, key = parts[1:3]
        if category in {"colors", "instruments"}:
            return validate_appearance({category: {key: value}})[category][key]
        if category == "preferences":
            return preference_values(
                {key: value}, reject_catalog_overrides=True
            )[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Ungültiger numerischer Feldstandard")
        try:
            number = float(value)
        except OverflowError:
            raise ValueError("Ungültiger numerischer Feldstandard") from None
        if not isfinite(number):
            raise ValueError("Ungültiger numerischer Feldstandard")
        definition = APPEARANCE_CATALOG[category][key]
        if (
            number < definition.get("minimum", number)
            or number > definition.get("maximum", number)
        ):
            raise ValueError("Feldstandard liegt außerhalb des zulässigen Bereichs")
        return value

    def field_value(self, name, *, source="active"):
        """Read A, catalog W or independent S with its W fallback for one field."""
        self.standard_fields(field=name)
        if not isinstance(source, str) or source not in {
            "active", "factory", "standard"
        }:
            raise ValueError("Ungültige Feldwertquelle")
        if source == "standard" and name in self.field_standards:
            return self.field_standards[name]
        parts = name.split(".")
        if parts[0] == "parameters":
            return (
                self.parameters.values[parts[1]] if source == "active"
                else BY_KEY[parts[1]].default
            )
        category, key = parts[1:3]
        if category == "colors":
            factory = next(
                spec["default"] for spec in APPEARANCE_CATALOG[category]
                if spec["id"] == key
            )
        else:
            factory = APPEARANCE_CATALOG[category][key]["default"]
        if category == "scales":
            return (
                self.appearance[category][key][parts[3]] if source == "active"
                else factory[parts[3]]
            )
        return (
            self.appearance[category].get(key, factory) if source == "active"
            else factory
        )

    def with_field_values(self, values: Mapping[str, object]) -> Configuration:
        """Validate a complete A candidate while retaining all excluded owners."""
        if not isinstance(values, Mapping):
            raise ValueError("Ungültige Feldwerte")
        parameters = self.parameters.as_dict()
        appearance = validate_appearance(self.appearance)
        for name, value in values.items():
            self.standard_fields(field=name)
            parts = name.split(".")
            if parts[0] == "parameters":
                parameters[parts[1]] = value
            elif parts[1] == "scales":
                appearance[parts[1]][parts[2]][parts[3]] = value
            else:
                appearance[parts[1]][parts[2]] = value
        parameters = Parameters(parameters)
        validate_programs(
            self.temperature_programs,
            minimum_c=parameters.minimum_for("target_temperature_c"),
            maximum_c=BY_KEY["target_temperature_c"].maximum,
            maximum_gangs=BY_KEY["temperature_gangs"].maximum,
        )
        return replace(self, parameters=parameters, appearance=appearance)

    def with_field_standard(self, name, value) -> Configuration:
        """Copy one validated draft value to S without changing active settings."""
        value = self._validated_field_standard(name, value)
        return replace(self, field_standards={**self.field_standards, name: value})

    def without_field_standards(self, fields) -> Configuration:
        """Remove S only in an explicitly resolved permanent field scope."""
        names = set()
        for name in fields:
            self.standard_fields(field=name)
            names.add(name)
        return replace(
            self,
            field_standards={
                name: value for name, value in self.field_standards.items()
                if name not in names
            },
        )

    @classmethod
    def from_options(cls, options: Mapping, *, historical=False) -> Configuration:
        if (
            not isinstance(options, Mapping)
            or not {CONF_BINDINGS, CONF_PARAMETERS} <= set(options)
            or set(options)
            - {
                CONF_BINDINGS,
                CONF_PARAMETERS,
                "log_level",
                "control_input_mode",
                "button_event_type",
                "program_mode",
                "button_program",
                "button_session_gesture",
                "temperature_programs",
                "selected_program_id",
                "control_mode",
                "presence_source",
                "temperature_steps",
                "button_temperature_c",  # Obsolete saved option; ignored on load.
                "appearance",
                "field_standards",
                CONF_NOTIFICATION_ASSIGNMENTS,
            }
        ):
            raise ValueError(
                "Vollständige Entitäts- und Parameterkonfiguration erforderlich"
            )
        level = options.get("log_level", instance_default("log_level"))
        if level not in LEVELS:
            raise ValueError("Ungültige Protokollstufe")
        mode = options.get("control_input_mode", instance_default("control_input_mode"))
        event_type = options.get("button_event_type", instance_default("button_event_type"))
        if mode not in ("button", "switch") or not isinstance(event_type, str):
            raise ValueError("Ungültige Taster- oder Schaltereinstellung")
        values = dict(options[CONF_PARAMETERS])
        override_key = "manual_override_minutes"
        if override_key in values:
            # Gespeicherte Werte hatten bisher die allgemeine Obergrenze von
            # 1.000.000 Minuten. Nur damals gültige Altwerte werden übernommen;
            # neue Eingaben bleiben in Parameters auf zehn Minuten begrenzt.
            override_definition = BY_KEY[override_key]
            previous_value = replace(override_definition, maximum=1_000_000).validate(
                values[override_key]
            )
            values[override_key] = min(previous_value, override_definition.maximum)
        # Diese frühere, getrennte Lichtdauer ist durch die Sitzungspause
        # ersetzt. Sie wird nur bei alten gespeicherten Optionen verworfen;
        # andere unbekannte Werte bleiben weiterhin ungültig.
        values.pop("session_light_minutes", None)
        if "program_mode" in options:
            program_mode = options["program_mode"]
        else:
            program_mode = (
                "progressive" if "final_temperature_c" in values else "constant"
            )
        # Die frühere Kältetoleranz ist der untere Abstand zum Sollwert.
        if "cold_tolerance_c" in values or "hot_tolerance_c" in values:
            if "cold_tolerance_c" in values:
                values.setdefault("readiness_hysteresis_c", values.pop("cold_tolerance_c"))
            values.pop("hot_tolerance_c", None)
        if (
            "sauna_min_temperature_c" not in values
            and "temperature_programs" not in options
        ):
            minimum_c = BY_KEY["sauna_min_temperature_c"].default
            for key in (
                "preset_start_c",
                "target_temperature_c",
                "final_temperature_c",
                "program_1_start_c",
                "program_1_end_c",
                "program_2_start_c",
                "program_2_end_c",
            ):
                if key in values:
                    minimum_c = min(minimum_c, BY_KEY[key].validate(values[key]))
            values["sauna_min_temperature_c"] = minimum_c
        if not historical:
            values = {
                key: value for key, value in values.items()
                if key not in CATALOG_PARAMETER_KEYS
            }
        parameters = Parameters(values)
        minimum_c = parameters.minimum_for("target_temperature_c")
        maximum_c = BY_KEY["target_temperature_c"].maximum
        maximum_gangs = BY_KEY["temperature_gangs"].maximum
        if "temperature_programs" in options:
            programs = load_programs(
                options["temperature_programs"],
                minimum_c=minimum_c,
                maximum_c=maximum_c,
                maximum_gangs=maximum_gangs,
            )
        else:
            programs = migrate_legacy_programs(
                options,
                minimum_c=minimum_c,
                maximum_c=maximum_c,
                maximum_gangs=maximum_gangs,
            )
        button_program = options.get("button_program", instance_default("button_program"))
        selected_program_id = options.get("selected_program_id")
        control_mode = options.get("control_mode", instance_default("control_mode"))
        steps = options.get("temperature_steps")
        program_ids = {program.id for program in programs}
        if button_program == "current":
            button_program = (
                selected_program_id
                if selected_program_id in program_ids
                else "constant"
            )
        if (
            program_mode not in ("constant", "progressive")
            or button_program not in {"constant", *program_ids}
            or (
                selected_program_id is not None
                and selected_program_id not in program_ids
            )
            or control_mode not in ("automatic", "manual")
        ):
            raise ValueError("Ungültige Temperaturprogrammeinstellung")
        return cls(
            Bindings(options[CONF_BINDINGS]),
            parameters,
            level,
            mode,
            event_type.strip(),
            program_mode,
            button_program,
            programs,
            selected_program_id,
            control_mode,
            options.get("presence_source", instance_default("presence_source")),
            steps,
            options.get("appearance", default_appearance()),
            options.get("button_session_gesture", instance_default("button_session_gesture")),
            options.get("field_standards", {}),
            decode_notification_assignments(options.get(CONF_NOTIFICATION_ASSIGNMENTS, [])),
        )

    def as_options(self, *, snapshot=False) -> dict:
        return {
            CONF_BINDINGS: self.bindings.as_dict(),
            CONF_PARAMETERS: self.parameters.as_dict() if snapshot else self.parameters.as_options(),
            "log_level": self.log_level,
            "control_input_mode": self.control_input_mode,
            "button_event_type": self.button_event_type,
            "program_mode": self.program_mode,
            "button_program": self.button_program,
            "button_session_gesture": self.button_session_gesture,
            "temperature_programs": [
                program.as_dict() for program in self.temperature_programs
            ],
            "selected_program_id": self.selected_program_id,
            "control_mode": self.control_mode,
            "presence_source": self.presence_source,
            "temperature_steps": self.temperature_steps,
            "appearance": (
                validate_appearance(self.appearance) if snapshot
                else appearance_options(self.appearance)
            ),
            "field_standards": dict(self.field_standards),
            **({CONF_NOTIFICATION_ASSIGNMENTS: encode_notification_assignments(self.notification_assignments)}
               if not snapshot else {}),
        }

    def as_snapshot(self) -> dict:
        """Copy effective values and field standards for a historical snapshot."""
        return self.as_options(snapshot=True)


class SaunaRuntime:
    """Serialisiert Kernzugriffe; Timer/Listener können kontrolliert abgemeldet werden.

    Messungen, Bedienung und Timer erreichen denselben serialisierten Kern.
    Gerätebefehle führt ausschließlich der Geräteadapter aus.
    """

    def __init__(
        self, configuration: Configuration, clock: Callable[[], datetime] | None = None
    ) -> None:
        self.configuration = configuration
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)
        self.controller = Controller(
            configuration.parameters,
            program_mode=configuration.program_mode,
            control_mode=configuration.control_mode,
            temperature_steps=configuration.temperature_steps,
            decision_clock=lambda: self._clock(),
        )
        self._presence_source_selection = PresenceSourceSelection(configuration.presence_source)
        self._lock = asyncio.Lock()
        self._tick_pending = False
        self._pending_device_inputs = deque()
        self._climate_stream = SampleStream()
        self._device_input_pending = False
        self._reset_button_gestures()
        self._button_hold_session_id = None
        self._button_start_hold_session_id = None
        self.save_configuration = None
        self._cleanup: list[Callable[[], None]] = []
        self._subscribers: set[Callable[[], None]] = set()
        self.closed = False
        self._close_task = None
        self.reconfiguring = False
        self.archive = None
        self._archived_completed = 0
        self.device = None
        self.detector = None
        self._detector_session = None
        self._detector_heating_gates = []
        self._detector_infusion_events: dict[int, tuple[Detection, str]] = {}
        self._archive_signature = None
        self._saved_decisions = 0
        self._saved_manual_heater_ends = 0
        self.presence = PresenceProjection()
        self._selected_presence_report = None
        self.presence_adapter = None
        self._saved_consumer_events = 0
        self._consumer_ids = set()
        self.consumer_events = []
        self._saved_phase = None
        self._saved_faults = None
        self.log = SaunaLog(id(self), configuration.log_level)
        self._logged_faults = {}
        self.notification_entry_id = None
        self.notification_transport = None
        self.notification_resolutions = ()
        self._notification_recipients = None

    @property
    def notification_recipients(self):
        self._sync_notification_session()
        return self._notification_recipients

    def _sync_notification_session(self):
        session_id = self.session.session_id if self.session and not self.closed else None
        previous = self._notification_recipients
        if previous is not None and previous.session_id != session_id:
            if self.notification_transport is not None:
                self.notification_transport.forget_session(previous.session_id)
            self._notification_recipients = None
        if session_id and self.notification_entry_id and self._notification_recipients is None:
            self._notification_recipients = SessionRecipients(self.notification_entry_id, session_id)

    def _record_notification_participant(self, actor):
        self._sync_notification_session()
        if actor is not None and self._notification_recipients is not None:
            self._notification_recipients = self._notification_recipients.accumulate(
                actor, self.configuration.notification_assignments, self.notification_resolutions,
            )

    def set_log_level(self, level):
        self.log.set_level(level)
        self.configuration = replace(self.configuration, log_level=level)
        self.log.info("log_level", "Protokollstufe auf %s gesetzt.", level)

    def _sync_detector(self):
        session_id = self.session.session_id if self.session else None
        if session_id == self._detector_session:
            return
        # Deliver the preceding session's canonical end before a new session
        # replaces its proxy evidence with an initial availability report.
        self._publish_controller_events()
        self._detector_session = session_id
        self._detector_heating_gates = []
        self._detector_infusion_events.clear()
        if session_id:
            now = self._clock()
            self._record_presence(PresenceReport(
                f"presence:proxy:initial:{session_id}", "unknown", "provisional_proxy",
                "proxy", session_id, now, now, True, "no_proxy_evidence",
            ))
        if session_id and self.archive:
            if self.device and "presence_illuminance" in self.configuration.bindings.values:
                self.device.archive_illuminance_snapshot(self._clock())
            self.archive.append("presence_source", self._clock(), {
                "configured_source": self.configuration.presence_source,
                "effective_source": self._presence_source_selection.effective_source,
                "entity_id": self.configuration.bindings.values.get("presence"),
                "external_snapshot": self.presence.external,
            }, session_id)

        def observed(trace):
            self.log.debug("detection_check", "Erkennungsprüfung: %s", trace)
            # The detector keeps its live diagnostic in either recording profile.
            # Only an explicit analysis archive receives every internal check.
            if self.archive and self.archive.trace_profile == "analysis":
                try:
                    self.archive.append("detector_trace", self._clock(), trace, session_id)
                except (ValueError, OverflowError) as error:
                    # A malformed diagnostic is not an actuator input. Keep
                    # the raw measurements and finish this regulation cycle.
                    self.log.error(
                        "detector_trace_invalid",
                        "Erkennungsdiagnose konnte nicht gespeichert werden: %s",
                        error,
                    )

        self.detector = (
            Detector(
                self.configuration.parameters,
                self.session.started_at,
                positions=tuple(
                    Position(position)
                    for position in self.configuration_model.measurement_positions
                ),
                observer=observed,
            )
            if self.session
            else None
        )
        if self.detector and self.device:
            for measurement in sorted(
                self.device.measurements.values(), key=lambda m: m.received_at
            ):
                if (f"{measurement.position.value}_{measurement.quantity.value}"
                        in getattr(self.device, "packet_roles", ()) and measurement.sample is None):
                    continue
                self.detector.accept(measurement)
                if self.archive:
                    self.archive.append(
                        "source_snapshot", self._clock(), measurement, session_id
                    )

    def _report_detector_heating(self, at):
        """Book logical command permission; physical evidence stays in intervals."""
        if self.detector is None:
            return False
        heating = bool(
            self.device
            and self.device.command is True
            and not self.device.command_error
            and self.session
            and self.session.operation_enabled
        )
        marks = self._detector_heating_gates
        if marks:
            at = max(at, marks[-1][0])
        if not marks or marks[-1][1] != heating:
            marks.append((at, heating))
        return heating

    async def _cycle(self):
        with self.controller.confirmation_batch(self._clock):
            await self._run_cycle()

    async def _run_cycle(self):
        await self._drain_device_inputs()
        self._deliver_detection(self._clock())
        self._report_detector_availability()
        # Finish the previously admitted detector window before booking a
        # source boundary at its actual current processing time.
        now = self._clock()
        self._advance_presence_source(now)
        self.controller.advance(now, finish_confirmation_batch=True)
        if self.device:
            self.device.refresh(now)
        if self.device:
            await self.device.apply(now)
        self.notify()

    def _advance_presence_source(self, at):
        """Qualify the central source owner only at a current input boundary."""
        previous_source = self._presence_source_selection.effective_source
        changed = self._presence_source_selection.advance(
            at,
            fallback_delay_seconds=self.configuration.parameters.values["presence_fallback_delay_seconds"],
            recovery_delay_seconds=self.configuration.parameters.values["presence_recovery_delay_seconds"],
        )
        session_id = self.session.session_id if self.session else None
        if changed and self.archive:
            self.archive.append("presence_source", at, {
                "configured_source": self.configuration.presence_source,
                "effective_source": self._presence_source_selection.effective_source,
                "previous_source": previous_source,
                "entity_id": self.configuration.bindings.values.get("presence"),
                "reason": "availability_qualified",
            }, session_id)
        if changed and self._presence_source_selection.effective_source == "proxy":
            # A new source section has no person evidence from an earlier one.
            # Archive its unknown report after the same-time source boundary.
            reference = f"{session_id or 'no_session'}:{at.isoformat()}"
            self._record_presence(PresenceReport(
                f"presence:proxy:source:{reference}",
                "unknown", "provisional_proxy", "proxy", f"presence_source:proxy:{reference}",
                at, at, self._detector_presence_available(), "no_proxy_evidence",
            ), session_id=session_id)
        elif changed:
            report = self.presence.external.get(self.configuration.bindings.values.get("presence"))
            if report is not None:
                self._consume_presence_report(
                    report, qualified=True, defer_evaluation=True, first_usable_at=at,
                )
        return changed

    def _deliver_detection(self, through, *, include_current=True):
        """Book received recognition chronologically, without actuator I/O."""
        self._sync_detector()
        self._report_detector_heating(self._clock())
        if self.detector:
            detector = self.detector
            session_id = self.session.session_id

            index = 0

            def detected(detection):
                nonlocal index
                i, index = index, index + 1
                received_at = self._clock()
                booking_at = max(detection.trace_at, self.controller._last_at or detection.trace_at)
                event = Event(
                    f"detector:{self.session.session_id}:{detection.effective_at.isoformat()}:{i}:{detection.kind}",
                    self.session.session_id,
                    detection.kind,
                    detection.effective_at,
                    detection.detected_at,
                    booking_at=booking_at,
                    trace_at=detection.trace_at,
                    door_close_evidence=detection.door_close_evidence,
                )
                if detection.kind == Kind.INFUSION:
                    # Retain the exact confirmation across advance calls; the
                    # per-call callback index is not an episode identity.
                    self._detector_infusion_events[id(detection)] = (
                        detection, event.event_id,
                    )
                self._process_event(
                    event, defer_confirmation=True, recognition_at=detection.trace_at,
                )
                self.log.info(
                    "detection",
                    "Erkanntes Ereignis: %s; zugeordnete Zeit: %s.",
                    EVENTS.get(detection.kind, "Erkennungssignal"),
                    detection.effective_at,
                )
                if self.archive:
                    self.archive.append(
                        "detection",
                        received_at,
                        {
                            "event": event,
                            "channels": detection.channels,
                            "trace_at": detection.trace_at,
                        },
                        self.session.session_id,
                    )

            def infusion_updated(update):
                if (
                    self.detector is not detector
                    or self.session is None
                    or self.session.session_id != session_id
                    or self._detector_session != session_id
                ):
                    return
                source = self._detector_infusion_events.get(
                    id(update.confirmed_detection)
                )
                if source is None or source[0] is not update.confirmed_detection:
                    return
                received_at = self._clock()
                source_event_id = source[1]
                if not self.controller.record_infusion_end(
                    session_id, source_event_id, update.end,
                ):
                    return
                if self.archive:
                    self.archive.append(
                        "infusion_update", received_at,
                        {"source_event_id": source_event_id, "end": update.end},
                        session_id,
                    )

            def before_sample(at):
                # Direct semantic callers may already have advanced the core;
                # canonical booking never moves that leading clock backwards.
                if self.controller._last_at is None or at >= self.controller._last_at:
                    self.controller.advance(at, inclusive_confirmation=False)
                if self.session is None or self.session.session_id != session_id:
                    return False
                return True

            def after_sample(at):
                if self.controller._last_at is None or at >= self.controller._last_at:
                    self.controller.advance(at, finish_confirmation_batch=True)

            person_evidence_qualified = self._presence_source_selection.effective_source == "proxy"
            detector.advance(
                through,
                enabled=self.session.operation_enabled,
                allowed=lambda kind: self.controller.recognition_allowed(
                    kind, person_evidence_qualified=person_evidence_qualified,
                ),
                on_detection=detected,
                on_infusion_update=infusion_updated,
                heating_intervals=lambda: self.session.heating.intervals,
                heating_gates=self._detector_heating_gates,
                recognition_context=self.controller.recognition_context_at,
                allowed_at=lambda kind, at: self.controller.recognition_allowed_at(
                    kind, at, person_evidence_qualified=person_evidence_qualified,
                ),
                onset_allowed=self.controller.detection_onset_allowed,
                before_sample=before_sample,
                after_sample=after_sample,
                include_current=include_current,
                detected_at=self._clock,
            )
            sampled_at = detector.origin + timedelta(seconds=detector.index * detector.grid_seconds)
            past = [i for i, (gate_at, _) in enumerate(self._detector_heating_gates)
                    if gate_at <= sampled_at]
            if past:
                self._detector_heating_gates = self._detector_heating_gates[past[-1]:]
            self.controller.discard_recognition_context_before(sampled_at)
            self._sync_detector()

    def _detector_presence_available(self):
        return bool(
            self.detector is not None
            and self.session is not None
            and self.detector.active_positions
        )

    def _report_detector_availability(self):
        if self.detector is None or self.session is None:
            return
        available = self._detector_presence_available()
        current = self.presence.current
        now = self._clock()
        if current is None or current.available != available:
            self._record_presence(PresenceReport(
                f"presence:proxy:availability:{self.session.session_id}:{now.isoformat()}",
                "unknown", "provisional_proxy", "proxy", "detector_availability",
                now, now, available, "no_proxy_evidence" if available else "source_unavailable",
            ))

    async def device_input(self, event):
        if self.closed:
            return
        if isinstance(event, ClimatePacket):
            for measurement in event.measurements:
                role = f"{measurement.position.value}_{measurement.quantity.value}"
                if self.device.bindings.get(role) != measurement.source:
                    raise ValueError("Physical packet does not belong to its configured source")
            if not self._climate_stream.accept(event):
                return
        elif any(self.device.bindings.get(role) == event.data["entity_id"]
                 for role in getattr(self.device, "packet_roles", ())):
            return
        received_at = event.received_at if isinstance(event, ClimatePacket) else self._clock()
        self._pending_device_inputs.append((received_at, event))
        if self._device_input_pending:
            return
        self._device_input_pending = True
        try:
            while self._pending_device_inputs and not self.closed:
                # One worker owns the complete FIFO. New inputs received
                # during output join its next lock entry, behind commands
                # already waiting, instead of creating an old callback queue.
                # Dispatched channels still share their received packet.
                await asyncio.sleep(0)
                async with self._lock:
                    if self.closed:
                        self._pending_device_inputs.clear()
                        return
                    if not self._pending_device_inputs:
                        return
                    with self.controller.confirmation_batch(self._clock):
                        await self._drain_device_inputs()
                        await self._cycle()
        finally:
            self._device_input_pending = False

    async def _drain_device_inputs(self):
        """Consume received edges in order, before any advance to wall time.

        The caller owns the runtime lock. This path books inputs and gestures;
        only the subsequent current-time cycle sends actuator commands.
        """
        while self._pending_device_inputs:
            self._sync_detector()
            received_at = self._pending_device_inputs[0][0]
            self._deliver_detection(received_at, include_current=False)
            packet = []
            while (self._pending_device_inputs
                   and self._pending_device_inputs[0][0] == received_at):
                packet.append([self._pending_device_inputs.popleft()[1], [], None])
            measurement_roles = {"upper_temperature", "upper_humidity",
                                 "lower_temperature", "lower_humidity"}
            # All already received channels at this same timestamp belong to
            # its one raster, even when a control edge was dispatched between.
            for item in packet:
                event, originals, _temperature = item
                if isinstance(event, ClimatePacket):
                    for measurement in event.measurements:
                        originals.append(self.device.ingest_measurement(
                            measurement, defer_archive=True,
                        ))
                else:
                    entity_id = event.data["entity_id"]
                    for role, source in self.device.bindings.items():
                        if source == entity_id and role in measurement_roles:
                            measurement = self.device.ingest(
                                role, event.data.get("new_state"), received_at, defer_archive=True,
                            )
                            if measurement is not None:
                                originals.append(measurement)
                regulation = self.device.regulation_measurement(received_at)
                # A transport snapshot preserves each input's selected value,
                # including repeated roles at one time. The detector still gets
                # all same-time channels before any control edge is booked.
                item[2] = (
                    regulation,
                    self.device.regulation_valid_until(regulation),
                )
            # Recognition consumes the same final received raster as the
            # detector. Thermostat/readiness and control edges retain FIFO.
            final_regulation, final_valid_until = packet[-1][2]
            with self.controller.recognition_temperature_raster(
                final_regulation.value if final_regulation is not None else None,
                final_valid_until,
            ):
                for event, originals, regulation_input in packet:
                    self._sync_detector()
                    # Original provenance follows the received FIFO, including a
                    # session start/end between same-time measurements. Each value
                    # is archived once, even when a role occurs repeatedly here.
                    if self.archive:
                        for measurement in originals:
                            self.archive.append(
                                "measurement", received_at, measurement,
                                self.session.session_id if self.session else None,
                            )
                    action_at = max(received_at, self.controller._last_at or received_at)
                    # Book only the received regulation input here. Protection
                    # monitoring and actuator output remain in the wall-time cycle.
                    regulation, valid_until = regulation_input
                    self.controller.consume_temperature_input(TemperatureInput(
                        value=regulation.value if regulation is not None else None,
                        valid_until=valid_until,
                        timing=InputTiming(
                            effective_at=(regulation.measured_at or regulation.received_at
                                          if regulation is not None else None),
                            received_at=(regulation.received_at
                                         if regulation is not None else None),
                            first_usable_at=None,
                            evaluated_at=action_at,
                        ),
                    ))
                    if isinstance(event, ClimatePacket):
                        continue
                    entity_id = event.data["entity_id"]
                    for role, source in self.device.bindings.items():
                        if source == entity_id and role not in measurement_roles:
                            self.device.ingest(role, event.data.get("new_state"), received_at)
                            if role in {"heater", "heater_power", "heater_feedback"}:
                                self.device.report_received_feedback(received_at)
                    action_at = max(received_at, self.controller._last_at or received_at)
                    light_selection = self.device.external_light_selection(event, received_at)
                    if light_selection is not None:
                        context = getattr(event.data.get("new_state"), "context", None)
                        self._set_light_override(
                            light_selection, action_at,
                            actor=ActorRef(getattr(context, "user_id", None), "ha_context"),
                        )
                    action = self.device.physical_action(event)
                    if action is not None:
                        self._deliver_detection(action_at)
                    if self.configuration.control_input_mode == "button" and action is not None:
                        try:
                            await self._handle_button_event(
                                action, action_at, received_at=received_at, refresh_device=False,
                            )
                        except ValueError as error:
                            self._record_physical_rejection(error)
                    elif action is not None:
                        try:
                            context = getattr(event.data.get("new_state"), "context", None)
                            self._apply_operation(
                                action, physical=True,
                                actor=ActorRef(getattr(context, "user_id", None), "ha_context"),
                                at=action_at, received_at=received_at,
                                refresh_device=False,
                            )
                        except ValueError as error:
                            self._record_physical_rejection(error)
                    self._sync_detector()

    @asynccontextmanager
    async def serialized(self, *, semantic_event=False):
        """Serialize commands after all already received device inputs."""
        # Timer/command dispatch shares the same bounded packet boundary as
        # device callbacks, including callbacks queued after this handler.
        await asyncio.sleep(0)
        async with self._lock:
            with self.controller.confirmation_batch(self._clock):
                drained = not self.closed and bool(self._pending_device_inputs)
                if drained:
                    # Booking facts has no actuator I/O. A waiting command must
                    # enter before output awaits can admit an endless input stream.
                    await self._drain_device_inputs()
                source_changed = False
                if not self.closed and not semantic_event:
                    self._deliver_detection(self._clock())
                    source_changed = self._advance_presence_source(self._clock())
                command_error = None
                try:
                    yield
                except BaseException as error:
                    command_error = error
                    raise
                finally:
                    # Configuration-only and rejected commands must also deliver
                    # consumed inputs and source changes. New inputs remain the
                    # responsibility of their waiting handlers.
                    if (drained or source_changed) and not self.closed:
                        try:
                            await self._cycle()
                        except BaseException as error:
                            if command_error is not None:
                                note = f"Anschließende Eingangsausgabe fehlgeschlagen: {error!r}"
                                command_error.add_note(note)
                                self.log.error("input_output_failed", "%s", note)
                            else:
                                raise

    async def reset_protection(self, *, actor: ActorRef | None = None):
        async with self.serialized():
            self._require_open()
            if self.session and self.session.operation_enabled:
                raise ValueError("Betrieb vor Quittierung ausschalten")
            if self.device and (
                self.device.contactor_feedback() is not False
                or self.device.feedback() is not False
                or self.device.command_error
            ):
                raise ValueError("Quittierung benötigt bestätigten Ofen-Aus-Zustand")
            changed = bool(self.controller.protection)
            self.controller.protection.clear()
            if changed:
                self._record_notification_participant(actor)
            await self._cycle()

    async def start_archive(self, path, entry_id, *, trace_profile="normal"):
        from .archive import Archive

        self.archive = Archive(
            path, entry_id, sessions_only=True, parameters=self.configuration.parameters,
            trace_profile=trace_profile,
        )
        await self.archive.start()
        self._consumer_ids = await asyncio.to_thread(self.archive.consumer_event_ids)
        self.archive.append("presence_source", self._clock(), {
            "configured_source": self.configuration.presence_source,
            "effective_source": self._presence_source_selection.effective_source,
            "entity_id": self.configuration.bindings.values.get("presence"),
            "reason": "configuration_loaded",
        })

    def _publish_consumer(self, event):
        if event.event_id in self._consumer_ids:
            return False
        self._consumer_ids.add(event.event_id)
        self.consumer_events.append(event)
        if self.archive:
            self.archive.append("consumer_event", event.received_at, event, event.session_id)
        return True

    def _record_presence(self, report, *, session_id=None):
        if not self.presence.accept(report):
            return
        if self._presence_source_selection.effective_source == "proxy" and report.source == "proxy":
            # Projection of an original event is not durable occupancy evidence.
            # In particular unknown/retraction never asserts absence.
            self._consume_presence_report(report, qualified=False, defer_evaluation=True)
        if session_id is None and self.session is not None:
            session_id = self.session.session_id
        event = ConsumerEvent(
            report.report_id, "occupancy", session_id,
            report.effective_at, report.received_at, report.source_ref, presence=report,
        )
        if self._publish_consumer(event) and self.archive:
            self.archive.append("presence", report.received_at, report, event.session_id)

    def _consume_presence_report(
        self, report, *, qualified, defer_evaluation=False, first_usable_at=None,
    ):
        accepted = self.controller.consume_occupancy_input(
            OccupancyInput(
                occupancy=report.occupancy,
                available=report.available,
                qualified_presence=qualified,
                report_id=report.report_id,
                timing=InputTiming(
                    effective_at=report.effective_at,
                    received_at=report.received_at,
                    first_usable_at=first_usable_at,
                    evaluated_at=self._clock(),
                ),
            ),
            defer_evaluation=defer_evaluation,
        )
        if accepted:
            self._selected_presence_report = report

    async def accept_presence(self, report):
        """Serialize direct occupancy through the same controller and output cycle."""
        async with self._lock:
            if self.closed:
                return
            selected = (
                report.source == self.configuration.bindings.values.get("presence")
                and report.assertion == "direct_presence"
            )
            previous = self.presence.external.get(report.source)
            if report.report_id in self.presence.reports:
                # Preserve the projection's identity/content validation without
                # restarting an already observed source-health interval.
                self._record_presence(report)
                return
            if ((selected and previous is not None and (
                    report.effective_at < previous.effective_at
                    or report.received_at < previous.received_at
                ))
                    or (report.source == self.configuration.bindings.values.get("presence")
                        and not selected)):
                return
            self._record_presence(report)
            direct = self.configuration_model.direct_presence
            if selected:
                self._presence_source_selection.observe(report.available, self._clock())
                if direct:
                    self._consume_presence_report(
                        report, qualified=True, defer_evaluation=True,
                    )
            if direct or selected:
                # Catch-up recognition must use the report already received by
                # this callback, without advancing past queued device inputs.
                with self.controller.confirmation_batch(self._clock):
                    with self.controller.direct_presence_catchup():
                        await self._drain_device_inputs()
                        self._deliver_detection(self._clock())
                    await self._run_cycle()
            self.notify()

    def _process_event(self, event, *, defer_confirmation=False, recognition_at=None):
        report = (ProxyPresenceSource.present(event)
                  if event.kind in (Kind.PERSON_STRONG, Kind.PERSON_WEAK) else None)
        selected = self.presence_source == "proxy"
        admitted_at = self._presence_source_selection.last_changed_at
        if (event.kind == Kind.INFUSION and admitted_at is not None
                and event.effective_at < admitted_at):
            # Keep the detector's original onset. An earlier episode may still
            # annotate an existing gang, but is not new admitted entry/cooling
            # evidence. Rejecting onset_allowed would instead retime it to now.
            selected = False
        result = self.controller.process(
            event, defer_confirmation=defer_confirmation, recognition_at=recognition_at,
            person_evidence_qualified=selected,
        )
        if result.changed:
            self._report_detector_heating(event.booking_at)
        if report is not None and result.changed:
            self._record_presence(report)
        return result

    def _proxy_evidence_for(self, event):
        """Return the proxy person signal represented by a consumer event."""
        if event.kind == "gang_retracted":
            return event.source_ref
        if event.kind != "gang_ended" or event.gang_id is None:
            return None
        sessions = (*self.controller.completed_sessions,)
        if self.session is not None:
            sessions = (*sessions, self.session)
        session = next(
            (item for item in sessions if item.session_id == event.session_id), None
        )
        if session is None:
            return None
        timeline = session.timeline
        gangs = (*timeline.completed, *timeline.retracted)
        if timeline.active is not None:
            gangs = (*gangs, timeline.active)
        for gang in gangs:
            if gang.gang_id == event.gang_id:
                return gang.recognition_event_id
        return None

    def _publish_controller_events(self):
        for event in self.controller.consumer_events[self._saved_consumer_events:]:
            self._publish_consumer(event)
            current = self.presence.current
            source_ref = self._proxy_evidence_for(event)
            if (
                source_ref is not None
                and current is not None
                and current.source == "proxy"
                and (
                    current.source_ref == source_ref
                    or current.occupancy != "present"
                )
            ):
                # A delayed correction can name an old gang after a new proxy
                # person signal has become current.  Only matching person
                # evidence may be invalidated; an intervening availability
                # marker is not person evidence.  A matching gang end retains
                # Head's ``unknown`` projection; it is not an absence assertion.
                report = PresenceReport(
                    f"presence:proxy:end:{event.event_id}:{source_ref}",
                    "unknown", "proxy_retraction", "proxy", source_ref,
                    event.effective_at, event.received_at, current.available,
                    event.kind,
                )
                self._record_presence(report, session_id=event.session_id)
        self._saved_consumer_events = len(self.controller.consumer_events)

    @property
    def presence_source(self):
        return self._presence_source_selection.effective_source

    @property
    def configuration_model(self):
        device = getattr(self, "device", None)
        return ConfigurationModel.from_configuration(
            self.configuration,
            button_event_types=device.button_event_types if device else None,
            effective_presence_source=self._presence_source_selection.effective_source,
        )

    @property
    def presence_status(self):
        return {
            "current": (self._selected_presence_report
                        if self._presence_source_selection.effective_source == "ha_presence"
                        else self.presence.current),
        }

    def persist_completed_sessions(self):
        if self.archive is None:
            return
        completed = self.controller.completed_sessions[self._archived_completed :]
        for session in completed:
            self.archive.save_session(
                session, self._clock(), self.configuration.as_snapshot()
            )
        self._archived_completed = len(self.controller.completed_sessions)
        if completed and self.device:
            self.device.invalidate_historical_warmup()

    async def erase_archive(self, session_id=None, *, reset=False):
        task = asyncio.create_task(self._erase_archive(session_id, reset=reset))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def _erase_archive(self, session_id=None, *, reset=False):
        async with self.serialized():
            self._require_open()
            if self.session is not None:
                raise ValueError("Archivdaten können erst nach Abschluss der Sitzung gelöscht werden.")
            if self.archive is None:
                raise ValueError("Kein Sitzungsarchiv verfügbar.")
            self.persist()
            count = await self.archive.erase(session_id, reset=reset)
            self.controller.completed_sessions = tuple(
                session for session in self.controller.completed_sessions
                if not reset and session.session_id != session_id
            )
            self._archived_completed = len(self.controller.completed_sessions)
            self.consumer_events = [
                event for event in self.consumer_events
                if not reset and event.session_id != session_id
            ]
            self._consumer_ids = await asyncio.to_thread(self.archive.consumer_event_ids)
            if self.device:
                self.device.invalidate_historical_warmup()
            self.notify()
            return count

    def persist(self):
        self._publish_controller_events()
        if self.archive is None:
            return
        now = self._clock()
        phase_key = (
            self.session.session_id if self.session else None,
            self.controller.phase,
        )
        if phase_key != self._saved_phase:
            self.archive.append(
                "phase", now, {"phase": self.controller.phase}, phase_key[0]
            )
            self._saved_phase = phase_key
        faults = dict(self.device.faults) if self.device else {}
        fault_key = (phase_key[0], encoded(faults))
        if fault_key != self._saved_faults:
            self.archive.append("diagnostic", now, {"faults": faults}, phase_key[0])
            self._saved_faults = fault_key
        self.persist_completed_sessions()
        if self.session is not None:
            signature = plain(self.session)
            signature["heating"].pop("accounted_at")
            signature["heating"].pop("elapsed_seconds")
            signature.pop("energy")  # Counters do not create a revision every second.
            signature = encoded_plain(signature)
            if signature != self._archive_signature:
                self.archive.save_session(
                    self.session, now, self.configuration.as_snapshot()
                )
                self._archive_signature = signature
        self._persist_manual_heater_ends()
        self._persist_decisions()

    def _persist_manual_heater_ends(self):
        """Deliver original choice ends without guessing today's session."""
        if self.archive is None:
            return
        for end in self.controller.manual_heater_ends[self._saved_manual_heater_ends :]:
            self.archive.append(
                "manual_heater", end.at,
                {
                    "ends_previous_only": True,
                    "selection_id": end.selection_id,
                    "change": plain(end),
                },
                end.session_id,
            )
            self._saved_manual_heater_ends += 1

    def _persist_decisions(self):
        """Deliver pending decisions through the same cursor during close."""
        if self.archive is None:
            return
        for decision in self.controller.decisions[self._saved_decisions :]:
            self.archive.append(
                "decision",
                decision.created_at or decision.at,
                decision,
                decision.session_id,
            )
            self._saved_decisions += 1

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
        self._sync_notification_session()
        phase = self.controller.phase
        self.log.change(
            "phase", phase, logging.INFO, "Betriebszustand: %s.", PHASES[phase]
        )
        self.log.change(
            "target",
            self.controller.target_temperature,
            logging.INFO,
            "Aktuelle Solltemperatur: %s.",
            f"{self.controller.target_temperature:g} °C"
            if self.controller.target_temperature is not None
            else "noch nicht eingestellt",
        )
        decision = self.controller.last_decision
        if decision:
            self.log.change(
                "decision",
                (decision.heat, decision.reason),
                logging.DEBUG,
                "Heizentscheidung: %s",
                decision_message(decision),
            )
        faults = dict(self.device.faults) if self.device else {}
        for key, value in faults.items():
            if self._logged_faults.get(key) != value:
                level = (
                    logging.ERROR
                    if value == "confirmed"
                    or key
                    in (
                        "archive",
                        "operation_light",
                        "after_run_light",
                        "session_light",
                        "heater_service_unavailable",
                    )
                    else logging.WARNING
                )
                self.log.logger.log(
                    level, "%s", fault_message(key, value), extra={"sauna_event": key}
                )
        for key in self._logged_faults.keys() - faults.keys():
            self.log.info("fault_cleared", "%s", fault_resolved(key))
        self._logged_faults = faults
        self.persist()
        self.notify_entities()

    def notify_entities(self):
        """Publish changed display values without advancing or persisting the controller."""
        for callback in tuple(self._subscribers):
            callback()

    def check_configuration_change(self):
        self._require_open()
        if self.session is not None:
            raise ValueError(
                "Einstellungen können erst nach Ende der Saunasitzung geändert werden"
            )

    def _set_operation(
        self, enabled, *, preserve_button=False, at=None, received_at=None,
        refresh_device=True,
    ):
        at = self._clock() if at is None else at
        if enabled and self.reconfiguring:
            raise ValueError(
                "Die Grundeinstellungen werden gerade übernommen. Bitte kurz warten."
            )
        if self.device:
            if refresh_device:
                self.device.refresh(at)
            if enabled and not (self.session and self.session.operation_enabled):
                errors = self.device.start_errors()
                if errors:
                    self.log.change(
                        "start_rejected",
                        tuple(errors),
                        logging.WARNING,
                        "Start verhindert: %s",
                        " ".join(errors),
                    )
                    raise ValueError("Start nicht möglich. " + " ".join(errors))
            self.device.faults.pop("start_rejected", None)
        self.log.info(
            "operation",
            "Saunabetrieb %s angefordert.",
            "einschalten" if enabled else "ausschalten",
        )
        result = self.controller.consume_operation_input(OperationInput(
            enabled=enabled,
            session_id=None,
            timing=InputTiming(
                effective_at=received_at,
                received_at=received_at,
                first_usable_at=None,
                evaluated_at=at,
            ),
        ))
        self._report_detector_heating(at)
        if enabled:
            self._button_start_hold_session_id = None
            self._button_hold_session_id = None
            if not preserve_button:
                self._reset_button_gestures()
            if self.device:
                self.device.finish_button_hold_light()
            if self.save_configuration:
                self.save_configuration(self.configuration)
        return result

    def _prepare_operation(self, enabled, *, physical=False, at=None, received_at=None):
        at = self._clock() if at is None else at
        if enabled and self.reconfiguring:
            raise ValueError(
                "Die Grundeinstellungen werden gerade übernommen. Bitte kurz warten."
            )
        manual_transition = bool(
            physical
            and enabled
            and self.controller.control_mode == "manual"
        )
        if manual_transition:
            self._set_control_mode(
                "automatic", at=at, received_at=received_at,
                restore_program=self.configuration.button_program == "current",
            )
            self.log.info(
                "button_control_mode",
                "Saunataster startet das Standardprogramm im Automatikbetrieb.",
            )
        # Der externe Taster startet mit dem konfigurierten Profil.  Ausschalten
        # ist absichtlich zustandsneutral, damit ein späteres Einschalten die
        # gleiche explizite Auswahl wieder anwenden kann.
        if (
            physical
            and enabled
            and (manual_transition
                 or not (self.session and self.session.operation_enabled))
            and self.configuration.button_program != "current"
        ):
            self._select_button_program(at)

    def _select_button_program(self, at):
        """Apply the configured physical-button profile; caller owns ``_lock``."""
        parameters, mode = program_parameters(
            self.configuration.parameters,
            self.configuration.button_program,
            catalog=self.configuration.temperature_programs,
        )
        self.controller.update_temperature_parameters(
            parameters,
            at,
            program_mode=mode,
            new_program=True,
            temperature_steps=next(
                (
                    program.temperature_steps
                    for program in self.configuration.temperature_programs
                    if program.id == self.configuration.button_program
                ),
                None,
            ),
        )
        # ``update_temperature_parameters`` advances deadlines. Archive any
        # session it completed before replacing the button-program values.
        self.persist_completed_sessions()
        self.configuration = replace(
            self.configuration,
            parameters=parameters,
            program_mode=mode,
            selected_program_id=(
                self.configuration.button_program
                if self.configuration.button_program
                in {program.id for program in self.configuration.temperature_programs}
                else None
            ),
            temperature_steps=self.controller.temperature_steps,
        )
        if self.device:
            self.device.values = parameters.values

    def _toggle_button_heater_override(self, now, *, received_at=None, refresh_device=True):
        """Toggle the physical-button override while the runtime lock is held."""
        if self.device and refresh_device:
            self.device.refresh(now)
        session = self.session
        if (
            not self.controller.automatic_operation_enabled
            and self.controller.control_mode != "manual"
        ):
            self._set_control_mode(
                "manual", preserve_light=True, at=now, received_at=received_at,
            )
            if self.save_configuration:
                self.save_configuration(self.configuration)
        # The current model has only the active Ofenkühlung (`after_run`).
        # Legacy cooling objects remain archival data and must not steer a
        # physical-button override.
        cooling_or_after_run = bool(
            session is not None and session.after_run is not None
        )
        if (
            self.controller.heater_override is not None
            and self.controller.control_mode != "manual"
        ):
            return self._set_heater_override(None, now, received_at=received_at)
        if self.controller.control_mode != "manual" and cooling_or_after_run:
            return self._set_heater_override(True, now, received_at=received_at)
        if self.controller.control_mode == "manual":
            # A thermostat may currently be satisfied (contactor OFF) while
            # remaining enabled. The button toggles its enabled state.
            current = self.controller.heater_override
        elif self.device:
            known = self.device.contactor_feedback()
            current = self.device.command if known is None else known
        else:
            current = self.controller.contactor
            if current is None and self.controller.last_decision is not None:
                current = self.controller.last_decision.heat
        value = not bool(current)
        return self._set_heater_override(value, now, received_at=received_at)

    def _reset_button_gestures(self):
        if getattr(self, "device", None) is not None:
            self.device.release_button_press_light()
        self._button = ButtonGestures(
            (
                timedelta(seconds=self.configuration.parameters.values["button_hold_seconds"])
                if self.configuration_model.button.local_hold_timing else None
            ),
            self.configuration.button_session_gesture,
        )

    @property
    def available_button_session_gestures(self):
        return self.configuration_model.button.session_gestures

    @property
    def button_start_hold_active(self):
        return bool(
            self.session
            and self._button_start_hold_session_id == self.session.session_id
        )

    async def _handle_button_event(self, event, now, *, received_at=None, refresh_device=True):
        """Apply one already-normalized gesture; caller owns ``_lock``."""
        enabled = self.controller.automatic_operation_enabled
        gesture_at = now if received_at is None else received_at
        supports_hold_feedback = self.configuration_model.button.press_release_feedback
        if self.device is not None:
            if (
                supports_hold_feedback
                and event in ("press", "on")
                and self.configuration.button_session_gesture == "long"
            ):
                self.device.begin_button_press_light(now, starting=not enabled)
            elif event in ("release", "off", "unavailable"):
                self.device.release_button_press_light()
        for action in self._button.handle_actions(
            event, enabled, gesture_at, supports_hold_feedback=supports_hold_feedback,
        ):
            await self._apply_button_action(
                action, now, received_at=received_at, refresh_device=refresh_device,
            )

    async def _apply_button_action(
        self, action, now, *, received_at=None, refresh_device=True,
    ):
        """Apply a semantic button action; caller owns ``_lock``."""
        if action in (START_STANDARD_PROGRAM, START_HOLD):
            if self._button_hold_session_id is not None:
                if self.device:
                    self.device.finish_button_hold_light(self._button_hold_session_id)
                self._button_hold_session_id = None
            self._prepare_operation(True, physical=True, at=now, received_at=received_at)
            self._set_operation(
                True, preserve_button=True, at=now, refresh_device=refresh_device,
                received_at=received_at,
            )
            if action == START_HOLD:
                self._button_start_hold_session_id = self.session.session_id
                if self.device:
                    self.device.begin_button_hold_light(
                        self.session.session_id, starting=True, at=now
                    )
        elif action == START_RELEASE:
            session_id = self._button_start_hold_session_id
            self._button_start_hold_session_id = None
            if session_id is not None and self.device:
                self.device.finish_button_hold_light(session_id)
        elif action == HEATER_TOGGLE_OVERRIDE:
            self._toggle_button_heater_override(
                now, received_at=received_at, refresh_device=refresh_device,
            )
        elif action == END_HOLD and self.session is not None:
            session_id = self.session.session_id
            self.controller.consume_operation_input(OperationInput(
                enabled=False,
                session_id=None,
                timing=InputTiming(
                    effective_at=received_at,
                    received_at=received_at,
                    first_usable_at=None,
                    evaluated_at=now,
                ),
            ))
            self._button_hold_session_id = session_id
            if self.device:
                self.device.begin_button_hold_light(session_id, at=now)
        elif action == END_RELEASE:
            session_id = self._button_hold_session_id
            if session_id is not None:
                self._button_hold_session_id = None
                if self.device:
                    self.device.finish_button_hold_light(session_id)
                light = self.controller.light_after_run
                if light is not None and light.session_id == session_id:
                    self.controller.start_session_light(session_id, now)
            elif self.session is not None:
                self.controller.consume_operation_input(OperationInput(
                    enabled=False,
                    session_id=None,
                    timing=InputTiming(
                        effective_at=received_at,
                        received_at=received_at,
                        first_usable_at=None,
                        evaluated_at=now,
                    ),
                ))

    async def set_operation(self, enabled: bool, *, actor: ActorRef | None = None):
        received_at = self._clock()
        async with self.serialized():
            self._require_open()
            result = self._apply_operation(enabled, actor=actor, received_at=received_at)
            await self._cycle()
            return result

    def _apply_operation(
        self, enabled, *, physical=False, actor: ActorRef | None = None, at=None,
        received_at=None, refresh_device=True,
    ):
        """Attribute an effective operation after preparation and admission succeed."""
        session = self.session
        previous = (
            session.session_id if session else None,
            bool(session and session.operation_enabled),
            self.controller.control_mode,
            self.controller.heater_override,
        )
        self._prepare_operation(
            enabled, physical=physical, at=at, received_at=received_at,
        )
        result = self._set_operation(
            enabled, at=at, received_at=received_at, refresh_device=refresh_device,
        )
        session = self.session
        current = (
            session.session_id if session else None,
            bool(session and session.operation_enabled),
            self.controller.control_mode,
            self.controller.heater_override,
        )
        if previous != current:
            self._record_notification_participant(actor)
        return result

    def _set_control_mode(
        self, mode, *, preserve_light=False, restore_program=True, at=None, received_at=None,
    ):
        """Apply a mode and its output initialization while holding the lock."""
        at = self._clock() if at is None else at
        if self.reconfiguring:
            raise ValueError(
                "Die Grundeinstellungen werden gerade übernommen. Bitte kurz warten."
            )
        previous_mode = self.controller.control_mode
        previous_session = self.session
        self.controller.consume_control_mode_input(ControlModeInput(
            mode=mode,
            timing=InputTiming(
                effective_at=received_at,
                received_at=received_at,
                first_usable_at=None,
                evaluated_at=at,
            ),
        ))
        if previous_mode != mode and previous_session is not None and self.archive:
            # Keep the manual/current configuration as its own revision before
            # the same recording continues under the newly selected mode.
            self.archive.save_session(
                previous_session, at, self.configuration.as_snapshot()
            )
        # A due gap may complete at this boundary. Save it with its former
        # configuration before applying a mode to a subsequent recording.
        self.persist_completed_sessions()
        if previous_mode != mode and mode == "manual":
            parameters = Parameters({
                **self.configuration.parameters.as_dict(),
                "target_temperature_c": self.configuration.parameters.values[
                    "standard_temperature_c"
                ],
            })
            self.controller.update_temperature_parameters(parameters, at)
            self.configuration = replace(self.configuration, parameters=parameters)
            if self.device:
                self.device.values = parameters.values
        elif (previous_mode != mode and restore_program
              and self.configuration.selected_program_id is not None):
            program = next(
                item for item in self.configuration.temperature_programs
                if item.id == self.configuration.selected_program_id
            )
            parameters, program_mode = program_parameters(
                self.configuration.parameters, program.id,
                catalog=self.configuration.temperature_programs,
            )
            self.controller.update_temperature_parameters(
                parameters, at, program_mode=program_mode,
                new_program=True, temperature_steps=program.temperature_steps,
            )
            self.configuration = replace(
                self.configuration, parameters=parameters, program_mode=program_mode,
                temperature_steps=program.temperature_steps,
            )
            if self.device:
                self.device.values = parameters.values
        if previous_mode != mode and self.device:
            brightness = 0
            if preserve_light:
                brightness = self.device.button_press_original_brightness
                if brightness is None:
                    brightness = self.device.light_observation["brightness_percent"]
            self.device.set_light_override(
                (brightness or 0) if mode == "manual" else None, at=at,
            )
        self.configuration = replace(self.configuration, control_mode=mode)
        if previous_mode != mode:
            self._archive_signature = None
        return self.configuration

    async def set_light_override(self, value, *, actor: ActorRef | None = None):
        """Apply a manual light selection through the serialized runtime path."""
        async with self.serialized():
            self._require_open()
            if self.device is None:
                raise ValueError("Lichtsteuerung ist nicht verfügbar")
            observation = self.device.light_observation
            actual = observation["brightness_percent"]
            manual_target = self.device.manual_light_feedback_target

            def matches(brightness):
                return (
                    (value is False and brightness == 0)
                    or (value is True and brightness > 0)
                    or (
                        not isinstance(value, bool)
                        and isinstance(value, (int, float))
                        and value == round(brightness)
                    )
                )

            if (
                observation["available"]
                and matches(actual)
                and (manual_target is None or matches(manual_target))
            ):
                return
            now = self._clock()
            self._set_light_override(value, now, actor=actor)
            await self._cycle()

    def _set_light_override(self, value, now, *, actor: ActorRef | None = None):
        """Record a UI or physical light selection within the current lock."""
        previous = (self.device.light_output.manual_brightness, self.device.light_output.manual_ends_at)
        self.device.set_light_override(value, at=now)
        self.log.info("light_override", "Manuelle Lichtwahl: %s.", value)
        current = (self.device.light_output.manual_brightness, self.device.light_output.manual_ends_at)
        if previous != current:
            self._record_notification_participant(actor)

    def record_manual_light_changes(self, changes, *, selection=None):
        """Archive original light-owner changes before asynchronous output."""
        if self.archive is None:
            return
        for change in changes:
            if not isinstance(change.at, datetime):
                continue
            payload = {
                "value": (selection["value"]
                          if selection is not None else change.value),
                "change": plain(change),
            }
            if (
                change.reason == "selection"
                and change.previous_value is not None
                and change.previous_session_id != change.session_id
            ):
                self.archive.append(
                    "manual_light", change.at,
                    {**payload, "ends_previous_only": True},
                    change.previous_session_id,
                )
            self.archive.append(
                "manual_light", change.at, payload,
                change.session_id if change.reason in {"selection", "keep"}
                else change.previous_session_id,
            )

    def _validate_heater_override(self, value):
        self.controller.validate_heater_override(value)
        if value is True and self.device:
            errors = self.device.start_errors()
            if errors:
                raise ValueError("Einschalten nicht möglich. " + " ".join(errors))

    @property
    def heater_on_blocked_reason(self):
        """Project the same admission rule used by the heater command."""
        try:
            self._validate_heater_override(True)
        except ValueError as error:
            return str(error)
        return None

    async def set_heater_override(self, value: bool | None, *, actor: ActorRef | None = None):
        """Apply a manual heater selection through the serialized runtime path."""
        received_at = self._clock()
        async with self.serialized():
            self._require_open()
            now = self._clock()
            if self.device:
                self.device.refresh(now)
            current = (
                self.controller.heater_override
                if self.controller.control_mode == "manual"
                else self.controller.contactor
            )
            if (
                isinstance(value, bool)
                and value is current
                and self.controller.heater_override in (None, value)
            ):
                return self.controller.last_decision
            previous = self.controller.heater_override
            decision = self._set_heater_override(value, now, received_at=received_at)
            if previous != self.controller.heater_override:
                self._record_notification_participant(actor)
            await self._cycle()
            return decision

    def _set_heater_override(self, value, now, *, received_at=None):
        """UI and physical inputs share command validation and archive assignment."""
        try:
            self._validate_heater_override(value)
        except ValueError as error:
            raise HeaterOverrideRejected(str(error)) from error
        previous_id = self.session.session_id if self.session else None
        decision = self.controller.consume_heater_override_input(HeaterOverrideInput(
            heat=value,
            timing=InputTiming(
                effective_at=received_at,
                received_at=received_at,
                first_usable_at=None,
                evaluated_at=now,
            ),
        ))
        if value is True and self.device:
            self.device.faults.pop("start_rejected", None)
        self.log.info("heater_override", "Manuelle Heizwahl: %s.", value)
        if self.archive:
            selection = self.controller.last_heater_selection
            self.archive.append(
                "manual_heater", now,
                {
                    "value": value, "decision": plain(decision),
                    "selection_id": selection.selection_id if selection else None,
                    "selection": plain(selection),
                },
                selection.session_id if selection is not None
                else self.session.session_id if self.session else previous_id,
            )
        return decision

    def _record_physical_rejection(self, error):
        if isinstance(error, HeaterOverrideRejected):
            # This is the same current admission result shown by the UI,
            # e.g. cooling still active. It is not a persistent device fault.
            self.log.logger.info(
                "Ofenbedienung abgelehnt: %s", error,
                extra={"sauna_event": "heater_override_rejected"},
            )
            if self.archive:
                self.archive.append(
                    "heater_override_rejected", self._clock(), {"reason": str(error)},
                    self.session.session_id if self.session else None,
                )
        elif self.device:
            self.device.faults["start_rejected"] = str(error)
        else:
            raise error

    async def finish_phase(self, purpose, token, *, actor: ActorRef | None = None):
        async with self.serialized():
            self._require_open()
            now = self._clock()
            deadline = self.controller.finish_phase(purpose, token, now)
            self._record_notification_participant(actor)
            label = "Ofenkühlung"
            self.log.info(
                "phase_finished_manually",
                "%s manuell beendet; regulärer Folgeablauf wird fortgesetzt.",
                label,
            )
            if self.archive:
                self.archive.append(
                    "manual_phase_end",
                    now,
                    {
                        "purpose": purpose,
                        "token": token,
                        "planned_ends_at": deadline.due_at if deadline else None,
                        "ended_at": now,
                    },
                    deadline.session_id if deadline else self.session.session_id,
                )
            await self._cycle()

    async def finish_session_gap(self, token, *, actor: ActorRef | None = None):
        """End light after-run while preserving the shared recording interval."""
        async with self.serialized():
            self._require_open()
            now = self._clock()
            previous_light = self.controller.light_after_run
            previous_manual = (self.device.light_output.manual_brightness if self.device else None)
            deadline = self.controller.finish_session_gap(token, now)
            changed = previous_light != self.controller.light_after_run or previous_manual is not None
            if self.device:
                # A manual brightness must not be restored after the required
                # session-light OFF.  The due LightAfterRun object keeps the
                # adapter's normal OFF/retry path responsible for the output.
                self.device.set_light_override(None, at=now)
                self.device.light_output.finish_automatic()
            if changed:
                self._record_notification_participant(actor)
            self.log.info(
                "session_light_finished_manually",
                "Lichtnachlauf beendet; Wiederaufnahmefrist bleibt erhalten.",
            )
            if self.archive:
                self.archive.append(
                    "manual_session_light_end",
                    now,
                    {
                        "purpose": "session_gap",
                        "token": token,
                        "recording_ends_at": deadline.due_at,
                        "light_ended_at": min(now, deadline.due_at),
                    },
                    deadline.session_id,
                )
            await self._cycle()

    async def tick(self, _at=None):
        # HA schedules each interval independently. Keep at most one current
        # tick waiting/running; it reads the current clock after taking the lock.
        if self.closed or self._tick_pending:
            return
        self._tick_pending = True
        try:
            async with self.serialized():
                if self.closed:
                    return
                if self.configuration.control_input_mode == "button":
                    now = self._clock()
                    try:
                        await self._apply_button_action(
                            self._button.advance(
                                now, self.controller.automatic_operation_enabled
                            ),
                            now,
                        )
                    except ValueError as error:
                        self._record_physical_rejection(error)
                await self._cycle()
        finally:
            self._tick_pending = False

    async def begin_session(self, session_id: str) -> Session:
        async with self.serialized():
            self._require_open()
            result = self.controller.begin_session(session_id, self._clock())
            self.notify()
            return result

    async def receive(self, event: Event) -> Result:
        async with self.serialized(semantic_event=True):
            self._require_open()
            if event.detected_at > self._clock():
                raise ValueError("Erkennungszeit liegt nach der Laufzeituhr")
            if event.kind == Kind.OPERATION_OFF:
                self._deliver_detection(event.booking_at)
            result = self._process_event(event)
            await self._cycle()
            return result

    def on_close(self, unsubscribe: Callable[[], None]) -> None:
        self._require_open()
        self._cleanup.append(unsubscribe)

    async def close(self) -> None:
        if self._close_task is not None and self._close_task.done() and not self.closed:
            # A shielded caller may have left before the failed preflight ended.
            # Retrieve its failure and let this next caller retry the shutdown.
            if not self._close_task.cancelled():
                self._close_task.exception()
            self._close_task = None
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_once())
        closing = self._close_task
        try:
            await asyncio.shield(closing)
        finally:
            if closing.done() and not self.closed and self._close_task is closing:
                self._close_task = None

    async def _close_once(self) -> None:
        async with self._lock:
            failures = []
            try:
                with self.controller.confirmation_batch(self._clock):
                    await self._drain_device_inputs()
                    if self.detector is not None:
                        self._deliver_detection(self._clock())
            except Exception as error:
                failures.append(error)
            if self.device:
                try:
                    self.controller.consume_operation_input(OperationInput(
                        enabled=False,
                        session_id=None,
                        timing=InputTiming(
                            effective_at=None,
                            received_at=None,
                            first_usable_at=None,
                            evaluated_at=self._clock(),
                        ),
                    ))
                    # The shutdown decision and any gang end belong to the
                    # still-open originating archive, including a retry.
                    self._publish_controller_events()
                except Exception as error:
                    failures.append(error)
                try:
                    # A pending light service must never defer the heater OFF.
                    await self.device.close()
                    heater_handoff = True
                except Exception as error:
                    failures.append(error)
                    heater_handoff = False
                try:
                    light_handoff = await self.device.prepare_light_handoff()
                except Exception as error:
                    failures.append(error)
                    light_handoff = False
                if not light_handoff or not heater_handoff:
                    self.device.restore_light_ownership()
                    self.device.restore_heater_ownership()
                    try:
                        self.persist()
                    except Exception as error:
                        failures.append(error)
                    error = RuntimeError(
                        "Geräteausgabe nicht abgeschlossen; Sauna-Laufzeit bleibt für einen erneuten Abschluss offen"
                    )
                    if failures:
                        raise error from ExceptionGroup(
                            "Sicherer Abschluss der Sauna-Laufzeit fehlgeschlagen", failures
                        )
                    raise error
            self.controller._end_heater_selection(
                self._clock(), "integration_unloaded"
            )
            self.closed = True
            self._notification_recipients = None
            self._pending_device_inputs.clear()
            self.log.info("unload", "Sauna-Integration wird beendet; Ofen ausschalten.")
            callbacks, self._cleanup = self._cleanup, []
            for unsubscribe in reversed(callbacks):
                try:
                    unsubscribe()
                except Exception as error:
                    failures.append(error)
            if self.archive is not None:
                try:
                    self._persist_manual_heater_ends()
                except Exception as error:
                    failures.append(error)
                try:
                    self._persist_decisions()
                except Exception as error:
                    failures.append(error)
                try:
                    self.persist_completed_sessions()
                except Exception as error:
                    failures.append(error)
                try:
                    if self.session is not None:
                        now = self._clock()
                        self.archive.append(
                            "interruption",
                            now,
                            {"reason": "integration_unloaded"},
                            self.session.session_id,
                        )
                        self.archive.save_session(
                            self.session,
                            now,
                            self.configuration.as_snapshot(),
                        )
                except Exception as error:
                    failures.append(error)
                try:
                    await self.archive.close()
                except Exception as error:
                    failures.append(error)
            if failures:
                raise ExceptionGroup(
                    "Abmeldung der Sauna-Laufzeit fehlgeschlagen", failures
                )
