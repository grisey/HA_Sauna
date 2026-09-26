"""Führender Sessionablauf mit Gang-, Heizzeit- und Ofenkühlungsregeln."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from math import isfinite
from uuid import uuid4

from . import energy, heating, thermostat
from .consumer_events import gang_changes
from .mechanical_timer import MechanicalTimer
from .contracts import BasePhaseMark, ContactorMark, ControlInputs
from .oven_cooling import calculate_oven_cooling
from .temporary_door_heat import TemporaryDoorHeatState, advance as advance_door_heat
from .models import Deadline, Energy, LightAfterRun, Session, TimedPhase
from .parameters import LIVE_TEMPERATURE_KEYS, Parameters
from .temperature_program import TemperatureProgram
from .timeline import Event, Kind, apply, utc

GANG_SIGNALS = (Kind.PERSON_STRONG, Kind.PERSON_WEAK, Kind.INFUSION)
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
        program_mode: str = "constant",
        control_mode: str = "automatic",
        temperature_steps: tuple[float, ...] | None = None,
    ) -> None:
        if program_mode not in PROGRAM_MODES:
            raise ValueError("Ungültiger Temperaturprogrammmodus")
        if control_mode not in CONTROL_MODES:
            raise ValueError("Ungültiger Betriebsmodus")
        self.parameters = parameters
        self.program_mode = program_mode
        self.control_mode = control_mode
        self.temperature_steps = temperature_steps
        self._session: Session | None = None
        self.completed_sessions: tuple[Session, ...] = ()
        self.light_after_run: LightAfterRun | None = None
        self._last_at: datetime | None = None
        # Reale Messlage und Schutz bleiben außerhalb der Session-Rücksetzung.
        self.temperature: float | None = None
        self.feedback: bool | None = None
        self.contactor: bool | None = None
        self.power_w: float | None = None
        self.power_valid_until: datetime | None = None
        self.protection: set[str] = set()
        self.inhibits: set[str] = set()
        self.last_decision: thermostat.Decision | None = None
        # Die automatische Regelung und ihr tatsächlich ausgegebener Befehl
        # sind absichtlich getrennt: eine Bedienung darf die Regelgrundlage
        # nicht umschreiben.
        self.automatic_decision: thermostat.Decision | None = None
        self.heater_override: bool | None = None
        self._override_snapshot: tuple[tuple[str | None, str], tuple[bool]] | None = (
            None
        )
        self.decisions: list[thermostat.Decision] = []
        self.consumer_events = []
        self._consumer_snapshot = None
        self.door_request = TemporaryDoorHeatState()
        self._door_request_pending = False
        self.mechanical_timer = MechanicalTimer()
        self.phase_since = None
        self._phase_key = (None, "aus")

    def set_control_mode(self, control_mode: str) -> None:
        """Choose the regulation mode before starting the next session."""
        if control_mode not in CONTROL_MODES:
            raise ValueError("Ungültiger Betriebsmodus")
        if self._session is not None:
            raise ValueError(
                "Der Betriebsmodus kann nur ohne laufende Session geändert werden"
            )
        self.control_mode = control_mode
        if control_mode == "manual":
            self.light_after_run = None

    @property
    def session(self) -> Session | None:
        return self._session

    @property
    def target_temperature(self) -> float | None:
        start = self.parameters.values.get("target_temperature_c")
        end = self.parameters.values.get("final_temperature_c")
        session = self._session
        mode = (
            session.temperature_program_mode
            if session and session.temperature_program_mode
            else self.program_mode
        )
        if mode != "progressive" or start is None or end is None:
            return start
        completed = session.timeline.gang_count if session else 0
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
        if self._session and (changed or explicit_target or new_program):
            completed = self._session.timeline.gang_count
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
            ) == "progressive" and changed & {
                "final_temperature_c",
                "temperature_gangs",
            }:
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
        target = self.target_temperature
        return (
            None
            if target is None
            else target + self.parameters.values["readiness_offset_c"]
        )

    @property
    def mechanical_timer_ends_at(self):
        return self.mechanical_timer_status["ends_at"]

    @property
    def mechanical_timer_status(self):
        status = self.mechanical_timer.status(
            self._last_at, self.parameters.seconds("mechanical_timer_minutes")
        )
        status["pause_reason"] = (
            (
                "operation_off"
                if not self._session or not self._session.operation_enabled
                else "contactor_off"
                if self.contactor is False
                else "contactor_unavailable"
            )
            if status["state"] == "paused"
            else None
        )
        return status

    def _sync_mechanical_timer(self, at):
        if self._session and self._session.operation_enabled:
            self.mechanical_timer = self.mechanical_timer.start(
                at, self._session.session_id
            )
            if self.contactor is True:
                return
        self.mechanical_timer = self.mechanical_timer.pause(at)

    def report_contactor(self, value: bool | None, at: datetime):
        """Stromversorgung des Timerantriebs, getrennt von gemessener Heizleistung."""
        self.advance(at, evaluate=False)
        self.contactor = value
        if self._session is not None:
            marks = self._session.contactor_history
            if not marks or marks[-1].state is not value:
                self._session = replace(
                    self._session,
                    contactor_history=marks + (ContactorMark(utc(at), value),),
                )
        self._sync_mechanical_timer(utc(at))
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
                and session.timeline.active.infusion_events
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
        if self._session is not None:
            raise ValueError("Bestehende Session darf nicht beiläufig ersetzt werden")
        at = utc(at)
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
        self._sync_mechanical_timer(at)
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
                self._sync_mechanical_timer(at)
                self._latch_readiness(at)
        elif self._session is not None and self._session.operation_enabled:
            self.process(
                Event(uuid4().hex, self._session.session_id, Kind.OPERATION_OFF, at, at)
            )
        self._evaluate(at)
        return self._session

    def set_heater_override(self, heat: bool | None, at: datetime):
        """Manueller Ofenbefehl; ``None`` übergibt wieder an die Automatik."""
        if heat is not None and not isinstance(heat, bool):
            raise ValueError(
                "Heizübersteuerung muss wahr, falsch oder automatisch sein"
            )
        if heat is True and (
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
        at = utc(at)
        self.advance(at, evaluate=False)
        previous_override = self.heater_override
        self.heater_override = heat
        if heat is False:
            self._drop_manual_heating_demand()
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

    def set_temperature(self, value: float | None, at: datetime):
        self.advance(at, evaluate=False)
        self.temperature = value
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
            if value is True and self.contactor is True:
                # Once both physical observations confirm the requested run,
                # normal feedback-based minimum heating owns its remaining
                # hold.  Until then this remains a real level demand.
                self._door_request_pending = False
        self._evaluate(utc(at))

    def report_power(
        self, value: float | None, valid_until: datetime | None, at: datetime
    ):
        self.advance(at, evaluate=False)
        self.power_w, self.power_valid_until = value, valid_until

    def _account_heat(self, at):
        if self._session is None:
            return
        state = self._session.heating
        if state.accounted_at is not None and state.accounted_at > at:
            return
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

    def advance(self, at: datetime, *, evaluate=True, inclusive_confirmation=True):
        at = utc(at)
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
            self._evaluate(at)
        return self._session

    def process_presence(self, report, event):
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
        return self.process(event)

    def process(self, event: Event) -> Result:
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
        if self._last_at is not None and event.detected_at < self._last_at:
            raise ValueError(
                "Verspätetes Ereignis darf keine aktuelle Betriebsentscheidung ändern"
            )
        # Fehlerhafte Eingänge dürfen nicht beiläufig Timer oder Zustand verändern.
        if event.effective_at < previous.started_at:
            raise ValueError("Ereignis liegt vor dem Sessionbeginn")
        if event.kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE):
            apply(previous.timeline, event)  # Validieren vor Fortschreiben der Uhr.
        self.advance(event.detected_at, evaluate=False, inclusive_confirmation=False)
        previous = self._session
        if previous is None:
            raise ValueError("Ereignis gehört zu einer beendeten Session")
        if event.kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE):
            # Capture eligibility before the timeline applies an edge that may
            # finish/retract a gang.  A cycle observed under gang/cooling/OFF
            # is disposed permanently and cannot be revived on its close.
            transition = advance_door_heat(
                self.door_request,
                event_id=event.event_id,
                door_open=event.kind == Kind.DOOR_OPEN,
                enabled=previous.operation_enabled and self.control_mode == "automatic",
                gang_active=previous.timeline.active is not None,
                cooling=previous.after_run is not None,
            )
            self.door_request = transition.state
            if transition.request and self.contactor is not True:
                self._door_request_pending = True
        blocked = (
            self._gang_phase_blocked(previous)
            if event.kind in GANG_SIGNALS and previous.timeline.active is None
            else None
        )
        if (
            blocked is None
            and event.kind == Kind.PERSON_WEAK
            and previous.timeline.active is None
        ):
            anchor = previous.timeline.anchor
            if anchor is None:
                blocked = "entry_context_missing"
            elif event.effective_at < anchor.effective_at:
                blocked = "entry_context_changed"
            elif event.detected_at > anchor.effective_at + timedelta(
                seconds=self.parameters.seconds("confirmation_minutes")
            ):
                # A catch-up sample may be old enough to match, but cannot
                # start a gang after its real confirmation opportunity ended.
                blocked = "entry_context_expired"
        if blocked:
            self._session = replace(
                previous,
                timeline=replace(
                    previous.timeline, processed=previous.timeline.processed + (event,)
                ),
            )
            self._evaluate(event.detected_at)
            return Result(self._session, False, blocked, event.event_id)
        timeline = apply(previous.timeline, event)
        self._session = replace(previous, timeline=timeline)
        active = timeline.active
        # A person signal is only provisional.  The latch is consumed when an
        # infusion actually confirms the gang, so a retracted signal can keep
        # an already established readiness without creating a new one from an
        # old temperature sample.
        if (
            active is not None
            and active.infusion_events
            and (
                previous.timeline.active is None
                or not previous.timeline.active.infusion_events
            )
        ):
            self._clear_readiness()
        if active is None or active.infusion_events:
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
            and timeline.completed[-1].infusion_events
            and self.control_mode == "automatic"
            and event.kind != Kind.OPERATION_OFF
        ):
            self._begin_after_run(timeline.completed[-1].gang_id, event.detected_at)
        if event.kind == Kind.OPERATION_OFF:
            self._abort_after_run(event.detected_at)
            self._session = replace(
                self._session,
                operation_enabled=False,
                operation_off_at=event.detected_at,
            )
            self.mechanical_timer = self.mechanical_timer.pause(event.detected_at)
            ends_at = event.detected_at + timedelta(
                seconds=self.parameters.seconds("session_gap_minutes")
            )
            self._schedule(
                "session_gap",
                ends_at,
            )
            if self.control_mode == "automatic":
                self._create_session_light(
                    self._session.session_id, event.detected_at, ends_at
                )
        self.advance(event.detected_at)
        return Result(self._session, True, "gang_model_updated", event.event_id)

    def recognition_allowed(self, kind: Kind) -> bool:
        """Nur Signale prüfen, die den führenden Gangzustand noch ändern können."""
        session = self._session
        if session is None or not session.operation_enabled:
            return False
        active = session.timeline.active
        if kind in (Kind.PERSON_STRONG, Kind.PERSON_WEAK):
            if active is not None:
                return False
            source = (
                session.timeline.anchor.event_id
                if session.timeline.anchor
                else "recognition_only"
            )
            if source in session.timeline.rejected_start_sources:
                return False
            if kind == Kind.PERSON_WEAK and session.timeline.anchor is None:
                return False
        elif kind != Kind.INFUSION:
            return False
        return active is not None or self._gang_phase_blocked(session) is None

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

    def oven_cooling_duration_seconds(self) -> float:
        calculation = self.oven_cooling_calculation()
        return (
            calculation.duration_seconds
            if calculation is not None
            else self.parameters.seconds("after_run_minutes")
        )

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
            self._session, after_run=replace(phase, ends_at=None)
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
        return tuple((start, end if end is not None else at)
                     for start, end in phase.active_intervals)

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
        self.heater_override = None
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
            gang_heat_demand=bool(session and session.timeline.active),
            temporary_door_heat=self._door_request_pending,
            # A live cooling phase keeps priority even if contradictory gang
            # evidence arrives.  Timeline/projection remains observational.
            cooling=bool(session and session.after_run),
        )

    def _evaluate_manual(self, at, session):
        """Issue only an explicit heater demand while retaining interlocks."""
        if not session.operation_enabled:
            return thermostat.Decision(at, False, "operation_off")
        if self.protection:
            return thermostat.Decision(
                at, False, "protection:" + ",".join(sorted(self.protection))
            )
        if self.inhibits:
            return thermostat.Decision(
                at, False, "inhibit:" + ",".join(sorted(self.inhibits))
            )
        if self.temperature is None or not isfinite(self.temperature):
            return thermostat.Decision(at, False, "upper_temperature_unavailable")
        if session.after_run is not None:
            return thermostat.Decision(at, False, "after_run")
        if self.heater_override is True and self._manual_heating_allowed():
            return thermostat.Decision(at, True, "manual_override")
        return thermostat.Decision(at, False, "manual_mode")

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

    def _evaluate(self, at, *, preserve_override=False):
        session = self._session
        if session is None:
            decision = thermostat.Decision(at, False, "operation_off")
        elif self.control_mode == "manual":
            decision = self._evaluate_manual(at, session)
        else:
            decision = self._evaluate_thermostat(at)
        self.automatic_decision = decision
        phase_key = self._current_phase_key()
        if self.heater_override is True and not self._manual_heating_allowed():
            self._clear_heater_override()
            # Eine zuvor manuell pausierte Kühlung darf nach dem Entzug der
            # Einschaltfreigabe nicht auf einen weiteren Eingang warten.
            return self._evaluate(at)
        if (
            self.control_mode == "automatic"
            and self.heater_override is not None
            and not preserve_override
            and self._override_snapshot is not None
            and (phase_key, self._automatic_signature()) != self._override_snapshot
        ):
            self._handoff_manual_heating(self.heater_override, at)
            self._clear_heater_override()
            # Das Löschen der Bedienung darf die pausierte Kühlung nicht
            # bis zum nächsten Eingang im Aufheizzustand lassen.
            return self._evaluate(at)
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
            issued = thermostat.Decision(at, self.heater_override, "manual_override")
        if self.last_decision is None or (issued.heat, issued.reason) != (
            self.last_decision.heat,
            self.last_decision.reason,
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
        self.consumer_events.extend(gang_changes(self._consumer_snapshot, self._session, at))
        self._consumer_snapshot = self._session
        self._record_base_phase(at)
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
        """Archive the current session and perform its one-time timer reset."""
        session = self._session
        if session is None:
            return None
        self.completed_sessions += (replace(session, ended_at=at, deadlines=()),)
        if session.timeline.gang_count:
            self.mechanical_timer = replace(self.mechanical_timer, reset_pending=True)
        self._session = None
        self._clear_heater_override()
        if light_after_run and self.light_after_run is None:
            self.start_session_light(session.session_id, at)
        return session

    def finish_session(self, at: datetime, *, light_after_run=True):
        """End a session immediately, including an active gang, and archive it."""
        at = utc(at)
        if self._session is None:
            raise ValueError("Es läuft keine Session.")
        self.set_operation(False, at)
        self._cancel("session_gap")
        if not light_after_run:
            self.light_after_run = None
        self._complete_session(at, light_after_run=light_after_run)
        self._evaluate(at)

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
        self._evaluate(at)
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
                and not active.infusion_events
            ):
                event = Event(
                    f"deadline:{deadline.token}",
                    session.session_id,
                    Kind.CONFIRMATION_EXPIRED,
                    deadline.due_at,
                    now,
                )
                self._session = replace(
                    self._session, timeline=apply(session.timeline, event)
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
