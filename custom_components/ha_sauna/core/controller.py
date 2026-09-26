"""Führender Sessionablauf mit Gang-, Heizzeit- und Ofenkühlungsregeln."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from math import isfinite
from uuid import uuid4

from . import energy, heating, thermostat
from .mechanical_timer import MechanicalTimer
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
    ) -> None:
        if program_mode not in PROGRAM_MODES:
            raise ValueError("Ungültiger Temperaturprogrammmodus")
        if control_mode not in CONTROL_MODES:
            raise ValueError("Ungültiger Betriebsmodus")
        self.parameters = parameters
        self.program_mode = program_mode
        self.control_mode = control_mode
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
        # The program anchor stays at the explicit program selection.  A live
        # edit only moves the temperature anchor, so repeated edits cannot
        # make already completed actual gangs disappear.
        elapsed_before_base = max(0, base_anchor - program_anchor)
        remaining = gangs - elapsed_before_base
        if base_anchor > program_anchor:
            remaining = max(2, remaining)
        elapsed_after_base = max(0, completed - base_anchor)
        return TemperatureProgram(start, end, remaining).target(elapsed_after_base)

    def update_temperature_parameters(
        self,
        parameters,
        at,
        *,
        explicit_target=False,
        program_mode=None,
        new_program=False,
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
        self._sync_mechanical_timer(utc(at))

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
            or session.timeline.active is not None
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
        if session.timeline.active:
            return "saunagang"
        if session.after_run:
            return "nachlauf"
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
        self._session = replace(
            Session.create(session_id, at),
            operation_enabled=True,
            energy=Energy(accounted_at=at),
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
        self._ensure_after_run(at)
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
        if enabled:
            if self._session is None:
                return self.begin_session(session_id or uuid4().hex, at)
            if not self._session.operation_enabled:
                self._session = replace(
                    self._session, operation_enabled=True, operation_off_at=None
                )
                self._cancel("session_gap")
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
        self.heater_override = heat
        if heat is None:
            self._clear_heater_override()
            self._ensure_after_run(at)
            self._evaluate(at)
            return self.last_decision
        if self.control_mode == "automatic" and self._session is not None:
            self._schedule(
                "manual_override",
                at
                + timedelta(seconds=self.parameters.seconds("manual_override_minutes")),
                uuid4().hex,
            )
        if heat is False:
            # Ein manueller AUS-Befehl ist kein manuelles Heizen und darf die
            # Ofenkühluhr daher nicht stillstehen lassen.
            self._ensure_after_run(at)
        if (
            heat is True
            and self.control_mode == "automatic"
            and self._session is not None
            and self._session.after_run is not None
        ):
            self._pause_after_run(at)
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
            self._ensure_after_run(utc(at))
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
        if self._session is not None:
            self._ensure_after_run(at)
        if evaluate:
            self._evaluate(at)
        return self._session

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
        blocked = (
            self._gang_phase_blocked(previous)
            if event.kind in GANG_SIGNALS and previous.timeline.active is None
            else None
        )
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
        # Erst der Aufguss bestätigt den neuen Gang. Bis dahin bleibt ein
        # pausierter alter Ofenkühlung unverändert; eine aufgehobene Erkennung
        # darf ihn weder beenden noch seine Restzeit verbrauchen.
        paused_after_run = previous.after_run
        if (
            active is not None
            and active.infusion_events
            and paused_after_run is not None
        ):
            self._cancel("after_run")
            self._finish_after_run(replace(paused_after_run, ends_at=event.detected_at))
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
        ):
            self._begin_after_run(timeline.completed[-1].gang_id, event.detected_at)
        if event.kind == Kind.OPERATION_OFF:
            self._session = replace(
                self._session,
                operation_enabled=False,
                operation_off_at=event.detected_at,
            )
            self.mechanical_timer = self.mechanical_timer.pause(event.detected_at)
            self._schedule(
                "session_gap",
                event.detected_at
                + timedelta(seconds=self.parameters.seconds("session_gap_minutes")),
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
            if kind == Kind.PERSON_WEAK and session.timeline.preparation is None:
                return False
        elif kind != Kind.INFUSION:
            return False
        return active is not None or self._gang_phase_blocked(session) is None

    def _paused_after_run_reentry_allowed(self, session=None) -> bool:
        session = session or self._session
        phase = session.after_run if session else None
        return bool(
            self.control_mode == "automatic"
            and phase is not None
            and phase.paused_at is not None
            and self.heater_override is True
            and not self.protection
            and not self.inhibits
        )

    def _gang_phase_blocked(self, session) -> str | None:
        """One phase admission rule for detector polling and stored signals."""
        if not session.operation_enabled:
            return "operation_off"
        if session.after_run is not None and not self._paused_after_run_reentry_allowed(
            session
        ):
            return "after_run"
        return None

    def _begin_after_run(self, gang_id, at):
        duration = self.oven_cooling_duration_seconds()
        phase = TimedPhase(
            gang_id, at, at + timedelta(seconds=duration), duration, accounted_at=at
        )
        self._session = replace(self._session, after_run=phase)
        self._schedule("after_run", phase.ends_at, phase.phase_id)

    def oven_cooling_duration_seconds(self) -> float:
        """Konfigurierte Ofenkühlung; zentrale Stelle für die spätere Bemessung.

        Die spätere Datengrundlage sind vorhandene Phasenzeitstempel und reale
        Schützrückmeldungen: Bereitschaft ∩ Betrieb EIN ∩ Schütz bestätigt AUS.
        Bereitschaft umfasst Heiz- und Idle-Intervalle; unbekannter Schütz,
        Betrieb AUS und Ofenkühlung liefern keine Bereitschafts-Auszeit.
        Zeitraum, ausreichende Dauer/Verteilung und Ableitung/Aktualisierung
        der Kühldauer sind noch offen. Kein zusätzlicher Pausenzähler.
        """
        return self.parameters.seconds("after_run_minutes")

    def _ensure_after_run(self, at):
        if self._session is None or self.control_mode == "manual":
            return
        phase = self._session.after_run
        if (
            phase is not None
            and phase.paused_at is not None
            and self.heater_override is not True
            and self._session.timeline.active is None
        ):
            self._resume_after_run(at)

    def _account_after_run(self, at):
        phase = self._session.after_run if self._session else None
        if (
            phase is None
            or phase.paused_at is not None
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

    def _pause_after_run(self, at):
        self._account_after_run(at)
        phase = self._session.after_run
        if phase is None or phase.paused_at is not None:
            return
        self._cancel("after_run")
        self._session = replace(
            self._session, after_run=replace(phase, ends_at=None, paused_at=at)
        )

    def _resume_after_run(self, at):
        phase = self._session.after_run
        if phase is None or phase.paused_at is None:
            return
        if phase.remaining_seconds == 0:
            self._finish_after_run(
                replace(phase, ends_at=at, paused_at=None, accounted_at=at)
            )
            return
        phase = replace(
            phase,
            ends_at=at + timedelta(seconds=phase.remaining_seconds),
            accounted_at=at,
            paused_at=None,
        )
        self._session = replace(self._session, after_run=phase)
        self._schedule("after_run", phase.ends_at, phase.phase_id)

    def _finish_after_run(self, phase):
        session = self._session
        self._session = replace(
            session,
            after_run=None,
            timeline=replace(session.timeline, anchor=None, preparation=None),
            after_run_history=session.after_run_history + (phase,),
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
        deadline = (
            next(
                (
                    d
                    for d in session.deadlines
                    if d.purpose == purpose and d.token == token
                ),
                None,
            )
            if session
            else None
        )
        phase = session.after_run
        if phase is None or phase.phase_id != token:
            raise ValueError("Es läuft keine passende Ofenkühlung.")
        if deadline is None and phase.paused_at is None:
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

    def _evaluate_manual(self, at, session):
        """Issue only an explicit heater demand while retaining interlocks."""
        if not session.operation_enabled:
            return thermostat.Decision(at, False, "operation_off")
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
            gang=session.timeline.active is not None,
            after_run=(
                session.after_run is not None and session.timeline.active is None
            ),
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
            self._ensure_after_run(at)
            return self._evaluate(at)
        if (
            self.control_mode == "automatic"
            and self.heater_override is not None
            and not preserve_override
            and self._override_snapshot is not None
            and (phase_key, self._automatic_signature()) != self._override_snapshot
        ):
            self._clear_heater_override()
            # Das Löschen der Bedienung darf die pausierte Kühlung nicht
            # bis zum nächsten Eingang im Aufheizzustand lassen.
            self._ensure_after_run(at)
            return self._evaluate(at)
        issued = decision
        if (
            self.control_mode == "automatic"
            and self.heater_override is not None
            and self._session is not None
            and self._session.operation_enabled
            and not self.protection
            and not self.inhibits
        ):
            issued = thermostat.Decision(at, self.heater_override, "manual_override")
        if self.last_decision is None or (issued.heat, issued.reason) != (
            self.last_decision.heat,
            self.last_decision.reason,
        ):
            self.decisions.append(issued)
        self.last_decision = issued
        if phase_key != self._phase_key:
            self._phase_key, self.phase_since = phase_key, at
        return decision

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
        if light_after_run:
            self.start_session_light(session.session_id, at)
        return session

    def finish_session(self, at: datetime, *, light_after_run=True):
        """End a session immediately, including an active gang, and archive it."""
        at = utc(at)
        if self._session is None:
            raise ValueError("Es läuft keine Session.")
        self.set_operation(False, at)
        self._cancel("session_gap")
        self._complete_session(at, light_after_run=light_after_run)
        self._evaluate(at)

    def start_session_light(self, session_id: str, at: datetime):
        """Start the post-session light after a deferred physical release."""
        at = utc(at)
        if (
            self._session is not None
            or not self.completed_sessions
            or self.completed_sessions[-1].session_id != session_id
        ):
            raise ValueError("Die Session ist nicht die zuletzt beendete Session.")
        self.light_after_run = LightAfterRun(
            session_id,
            at,
            at + timedelta(seconds=self.parameters.seconds("session_light_minutes")),
            self.parameters.values["session_light_brightness_percent"],
        )
        return self.light_after_run

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
            if phase is not None and phase.phase_id == deadline.token:
                self._finish_after_run(phase)
        elif deadline.purpose == "manual_override":
            self._clear_heater_override()
            self._ensure_after_run(deadline.due_at)
        elif deadline.purpose == "session_gap" and not session.operation_enabled:
            self._complete_session(
                deadline.due_at,
                light_after_run=self.control_mode == "automatic",
            )
        return True
