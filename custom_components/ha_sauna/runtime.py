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
from uuid import uuid4

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
from .core.recognition import ClimateRecognizer, Detection, recognition_event_id
from .core import recognition_archive
from .core.input_handoff import (
    ControlModeInput,
    HeaterOverrideInput,
    InputTiming,
    OccupancyInput,
    OperationInput,
    TemperatureInput,
)
from .core import calibration, notification_rules
from .core.models import Position, Session
from .core.sensor_sample import ClimatePacket, SampleStream
from .core.notification_exchange import NotificationEnvelope
from .core.notification_recipients import ActorRef, RecipientAssignment, SessionRecipients
from .notification_options import (
    CONF_NOTIFICATION_ASSIGNMENTS, decode_notification_assignments, encode_notification_assignments,
)
from .core.parameters import (
    BY_KEY,
    CATALOG_PARAMETER_KEYS,
    EDITABLE_DEFINITIONS,
    LIVE_TEMPERATURE_KEYS,
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
from .core.timeline import Confirmation, Door, Event, Kind
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
            and definition.key not in LIVE_TEMPERATURE_KEYS
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

    def field_states(self) -> dict[str, dict]:
        """Project the three value levels of every permanent field for display.

        ``reference`` names the level the active value is compared with: the
        saved standard (S) or, without one, the factory value (W).
        """
        result = {}
        for name in self.standard_field_groups():
            reference = self.field_value(name, source="standard")
            result[name] = {
                "reference": "standard" if name in self.field_standards else "factory",
                "reference_value": reference,
                "differs": self.field_value(name) != reference,
            }
        return result

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
        self._detector_infusion_events: dict[int, tuple[Detection, str]] = {}
        # Learned recognition quantities; ``recognition_finished`` receives every
        # finished session's recognizer (session id, recognizer) for learning
        # evidence, which only enters an estimate after an explicit release.
        self._recognition_parameters = None
        # outside air of the sauna for the recognition: () -> {"temperature", "absolute_humidity"} or None
        self.outside_air = None
        # bound environment source: () -> ``environment.environment_snapshot`` (None: not bound)
        self.environment = None
        self._environment_saved = None      # (session id, what was new) of the last environment record
        self.recognition_finished = None
        self.recognition_learning = None
        self.recognition_feedback = None
        self.recognition_release = None
        self.recognition_relearn = None
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
        # Calibration runs of the current session (V31). They instruct and
        # observe only; controller, recognizer and gang model never read them.
        self._calibration_runs: list[calibration.CalibrationRun] = []
        self._calibration_targets = {}
        self._calibration_tasks = set()
        # Display state of the last run ended with its session (W-2c).
        self._calibration_last = None
        self._init_notifications()

    @property
    def calibration_runs(self) -> tuple[calibration.CalibrationRun, ...]:
        return tuple(self._calibration_runs)

    @property
    def calibration_status(self) -> dict:
        """Calibration state for the panel (W-2c).

        ``active``: the running run, if any, with its ``run_id`` so that the
        panel cancels only this run (L-W2c-3); in a pause with its actions.
        ``last``: the last ended run with its validity, reason and valid and
        invalid parts; after its session ended also the totals of the
        evaluation. ``procedure``: the parts of the basic procedure (W-3),
        ``modules``: the additional modules on offer, P, P1 and P2 only with a
        presence sensor. ``blocked``: the reason the start offered now is
        locked, with its text (W-2d, W-2e): without a session the basic
        procedure, in a session the additional module.
        """
        active = next((run for run in self._calibration_runs if run.active), None)
        ended = [run for run in self._calibration_runs if not run.active]
        presence = self.configuration_model.direct_presence
        blocked = self._calibration_start_lock(
            presence_known=not presence or self._calibration_presence() is not None,
        )

        def offered(module):
            # B4a: Raster der Schritte mit den erwarteten Erkennungen.
            return {"id": module.module_id, "label": module.label, "title": module.title,
                    "optional": module.optional,
                    "steps": [{"key": step.key, "reference": step.reference,
                               "expects": list(step.expects)} for step in module.steps]}

        return {
            "active": {**active.status(), "run_id": active.run_id} if active is not None else None,
            "last": {**ended[-1].status(), "totals": None} if ended else self._calibration_last,
            "procedure": [offered(module) for module in calibration.procedure(presence=presence)],
            "modules": [offered(module)
                        for module in calibration.additional_modules(presence=presence)],
            "blocked": self._calibration_lock_status(blocked),
        }

    def _calibration_start_lock(self, *, presence_known):
        """Reason the start offered now is locked, else ``None`` (W-2d, W-2e).

        Without a session the basic procedure is offered; it switches to
        automatic itself and needs a known presence state. Its Gang 1 waits for
        Bereit and the minimum temperature. In a session the additional module
        is offered: locked in every state that would interrupt it at once
        (Betrieb AUS in the resumption window included) and before the session
        first reaches the minimum temperature, when its entry would not count
        (L-G6a-2). The service keeps its behaviour.
        """
        if self.session is None:
            return None if presence_known else "presence_unavailable"
        if not self.session.operation_enabled:
            return "operation_off"
        blocked = self._calibration_blocking(presence_known=presence_known)
        if blocked is None and self.controller.minimum_temperature_reached_at is None:
            return "minimum_temperature_pending"
        return blocked

    def _calibration_lock_status(self, reason):
        if reason is None:
            return None
        if reason in calibration.START_LOCKS:
            text = calibration.START_LOCKS[reason]
            return {"reason": reason, "reason_text": text, "text": (
                "Zusatzmodul starten ist bis zum ersten Erreichen der Mindesttemperatur "
                "gesperrt: vorher zählt kein Eintritt, der Gang des Zusatzmoduls fehlte."
            )}
        text = calibration.INTERRUPTIONS[reason]
        subject = ("Ein jetzt gestartetes Zusatzmodul" if self.session is not None
                   else "Eine jetzt gestartete Kalibrierung")
        return {"reason": reason, "reason_text": text,
                "text": f"{subject} wäre sofort ungültig: {text}."}

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

    @property
    def recognition_parameters(self) -> dict:
        """Learned recognition quantities (shipped parameters until a learned set is supplied)."""
        if self._recognition_parameters is None:
            from .recognition_parameters import shipped_parameters

            self._recognition_parameters = shipped_parameters()
        return self._recognition_parameters

    @recognition_parameters.setter
    def recognition_parameters(self, value: dict) -> None:
        # A new set applies from the next session; the running recognizer keeps its quantities.
        self._recognition_parameters = value

    def _outside_air(self):
        """Current outside air from the configured weather source or sensors (None: not available)."""
        if self.outside_air is None:
            return None
        try:
            return self.outside_air()
        except Exception as error:  # noqa: BLE001 - a weather read must not prevent the recognition
            self.log.error("outside_air", "Außenluft nicht lesbar: %s", error)
            return None

    def environment_changed(self):
        """The bound environment source reported a change (its state listener)."""
        if not self.closed:
            self._archive_environment("source_changed")

    def _archive_environment(self, reason):
        """Archive the bound environment source with the running session.

        At the session start, then only when the measurement time of the
        source changes; a source without a measurement time when one of its
        values changes. A mere update of the HA timestamp is not archived; no
        own interval. Without a binding nothing is archived.
        """
        session = self.session
        if session is None or not self.archive or self.environment is None:
            return
        try:
            snapshot = self.environment()
            if not snapshot or not snapshot.get("configured"):
                return
            payload = plain({
                **{key: value for key, value in snapshot.items() if key != "configured"},
                # display labels are not archived
                "values": [{key: value for key, value in item.items() if key != "label"}
                           for item in snapshot.get("values") or ()],
            })
            measured = payload.get("measurement_time")
            news = (session.session_id, ("measurement_time", measured) if measured is not None
                    else ("values", [(item.get("key"), item.get("value"))
                                     for item in payload["values"]]))
            # the session start is always archived: K-S20 is its value
            if reason != "session_start" and self._environment_saved == news:
                return
            self.archive.append("environment", self._clock(), {"reason": reason, **payload},
                                session.session_id)
        except Exception as error:  # noqa: BLE001 - a weather source must not disturb the regulation
            self.log.error("environment", "Umgebung nicht gespeichert: %s", error)
            return
        self._environment_saved = news

    def _archive_recognition(self, kind, payload, session_id):
        """One recognition record of the normal profile (state change, rejected candidate, episode)."""
        try:
            self.archive.append(kind, self._clock(), payload, session_id)
        except (ValueError, OverflowError) as error:
            # A malformed diagnostic is not an actuator input.
            self.log.error(
                "recognition_record_invalid",
                "Erkennungseintrag konnte nicht gespeichert werden: %s", error,
            )

    def _sync_detector(self):
        session_id = self.session.session_id if self.session else None
        if session_id == self._detector_session:
            return
        # Deliver the preceding session's canonical end before a new session
        # replaces its proxy evidence with an initial availability report.
        self._publish_controller_events()
        finished, finished_session = self.detector, self._detector_session
        self._detector_session = session_id
        self._detector_infusion_events.clear()
        if finished is not None and finished_session and self.recognition_finished is not None:
            try:
                self.recognition_finished(finished_session, finished)
            except Exception as error:  # learning must never disturb the regulation
                self.log.error("recognition_learning", "Nachlernen nicht gestartet: %s", error)
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
            self._archive_environment("session_start")
        recorder = recognition_archive.RecognitionArchive()
        observed_recognizer = []

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
            # Both profiles keep the state changes and the rejected candidates.
            if self.archive and observed_recognizer:
                change = recorder.state_change(trace)
                if change is not None:
                    self._archive_recognition(recognition_archive.STATE, change, session_id)
                for candidate in recorder.rejected_candidates(observed_recognizer[0].decisions):
                    self._archive_recognition(
                        recognition_archive.REJECTED_CANDIDATE, candidate, session_id
                    )

        self.detector = (
            ClimateRecognizer(
                self.recognition_parameters,
                self.session.started_at,
                positions=tuple(
                    Position(position)
                    for position in self.configuration_model.measurement_positions
                ),
                observer=observed,
                outside=self._outside_air(),
            )
            if self.session
            else None
        )
        if self.detector:
            observed_recognizer.append(self.detector)
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
        # a session that ended at this advance releases its recognizer now (and its learning starts)
        self._sync_detector()
        if self.device:
            self.device.refresh(now)
        if self.device:
            await self.device.apply(now)
        self._advance_calibration(now)
        self._advance_notifications(now)
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
        """Book received recognition chronologically, without actuator I/O.

        The recognizer evaluates every measurement up to ``through`` (without
        ``through`` itself when ``include_current`` is false, so that same-time
        channels of one raster stay together).  Each event is booked at its
        confirmation; the controller is first advanced to that time.
        """
        self._sync_detector()
        if not self.detector:
            return
        recognizer = self.detector
        session_id = self.session.session_id
        if not include_current:
            through = through - timedelta.resolution

        def current():
            return (self.detector is recognizer and self.session is not None
                    and self.session.session_id == session_id)

        def detected(detection, origin=None):
            if not current():
                return
            confirmed = detection.trace_at or detection.detected_at
            if self.controller._last_at is None or confirmed >= self.controller._last_at:
                self.controller.advance(confirmed, inclusive_confirmation=False)
            if not current():
                return
            received_at = self._clock()
            effective = max(detection.effective_at, self.session.started_at)
            trace_at = max(confirmed, effective)
            booking_at = max(trace_at, self.controller._last_at or trace_at)
            event = Event(
                recognition_event_id(session_id, detection.kind, effective),
                session_id,
                detection.kind,
                effective,
                max(received_at, booking_at),
                booking_at=booking_at,
                trace_at=trace_at,
                door_close_evidence=detection.door_close_evidence,
            )
            # L-B4-4: state, next best explanation and their ratio at the decision,
            # read from the recognizer's decision log like for rejected candidates.
            explained = (
                recognition_archive.decision(detection, recognizer.detections, recognizer.decisions)
                if self.archive and origin is None else None
            ) or {}
            try:
                self._process_event(event, defer_confirmation=True, recognition_at=trace_at)
            except ValueError as error:
                # A physically recognized event that the gang model cannot
                # take in its current state is kept for the analysis only.
                self.log.info(
                    "detection_rejected", "Erkanntes Ereignis nicht übernommen: %s (%s).",
                    EVENTS.get(detection.kind, "Erkennungssignal"), error,
                )
                if self.archive:
                    self.archive.append("detection_rejected", received_at, {
                        "event": event, "reason": str(error), "probability": detection.probability,
                        **explained,
                    }, session_id)
                return
            if detection.kind == Kind.INFUSION:
                self._detector_infusion_events[id(detection)] = (detection, event.event_id)
            self.log.info(
                "detection",
                "Erkanntes Ereignis: %s; zugeordnete Zeit: %s.",
                EVENTS.get(detection.kind, "Erkennungssignal"),
                detection.effective_at,
            )
            if self.archive:
                self.archive.append("detection", received_at, {
                    "event": event,
                    "channels": detection.channels,
                    "trace_at": trace_at,
                    "probability": detection.probability,
                    **explained,
                    **({"origin": origin} if origin is not None else {}),
                }, session_id)

        def rejected_opening(rejected):
            # G9, G9b: the gang model may take an episode of rejected openings as
            # one door operation; opening and closing are booked at its end.
            door = False
            if current():
                if (self.controller._last_at is None
                        or rejected.decided_at >= self.controller._last_at):
                    self.controller.advance(rejected.decided_at, inclusive_confirmation=False)
                door = current() and self.controller.rejected_opening_is_door(
                    rejected.opened_at, rejected.closed_at, rejected.close_candidate_at,
                    rejected.ended_at, rejected.decided_at)
            if self.archive:
                self._archive_recognition(
                    recognition_archive.EPISODE,
                    recognition_archive.episode_record(rejected, door), session_id,
                )
            if not door:
                return
            detected(Detection(Kind.DOOR_OPEN, rejected.opened_at, rejected.decided_at, rejected.channels,
                               trace_at=rejected.decided_at), origin="presence")
            detected(Detection(Kind.DOOR_CLOSE, rejected.closed_at, rejected.decided_at, rejected.channels,
                               trace_at=rejected.decided_at,
                               door_close_evidence=rejected.door_close_evidence), origin="presence")

        def infusion_updated(update):
            if not current():
                return
            source = self._detector_infusion_events.get(id(update.confirmed_detection))
            if source is None or source[0] is not update.confirmed_detection:
                return
            if not self.controller.record_infusion_end(session_id, source[1], update.end):
                return
            if self.archive:
                self.archive.append("infusion_update", self._clock(),
                                    {"source_event_id": source[1], "end": update.end}, session_id)

        # the heater feedback tells the recognition whether a closing shows as recovery
        recognizer.report_heating(self._clock(), self.controller.feedback)
        recognizer.advance(through, on_detection=detected, on_infusion_update=infusion_updated,
                           on_rejected_opening=rejected_opening)
        processed = recognizer.processed_until
        if processed is not None and current():
            if self.controller._last_at is None or processed >= self.controller._last_at:
                self.controller.advance(processed, finish_confirmation_batch=True)
            self.controller.discard_recognition_context_before(processed)
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
                    for run in self._calibration_runs:
                        run.observe_presence(
                            report.effective_at, self._calibration_state(report),
                        )
            if direct or selected:
                # Catch-up recognition must use the report already received by
                # this callback, without advancing past queued device inputs.
                with self.controller.confirmation_batch(self._clock):
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

    async def label_session(self, session_id, test, *, actor: ActorRef | None = None):
        """Testbetrieb (G25): eine abgeschlossene Sitzung kennzeichnen oder die Kennzeichnung
        aufheben. Sie zählt dann in Summen, nicht in Mitteln, Vergleichen und Rekorden."""
        self._require_open()
        if self.archive is None:
            raise ValueError("Kein Sitzungsarchiv verfügbar.")
        result = await self.archive.label_session(
            session_id, test, self._clock(), actor.user_id if actor else None,
        )
        self.log.info("session_labelled", "Testbetrieb für Sitzung %s: %s.",
                      session_id, "ja" if result else "nein")
        return result

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
            if signature["after_run"] is not None:
                for counter in ("remaining_seconds", "elapsed_seconds", "accounted_at"):
                    signature["after_run"].pop(counter)
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

    async def end_gang(self, gang_id, *, actor: ActorRef | None = None):
        """Den laufenden Gang von Hand beenden (GE1); Folgen wie bei einem erkannten Gangende."""
        async with self.serialized():
            self._require_open()
            if any(run.active for run in self._calibration_runs):
                # GE1a: die Kalibrierung prüft gegen das Gangmodell.
                raise ValueError(
                    "Während einer Kalibrierung lässt sich ein Gang nicht von Hand beenden."
                )
            now = self._clock()
            ended = self.controller.end_gang(gang_id, now)
            self._record_notification_participant(actor)
            self.log.info(
                "gang_ended_manually",
                "Gang von Hand beendet; regulärer Folgeablauf wird fortgesetzt.",
            )
            if self.archive:
                self.archive.append(
                    "manual_gang_end",
                    now,
                    {
                        "gang_id": gang_id,
                        "ended_at": now,
                        "confirmation": ended.confirmation.value,
                        "user_id": actor.user_id if actor else None,
                    },
                    ended.session_id,
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

    async def start_calibration(self, *, actor: ActorRef, module=None, resolutions=None):
        """Start the basic procedure or one additional module (V31, W-3).

        Without a session this starts an automatic session with the basic
        procedure: P1 at once while heating, Gang 1 after Bereit, P2 after
        Gang 1, Gang 2 and the optional Gang 3 after „Nächster Gang“. An
        additional module needs a running session and starts once no gang is
        active. Instructions go to the starting user's Companion targets with
        replies. Only one run is active at a time.
        """
        async with self.serialized():
            self._require_open()
            if resolutions is not None:
                self.notification_resolutions = resolutions
            targets = calibration.reply_targets(
                actor.user_id if actor else None,
                self.configuration.notification_assignments,
                self.notification_resolutions,
            )
            if not targets:
                raise ValueError(
                    "Kein Companion-Ziel mit Antworten für den startenden Benutzer"
                )
            if self.notification_transport is None or self.notification_entry_id is None:
                raise ValueError("Benachrichtigungstransport ist nicht verfügbar")
            if any(run.active for run in self._calibration_runs):
                raise ValueError("Es läuft bereits eine Kalibrierung")
            presence = self.configuration_model.direct_presence
            now = self._clock()
            if module is not None:
                if self.session is None:
                    raise ValueError("Ein Zusatzmodul startet in einer laufenden Sitzung")
                selected = (calibration.additional_module(module, presence=presence),)
                kind = "module"
            else:
                if self.session is not None:
                    raise ValueError(
                        "Die Kalibrierung startet ohne laufende Sitzung; "
                        "in einer laufenden Sitzung startet ein Zusatzmodul"
                    )
                selected, kind = calibration.procedure(presence=presence), "procedure"
                if self.controller.control_mode != "automatic":
                    self._set_control_mode("automatic", at=now, received_at=now)
                self._apply_operation(True, actor=actor, received_at=now)
            run = calibration.CalibrationRun(
                uuid4().hex, kind, selected, actor.user_id, self.session.session_id, now,
                has_presence=presence,
                presence=[(now, self._calibration_presence())] if presence else [],
            )
            self._calibration_runs.append(run)
            self._calibration_targets[run.run_id] = targets
            await self._cycle()
            return {
                "run_id": run.run_id,
                "kind": kind,
                "session_id": run.session_id,
                "modules": [item.module_id for item in selected],
                "targets": [
                    {"transport": target.transport, "endpoint": target.endpoint}
                    for target in targets
                ],
            }

    async def cancel_calibration(self, *, actor: ActorRef | None = None, run_id: str | None = None):
        """Cancel the running calibration; with ``run_id`` only that run (L-W2c-3).

        The service passes no ``run_id`` and cancels whatever runs at the time
        of the request. The panel names the run it shows, so a late click after
        that run ended never ends a run started meanwhile.
        """
        async with self.serialized():
            self._require_open()
            run = next((item for item in self._calibration_runs if item.active), None)
            if run is None:
                raise ValueError("Es läuft keine Kalibrierung")
            if run_id is not None and run.run_id != run_id:
                raise ValueError("Die angezeigte Kalibrierung läuft nicht mehr")
            self._send_calibration(run, run.interrupt(
                self._clock(), "cancelled", user_id=actor.user_id if actor else None,
            ))
            await self._cycle()
            return {"run_id": run.run_id}

    async def answer_calibration_pause(self, action, *, run_id, actor: ActorRef | None = None):
        """Answer the pause of the shown run from the panel (W-3): „Nächster Gang“
        or „Beenden“, as its message offers them. It never operates the sauna."""
        async with self.serialized():
            self._require_open()
            run = next((item for item in self._calibration_runs if item.active), None)
            if run is None:
                raise ValueError("Es läuft keine Kalibrierung")
            if run.run_id != run_id:
                raise ValueError("Die angezeigte Kalibrierung läuft nicht mehr")
            disposition, instructions = run.answer_pause(
                action, self._clock(), actor.user_id if actor else None,
            )
            if disposition != "accepted":
                raise ValueError("Die Kalibrierung wartet nicht auf diese Antwort")
            self._send_calibration(run, instructions)
            await self._cycle()
            return {"run_id": run.run_id, "action": action}

    async def calibration_reply(self, association):
        """Pass a reply with a calibration action to its run; it never operates the sauna."""
        reply = association.reply
        action = reply.reply_key.value if reply.reply_key is not None else None
        if not calibration.is_action(action) or association.notification is None:
            return None
        async with self.serialized():
            if self.closed:
                return None
            run = next((item for item in self._calibration_runs if item.owns(action)), None)
            if run is None:
                return "unknown"
            disposition, instructions = run.reply(
                action, reply.observed_at or self._clock(), reply.actor.user_id,
                message_id=association.notification.message_id,
                restart_allowed=not any(
                    item.active for item in self._calibration_runs if item is not run
                ),
            )
            self._send_calibration(run, instructions)
            await self._cycle()
            return disposition

    @staticmethod
    def _calibration_state(report):
        if report is None or not report.available:
            return None
        return {"present": True, "absent": False}.get(report.occupancy)

    def _calibration_presence(self):
        """Presence state a run starting now records: the sensor's last report."""
        return self._calibration_state(
            self.presence.external.get(self.configuration.bindings.values.get("presence"))
        )

    def _advance_calibration(self, now):
        """Send due calibration messages; evaluate the runs of an ended session.

        An interrupting state ends a run before it sends anything further
        (W-2a, W-2b).
        """
        if not self._calibration_runs:
            return
        session = self.session
        for run in tuple(self._calibration_runs):
            if session is None or run.session_id != session.session_id:
                self._finish_calibration(run, now)
                continue
            reason = self._calibration_interruption(run, session)
            if reason is not None:
                self._send_calibration(run, run.interrupt(now, reason))
            else:
                # W-3: Gang 1 begins after Bereit, at the earliest once the
                # session reached the minimum temperature (W-2e).
                self._send_calibration(run, run.advance(
                    now,
                    ready=(self.controller.phase == "bereit"
                           and self.controller.minimum_temperature_reached_at is not None),
                    gang_active=session.timeline.active is not None,
                ))
            self._archive_calibration(run)

    def _calibration_interruption(self, run, session):
        """Interrupting state at this cycle end; every path into it ends with a cycle.

        A manual heater selection holds while its override lasts. Presence is
        the state the sensor last reported to the run, at its start the
        current one; each report ends with a cycle.
        """
        if not session.operation_enabled:
            return "operation_off"
        return self._calibration_blocking(
            presence_known=not run.has_presence or run.presence[-1][1] is not None,
        )

    def _calibration_blocking(self, *, presence_known):
        """Manual mode, a manual heater selection or unknown presence (L-W2b-4/5).

        Each ends a run at the next cycle end, a run started in it at once;
        the same check locks the module start in the panel (W-2d).
        """
        if self.controller.control_mode == "manual":
            return "manual_mode"
        if self.controller.heater_override is not None:
            return "heater_override"
        if not presence_known:
            return "presence_unavailable"
        return None

    def _finish_calibration(self, run, now):
        """Doors and infusions of the valid parts are evaluated once the session has ended (V04).

        A run still active at the session end is interrupted; only a part
        running then becomes invalid (W3-2). Without a valid part the run only
        reports that it is invalid (W-2a).
        """
        instructions = run.interrupt(now, "session_ended")
        session = next(
            (item for item in reversed(self.controller.completed_sessions)
             if item.session_id == run.session_id),
            None,
        )
        totals = None
        if run.evaluable and session is not None:
            result = self._evaluate_calibration(run, session, now)
            run.records.append(
                {"event": "evaluation", "run_id": run.run_id, "at": now, "result": result}
            )
            # The notice of an invalid part stays; the evaluation follows it.
            notice = instructions if run.invalid_execution is not None else []
            instructions = [*notice, run.evaluation_message(
                now, calibration.evaluation_summary(result),
            )]
            totals = result["totals"]
        self._calibration_last = {**run.status(), "totals": totals}
        self._send_calibration(run, instructions)
        self._archive_calibration(run)
        self._calibration_runs.remove(run)
        self._calibration_targets.pop(run.run_id, None)

    def _evaluate_calibration(self, run, session, now):
        timeline = session.timeline
        retracted = {gang.gang_id for gang in timeline.retracted}
        gangs = [
            {"start": gang.started_at, "confirmed": gang.confirmed_at,
             "end": gang.ended_at, "end_reason": gang.end_reason,
             "infusions": len(gang.infusion_events)}
            for gang in (*timeline.completed, *([timeline.active] if timeline.active else []))
            if gang.gang_id not in retracted
        ]
        events = [(event.kind.value, event.effective_at) for event in timeline.processed
                  if event.event_id.startswith("recognition:")]
        until = session.ended_at or now
        decisions = [(item.at, item.heat, item.reason) for item in self.controller.decisions
                     if item.session_id == session.session_id]
        heating = [(item.started_at, item.ended_at) for item in session.heating.intervals]
        return calibration.evaluate(
            run.valid_steps, presence=run.presence, events=events, gangs=gangs,
            gang_heat=calibration.gang_heat_intervals(decisions, heating, until),
            until=until, has_presence=run.has_presence,
        )

    def _send_calibration(self, run, instructions):
        """Register each copy now; its handoff runs outside the control lock."""
        transport = self.notification_transport
        if not instructions or transport is None or self.notification_entry_id is None:
            return
        loop = asyncio.get_running_loop()
        for instruction in instructions:
            for target in self._calibration_targets.get(run.run_id, ()):
                message_id = uuid4().hex
                payload = {"title": instruction.title, "message": instruction.message}
                if instruction.actions:
                    payload["data"] = {"actions": run.native_actions(instruction, message_id)}
                envelope = NotificationEnvelope(
                    message_id, f"calibration:{run.run_id}:{instruction.seq}",
                    self.notification_entry_id, target, payload, run.session_id,
                )
                task = loop.create_task(self._dispatch_calibration(
                    transport, run, instruction, envelope, transport.prepare_handoff(envelope),
                ))
                self._calibration_tasks.add(task)
                task.add_done_callback(self._calibration_tasks.discard)

    async def _dispatch_calibration(self, transport, run, instruction, envelope, prepared):
        handoff = await transport.dispatch(prepared)
        async with self._lock:
            if self.closed:
                return
            run.handed_off(instruction, envelope.message_id, envelope.target,
                           handoff.accepted, handoff.evidence)
            self._archive_calibration(run)

    def _archive_calibration(self, run):
        records = run.take_records()
        if self.archive is None:
            return
        for record in records:
            self.archive.append("calibration", record["at"], record, run.session_id)

    # -- BN: Benachrichtigungen der Sauna ------------------------------------
    # Der Ablauf (core/notification_rules.py) liest den Zustand nur. Antworten
    # mit Wirkung gehen über end_gang und set_operation mit der Steuerberechtigung
    # des Antwortenden. Ohne geladene Einstellungen (``notifier`` None) ruht er.

    def _init_notifications(self):
        self.notifier = None
        # callable(targets) -> frozenset der Ziele, deren Companion-Gerät „home“ meldet
        self.notification_presence = None
        self.save_notification_settings = None
        self._bn_tasks = set()
        self._bn_participants = (None, frozenset())
        self._bn_starters = {}
        self._bn_last_present = None

    def enable_notifications(self, settings=None, zone=None):
        """Start the occasions with the loaded settings (HA setup, preview, tests)."""
        self.notifier = notification_rules.Notifier(settings, zone)

    @property
    def notification_settings(self):
        return self.notifier.settings if self.notifier is not None else None

    async def set_notification_settings(self, data):
        """Change occasions and values; effective at once, also during a session."""
        async with self._lock:
            self._require_open()
            if self.notifier is None:
                raise ValueError("Benachrichtigungen sind nicht verfügbar")
            settings = notification_rules.replace_settings(self.notifier.settings, data)
            self.notifier.configure(settings)
            if self.save_notification_settings is not None:
                self.save_notification_settings(settings.as_dict())
            return settings.as_dict()

    def _user_targets(self, user_id, *, interactive=False):
        if user_id is None:
            return frozenset()
        references = {
            reference
            for assignment in self.configuration.notification_assignments
            if assignment.user_id == user_id
            for reference in assignment.recipients
        }
        return frozenset(
            item.recipient.target for item in self.notification_resolutions
            if item.reference in references and item.recipient is not None
            and item.recipient.capability.sendable
            and (not interactive or item.recipient.capability.interactive)
        )

    def _assigned_targets(self):
        users = {assignment.user_id for assignment in self.configuration.notification_assignments}
        return frozenset().union(*(self._user_targets(user) for user in users)) if users else frozenset()

    def _interactive(self, target):
        return any(
            item.recipient is not None and item.recipient.target == target
            and item.recipient.capability.interactive
            for item in self.notification_resolutions
        )

    async def send_test_notification(self, *, actor, resolutions=None):
        """Testnachricht an die eigenen Ziele des Benutzers (BN-1, Anlass T)."""
        async with self._lock:
            self._require_open()
            if resolutions is not None:
                self.notification_resolutions = resolutions
            if self.notifier is None or self.notification_transport is None:
                raise ValueError("Benachrichtigungen sind nicht verfügbar")
            user_id = actor.user_id if actor else None
            targets = self._user_targets(user_id)
            if not targets:
                raise ValueError("Diesem Benutzer ist kein Benachrichtigungsziel zugeordnet")
            message = self.notifier.test(self._clock(), user_id)
            self._send_bn(message, targets, "eigene Ziele")
            self._archive_bn()
            return {"seq": message.seq, "targets": len(targets)}

    def notification_status(self, user_id):
        """Test status of this user and the timer state of the session."""
        if self.notifier is None:
            return None
        return {
            "settings": self.notifier.settings.as_dict(),
            "factory_deviations": notification_rules.factory_deviations(self.notifier.settings),
            "test": dict(self.notifier.tests[user_id]) if user_id in self.notifier.tests else None,
            "targets": len(self._user_targets(user_id)),
            "timer": self.notifier.timer,
        }

    def _notification_facts(self, now):
        session, controller, device = self.session, self.controller, self.device
        upper = None
        upper_failed = False
        heating_against_off = False
        if device is not None:
            measurement = device.regulation_measurement(now)
            if measurement is not None and measurement.position == Position.UPPER:
                upper = measurement
            state = device.measurement_status(now).get("upper_temperature", {}).get("state")
            upper_failed = state in ("unavailable", "stale")
            faults = device.faults
            heating_against_off = (
                faults.get("heater_still_heating") == "confirmed"
                or (faults.get("heater_feedback_mismatch") == "confirmed"
                    and device.command is False and controller.contactor is True)
            )
        report = (self.presence.external.get(self.configuration.bindings.values.get("presence"))
                  if self.configuration_model.direct_presence else None)
        presence = None
        if report is not None and report.available and report.occupancy in ("present", "absent"):
            presence = (report.occupancy, report.effective_at)
            if report.occupancy == "present":
                self._bn_last_present = max(
                    self._bn_last_present or report.effective_at, report.effective_at)
        common = {
            "now": now, "contactor": controller.contactor,
            "upper_temperature_c": upper.value if upper is not None else None,
            "upper_failed": upper_failed, "heating_against_off": heating_against_off,
            "presence": presence,
        }
        if session is None:
            return notification_rules.Facts(**common)
        timeline = session.timeline
        active = timeline.active
        acting = controller.acting_gang
        gang = None
        if active is not None:
            gang = notification_rules.GangFact(
                active.gang_id, active.started_at, timeline.gang_count + 1,
                acting is not None and acting.gang_id == active.gang_id,
            )
        ends = [item.ended_at for item in timeline.completed
                if item.ended_at is not None and item.confirmation == Confirmation.CONFIRMED]
        return notification_rules.Facts(
            **common,
            session_id=session.session_id,
            session_started_at=timeline.session_started_at,
            operation_enabled=session.operation_enabled,
            ready_marks=tuple((mark.at, mark.ready) for mark in session.ready_history),
            target_c=controller.target_temperature,
            gang=gang,
            gang_count=timeline.gang_count,
            last_gang_end=max(ends, default=None),
            door_open=timeline.door == Door.OPEN,
            door_events=tuple(
                (event.kind.value, event.effective_at) for event in timeline.processed
                if event.kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE)
            ),
            last_present_at=self._bn_last_present,
            heating=tuple((item.started_at, item.ended_at) for item in session.heating.intervals),
            switch_offs=notification_rules.upper_switch_offs(
                (item.at, item.heat, item.reason) for item in controller.decisions
                if item.session_id == session.session_id
            ),
            temperature=(upper.received_at, upper.value) if upper is not None else None,
        )

    def _advance_notifications(self, now):
        """Send the due occasions of this cycle; never part of the control decision."""
        if self.notifier is None or self.closed:
            return
        try:
            session = self.session
            if session is not None:
                participants = self.notification_recipients
                if participants is not None:
                    self._bn_participants = (session.session_id, participants.targets)
                if session.session_id not in self._bn_starters:
                    # Ziele des startenden Benutzers; ohne Benutzer gilt die Anwesenheit.
                    self._bn_starters = {session.session_id: (
                        participants.targets if participants is not None else frozenset())}
            else:
                self._bn_last_present = None
            messages, ended = self.notifier.advance(self._notification_facts(now))
            if ended is not None:
                self._bn_session_end(now, ended)
            for message in messages:
                targets, basis = self._bn_targets(message)
                self._send_bn(message, targets, basis)
            self._archive_bn()
        except Exception as error:  # noqa: BLE001 - Benachrichtigungen dürfen den Zyklus nie stören.
            self.log.logger.error("Benachrichtigung fehlgeschlagen: %s", error,
                                  extra={"sauna_event": "notification_failed"})

    def _bn_targets(self, message):
        audience = message.audience
        if audience == "assigned":
            return self._assigned_targets(), "alle Zugeordneten"
        if audience == "participants":
            session_id, targets = self._bn_participants
            return (targets if session_id == message.session_id else frozenset()), "Teilnehmer"
        if audience == "timer":
            started = self._bn_starters.get(message.session_id) or frozenset()
            if started:
                return started, "startender Benutzer"
            assigned = self._assigned_targets()
            present = (frozenset(self.notification_presence(assigned))
                       if self.notification_presence is not None else frozenset())
            if present:
                return present, "anwesende Geräte"
            return assigned, "alle Zugeordneten (kein Gerät anwesend)"
        return frozenset(), "keine"

    def _bn_session_end(self, now, session_id):
        """Sauna aus (E): text with the key figures of the archived session."""
        if self.notifier is None or not self.notifier.settings.enabled("session_end"):
            return
        _, participants = self._bn_participants
        if self._bn_participants[0] != session_id:
            participants = frozenset()
        heater_off = (False if self.controller.contactor is True
                      else True if self.controller.contactor is False else None)
        self._bn_starters.pop(session_id, None)

        async def compose():
            figures = None
            archive = self.archive
            if archive is not None:
                try:
                    await archive.flush()
                    values = self.configuration.parameters.values
                    result = await asyncio.to_thread(
                        archive.session_key_figures, session_id,
                        volume_m3=values.get("cabin_volume_m3"),
                        price_eur_per_kwh=values.get("electricity_price_eur_per_kwh"),
                    )
                    figures = result["figures"] if result else None
                except Exception as error:  # noqa: BLE001 - ohne Kennzahlen bleibt die Kopfzeile.
                    self.log.logger.info("Kennzahlen nicht verfügbar: %s", error,
                                         extra={"sauna_event": "notification_figures"})
            async with self._lock:
                if self.closed or self.notifier is None:
                    return
                message = self.notifier.session_end(
                    self._clock(), session_id,
                    notification_rules.summary(figures, heater_off=heater_off),
                )
                if message is not None:
                    self._send_bn(message, participants, "Teilnehmer")
                    self._archive_bn()

        task = asyncio.get_running_loop().create_task(compose())
        self._bn_tasks.add(task)
        task.add_done_callback(self._bn_tasks.discard)

    def _send_bn(self, message, targets, basis):
        """Register each copy now; its handoff runs outside the control lock."""
        self.notifier.addressed(message, len(targets), basis)
        transport = self.notification_transport
        if not targets or transport is None or self.notification_entry_id is None:
            return
        loop = asyncio.get_running_loop()
        for target in sorted(targets, key=lambda item: (item.transport, item.endpoint)):
            message_id = uuid4().hex
            payload = {"title": message.title, "message": message.text}
            if message.actions and self._interactive(target):
                payload["data"] = {"actions": [
                    {"action": f"{notification_rules.ACTION_PREFIX}{message_id}_{message.seq}_{kind.upper()}",
                     "title": notification_rules.ACTION_LABELS[kind]}
                    for kind in message.actions
                ]}
            envelope = NotificationEnvelope(
                message_id, f"notification:{message.seq}", self.notification_entry_id,
                target, payload, message.session_id,
            )
            try:
                prepared = transport.prepare_handoff(envelope)
            except Exception as error:  # noqa: BLE001 - ein Ziel hindert die anderen nicht.
                self.notifier.handed_off(message, {"transport": target.transport,
                                                   "endpoint": target.endpoint},
                                         False, f"{type(error).__name__}: {error}", message_id)
                continue
            task = loop.create_task(self._dispatch_bn(transport, message, envelope, prepared))
            self._bn_tasks.add(task)
            task.add_done_callback(self._bn_tasks.discard)

    async def _dispatch_bn(self, transport, message, envelope, prepared):
        try:
            handoff = await transport.dispatch(prepared)
            accepted, evidence = handoff.accepted, handoff.evidence
        except Exception as error:  # noqa: BLE001 - die Übergabe wird als Fehlschlag protokolliert.
            accepted, evidence = False, f"{type(error).__name__}: {error}"
        async with self._lock:
            if self.closed or self.notifier is None:
                return
            self.notifier.handed_off(
                message, {"transport": envelope.target.transport,
                          "endpoint": envelope.target.endpoint},
                accepted, evidence, envelope.message_id,
            )
            self._archive_bn()

    def _archive_bn(self):
        if self.notifier is None:
            return
        records = self.notifier.take_records()
        if self.archive is None:
            return
        for record in records:
            self.archive.append("notification", record["at"], record, record["session_id"])

    async def notification_reply(self, association, *, permitted):
        """Apply a reply to a sauna notification (BN).

        Confirmations change only the notification state. „Gang beenden“ and
        „Ausschalten“ use end_gang and set_operation with the replying user, and
        only with that user's control permission; a reply that no longer applies
        stays without effect and is recorded.
        """
        reply = association.reply
        action = reply.reply_key.value if reply.reply_key is not None else None
        if not notification_rules.is_action(action) or association.notification is None:
            return None
        try:
            _, seq, kind = action[len(notification_rules.ACTION_PREFIX):].split("_", 2)
            seq, kind = int(seq), kind.lower()
        except ValueError:
            return "unknown"
        at = reply.observed_at or self._clock()
        user_id = reply.actor.user_id
        async with self._lock:
            if self.closed or self.notifier is None:
                return None
            outcome = self.notifier.reply(
                seq, kind, at, user_id,
                current_session=self.session.session_id if self.session else None,
            )
            message = self.notifier.message(seq)
            if outcome != "effect":
                self.notifier.replied(seq, kind, at, user_id, outcome)
                self._archive_bn()
                return outcome
            if not permitted:
                self.notifier.replied(seq, kind, at, user_id, "no_permission")
                self._archive_bn()
                return "no_permission"
        actor = ActorRef(user_id, "notification_reply")
        try:
            if kind == notification_rules.END_GANG:
                await self.end_gang(message.gang_id, actor=actor)
            else:
                if (self.session is None or self.session.session_id != message.session_id
                        or not self.session.operation_enabled):
                    raise ValueError("Der Betrieb ist bereits aus.")
                await self.set_operation(False, actor=actor)
            outcome, detail = "effect", None
        except (ValueError, RuntimeError) as error:
            outcome, detail = "late", f"wirkungslos: {error}"
        async with self._lock:
            if self.notifier is not None and not self.closed:
                self.notifier.replied(seq, kind, at, user_id, outcome, detail)
                self._archive_bn()
        return outcome

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
            for run in self._calibration_runs:
                # The transport closes with the integration; only the protocol remains.
                run.interrupt(self._clock(), "integration_unloaded")
            for task in tuple(self._calibration_tasks):
                task.cancel()
            self.log.info("unload", "Sauna-Integration wird beendet; Ofen ausschalten.")
            callbacks, self._cleanup = self._cleanup, []
            for unsubscribe in reversed(callbacks):
                try:
                    unsubscribe()
                except Exception as error:
                    failures.append(error)
            if self.archive is not None:
                try:
                    for run in self._calibration_runs:
                        self._archive_calibration(run)
                except Exception as error:
                    failures.append(error)
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
