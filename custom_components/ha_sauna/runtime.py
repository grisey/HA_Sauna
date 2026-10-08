"""Laufzeitobjekt einer Sauna-Instanz; keine HA-Serviceaufrufe an Geräte."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

from .archive import encoded, encoded_plain, plain
from .appearance import default_appearance, validate_appearance
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
from .core.defaults import instance_default
from .core.detector import Detector
from .core.models import Position, Session
from .core.contracts import ConsumerEvent, PresenceReport
from .core.presence import PresenceProjection, ProxyPresenceSource
from .core.parameters import BY_KEY, Parameters
from .core.program_catalog import (
    DEFAULT_PROGRAMS,
    NamedTemperatureProgram,
    load_programs,
    migrate_legacy_programs,
)
from .core.temperature_program import temperature_steps as validate_temperature_steps
from .core.timeline import Door, Event, Kind
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
    button_temperature_c: float | None = None
    appearance: dict = field(default_factory=default_appearance)

    def __post_init__(self) -> None:
        """Keep a direct legacy-button construction serializable as options."""
        if self.presence_source not in {"proxy", "ha_presence"}:
            raise ValueError("Ungültige Präsenzquelle")
        object.__setattr__(self, "appearance", validate_appearance(self.appearance))
        if self.temperature_steps is not None:
            steps = validate_temperature_steps(self.temperature_steps)
            minimum = self.parameters.minimum_for("target_temperature_c")
            maximum = BY_KEY["target_temperature_c"].maximum
            if len(steps) > BY_KEY["temperature_gangs"].maximum or any(
                step < minimum or step > maximum for step in steps
            ):
                raise ValueError("Ungültige manuelle Temperaturstufen")
            object.__setattr__(self, "temperature_steps", steps)
        if self.button_temperature_c is None:
            object.__setattr__(
                self,
                "button_temperature_c",
                instance_default(
                    "button_temperature_c", parameters=self.parameters.values
                ),
            )
        Parameters(
            {
                **self.parameters.as_dict(),
                "target_temperature_c": self.button_temperature_c,
            }
        )
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
                "presence_source",
                "temperature_steps",
                "button_temperature_c",
                "appearance",
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
        # Older configurations omitted the optional warning when disabled.
        # Preserve that saved choice; new configurations and resets construct
        # Parameters directly and retain the current factory warning value.
        values.setdefault("mechanical_timer_warning_minutes", 0)
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
        button_temperature_c = options.get(
            "button_temperature_c",
            instance_default("button_temperature_c", parameters=parameters.values),
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
            button_temperature_c,
            options.get("appearance", default_appearance()),
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
            "presence_source": self.presence_source,
            "temperature_steps": self.temperature_steps,
            "button_temperature_c": self.button_temperature_c,
            "appearance": validate_appearance(self.appearance),
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
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)
        self.controller = Controller(
            configuration.parameters,
            program_mode=configuration.program_mode,
            control_mode=configuration.control_mode,
            temperature_steps=configuration.temperature_steps,
            decision_clock=lambda: self._clock(),
            presence_source=configuration.presence_source,
            presence_entity=configuration.bindings.values.get("presence"),
        )
        self._lock = asyncio.Lock()
        self._tick_pending = False
        self._pending_device_inputs = deque()
        self._device_input_pending = False
        self._button = ButtonGestures(
            timedelta(seconds=configuration.parameters.values["button_hold_seconds"])
        )
        self._button_hold_session_id = None
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
        self._archive_signature = None
        self._saved_decisions = 0
        self.presence = PresenceProjection()
        self.presence_adapter = None
        self._saved_consumer_events = 0
        self._consumer_ids = set()
        self.consumer_events = []
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
        # Deliver the preceding session's canonical end before a new session
        # replaces its proxy evidence with an initial availability report.
        self._publish_controller_events()
        self._detector_session = session_id
        self._detector_heating_gates = []
        if session_id:
            now = self._clock()
            self._record_presence(PresenceReport(
                f"presence:proxy:initial:{session_id}", "unknown", "provisional_proxy",
                "proxy", session_id, now, now, True, "no_proxy_evidence",
            ))
        if session_id and self.archive:
            self.archive.append("presence_source", self._clock(), {
                "configured_source": self.configuration.presence_source,
                "effective_source": self.configuration.presence_source,
                "entity_id": self.configuration.bindings.values.get("presence"),
                "external_snapshot": self.presence.external,
            }, session_id)

        def observed(trace):
            self.log.debug("detection_check", "Erkennungsprüfung: %s", trace)
            if self.archive:
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
                    for position in ("upper", "lower")
                    if f"{position}_temperature" in self.configuration.bindings.values
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
        now = self._clock()
        self._deliver_detection(now)
        self._report_detector_availability()
        self.controller.advance(now, finish_confirmation_batch=True)
        if self.device:
            self.device.refresh(now)
        if self.device:
            await self.device.apply(now)
        self.notify()

    def _deliver_detection(self, through, *, include_current=True):
        """Book received recognition chronologically, without actuator I/O."""
        self._sync_detector()
        self._report_detector_heating(self._clock())
        if self.detector:
            detector = self.detector
            session_id = self.session.session_id

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
                received_at = self._clock()
                booking_at = max(detection.trace_at, self.controller._last_at or detection.trace_at)
                event = Event(
                    f"detector:{self.session.session_id}:{detection.effective_at.isoformat()}:{i}:{detection.kind}",
                    self.session.session_id,
                    detection.kind,
                    detection.effective_at,
                    detection.detected_at,
                    booking_at=booking_at,
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

            def before_sample(at):
                # Direct semantic callers may already have advanced the core;
                # canonical booking never moves that leading clock backwards.
                if self.controller._last_at is None or at >= self.controller._last_at:
                    self.controller.advance(at, inclusive_confirmation=False)
                if self.session is None or self.session.session_id != session_id:
                    return False
                return True

            def after_sample(at):
                initialize_door()
                if self.controller._last_at is None or at >= self.controller._last_at:
                    self.controller.advance(at, finish_confirmation_batch=True)

            detector.advance(
                through,
                enabled=self.session.operation_enabled,
                allowed=self.controller.recognition_allowed,
                on_detection=detected,
                heating_intervals=lambda: self.session.heating.intervals,
                heating_gates=self._detector_heating_gates,
                recognition_context=self.controller.recognition_context_at,
                allowed_at=self.controller.recognition_allowed_at,
                before_sample=before_sample,
                after_sample=after_sample,
                include_current=include_current,
                detected_at=self._clock,
            )
            sampled_at = detector.origin + timedelta(seconds=detector.index)
            past = [i for i, (gate_at, _) in enumerate(self._detector_heating_gates)
                    if gate_at <= sampled_at]
            if past:
                self._detector_heating_gates = self._detector_heating_gates[past[-1]:]
            self.controller.discard_recognition_context_before(sampled_at)
            self._sync_detector()
            if self.session is None or self.session.session_id != session_id:
                return
            initialize_door()

    def _report_detector_availability(self):
        if self.detector is None or self.session is None:
            return
        available = bool(self.detector.active_positions)
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
        received_at = self._clock()
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
                entity_id = event.data["entity_id"]
                for role, source in self.configuration.bindings.values.items():
                    if source == entity_id and role in measurement_roles:
                        originals.append(self.device.ingest(
                            role, event.data.get("new_state"), received_at, defer_archive=True,
                        ))
                regulation = self.device.regulation_measurement(received_at)
                # A transport snapshot preserves each input's selected value,
                # including repeated roles at one time. The detector still gets
                # all same-time channels before any control edge is booked.
                item[2] = (
                    regulation.value if regulation is not None else None,
                    self.device.regulation_valid_until(regulation),
                )
            # Recognition consumes the same final received raster as the
            # detector. Thermostat/readiness and control edges retain FIFO.
            with self.controller.recognition_temperature_raster(*packet[-1][2]):
                for event, originals, regulation_input in packet:
                    self._sync_detector()
                    # Original provenance follows the received FIFO, including a
                    # session start/end between same-time measurements. Each value
                    # is archived once, even when a role occurs repeatedly here.
                    if self.archive and self.session:
                        for measurement in originals:
                            self.archive.append(
                                "measurement", received_at, measurement, self.session.session_id,
                            )
                    action_at = max(received_at, self.controller._last_at or received_at)
                    # Book only the received regulation input here. Protection
                    # monitoring and actuator output remain in the wall-time cycle.
                    temperature, valid_until = regulation_input
                    self.controller.set_temperature(temperature, action_at, valid_until=valid_until)
                    entity_id = event.data["entity_id"]
                    for role, source in self.configuration.bindings.values.items():
                        if source == entity_id and role not in measurement_roles:
                            self.device.ingest(role, event.data.get("new_state"), received_at)
                            if role in {"heater", "heater_power", "heater_feedback"}:
                                self.device.report_received_feedback(received_at)
                    action_at = max(received_at, self.controller._last_at or received_at)
                    light_selection = self.device.external_light_selection(event, received_at)
                    if light_selection is not None:
                        self._set_light_override(light_selection, action_at)
                    action = self.device.physical_action(event)
                    if action is not None:
                        self._deliver_detection(action_at)
                    if self.configuration.control_input_mode == "button" and action is not None:
                        try:
                            await self._handle_button_event(
                                action, action_at, received_at=received_at, refresh_device=False,
                            )
                        except ValueError as error:
                            self.device.faults["start_rejected"] = str(error)
                    elif action is not None:
                        try:
                            self._prepare_operation(action, physical=True, at=action_at)
                            self._set_operation(action, at=action_at, refresh_device=False)
                        except ValueError as error:
                            self.device.faults["start_rejected"] = str(error)
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
                if not self.closed and not semantic_event:
                    self._deliver_detection(self._clock())
                command_error = None
                try:
                    yield
                except BaseException as error:
                    command_error = error
                    raise
                finally:
                    # Configuration-only and rejected commands must also deliver
                    # consumed inputs. One current cycle is sufficient; new inputs
                    # remain the responsibility of their waiting handlers.
                    if drained and not self.closed:
                        try:
                            await self._cycle()
                        except BaseException as error:
                            if command_error is not None:
                                note = f"Anschließende Eingangsausgabe fehlgeschlagen: {error!r}"
                                command_error.add_note(note)
                                self.log.error("input_output_failed", "%s", note)
                            else:
                                raise

    async def reset_protection(self):
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
            self.controller.protection.clear()
            await self._cycle()

    async def start_archive(self, path, entry_id):
        from .archive import Archive

        self.archive = Archive(path, entry_id, sessions_only=True)
        await self.archive.start()
        self._consumer_ids = await asyncio.to_thread(self.archive.consumer_event_ids)
        self.archive.append("presence_source", self._clock(), {
            "configured_source": self.configuration.presence_source,
            "effective_source": self.configuration.presence_source,
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
        if session_id is None and self.session is not None:
            session_id = self.session.session_id
        event = ConsumerEvent(
            report.report_id, "occupancy", session_id,
            report.effective_at, report.received_at, report.source_ref, presence=report,
        )
        if self._publish_consumer(event) and self.archive:
            self.archive.append("presence", report.received_at, report, event.session_id)

    async def accept_presence(self, report):
        """Serialize direct occupancy through the same controller and output cycle."""
        async with self._lock:
            if self.closed:
                return
            if self.configuration.presence_source == "ha_presence":
                await self._drain_device_inputs()
                self._deliver_detection(self._clock())
            self._record_presence(report)
            if self.controller.observe_direct_presence(report, self._clock()):
                await self._run_cycle()
            self.notify()

    def _process_event(self, event, *, defer_confirmation=False, recognition_at=None):
        report = (ProxyPresenceSource.present(event)
                  if event.kind in (Kind.PERSON_STRONG, Kind.PERSON_WEAK) else None)
        # The source adapter retains the event identity and both original times.
        result = (self.controller.process_presence(
            report, event, defer_confirmation=defer_confirmation, recognition_at=recognition_at,
        ) if report is not None else self.controller.process(
            event, defer_confirmation=defer_confirmation, recognition_at=recognition_at,
        ))
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
    def presence_status(self):
        return {
            "configured_source": self.configuration.presence_source,
            "effective_source": self.configuration.presence_source,
            "external_activation": "active" if self.configuration.presence_source == "ha_presence" else "observer",
            "current": (self.controller.direct_presence
                        if self.configuration.presence_source == "ha_presence"
                        else self.presence.current),
            "external": self.presence.external,
        }

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
                    self.session, now, self.configuration.as_options()
                )
                self._archive_signature = signature
        self._persist_decisions()

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

    def _set_operation(self, enabled, *, preserve_button=False, at=None, refresh_device=True):
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
        result = self.controller.set_operation(enabled, at)
        self._report_detector_heating(at)
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

    def _prepare_operation(self, enabled, *, physical=False, at=None):
        at = self._clock() if at is None else at
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
                self.controller.finish_session(at, light_after_run=False)
            # The old session still belongs to the old control configuration.
            self.persist_completed_sessions()
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
            self._select_button_program(at)

    def _select_button_program(self, at):
        """Apply the configured physical-button profile; caller owns ``_lock``."""
        parameters, mode = program_parameters(
            self.configuration.parameters,
            self.configuration.button_program,
            catalog=self.configuration.temperature_programs,
        )
        if self.configuration.button_program == "constant":
            parameters = Parameters(
                {
                    **parameters.as_dict(),
                    "target_temperature_c": self.configuration.button_temperature_c,
                }
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

    def _toggle_button_heater_override(self, now, *, refresh_device=True):
        """Toggle the physical-button override while the runtime lock is held."""
        session = self.session
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
            if self.controller.heater_override is False and cooling_or_after_run:
                return self.controller.set_heater_override(True, now)
            return self.controller.set_heater_override(None, now)
        if self.controller.control_mode != "manual" and cooling_or_after_run:
            return self.controller.set_heater_override(True, now)
        if self.device:
            if refresh_device:
                self.device.refresh(now)
            known = self.device.contactor_feedback()
            current = self.device.command if known is None else known
        else:
            current = self.controller.contactor
        return self.controller.set_heater_override(not bool(current), now)

    async def _handle_button_event(self, event, now, *, received_at=None, refresh_device=True):
        """Apply one already-normalized gesture; caller owns ``_lock``."""
        enabled = bool(self.session and self.session.operation_enabled)
        gesture_at = now if received_at is None else received_at
        for action in self._button.handle_actions(event, enabled, gesture_at):
            await self._apply_button_action(action, now, refresh_device=refresh_device)

    async def _apply_button_action(self, action, now, *, refresh_device=True):
        """Apply a semantic button action; caller owns ``_lock``."""
        if action == START_STANDARD_PROGRAM:
            if self._button_hold_session_id is not None:
                if self.device:
                    self.device.finish_button_hold_light(self._button_hold_session_id)
                self._button_hold_session_id = None
            self._prepare_operation(True, physical=True, at=now)
            self._set_operation(
                True, preserve_button=True, at=now, refresh_device=refresh_device,
            )
        elif action == HEATER_TOGGLE_OVERRIDE:
            self._toggle_button_heater_override(now, refresh_device=refresh_device)
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
        async with self.serialized():
            self._require_open()
            self._prepare_operation(enabled)
            result = self._set_operation(enabled)
            await self._cycle()
            return result

    async def set_light_override(self, value):
        """Apply a manual light selection through the serialized runtime path."""
        async with self.serialized():
            self._require_open()
            if self.device is None:
                raise ValueError("Lichtsteuerung ist nicht verfügbar")
            now = self._clock()
            self._set_light_override(value, now)
            await self._cycle()

    def _set_light_override(self, value, now):
        """Record a UI or physical light selection within the current lock."""
        self.device.set_light_override(value, at=now)
        self.log.info("light_override", "Manuelle Lichtwahl: %s.", value)
        if self.archive:
            self.archive.append(
                "manual_light",
                now,
                {"value": value},
                self.session.session_id if self.session else None,
            )

    async def set_heater_override(self, value: bool | None, *, manual_only=False):
        """Apply a manual heater selection through the serialized runtime path."""
        async with self.serialized():
            self._require_open()
            if manual_only and self.configuration.control_mode != "manual":
                raise ValueError("Die Betriebsart wurde inzwischen geändert.")
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
        async with self.serialized():
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

    async def finish_session_gap(self, token):
        """Permanently finish the paused session selected by its gap token."""
        async with self.serialized():
            self._require_open()
            now = self._clock()
            deadline = self.controller.finish_session_gap(token, now)
            if self.device:
                # A manual brightness must not be restored after the required
                # session-light OFF.  The due LightAfterRun object keeps the
                # adapter's normal OFF/retry path responsible for the output.
                self.device.set_light_override(None)
                self.device.light_output.finish_automatic()
            self.log.info(
                "session_finished_manually",
                "Unterbrochene Saunasitzung endgültig beendet.",
            )
            if self.archive:
                self.archive.append(
                    "manual_session_end",
                    now,
                    {
                        "purpose": "session_gap",
                        "token": token,
                        "planned_ends_at": deadline.due_at,
                        "ended_at": min(now, deadline.due_at),
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
                    await self._apply_button_action(
                        self._button.advance(
                            now, bool(self.session and self.session.operation_enabled)
                        ),
                        now,
                    )
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
                    self.controller.set_operation(False, self._clock())
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
            self.closed = True
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
                            replace(self.session, ended_at=now),
                            now,
                            self.configuration.as_options(),
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
