"""Laufzeitobjekt einer Sauna-Instanz; keine HA-Serviceaufrufe an Geräte."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from .archive import encoded, plain
from .bindings import Bindings
from .const import CONF_BINDINGS, CONF_PARAMETERS
from .core.button import (
    END_HOLD,
    END_RELEASE,
    HEATER_TOGGLE_OVERRIDE,
    START_STANDARD_PROGRAM,
    ButtonGestures,
)
from .core.controller import Controller, Result
from .core.detector import Detector
from .core.models import Deadline, Session
from .core.parameters import BY_KEY, Parameters
from .core.program_catalog import (
    DEFAULT_PROGRAMS,
    NamedTemperatureProgram,
    load_programs,
    migrate_legacy_programs,
)
from .core.timeline import Door, Event
from .log import LEVELS, SaunaLog
from .presentation import (
    EVENTS,
    PHASES,
    decision_message,
    fault_message,
    fault_resolved,
)
from .settings import program_parameters


@dataclass(frozen=True)
class Configuration:
    bindings: Bindings
    parameters: Parameters
    log_level: str = "INFO"
    control_input_mode: str = "switch"
    button_event_type: str = ""
    program_mode: str = "constant"
    button_program: str = "current"
    temperature_programs: tuple[NamedTemperatureProgram, ...] = DEFAULT_PROGRAMS
    selected_program_id: str | None = None
    control_mode: str = "automatic"

    def __post_init__(self) -> None:
        """Keep a direct legacy-button construction serializable as options."""
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

    @classmethod
    def from_options(cls, options: Mapping) -> Configuration:
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
                "temperature_programs",
                "selected_program_id",
                "control_mode",
            }
        ):
            raise ValueError(
                "Vollständige Entitäts- und Parameterkonfiguration erforderlich"
            )
        level = options.get("log_level", "INFO")
        if level not in LEVELS:
            raise ValueError("Ungültige Protokollstufe")
        mode = options.get("control_input_mode", "switch")
        event_type = options.get("button_event_type", "")
        if mode not in ("button", "switch") or not isinstance(event_type, str):
            raise ValueError("Ungültige Taster- oder Schaltereinstellung")
        values = dict(options[CONF_PARAMETERS])
        if "program_mode" in options:
            program_mode = options["program_mode"]
        else:
            program_mode = (
                "progressive" if "final_temperature_c" in values else "constant"
            )
        # Einmalige Übernahme der alten beidseitigen Bandbreite. Danach werden
        # ausschließlich die neuen, gemeinsam gespeicherten Parameter konsumiert.
        if "cold_tolerance_c" in values or "hot_tolerance_c" in values:
            cold = values.pop("cold_tolerance_c", 0)
            hot = values.pop("hot_tolerance_c", 0)
            values.setdefault("readiness_hysteresis_c", cold + hot)
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
        button_program = options.get("button_program", "current")
        selected_program_id = options.get("selected_program_id")
        control_mode = options.get("control_mode", "automatic")
        program_ids = {program.id for program in programs}
        if (
            program_mode not in ("constant", "progressive")
            or button_program not in {"current", "constant", *program_ids}
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
        )

    def as_options(self) -> dict:
        return {
            CONF_BINDINGS: self.bindings.as_dict(),
            CONF_PARAMETERS: self.parameters.as_dict(),
            "log_level": self.log_level,
            "control_input_mode": self.control_input_mode,
            "button_event_type": self.button_event_type,
            "program_mode": self.program_mode,
            "button_program": self.button_program,
            "temperature_programs": [
                program.as_dict() for program in self.temperature_programs
            ],
            "selected_program_id": self.selected_program_id,
            "control_mode": self.control_mode,
        }


class SaunaRuntime:
    """Serialisiert Kernzugriffe; Timer/Listener können kontrolliert abgemeldet werden.

    Messungen, Bedienung und Timer erreichen denselben serialisierten Kern.
    Gerätebefehle führt ausschließlich der Geräteadapter aus.
    """

    def __init__(
        self, configuration: Configuration, clock: Callable[[], datetime] | None = None
    ) -> None:
        self.configuration = configuration
        self.controller = Controller(
            configuration.parameters,
            program_mode=configuration.program_mode,
            control_mode=configuration.control_mode,
        )
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)
        self._lock = asyncio.Lock()
        self._button = ButtonGestures(
            timedelta(seconds=configuration.parameters.values["button_hold_seconds"])
        )
        self._button_hold_session_id = None
        self.save_configuration = None
        self._cleanup: list[Callable[[], None]] = []
        self._subscribers: set[Callable[[], None]] = set()
        self.closed = False
        self.reconfiguring = False
        self.archive = None
        self._archived_completed = 0
        self.device = None
        self.detector = None
        self._detector_session = None
        self._archive_signature = None
        self._saved_decisions = 0
        self._saved_phase = None
        self._saved_faults = None
        self.log = SaunaLog(id(self), configuration.log_level)
        self._logged_faults = {}

    def set_log_level(self, level):
        self.log.set_level(level)
        self.configuration = replace(self.configuration, log_level=level)
        self.log.info("log_level", "Protokollstufe auf %s gesetzt.", level)

    def _sync_detector(self):
        session_id = self.session.session_id if self.session else None
        if session_id == self._detector_session:
            return
        self._detector_session = session_id

        def observed(trace):
            self.log.debug("detection_check", "Erkennungsprüfung: %s", trace)
            if self.archive:
                self.archive.append("detector_trace", self._clock(), trace, session_id)

        self.detector = (
            Detector(
                self.configuration.parameters,
                self.session.started_at,
                observer=observed,
            )
            if self.session
            else None
        )
        if self.detector and self.device:
            for measurement in sorted(
                self.device.measurements.values(), key=lambda m: m.received_at
            ):
                self.detector.accept(measurement)
                if self.archive:
                    self.archive.append(
                        "source_snapshot", self._clock(), measurement, session_id
                    )

    async def _cycle(self, *, sample=False):
        now = self._clock()
        self._sync_detector()
        if self.detector:
            heating = bool(
                self.device
                and self.device.command is True
                and not self.device.command_error
                and self.device.feedback() is True
                and self.session.operation_enabled
            )
            self.detector.report_heating(heating, now)
        if sample and self.detector:

            def initialize_door():
                if (
                    self.session
                    and self.session.timeline.door == Door.UNKNOWN
                    and self.detector.active_positions
                ):
                    # Dokumentierte Anfangsannahme, kein erfundenes Türereignis.
                    self.controller._session = replace(
                        self.session,
                        timeline=replace(self.session.timeline, door=Door.CLOSED),
                    )

            index = 0

            def detected(detection):
                nonlocal index
                initialize_door()
                i, index = index, index + 1
                event = Event(
                    f"detector:{self.session.session_id}:{detection.effective_at.isoformat()}:{i}:{detection.kind}",
                    self.session.session_id,
                    detection.kind,
                    detection.effective_at,
                    now,
                )
                self.controller.process(event)
                self.log.info(
                    "detection",
                    "Erkanntes Ereignis: %s; zugeordnete Zeit: %s.",
                    EVENTS.get(detection.kind, "Erkennungssignal"),
                    detection.effective_at,
                )
                if self.archive:
                    self.archive.append(
                        "detection",
                        now,
                        {"event": event, "channels": detection.channels},
                        self.session.session_id,
                    )

            self.detector.advance(
                now,
                enabled=self.session.operation_enabled,
                allowed=self.controller.recognition_allowed,
                on_detection=detected,
            )
            initialize_door()
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
            light_selection = self.device.external_light_selection(event, received_at)
            if light_selection is not None:
                self._set_light_override(light_selection, received_at)
            action = self.device.physical_action(event)
            if self.configuration.control_input_mode == "button" and action is not None:
                try:
                    await self._handle_button_event(action, received_at)
                except ValueError as error:
                    self.device.faults["start_rejected"] = str(error)
            elif action is not None:
                try:
                    self._prepare_operation(action, physical=True)
                    self._set_operation(action)
                except ValueError as error:
                    self.device.faults["start_rejected"] = str(error)
            await self._cycle()

    async def reset_protection(self):
        async with self._lock:
            self._require_open()
            if self.session and self.session.operation_enabled:
                raise ValueError("Betrieb vor Quittierung ausschalten")
            if self.device and (
                self.device.contactor_feedback() is not False
                or self.device.feedback() is not False
                or self.device.command_error
            ):
                raise ValueError("Quittierung benötigt bestätigten Ofen-Aus-Zustand")
            self.controller.protection.clear()
            await self._cycle()

    async def start_archive(self, path, entry_id):
        from .archive import Archive

        self.archive = Archive(path, entry_id)
        await self.archive.start()

    def persist_completed_sessions(self):
        if self.archive is None:
            return
        completed = self.controller.completed_sessions[self._archived_completed :]
        for session in completed:
            self.archive.save_session(
                session, self._clock(), self.configuration.as_options()
            )
        self._archived_completed = len(self.controller.completed_sessions)
        if completed and self.device:
            self.device.invalidate_historical_warmup()

    def persist(self):
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
            signature = encoded(signature)
            if signature != self._archive_signature:
                self.archive.save_session(
                    self.session, now, self.configuration.as_options()
                )
                self._archive_signature = signature
        for decision in self.controller.decisions[self._saved_decisions :]:
            self.archive.append(
                "decision",
                decision.at,
                decision,
                self.session.session_id if self.session else None,
            )
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
        for callback in tuple(self._subscribers):
            callback()

    def check_configuration_change(self):
        self._require_open()
        if self.session is not None:
            raise ValueError(
                "Einstellungen können erst nach Ende der Saunasitzung geändert werden"
            )

    def _set_operation(self, enabled, *, preserve_button=False):
        if enabled and self.reconfiguring:
            raise ValueError(
                "Die Grundeinstellungen werden gerade übernommen. Bitte kurz warten."
            )
        if self.device:
            self.device.refresh(self._clock())
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
        result = self.controller.set_operation(enabled, self._clock())
        if enabled:
            self._button_hold_session_id = None
            if not preserve_button:
                self._button = ButtonGestures(
                    timedelta(
                        seconds=self.configuration.parameters.values[
                            "button_hold_seconds"
                        ]
                    )
                )
            if self.device:
                self.device.finish_button_hold_light()
            if self.save_configuration:
                self.save_configuration(self.configuration)
        return result

    def _prepare_operation(self, enabled, *, physical=False):
        if enabled and self.reconfiguring:
            raise ValueError(
                "Die Grundeinstellungen werden gerade übernommen. Bitte kurz warten."
            )
        if (
            physical
            and enabled
            and not (self.session and self.session.operation_enabled)
            and self.controller.control_mode == "manual"
        ):
            if self.session:
                self.controller.finish_session(self._clock(), light_after_run=False)
            self.controller.set_control_mode("automatic")
            self.configuration = replace(self.configuration, control_mode="automatic")
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
            and not (self.session and self.session.operation_enabled)
            and self.configuration.button_program != "current"
        ):
            self._select_button_program()

    def _select_button_program(self):
        """Apply the configured physical-button profile; caller owns ``_lock``."""
        parameters, mode = program_parameters(
            self.configuration.parameters,
            self.configuration.button_program,
            catalog=self.configuration.temperature_programs,
        )
        self.controller.update_temperature_parameters(
            parameters, self._clock(), program_mode=mode, new_program=True
        )
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
        )
        if self.device:
            self.device.values = parameters.values

    def _toggle_button_heater_override(self, now):
        """Toggle the physical-button override while the runtime lock is held."""
        if (
            self.controller.heater_override is not None
            and self.controller.control_mode != "manual"
        ):
            session = self.session
            if (
                self.controller.heater_override is False
                and session is not None
                and session.after_run is not None
            ):
                return self.controller.set_heater_override(True, now)
            return self.controller.set_heater_override(None, now)
        if self.device:
            self.device.refresh(now)
            known = self.device.contactor_feedback()
            current = self.device.command if known is None else known
        else:
            current = self.controller.contactor
        return self.controller.set_heater_override(not bool(current), now)

    async def _handle_button_event(self, event, now):
        """Apply one already-normalized gesture; caller owns ``_lock``."""
        enabled = bool(self.session and self.session.operation_enabled)
        for action in self._button.handle_actions(event, enabled, now):
            await self._apply_button_action(action, now)

    async def _apply_button_action(self, action, now):
        """Apply a semantic button action; caller owns ``_lock``."""
        if action == START_STANDARD_PROGRAM:
            if self._button_hold_session_id is not None:
                if self.device:
                    self.device.finish_button_hold_light(self._button_hold_session_id)
                self._button_hold_session_id = None
            self._prepare_operation(True, physical=True)
            self._set_operation(True, preserve_button=True)
        elif action == HEATER_TOGGLE_OVERRIDE:
            self._toggle_button_heater_override(now)
        elif action == END_HOLD and self.session is not None:
            session_id = self.session.session_id
            self.controller.finish_session(now, light_after_run=False)
            self._button_hold_session_id = session_id
            if self.device:
                self.device.begin_button_hold_light(session_id)
        elif action == END_RELEASE:
            session_id = self._button_hold_session_id
            if session_id is not None:
                self._button_hold_session_id = None
                if self.device:
                    self.device.finish_button_hold_light(session_id)
                self.controller.start_session_light(session_id, now)
            elif self.session is not None:
                self.controller.finish_session(now, light_after_run=True)

    async def set_operation(self, enabled: bool):
        async with self._lock:
            self._require_open()
            self._prepare_operation(enabled)
            result = self._set_operation(enabled)
            await self._cycle()
            return result

    async def set_light_override(self, value):
        """Apply a manual light selection through the serialized runtime path."""
        async with self._lock:
            self._require_open()
            if self.device is None:
                raise ValueError("Lichtsteuerung ist nicht verfügbar")
            now = self._clock()
            self._set_light_override(value, now)
            await self._cycle()

    def _set_light_override(self, value, now):
        """Record a UI or physical light selection within the current lock."""
        self.device.set_light_override(value)
        self.log.info("light_override", "Manuelle Lichtwahl: %s.", value)
        if self.archive:
            self.archive.append(
                "manual_light",
                now,
                {"value": value},
                self.session.session_id if self.session else None,
            )

    async def set_heater_override(self, value: bool | None):
        """Apply a manual heater selection through the serialized runtime path."""
        async with self._lock:
            self._require_open()
            now = self._clock()
            if self.device:
                self.device.refresh(now)
            decision = self.controller.set_heater_override(value, now)
            self.log.info("heater_override", "Manuelle Heizwahl: %s.", value)
            if self.archive:
                self.archive.append(
                    "manual_heater",
                    now,
                    {"value": value, "decision": plain(decision)},
                    self.session.session_id if self.session else None,
                )
            await self._cycle()
            return decision

    async def finish_phase(self, purpose, token):
        async with self._lock:
            self._require_open()
            now = self._clock()
            deadline = self.controller.finish_phase(purpose, token, now)
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

    async def tick(self, _at=None):
        async with self._lock:
            if self.closed:
                return
            if self.configuration.control_input_mode == "button":
                now = self._clock()
                await self._apply_button_action(
                    self._button.advance(
                        now, bool(self.session and self.session.operation_enabled)
                    ),
                    now,
                )
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
            self.log.info("unload", "Sauna-Integration wird beendet; Ofen ausschalten.")
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
                    self.archive.append(
                        "interruption",
                        now,
                        {"reason": "integration_unloaded"},
                        self.session.session_id,
                    )
                    self.archive.save_session(
                        replace(self.session, ended_at=now),
                        now,
                        self.configuration.as_options(),
                    )
                try:
                    await self.archive.close()
                except Exception as error:
                    failures.append(error)
            if failures:
                raise ExceptionGroup(
                    "Abmeldung der Sauna-Laufzeit fehlgeschlagen", failures
                )
