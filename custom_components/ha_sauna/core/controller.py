"""Führender Sessionablauf mit Gang-, Heizzeit- und Ofenkühlungsregeln."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from math import isfinite
from uuid import uuid4

from . import energy, heating, thermostat
from .consumer_events import gang_changes
from .contracts import BasePhaseMark, ContactorMark, ControlInputs
from .defaults import instance_default
from .models import Deadline, Energy, LightAfterRun, Session, ThermostatState, TimedPhase
from .oven_cooling import calculate_oven_cooling
from .parameters import LIVE_TEMPERATURE_KEYS, Parameters
from .temperature_program import TemperatureProgram
from .temporary_door_heat import TemporaryDoorHeatState
from .temporary_door_heat import advance as advance_door_heat
from .timeline import Confirmation, Door, Event, Kind, apply, utc

GANG_SIGNALS = (Kind.PERSON_STRONG, Kind.PERSON_WEAK, Kind.INFUSION, Kind.PRESENCE_CONFIRMED)
PROGRAM_MODES = frozenset(("constant", "progressive"))
CONTROL_MODES = frozenset(("automatic", "manual"))


@dataclass(frozen=True)
class Result:
    session: Session
    changed: bool
    reason: str
    event_id: str | None = None


class Controller:
    """Ein führender Zustand. Die äußere Laufzeit serialisiert alle Eingänge."""

    def __init__(
        self,
        parameters: Parameters,
        *,
        program_mode: str = instance_default("program_mode"),
        control_mode: str = instance_default("control_mode"),
        temperature_steps: tuple[float, ...] | None = None,
        decision_clock: Callable[[], datetime] | None = None,
        presence_source: str = "proxy",
        presence_entity: str | None = None,
    ) -> None:
        if program_mode not in PROGRAM_MODES:
            raise ValueError("Ungültiger Temperaturprogrammmodus")
        if control_mode not in CONTROL_MODES:
            raise ValueError("Ungültiger Betriebsmodus")
        if presence_source not in ("proxy", "ha_presence"):
            raise ValueError("Ungültige Präsenzquelle")
        self.presence_source = presence_source
        self.presence_entity = presence_entity
        self.direct_presence = None
        self._direct_presence_history = []
        self.parameters = parameters
        self.program_mode = program_mode
        self.control_mode = control_mode
        self.temperature_steps = temperature_steps
        self._decision_clock = decision_clock
        self._session: Session | None = None
        self.completed_sessions: tuple[Session, ...] = ()
        self.light_after_run: LightAfterRun | None = None
        self._last_at: datetime | None = None
        # Reale Messlage und Schutz bleiben außerhalb der Session-Rücksetzung.
        self.temperature: float | None = None
        self._temperature_valid_until: datetime | None = None
        self._recognition_temperature_raster = None
        self.feedback: bool | None = None
        self.contactor: bool | None = None
        self.power_w: float | None = None
        self.power_valid_until: datetime | None = None
        self.fallback_heating: bool | None = None
        self.protection: set[str] = set()
        self.inhibits: set[str] = set()
        self.last_decision: thermostat.Decision | None = None
        # Die automatische Regelung und ihr tatsächlich ausgegebener Befehl
        # sind absichtlich getrennt: eine Bedienung darf die Regelgrundlage
        # nicht umschreiben.
        self.automatic_decision: thermostat.Decision | None = None
        self.heater_override: bool | None = False if control_mode == "manual" else None
        self._override_snapshot: tuple[tuple[str | None, str], tuple[bool]] | None = (
            None
        )
        self._manual_thermostat = ThermostatState()
        self.decisions: list[thermostat.Decision] = []
        self.consumer_events = []
        self._consumer_snapshot = None
        self.door_request = TemporaryDoorHeatState()
        self._door_request_pending = False
        self.phase_since = None
        self._phase_key = (None, "aus")
        self._recognition_gates = []
        self._gang_temperature_gates = []
        self._confirmation_batches = 0
        self._delivery_received_at = None

    @contextmanager
    def _delivery(self, received_at):
        previous_received_at = self._delivery_received_at
        if received_at is not None:
            received_at = received_at if callable(received_at) else utc(received_at)
            if previous_received_at is None:
                self._delivery_received_at = received_at
            else:
                def latest_received_at():
                    previous = (previous_received_at() if callable(previous_received_at)
                                else previous_received_at)
                    current = received_at() if callable(received_at) else received_at
                    return max(previous, current)
                self._delivery_received_at = latest_received_at
        try:
            yield
        finally:
            self._delivery_received_at = previous_received_at

    @contextmanager
    def confirmation_batch(self, received_at=None):
        """Keep equal-time confirmation open throughout one input delivery."""
        with self._delivery(received_at):
            self._confirmation_batches += 1
            try:
                yield
            finally:
                self._confirmation_batches -= 1

    def _received_at(self, at):
        """Real delivery provenance stays separate from canonical booking time."""
        received_at = self._delivery_received_at
        if callable(received_at):
            received_at = utc(received_at())
        return max(at, received_at or at)

    def _record_recognition_gate(self, at):
        """Remember only admission results booked by this leading controller."""
        session = self._session
        gate = (
            bool(session and session.operation_enabled),
            self._gang_phase_blocked(session) if session else "no_session",
        )
        if not self._recognition_gates or self._recognition_gates[-1][1:] != gate:
            self._recognition_gates.append((utc(at), *gate))

    def recognition_context_at(self, at):
        """Read the already booked operation/cooling boundary at a sample."""
        for gate_at, enabled, blocked in reversed(self._recognition_gates):
            if gate_at <= at:
                return enabled, blocked, gate_at
        return False, "no_session", None

    def discard_recognition_context_before(self, at):
        """Retain one valid anchor for subsequent detector samples."""
        past = [index for index, gate in enumerate(self._recognition_gates)
                if gate[0] <= at]
        if past:
            self._recognition_gates = self._recognition_gates[past[-1]:]
        past = [index for index, gate in enumerate(self._gang_temperature_gates)
                if gate[0] <= at]
        if past:
            self._gang_temperature_gates = self._gang_temperature_gates[past[-1]:]
        past = [index for index, report in enumerate(self._direct_presence_history)
                if report.effective_at <= at]
        if past:
            self._direct_presence_history = self._direct_presence_history[past[-1]:]

    def _recognition_context_current(self, at):
        """A later OFF/cooling boundary retires an earlier recognition stretch."""
        return bool(
            self._recognition_gates
            and self.recognition_context_at(at)[2] == self._recognition_gates[-1][0]
        )

    def recognition_allowed_at(self, kind, at):
        """An old permitted sample must also belong to today's active stretch."""
        return (
            self._recognition_context_current(at)
            and self.recognition_context_at(at)[1] is None
            and (self._session.timeline.active is not None
                 or self._gang_temperature_blocked_at(at) is None)
            and self.recognition_allowed(kind)
        )

    def set_control_mode(self, control_mode: str) -> None:
        """Choose the regulation mode before starting the next session."""
        if control_mode not in CONTROL_MODES:
            raise ValueError("Ungültiger Betriebsmodus")
        if self._session is not None:
            raise ValueError(
                "Der Betriebsmodus kann nur ohne laufende Session geändert werden"
            )
        if self.control_mode == control_mode:
            return
        self.control_mode = control_mode
        self._clear_heater_override()
        if control_mode == "manual":
            self.light_after_run = None

    @property
    def session(self) -> Session | None:
        return self._session

    @property
    def target_temperature(self) -> float | None:
        return self._temperature_target()

    def _temperature_target(self, *, next_gang=False) -> float | None:
        start = self.parameters.values.get("target_temperature_c")
        if self.control_mode == "manual":
            return start
        end = self.parameters.values.get("final_temperature_c")
        session = self._session
        mode = (
            session.temperature_program_mode
            if session and session.temperature_program_mode
            else self.program_mode
        )
        if mode != "progressive" or start is None or end is None:
            return start
        if (not next_gang and session and session.timeline.active is not None
                and session.timeline.active.gang_id == session.active_gang_temperature_id):
            return session.active_gang_temperature_c
        if session and session.next_gang_temperature_c is not None:
            active = session.timeline.active
            if next_gang or active is None or active.gang_id != session.next_gang_temperature_blocked_by:
                return session.next_gang_temperature_c
        completed = session.timeline.gang_count if session else 0
        if next_gang and session and session.timeline.active is not None:
            completed += 1
        base_anchor = session.temperature_base_gang_count if session else 0
        program_anchor = session.temperature_program_start_gang_count if session else 0
        if session and session.temperature_base_c is not None:
            start = session.temperature_base_c
        gangs = (
            session.temperature_program_gangs
            if session and session.temperature_program_gangs
            else self.parameters.values["temperature_gangs"]
        )
        steps = (
            session.temperature_program_steps
            if session and session.temperature_program_steps is not None
            else self.temperature_steps
        )
        # The program anchor stays at the explicit program selection.  A live
        # edit only moves the temperature anchor, so repeated edits cannot
        # make already completed actual gangs disappear.
        elapsed_before_base = max(0, base_anchor - program_anchor)
        remaining = gangs - elapsed_before_base
        if base_anchor > program_anchor:
            remaining = max(2, remaining)
        elapsed_after_base = max(0, completed - base_anchor)
        return TemperatureProgram(start, end, remaining, steps).target(
            elapsed_after_base
        )

    @property
    def next_gang_temperature(self) -> float | None:
        return self._temperature_target(next_gang=True)

    def set_next_gang_temperature(self, value, at):
        """Override one forthcoming actual gang without changing its program."""
        parameters = Parameters({**self.parameters.as_dict(), "target_temperature_c": value})
        self.advance(at, evaluate=False)
        session = self._session
        if (session is None or self.control_mode != "automatic"
                or (session.temperature_program_mode or self.program_mode) != "progressive"):
            raise ValueError("Ein laufendes Temperaturprogramm ist erforderlich")
        active = session.timeline.active
        # A new choice during an overridden gang must retain that gang's target.
        if (active is not None and session.next_gang_temperature_c is not None
                and active.gang_id != session.next_gang_temperature_blocked_by):
            session = replace(
                session,
                active_gang_temperature_c=session.next_gang_temperature_c,
                active_gang_temperature_id=active.gang_id,
            )
        self._session = replace(
            session,
            next_gang_temperature_c=parameters.values["target_temperature_c"],
            next_gang_temperature_blocked_by=active.gang_id if active else None,
        )
        self._latch_readiness(utc(at))
        self._evaluate(utc(at))

    def update_temperature_parameters(
        self,
        parameters,
        at,
        *,
        explicit_target=False,
        program_mode=None,
        new_program=False,
        temperature_steps=...,
    ):
        """Change live temperature settings without altering the actual gang count.

        ``explicit_target`` is a direct, constant target choice.  A program
        selection must instead set ``new_program`` (and optionally its mode),
        which anchors a fresh distribution at the already confirmed gang.
        """
        if program_mode is not None and program_mode not in PROGRAM_MODES:
            raise ValueError("Ungültiger Temperaturprogrammmodus")
        changed = {
            k
            for k in self.parameters.values.keys() | parameters.values.keys()
            if self.parameters.values.get(k) != parameters.values.get(k)
        }
        form_changed = (
            temperature_steps is not ...
            and temperature_steps != self.temperature_steps
        )
        if changed - LIVE_TEMPERATURE_KEYS:
            raise ValueError(
                "Während einer Saunasitzung sind nur Solltemperatur, Steigerungsverteilung und Endtemperatur änderbar."
            )
        self.advance(at, evaluate=False)
        before = self.target_temperature
        if temperature_steps is not ...:
            self.temperature_steps = temperature_steps
        self.parameters = parameters
        selected_mode = program_mode if program_mode is not None else self.program_mode
        if program_mode is not None:
            self.program_mode = program_mode
            new_program = True
        if self._session and (changed or explicit_target or new_program or form_changed):
            completed = self._session.timeline.gang_count
            if explicit_target or new_program:
                self._session = replace(
                    self._session,
                    next_gang_temperature_c=None,
                    next_gang_temperature_blocked_by=None,
                    active_gang_temperature_c=None,
                    active_gang_temperature_id=None,
                )
            if explicit_target:
                # A direct setpoint deliberately remains fixed for later gangs.
                self._session = replace(
                    self._session,
                    temperature_base_c=parameters.values["target_temperature_c"],
                    temperature_base_gang_count=completed,
                    temperature_program_mode="constant",
                    temperature_program_gangs=None,
                    temperature_program_steps=None,
                )
            elif new_program:
                self._session = replace(
                    self._session,
                    temperature_base_c=parameters.values["target_temperature_c"],
                    temperature_base_gang_count=completed,
                    temperature_program_mode=selected_mode,
                    temperature_program_gangs=parameters.values["temperature_gangs"]
                    if selected_mode == "progressive"
                    else None,
                    temperature_program_steps=(
                        self.temperature_steps
                        if selected_mode == "progressive"
                        else None
                    ),
                    temperature_program_start_gang_count=completed,
                )
            elif (
                self._session.temperature_program_mode or self.program_mode
            ) == "progressive" and (
                form_changed or changed & {"final_temperature_c", "temperature_gangs"}
            ):
                # Keep today's target.  The new distribution count is measured
                # from the last explicit program selection, not this edit.
                self._session = replace(
                    self._session,
                    temperature_base_c=before,
                    temperature_base_gang_count=completed,
                    temperature_program_mode="progressive",
                    temperature_program_gangs=parameters.values["temperature_gangs"],
                    temperature_program_steps=self.temperature_steps,
                )
        self._latch_readiness(utc(at))
        self._evaluate(utc(at))

    @property
    def thermostat_target(self) -> float | None:
        """Higher switch-off threshold; readiness itself uses the setpoint."""
        return thermostat.temperature_limits(self.target_temperature, self.parameters)[1]

    @property
    def thermostat_restart_temperature(self) -> float | None:
        """Lower switch-on threshold relative to the setpoint."""
        return thermostat.temperature_limits(self.target_temperature, self.parameters)[0]

    def report_contactor(self, value: bool | None, at: datetime):
        """Bestätigter Schützzustand, getrennt von gemessener Heizleistung."""
        self.advance(at, evaluate=False)
        self.contactor = value
        if self._session is not None:
            marks = self._session.contactor_history
            if not marks or marks[-1].state is not value:
                self._session = replace(
                    self._session,
                    contactor_history=marks + (ContactorMark(utc(at), value),),
                )
        self._align_after_run_to_contactor(utc(at))
        if value is not False:
            self._suspend_after_run_countdown(utc(at))
        self._evaluate(utc(at))

    def _latch_readiness(self, at):
        """Remember the first permitted measurement at the current setpoint."""
        session = self._session
        target = self.target_temperature
        temperature = self.temperature
        if (
            session is None
            or session.ready_at is not None
            or not session.operation_enabled
            or self.control_mode != "automatic"
            or self.protection
            or self.inhibits
            or (
                session.timeline.active is not None
                and session.timeline.active.confirmation == Confirmation.CONFIRMED
            )
            or session.after_run is not None
            or isinstance(temperature, bool)
            or not isinstance(temperature, (int, float))
            or not isfinite(temperature)
            or isinstance(target, bool)
            or not isinstance(target, (int, float))
            or not isfinite(target)
            or temperature < target
        ):
            return
        self._session = replace(session, ready_at=at)

    def _clear_readiness(self):
        if self._session is not None and self._session.ready_at is not None:
            self._session = replace(self._session, ready_at=None)

    @property
    def phase(self) -> str:
        session = self._session
        if session is None or not session.operation_enabled:
            return "aus"
        if session.after_run:
            return "nachlauf"
        if session.timeline.active:
            return "saunagang"
        if self.control_mode == "manual":
            return "manuell"
        if session.ready_at is not None:
            return "bereit"
        return "aufheizen"

    def begin_session(self, session_id: str, at: datetime) -> Session:
        if self.control_mode == "manual":
            raise ValueError(
                "Im manuellen Modus gibt es keine Saunasitzung. "
                "Zuerst zur Automatik wechseln."
            )
        if self._session is not None:
            raise ValueError("Bestehende Session darf nicht beiläufig ersetzt werden")
        at = utc(at)
        self._recognition_gates = []
        self._gang_temperature_gates = [
            (at, self._gang_temperature_blocked(at), self._gang_temperature_input()[1])
        ]
        self.light_after_run = None
        self.door_request = TemporaryDoorHeatState()
        self._door_request_pending = False
        self._session = replace(
            Session.create(session_id, at),
            operation_enabled=True,
            energy=Energy(accounted_at=at),
            contactor_history=(ContactorMark(at, self.contactor),),
        )
        self._session = replace(
            self._session,
            heating=heating.report(
                self._session.heating,
                self.feedback,
                at,
                self.parameters.seconds("heat_reset_minutes"),
            ),
        )
        self._last_at = at
        self._latch_readiness(at)
        self._evaluate(at)
        return self._session

    def set_operation(
        self, enabled: bool, at: datetime, *, session_id: str | None = None
    ):
        at = utc(at)
        self.advance(at, evaluate=False)
        if not enabled:
            self._clear_heater_override()
            self._door_request_pending = False
            if self.door_request.door_open:
                self.door_request = replace(
                    self.door_request, eligible_open=False, invalidated=True
                )
        if enabled:
            if self._session is None:
                return self.begin_session(session_id or uuid4().hex, at)
            if not self._session.operation_enabled:
                self._session = replace(
                    self._session, operation_enabled=True, operation_off_at=None
                )
                self._cancel("session_gap")
                self.light_after_run = None
                self._latch_readiness(at)
        elif self._session is not None and self._session.operation_enabled:
            self.process(
                Event(
                    uuid4().hex, self._session.session_id, Kind.OPERATION_OFF,
                    at, self._received_at(at), booking_at=at,
                )
            )
        self._evaluate(at)
        return self._session

    def validate_heater_override(self, heat: bool | None):
        """Validate direct heater demand independently of session recording."""
        if heat is not None and not isinstance(heat, bool):
            raise ValueError(
                "Heizübersteuerung muss wahr, falsch oder automatisch sein"
            )
        if heat is True and self.control_mode != "manual" and (
            self._session is None or not self._session.operation_enabled
        ):
            raise ValueError(
                "Bitte zuerst den Saunabetrieb einschalten. Danach kann die Heizregelung manuell übersteuert werden."
            )
        if heat is True and (self.protection or self.inhibits):
            raise ValueError(
                "Manuelles Einschalten ist bei aktivem Schutz oder einer Heizsperre nicht möglich"
            )
        if heat is True and (
            self.temperature is None or not isfinite(self.temperature)
        ):
            raise ValueError(
                "Manuelles Einschalten erfordert einen gültigen oberen Temperaturwert"
            )

        if heat is True and self._session is not None and self._session.after_run is not None:
            raise ValueError(
                "Während der Ofenkühlung ist Einschalten gesperrt. Zuerst die Kühlung beenden."
            )

    def set_heater_override(self, heat: bool | None, at: datetime):
        """Manueller Ofenbefehl; ``None`` übergibt wieder an die Automatik."""
        self.validate_heater_override(heat)
        at = utc(at)
        self.advance(at, evaluate=False)
        previous_override = self.heater_override
        self.heater_override = heat
        if heat is False:
            self._drop_manual_heating_demand()
            self._door_request_pending = False
            if self.door_request.door_open:
                self.door_request = replace(
                    self.door_request, eligible_open=False, invalidated=True
                )
        if heat is None:
            self._handoff_manual_heating(previous_override, at)
            self._clear_heater_override()
            self._evaluate(at)
            return self.last_decision
        if self.control_mode == "automatic" and self._session is not None:
            self._schedule(
                "manual_override",
                at
                + timedelta(seconds=self.parameters.seconds("manual_override_minutes")),
                uuid4().hex,
            )
        self._latch_readiness(at)
        # Erst nach den durch die Bedienung ausgelösten Phasenänderungen merken.
        self._evaluate(at, preserve_override=True)
        if self.control_mode == "automatic":
            self._override_snapshot = (
                self._current_phase_key(),
                self._automatic_signature(),
            )
        return self.last_decision

    @contextmanager
    def recognition_temperature_raster(self, value, valid_until):
        """Use one received raster for admission while control edges keep FIFO."""
        previous = self._recognition_temperature_raster
        self._recognition_temperature_raster = (value, valid_until)
        try:
            yield
        finally:
            self._recognition_temperature_raster = previous

    def set_temperature(
        self, value: float | None, at: datetime, *, valid_until: datetime | None = None,
    ):
        self.advance(at, evaluate=False)
        self.temperature = value
        self._temperature_valid_until = utc(valid_until) if valid_until is not None else None
        at = utc(at)
        blocked = self._gang_temperature_blocked(at)
        gate_valid_until = self._gang_temperature_input()[1]
        if (not self._gang_temperature_gates
                or self._gang_temperature_gates[-1][1] != blocked
                or (self._gang_temperature_gates[-1][2] is not None
                    and at > self._gang_temperature_gates[-1][2])):
            self._gang_temperature_gates.append((at, blocked, gate_valid_until))
        else:
            # Repeated fresh inputs extend one contiguous admission interval;
            # a gap creates a new interval and cannot revive an expired anchor.
            since = self._gang_temperature_gates[-1][0]
            self._gang_temperature_gates[-1] = (since, blocked, gate_valid_until)
        if (self._session is not None and self._session.timeline.active is None
                and self._session.timeline.anchor is not None
                and not self._gang_anchor_allowed_at(self._session.timeline.anchor.effective_at)):
            self._session = replace(
                self._session,
                timeline=replace(self._session.timeline, anchor=None, preparation=None),
            )
        self._latch_readiness(utc(at))
        self._evaluate(utc(at))

    def report_heating(self, value: bool | None, at: datetime):
        self.advance(at, evaluate=False)
        self.feedback = value
        if self._session is not None:
            self._session = replace(
                self._session,
                heating=heating.report(
                    self._session.heating,
                    value,
                    at,
                    self.parameters.seconds("heat_reset_minutes"),
                ),
            )
            if value is True:
                # Actual heating feedback transfers the request to the existing
                # minimum run; a contactor acknowledgement alone cannot do so.
                self._door_request_pending = False
        self._evaluate(utc(at))

    def report_power(
        self, value: float | None, valid_until: datetime | None, at: datetime
    ):
        self.advance(at, evaluate=False)
        self.power_w, self.power_valid_until = value, valid_until

    def report_fallback_heating(self, value: bool | None, at: datetime):
        """Remember the independent/native relay evidence for power expiry."""
        self.advance(at, evaluate=False)
        self.fallback_heating = value

    def _account_heat(self, at):
        if self._session is None:
            return
        state = self._session.heating
        if state.accounted_at is not None and state.accounted_at > at:
            return
        expiry = self.power_valid_until
        if (
            self.power_w is not None
            and expiry is not None
            and state.accounted_at is not None
            and state.accounted_at <= expiry <= at
        ):
            self._session = replace(
                self._session,
                heating=heating.report(
                    state, self.fallback_heating, expiry,
                    self.parameters.seconds("heat_reset_minutes"),
                ),
                energy=energy.advance(
                    self._session.energy, expiry, power_w=self.power_w,
                    valid_until=expiry, heating=state.reported_heating,
                    nominal_kw=self.parameters.values["nominal_power_kw"],
                ),
            )
            state = self._session.heating
            self.feedback = self.fallback_heating
        self._session = replace(
            self._session,
            heating=heating.advance(
                state, at, self.parameters.seconds("heat_reset_minutes")
            ),
            energy=energy.advance(
                self._session.energy,
                at,
                power_w=self.power_w,
                valid_until=self.power_valid_until,
                heating=state.reported_heating,
                nominal_kw=self.parameters.values["nominal_power_kw"],
            ),
        )

    def _cancel(self, purpose):
        self._session = replace(
            self._session,
            deadlines=tuple(d for d in self._session.deadlines if d.purpose != purpose),
        )

    def _schedule(self, purpose, due_at, token=None):
        self.register_deadline(
            Deadline(self._session.session_id, purpose, token or uuid4().hex, due_at)
        )

    def advance(
        self, at: datetime, *, evaluate=True, inclusive_confirmation=True,
        finish_confirmation_batch=False,
    ):
        at = utc(at)
        if self._confirmation_batches and not finish_confirmation_batch:
            inclusive_confirmation = False
        decision_session_id = self._session.session_id if self._session else None
        if self._last_at is not None and at < self._last_at:
            raise ValueError("Laufzeituhr darf nicht rückwärts laufen")
        while self._session is not None:
            due = sorted(
                (
                    d
                    for d in self._session.deadlines
                    if d.due_at <= at
                    and (
                        inclusive_confirmation
                        or d.purpose != "confirmation"
                        or d.due_at < at
                    )
                ),
                key=lambda d: (d.due_at, d.purpose),
            )
            if not due:
                break
            self._account_heat(due[0].due_at)
            self._account_after_run(due[0].due_at)
            self.consume_deadline(due[0], at)
        self._account_heat(at)
        self._account_after_run(at)
        self._last_at = at
        if evaluate:
            self._evaluate(at, decision_session_id=decision_session_id)
        return self._session

    def process_presence(
        self, report, event, *, defer_confirmation=False, recognition_at=None,
    ):
        """Proxy occupancy is the source boundary for unchanged gang assignment.

        Direct reports are observed by the runtime only until their gang rules
        are decided. No infusion or gang is converted into presence evidence.
        """
        if (
            report.source != "proxy" or report.assertion != "provisional_proxy"
            or report.occupancy != "present" or not report.available
            or event.kind not in (Kind.PERSON_STRONG, Kind.PERSON_WEAK)
            or (report.source_ref, report.effective_at, report.received_at)
            != (event.event_id, event.effective_at, event.detected_at)
        ):
            raise ValueError("Keine passende führende Proxy-Präsenzmeldung")
        return self.process(
            event, defer_confirmation=defer_confirmation, recognition_at=recognition_at,
        )

    def process(
        self, event: Event, *, defer_confirmation=False, recognition_at=None,
    ) -> Result:
        with self._delivery(event.detected_at):
            return self._process(
                event, defer_confirmation=defer_confirmation, recognition_at=recognition_at,
            )

    def _process(self, event, *, defer_confirmation, recognition_at):
        if self._session is None:
            raise ValueError("Ereignis ohne Session")
        previous = self._session
        existing = next(
            (e for e in previous.timeline.processed if e.event_id == event.event_id),
            None,
        )
        if existing is not None:
            if existing != event:
                raise ValueError("Ereignis-ID mit abweichendem Inhalt wiederverwendet")
            return Result(previous, False, "duplicate", event.event_id)
        if event.session_id != previous.session_id:
            raise ValueError("Ereignis gehört zu einer anderen Session")
        if self._last_at is not None and event.booking_at < self._last_at:
            raise ValueError(
                "Verspätetes Ereignis darf keine aktuelle Betriebsentscheidung ändern"
            )
        # Fehlerhafte Eingänge dürfen nicht beiläufig Timer oder Zustand verändern.
        if event.effective_at < previous.started_at:
            raise ValueError("Ereignis liegt vor dem Sessionbeginn")
        if event.kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE):
            apply(previous.timeline, event)  # Validieren vor Fortschreiben der Uhr.
        self.advance(event.booking_at, evaluate=False, inclusive_confirmation=False)
        previous = self._session
        if previous is None:
            raise ValueError("Ereignis gehört zu einer beendeten Session")
        observed_enabled, observed_blocked = previous.operation_enabled, None
        context_current = True
        if recognition_at is not None:
            observed_enabled, observed_blocked, _ = self.recognition_context_at(
                recognition_at
            )
            context_current = self._recognition_context_current(recognition_at)
        if event.kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE):
            # Capture eligibility before the timeline applies an edge that may
            # finish/retract a gang.  A cycle observed under gang/cooling/OFF
            # is disposed permanently and cannot be revived on its close.
            transition = advance_door_heat(
                self.door_request,
                event_id=event.event_id,
                door_open=event.kind == Kind.DOOR_OPEN,
                enabled=(previous.operation_enabled and observed_enabled and context_current
                         and self.control_mode == "automatic"
                         and self.presence_source == "proxy"),
                gang_active=previous.timeline.active is not None,
                cooling=previous.after_run is not None or observed_blocked == "after_run",
            )
            self.door_request = transition.state
            if transition.request and self.feedback is not True:
                self._door_request_pending = True
        blocked = (
            self._gang_start_blocked(previous)
            if event.kind in GANG_SIGNALS and previous.timeline.active is None
            else None
        )
        if (blocked is None and event.kind in GANG_SIGNALS
                and previous.timeline.active is None and recognition_at is not None):
            blocked = self._gang_temperature_blocked_at(recognition_at)
        if event.kind in GANG_SIGNALS and observed_blocked is not None:
            blocked = observed_blocked
        elif event.kind in GANG_SIGNALS and not context_current:
            blocked = "recognition_context_changed"
        if (
            blocked is None
            and event.kind in (Kind.PERSON_STRONG, Kind.PERSON_WEAK, Kind.INFUSION)
            and previous.timeline.active is None
        ):
            blocked = self._proxy_entry_blocked(
                previous.timeline, event.booking_at, event.effective_at,
            )
        if self.presence_source == "ha_presence" and (
            event.kind in (Kind.PERSON_STRONG, Kind.PERSON_WEAK)
            or (event.kind == Kind.INFUSION and previous.timeline.active is None)
        ):
            blocked = "direct_presence_leads"
        if blocked:
            self._session = replace(
                previous,
                timeline=replace(
                    previous.timeline, processed=previous.timeline.processed + (event,)
                ),
            )
            self._evaluate(event.booking_at)
            return Result(self._session, False, blocked, event.event_id)
        timeline = apply(previous.timeline, event)
        if (event.kind == Kind.DOOR_CLOSE
                and previous.timeline.active is None
                and not self._gang_anchor_allowed_at(event.effective_at)):
            # Preserve the observed closure, but a cold/invalid closure cannot
            # later lend its time to a warm gang or weak-person opportunity.
            timeline = replace(timeline, anchor=None, preparation=None)
        if (observed_blocked is not None or not context_current) and event.kind in (
            Kind.DOOR_CLOSE, Kind.VENTILATION
        ):
            # Preserve observed door history, without lending a blocked old
            # episode to a current person search after operation/cooling resumes.
            timeline = replace(timeline, anchor=None, preparation=None)
        self._session = replace(previous, timeline=timeline)
        if (previous.active_gang_temperature_id is not None
                and (timeline.active is None
                     or timeline.active.gang_id != previous.active_gang_temperature_id)):
            self._session = replace(
                self._session,
                active_gang_temperature_c=None,
                active_gang_temperature_id=None,
            )
        if previous.next_gang_temperature_c is not None:
            completed_ids = {gang.gang_id for gang in previous.timeline.completed}
            if any(
                gang.gang_id not in completed_ids
                and gang.confirmation == Confirmation.CONFIRMED
                and gang.gang_id != previous.next_gang_temperature_blocked_by
                for gang in timeline.completed
            ):
                self._session = replace(
                    self._session,
                    next_gang_temperature_c=None,
                    next_gang_temperature_blocked_by=None,
                )
        active = timeline.active
        # A person signal is only provisional.  The latch is consumed when an
        # infusion actually confirms the gang, so a retracted signal can keep
        # an already established readiness without creating a new one from an
        # old temperature sample.
        if (
            active is not None
            and active.confirmation == Confirmation.CONFIRMED
            and (
                previous.timeline.active is None
                or previous.timeline.active.confirmation == Confirmation.PROVISIONAL
            )
        ):
            self._clear_readiness()
        if active is None or active.confirmation == Confirmation.CONFIRMED:
            self._cancel("confirmation")
        elif previous.timeline.active is None:
            self._schedule(
                "confirmation",
                active.started_at
                + timedelta(seconds=self.parameters.seconds("confirmation_minutes")),
                active.gang_id,
            )
        if (
            len(timeline.completed) > len(previous.timeline.completed)
            and timeline.completed[-1].confirmation == Confirmation.CONFIRMED
            and self.control_mode == "automatic"
            and event.kind != Kind.OPERATION_OFF
        ):
            self._begin_after_run(timeline.completed[-1].gang_id, event.booking_at)
        if event.kind == Kind.OPERATION_OFF:
            self._abort_after_run(event.booking_at)
            self._session = replace(
                self._session,
                operation_enabled=False,
                operation_off_at=event.booking_at,
            )
            ends_at = event.booking_at + timedelta(
                seconds=self.parameters.seconds("session_gap_minutes")
            )
            self._schedule(
                "session_gap",
                ends_at,
            )
            if self.control_mode == "automatic":
                self._create_session_light(
                    self._session.session_id, event.booking_at, ends_at
                )
        if event.kind == Kind.DOOR_CLOSE:
            self._reconcile_direct_presence(event.booking_at, entry_event=event)
        self.advance(event.booking_at, inclusive_confirmation=not defer_confirmation)
        return Result(self._session, True, "gang_model_updated", event.event_id)

    def recognition_allowed(self, kind: Kind) -> bool:
        """Nur Signale prüfen, die den führenden Gangzustand noch ändern können."""
        session = self._session
        if session is None or not session.operation_enabled:
            return False
        active = session.timeline.active
        if self.presence_source == "ha_presence":
            return kind == Kind.INFUSION and active is not None
        if kind in (Kind.PERSON_STRONG, Kind.PERSON_WEAK):
            if active is not None:
                return False
        elif kind != Kind.INFUSION:
            return False
        return active is not None or (
            self._gang_start_blocked(session) is None
            and self._proxy_entry_blocked(session.timeline, self._last_at) is None
        )

    def _proxy_entry_blocked(self, timeline, at, effective_at=None):
        """Admit proxy recognition only from the current unused door cycle."""
        if not timeline.entry_cycle_available:
            return "entry_context_missing"
        anchor, opening = timeline.anchor, timeline.closed_opening
        if (
            not self._recognition_context_current(opening.effective_at)
            or self.recognition_context_at(opening.effective_at)[1] is not None
            or not self._gang_anchor_allowed_at(anchor.effective_at)
            or (effective_at is not None and effective_at < anchor.effective_at)
        ):
            return "entry_context_changed"
        if at > anchor.effective_at + timedelta(
            seconds=self.parameters.seconds("confirmation_minutes")
        ):
            return "entry_context_expired"
        return None

    def _gang_temperature_input(self):
        if self._recognition_temperature_raster is not None:
            return self._recognition_temperature_raster
        return self.temperature, self._temperature_valid_until

    def _gang_temperature_blocked(self, at=None):
        """The device supplies the current, valid selected temperature or None."""
        at = at or (self._received_at(self._last_at) if self._last_at else None)
        temperature, valid_until = self._gang_temperature_input()
        if (isinstance(temperature, bool)
                or not isinstance(temperature, (int, float))
                or not isfinite(temperature)
                or (at is not None and valid_until is not None and at > valid_until)):
            return "temperature_unavailable"
        if temperature < self.parameters.values["sauna_min_temperature_c"]:
            return "temperature_below_minimum"
        return None

    def _gang_temperature_blocked_at(self, at):
        for gate_at, blocked, valid_until in reversed(self._gang_temperature_gates):
            if gate_at <= at:
                return ("temperature_unavailable" if valid_until is not None
                        and at > valid_until else blocked)
        return "temperature_unavailable"

    def _gang_anchor_allowed_at(self, at):
        return (self._gang_temperature_blocked_at(at) is None
                and at >= self._gang_temperature_gates[-1][0])

    def _gang_start_blocked(self, session):
        return self._gang_phase_blocked(session) or self._gang_temperature_blocked()

    def _gang_phase_blocked(self, session) -> str | None:
        """One phase admission rule for detector polling and stored signals."""
        if not session.operation_enabled:
            return "operation_off"
        if session.after_run is not None:
            return "after_run"
        return None

    def _begin_after_run(self, gang_id, at):
        # The cooling demand begins immediately, but its timer and calculated
        # duration start only when the contactor actually reports OFF.
        phase = TimedPhase(
            gang_id,
            at,
            None,
            0,
            accounted_at=None,
            pending_start=True,
            requested_at=at,
        )
        self._session = replace(self._session, after_run=phase)
        self._start_pending_after_run(at)

    def oven_cooling_calculation(self, at=None):
        """Calculate one cooling duration from recorded, factual session data.

        The policy receives only the persisted contactor history and the
        read-only phase projection.  It never drives the heater from a
        retrospective projection.
        """
        session = self._session
        if session is None:
            return None
        at = utc(at) if at is not None else self._last_at or session.started_at
        projection = self.phase_projection(at)
        result = calculate_oven_cooling(
            contactor_history=session.contactor_history,
            readiness_pauses=projection.readiness_pauses,
            session_started_at=session.started_at,
            window_started_at=(
                session.last_completed_oven_cooling_at or session.started_at
            ),
            at=at,
            readiness_complete=projection.complete,
            after_run_minutes=self.parameters.values["after_run_minutes"],
            oven_cooling_max_minutes=self.parameters.values[
                "oven_cooling_max_minutes"
            ],
            oven_cooling_half_life_minutes=self.parameters.values[
                "oven_cooling_half_life_minutes"
            ],
            oven_cooling_heat_idle_ratio=self.parameters.values[
                "oven_cooling_heat_idle_ratio"
            ],
        )
        return result

    def _start_pending_after_run(self, at):
        """Freeze and start an armed cooling phase on real contactor OFF."""
        session = self._session
        phase = session.after_run if session else None
        if phase is None or not phase.pending_start or self.contactor is not False:
            return
        calculation = self.oven_cooling_calculation(at)
        # ``datetime`` stores microseconds.  Freeze the exact representable
        # duration used for both the deadline and accounting so an irrational
        # weighted result cannot leave a sub-microsecond reschedule loop.
        ends_at = at + timedelta(seconds=calculation.duration_seconds)
        duration = (ends_at - at).total_seconds()
        calculation = replace(calculation, duration_seconds=duration)
        started = replace(
            phase,
            started_at=at,
            ends_at=ends_at,
            duration_seconds=duration,
            accounted_at=at,
            active_intervals=((at, None),),
            pending_start=False,
            cooling_calculation=calculation,
        )
        self._session = replace(session, after_run=started)
        self._schedule("after_run", started.ends_at, started.phase_id)

    def _align_after_run_to_contactor(self, at):
        """Start pending cooling and count only its confirmed OFF runtime."""
        self._start_pending_after_run(at)
        session = self._session
        phase = session.after_run if session else None
        if phase is None or phase.pending_start or self.contactor is not False:
            return
        if (
            phase.ends_at is not None
            and phase.accounted_at is not None
            and phase.accounted_at >= at
        ):
            return
        # A changed ON/unknown feedback never earns cooling time.  When OFF
        # returns, retain the frozen duration and give its remaining interval
        # a fresh factual start; this is not a gang/manual pause or re-entry.
        phase = replace(
            phase,
            accounted_at=at,
            ends_at=at + timedelta(seconds=phase.remaining_seconds),
            active_intervals=phase.active_intervals + ((at, None),),
        )
        self._session = replace(session, after_run=phase)
        self._cancel("after_run")
        self._schedule("after_run", phase.ends_at)

    def _suspend_after_run_countdown(self, at):
        """Do not show an expiry while OFF cooling is no longer confirmed."""
        session = self._session
        phase = session.after_run if session else None
        if phase is None or phase.pending_start or phase.ends_at is None:
            return
        self._cancel("after_run")
        self._session = replace(
            self._session,
            after_run=replace(
                phase,
                ends_at=None,
                active_intervals=self._closed_cooling_intervals(phase, at),
            ),
        )

    def _abort_after_run(self, at):
        """Stop an active or pending cooling phase without earning its anchor."""
        session = self._session
        phase = session.after_run if session else None
        if phase is None:
            return
        self._cancel("after_run")
        self._finish_after_run(replace(phase, ends_at=at))

    def _account_after_run(self, at):
        phase = self._session.after_run if self._session else None
        if (
            phase is None
            or phase.pending_start
            or self.contactor is not False
            or phase.accounted_at is None
            or at <= phase.accounted_at
        ):
            return
        elapsed = min(
            phase.remaining_seconds, (at - phase.accounted_at).total_seconds()
        )
        self._session = replace(
            self._session,
            after_run=replace(
                phase, elapsed_seconds=phase.elapsed_seconds + elapsed, accounted_at=at
            ),
        )

    @staticmethod
    def _closed_cooling_intervals(phase, at):
        return tuple(
            (start, end if end is not None else at)
            for start, end in phase.active_intervals
            if (end if end is not None else at) > start
        )

    def _finish_after_run(self, phase):
        phase = replace(
            phase,
            active_intervals=self._closed_cooling_intervals(phase, phase.ends_at),
        )
        session = self._session
        self._session = replace(
            session,
            after_run=None,
            timeline=replace(session.timeline, anchor=None, preparation=None),
            after_run_history=session.after_run_history + (phase,),
            last_completed_oven_cooling_at=(
                phase.ends_at
                if phase.ends_at is not None
                and not phase.pending_start
                and phase.cooling_calculation is not None
                and phase.elapsed_seconds >= phase.duration_seconds
                else session.last_completed_oven_cooling_at
            ),
        )
        self._record_recognition_gate(phase.ends_at)

    def finish_phase(self, purpose, token, at):
        """Eine konkret angezeigte Phase wie bei Fristablauf abschließen."""
        if purpose != "after_run":
            raise ValueError(
                "Nur Ofenkühlung kann manuell beendet werden."
            )
        at = utc(at)
        self.advance(at)
        session = self._session
        if session is None:
            raise ValueError(
                "Es läuft keine Session mit einer manuell beendbaren Phase."
            )
        deadline = next(
            (d for d in session.deadlines if d.purpose == purpose), None
        )
        phase = session.after_run
        if phase is None or phase.phase_id != token:
            raise ValueError("Es läuft keine passende Ofenkühlung.")
        if (
            deadline is None
            and phase.paused_at is None
            and not phase.pending_start
            and phase.ends_at is not None
        ):
            raise ValueError(
                "Diese Phase ist bereits beendet oder wurde inzwischen ersetzt. Bitte die Anzeige aktualisieren."
            )
        self._account_after_run(at)
        phase = self._session.after_run
        self._cancel(purpose)
        self._finish_after_run(replace(phase, ends_at=at))
        self._evaluate(at)
        return deadline

    def _current_phase_key(self):
        return (self._session.session_id if self._session else None, self.phase)

    def _automatic_signature(self):
        decision = self.automatic_decision
        # Diagnosegründe können sich bei gleicher Heizentscheidung ändern
        # (etwa Mindestheizzeit). Das ist kein neuer automatischer Eingriff.
        return (decision.heat,) if decision else (False,)

    def _clear_heater_override(self):
        # Manual operation has an explicit OFF selection, never an automatic
        # demand to inherit from the preceding session or operating mode.
        self.heater_override = False if self.control_mode == "manual" else None
        self._manual_thermostat = ThermostatState()
        if self._session is not None:
            self._cancel("manual_override")
        self._override_snapshot = None

    def _handoff_manual_heating(self, previous_override, at):
        """Retain one real manual heat run when automatic control takes over."""
        session = self._session
        if previous_override is False:
            self._drop_manual_heating_demand()
            return
        if (
            previous_override is not True
            or self.control_mode != "automatic"
            or session is None
            or not session.operation_enabled
            or session.heating.reported_heating is not True
            or not session.heating.intervals
            or self.protection
            or self.inhibits
        ):
            return
        started_at = session.heating.intervals[-1].started_at
        if (at - started_at).total_seconds() >= self.parameters.seconds(
            "minimum_heating_minutes"
        ):
            return
        self._session = replace(
            session, thermostat=replace(session.thermostat, demand=True)
        )

    def _drop_manual_heating_demand(self):
        """An explicit manual OFF ends an earlier automatic minimum run."""
        session = self._session
        if session is not None and self.control_mode == "automatic":
            self._session = replace(
                session, thermostat=replace(session.thermostat, demand=False)
            )

    @property
    def heater_override_ends_at(self) -> datetime | None:
        """The scheduled automatic-mode override deadline, if still active."""
        session = self._session
        if session is None or self.control_mode != "automatic":
            return None
        deadline = next(
            (d for d in session.deadlines if d.purpose == "manual_override"), None
        )
        return deadline.due_at if deadline else None

    def _manual_heating_allowed(self):
        return (
            not self.protection
            and not self.inhibits
            and self.temperature is not None
            and isfinite(self.temperature)
        )

    @property
    def regulation_inputs(self):
        session = self._session
        return ControlInputs(
            gang_heat_demand=bool(
                session and session.timeline.active
                and (self.presence_source == "proxy" or (
                    self.direct_presence is not None and self.direct_presence.available
                ))
            ),
            temporary_door_heat=self._door_request_pending,
            # A live cooling phase keeps priority even if contradictory gang
            # evidence arrives.  Timeline/projection remains observational.
            cooling=bool(session and session.after_run),
        )

    def _evaluate_manual(self, at):
        """Regulate an explicitly enabled heater without session rules or timers."""
        self._manual_thermostat, decision = thermostat.evaluate(
            self._manual_thermostat,
            now=at,
            parameters=self.parameters,
            target_temperature=self.target_temperature,
            temperature=self.temperature,
            enabled=self.heater_override is True,
            protection=tuple(sorted(self.protection)),
            inhibits=tuple(sorted(self.inhibits)),
            pure_hysteresis=True,
        )
        if decision.reason == "operation_off":
            decision = replace(decision, reason="manual_mode")
        return decision

    def _evaluate_thermostat(self, at):
        """Evaluate and retain the thermostat state for the current session."""
        session = self._session
        state, decision = thermostat.evaluate(
            session.thermostat,
            now=at,
            parameters=self.parameters,
            target_temperature=self.target_temperature,
            temperature=self.temperature,
            enabled=session.operation_enabled,
            inputs=self.regulation_inputs,
            heating_active=bool(self.last_decision and self.last_decision.heat
                                and session.heating.reported_heating is True),
            protection=tuple(sorted(self.protection)),
            inhibits=tuple(sorted(self.inhibits)),
            heating_since=(
                session.heating.intervals[-1].started_at
                if session.heating.reported_heating is True
                else None
            ),
        )
        self._session = replace(session, thermostat=state)
        return decision

    def observe_direct_presence(self, report, at):
        """Use only the selected entity; absence alone never finishes a round."""
        if (self.presence_source != "ha_presence"
                or report.source != self.presence_entity
                or report.assertion != "direct_presence"):
            return False
        previous = self.direct_presence
        if previous is not None and (
            report.report_id == previous.report_id
            or report.effective_at < previous.effective_at
        ):
            return False
        self.advance(at, evaluate=False)
        self.direct_presence = report
        self._direct_presence_history.append(report)
        if self._session is None:
            self._direct_presence_history = [report]
        self._evaluate(at)
        return True

    def _reconcile_direct_presence(self, at, *, entry_event=None):
        """Only a complete door closure can start a round with present occupancy.

        Presence updates can finish a round after a new exit opening, but cannot
        reuse an earlier entry cycle. Delayed closure uses occupancy at its
        original time, without an invented waiting duration.
        """
        session = self._session
        report = (
            next((report for report in reversed(self._direct_presence_history)
                  if report.effective_at <= entry_event.effective_at
                  and report.received_at <= self._received_at(at)), None)
            if entry_event is not None else self.direct_presence
        )
        if (self.presence_source != "ha_presence" or session is None
                or not session.operation_enabled or report is None
                or not report.available or report.received_at > self._received_at(at)):
            return False
        t = session.timeline
        opening = t.opening if t.door == Door.OPEN else t.closed_opening
        if (opening is None
                or self.recognition_context_at(opening.effective_at)[1] is not None
                or not self._recognition_context_current(opening.effective_at)):
            return False
        if t.active is None:
            if (entry_event is None or not t.entry_cycle_available
                    or t.anchor.event_id != entry_event.event_id
                    or report.occupancy != "present" or self._gang_start_blocked(session)
                    or not self._gang_anchor_allowed_at(t.anchor.effective_at)):
                return False
            kind = Kind.PRESENCE_CONFIRMED
            source = t.anchor
        else:
            if (report.occupancy != "absent"
                    or report.effective_at < opening.effective_at
                    or opening.event_id in t.rejected_start_sources
                    or opening.effective_at <= t.active.started_at):
                return False
            kind = Kind.PRESENCE_ENDED
            source = opening
        effective = max(source.effective_at, report.effective_at)
        if effective > at:
            return False
        self.process(Event(
            f"{kind.value}:{source.event_id}:{report.report_id}", session.session_id,
            kind, effective, self._received_at(at), at,
        ))
        return True

    def _evaluate(self, at, *, preserve_override=False, decision_session_id=None):
        if self._reconcile_direct_presence(at):
            return self.last_decision
        created_at = (
            utc(self._decision_clock()) if self._decision_clock is not None
            else self._received_at(at)
        )
        session = self._session
        session_id = session.session_id if session else decision_session_id
        if self.control_mode == "manual":
            decision = self._evaluate_manual(at)
        elif session is None:
            decision = thermostat.Decision(at, False, "operation_off")
        else:
            decision = self._evaluate_thermostat(at)
        if (
            session is None
            and decision_session_id is None
            and self.last_decision is not None
            and self.last_decision.heat is False
            and self.last_decision.reason == decision.reason
        ):
            # A refresh after finalization is not a new OFF decision. Keep
            # the still pending command's original session until a new
            # session or a genuinely different decision replaces it.
            decision = self.last_decision
            session_id = decision.session_id
        decision = replace(
            decision, session_id=session_id,
            created_at=decision.created_at or created_at,
        )
        self.automatic_decision = decision
        phase_key = self._current_phase_key()
        if self.heater_override is True and not self._manual_heating_allowed():
            self._clear_heater_override()
            # Nach Entzug der Einschaltfreigabe die führende automatische
            # Entscheidung einschließlich übergeordneter Kühlung neu bestimmen.
            return self._evaluate(at, decision_session_id=decision_session_id)
        if (
            self.control_mode == "automatic"
            and self.heater_override is not None
            and not preserve_override
            and self._override_snapshot is not None
            and (phase_key, self._automatic_signature()) != self._override_snapshot
        ):
            self._handoff_manual_heating(self.heater_override, at)
            self._clear_heater_override()
            # Nach Rückgabe der Bedienung den aktuellen automatischen Ablauf
            # sofort bestimmen; eine Kühlung bleibt dabei übergeordnet.
            return self._evaluate(at, decision_session_id=decision_session_id)
        issued = decision
        if (
            self.control_mode == "automatic"
            and self.heater_override is not None
            and self._session is not None
            and self._session.operation_enabled
            and not self.protection
            and not self.inhibits
            and self._session.after_run is None
            and self._manual_heating_allowed()
        ):
            issued = thermostat.Decision(
                at, self.heater_override, "manual_override", session_id, created_at
            )
        if self.last_decision is None or (issued.heat, issued.reason, issued.session_id) != (
            self.last_decision.heat,
            self.last_decision.reason,
            self.last_decision.session_id,
        ):
            self.decisions.append(issued)
        self.last_decision = issued
        if (
            self._session is None
            or not self._session.operation_enabled
            or self.protection
            or self.inhibits
            or self._session.after_run is not None
        ):
            self._door_request_pending = False
            if self.door_request.door_open:
                self.door_request = replace(
                    self.door_request, eligible_open=False, invalidated=True
                )
        self.consumer_events.extend(gang_changes(
            self._consumer_snapshot, self._session, self._received_at(at),
        ))
        self._consumer_snapshot = self._session
        self._record_base_phase(at)
        self._record_recognition_gate(at)
        if phase_key != self._phase_key:
            self._phase_key, self.phase_since = phase_key, at
        return decision

    def _record_base_phase(self, at):
        session = self._session
        if session is None:
            return
        phase = (
            "aus" if not session.operation_enabled else
            "manuell" if self.control_mode == "manual" else
            "bereit" if session.ready_at is not None else "aufheizen"
        )
        mark = BasePhaseMark(at, phase, session.operation_enabled)
        history = session.base_phases
        if history and (history[-1].phase, history[-1].operation_enabled) == (phase, session.operation_enabled):
            return
        if history and history[-1].at == at:
            history = history[:-1]
        self._session = replace(session, base_phases=history + (mark,))

    def phase_projection(self, now):
        from .phases import project_session
        return project_session(self._session, now) if self._session else None

    def _complete_session(self, at, *, light_after_run):
        """Archive the current session and complete its active state."""
        session = self._session
        if session is None:
            return None
        self.completed_sessions += (replace(session, ended_at=at, deadlines=()),)
        self._session = None
        self._record_recognition_gate(at)
        self._clear_heater_override()
        if light_after_run and self.light_after_run is None:
            self.start_session_light(session.session_id, at)
        return session

    def finish_session(self, at: datetime, *, light_after_run=True):
        """End a session immediately, including an active gang, and archive it."""
        at = utc(at)
        if self._session is None:
            raise ValueError("Es läuft keine Session.")
        session_id = self._session.session_id
        self.set_operation(False, at)
        # Advancing to ``at`` can itself consume the session-gap deadline and
        # finish the session. That completion must not be repeated below.
        if self._session is None:
            if light_after_run and self.light_after_run is None:
                self.start_session_light(session_id, at)
            elif not light_after_run:
                self.light_after_run = None
            self._evaluate(at, decision_session_id=session_id)
            return
        self._cancel("session_gap")
        if not light_after_run:
            self.light_after_run = None
        self._complete_session(at, light_after_run=light_after_run)
        self._evaluate(at, decision_session_id=session_id)

    def finish_session_gap(self, token: str, at: datetime):
        """End the currently paused session through its displayed gap deadline.

        The token ties this action to the exact interruption the user saw.  The
        post-session light object remains in place, but becomes due immediately,
        so the device adapter can reliably retry its required OFF command.
        """
        at = utc(at)
        session = self._session
        deadline = (
            next(
                (
                    candidate
                    for candidate in session.deadlines
                    if candidate.purpose == "session_gap" and candidate.token == token
                ),
                None,
            )
            if session is not None
            else None
        )
        if session is None or session.operation_enabled or deadline is None:
            raise ValueError("Diese Unterbrechungsfrist ist nicht mehr gültig.")
        # Erst nach der unverändernden Tokenprüfung bis zur realen Uhrzeit
        # abrechnen. Eine inzwischen regulär fällige Frist schließt dabei über
        # ihren normalen Pfad mit dem ursprünglichen Endzeitpunkt ab.
        self.advance(at, evaluate=False)
        ends_at = min(at, deadline.due_at)
        if self.light_after_run is None:
            self._create_session_light(session.session_id, ends_at, ends_at)
        else:
            self.light_after_run = replace(self.light_after_run, ends_at=ends_at)
        self._complete_session(ends_at, light_after_run=False)
        self._evaluate(at, decision_session_id=session.session_id)
        return deadline

    def _create_session_light(
        self, session_id: str, started_at: datetime, ends_at: datetime
    ):
        """Create one light phase with the already determined session deadline."""
        self.light_after_run = LightAfterRun(
            session_id,
            started_at,
            ends_at,
            self.parameters.values["session_light_brightness_percent"],
        )
        return self.light_after_run

    def start_session_light(self, session_id: str, at: datetime):
        """Start the post-session light after a deferred physical release."""
        at = utc(at)
        if (
            self._session is not None
            or not self.completed_sessions
            or self.completed_sessions[-1].session_id != session_id
        ):
            raise ValueError("Die Session ist nicht die zuletzt beendete Session.")
        return self._create_session_light(
            session_id,
            at,
            at + timedelta(seconds=self.parameters.seconds("session_gap_minutes")),
        )

    def register_deadline(self, deadline: Deadline) -> None:
        session = self._session
        if session is None or deadline.session_id != session.session_id:
            raise ValueError("Frist ohne passende Session")
        previous = next(
            (d for d in session.deadlines if d.purpose == deadline.purpose), None
        )
        if (
            previous is not None
            and previous.token == deadline.token
            and previous != deadline
        ):
            raise ValueError("Geänderte Frist benötigt ein neues Token")
        kept = tuple(d for d in session.deadlines if d.purpose != deadline.purpose)
        self._session = replace(session, deadlines=kept + (deadline,))

    def consume_deadline(self, deadline: Deadline, now: datetime) -> bool:
        now = utc(now)
        session = self._session
        if (
            session is None
            or deadline.session_id != session.session_id
            or deadline not in session.deadlines
        ):
            return False
        if now < deadline.due_at:
            raise ValueError("Frist ist noch nicht abgelaufen")
        self._cancel(deadline.purpose)
        if deadline.purpose == "confirmation":
            active = session.timeline.active
            if (
                active is not None
                and active.gang_id == deadline.token
                and active.confirmation == Confirmation.PROVISIONAL
            ):
                event = Event(
                    f"deadline:{deadline.token}",
                    session.session_id,
                    Kind.CONFIRMATION_EXPIRED,
                    deadline.due_at,
                    self._received_at(now),
                    booking_at=now,
                )
                self._session = replace(
                    self._session,
                    timeline=apply(session.timeline, event),
                    active_gang_temperature_c=None,
                    active_gang_temperature_id=None,
                )
        elif deadline.purpose == "after_run":
            phase = session.after_run
            if phase is not None:
                if phase.remaining_seconds <= 1e-6:
                    phase = replace(phase, elapsed_seconds=phase.duration_seconds)
                    self._finish_after_run(phase)
                elif self.contactor is not False:
                    # The deadline became stale while feedback is ON or
                    # unknown.  Keep the phase active but without inventing a
                    # future expiry; a later confirmed OFF creates it.
                    self._session = replace(
                        self._session, after_run=replace(phase, ends_at=None)
                    )
                else:
                    # A stale wall-clock deadline cannot complete cooling when
                    # the contactor was ON or unknown.  Keep the same frozen
                    # duration and wait for the remaining confirmed OFF time.
                    resumed = replace(
                        phase,
                        ends_at=now + timedelta(seconds=phase.remaining_seconds),
                    )
                    self._session = replace(self._session, after_run=resumed)
                    self._schedule("after_run", resumed.ends_at)
        elif deadline.purpose == "manual_override":
            self._handoff_manual_heating(self.heater_override, deadline.due_at)
            self._clear_heater_override()
        elif deadline.purpose == "session_gap" and not session.operation_enabled:
            self._complete_session(deadline.due_at, light_after_run=False)
        return True
