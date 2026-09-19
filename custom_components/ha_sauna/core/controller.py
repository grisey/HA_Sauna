"""Führender Sessionablauf mit Gang-, Heizzeit-, Nachlauf- und Kühlungsregeln."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from uuid import uuid4

from . import energy, heating, thermostat
from .models import CoolingCycle, Deadline, Energy, Session, TimedPhase
from .mechanical_timer import MechanicalTimer
from .parameters import Parameters, LIVE_TEMPERATURE_KEYS
from .timeline import Event, Kind, apply, utc

GANG_SIGNALS = (Kind.PERSON_STRONG, Kind.PERSON_WEAK, Kind.INFUSION)


@dataclass(frozen=True)
class Result:
    session: Session
    changed: bool
    reason: str
    event_id: str | None = None


class Controller:
    """Ein führender Zustand. Die äußere Laufzeit serialisiert alle Eingänge."""

    def __init__(self, parameters: Parameters) -> None:
        self.parameters = parameters
        self._session: Session | None = None
        self.completed_sessions: tuple[Session, ...] = ()
        self._last_at: datetime | None = None
        # Reale Messlage und Schutz bleiben außerhalb der Session-Rücksetzung.
        self.temperature: float | None = None
        self.feedback: bool | None = None
        self.power_w: float | None = None
        self.power_valid_until: datetime | None = None
        self.protection: set[str] = set()
        self.inhibits: set[str] = set()
        self.last_decision: thermostat.Decision | None = None
        self.decisions: list[thermostat.Decision] = []
        self.overtemperature_since: datetime | None = None
        self._temperature_cooling_requested = False
        self.mechanical_timer = MechanicalTimer()
        self.phase_since = None
        self._phase_key = (None, "aus")

    @property
    def session(self) -> Session | None:
        return self._session

    @property
    def heating_limit_seconds(self) -> float:
        limit = self.parameters.seconds("heating_minutes")
        if self._session and self._session.cooling_history:
            limit -= self.parameters.seconds("heating_reduction_minutes")
        return limit

    @property
    def target_temperature(self) -> float | None:
        target = self.parameters.values.get("target_temperature_c")
        end = self.parameters.values.get("final_temperature_c")
        count = self._session.timeline.gang_count if self._session else 0
        if self._session and self._session.temperature_base_c is not None:
            target = self._session.temperature_base_c
            count -= self._session.temperature_base_gang_count
        if target is not None and end is not None:
            return min(end, target + count * self.parameters.values["temperature_increase_c"])
        return target

    def update_temperature_parameters(self, parameters, at, *, explicit_target=False):
        changed = {k for k in self.parameters.values.keys() | parameters.values.keys()
                   if self.parameters.values.get(k) != parameters.values.get(k)}
        if changed - LIVE_TEMPERATURE_KEYS:
            raise ValueError("Während einer Saunasitzung sind nur Solltemperatur, Steigerungsrate und Endtemperatur änderbar.")
        self.advance(at, evaluate=False)
        before = self.target_temperature
        self.parameters = parameters
        if self._session and (changed or explicit_target):
            target = parameters.values["target_temperature_c"] if explicit_target else before
            end = parameters.values.get("final_temperature_c")
            if end is not None:
                target = min(target, end)
            self._session = replace(self._session, temperature_base_c=target,
                temperature_base_gang_count=self._session.timeline.gang_count)
            if self.target_temperature != before:
                self._session = replace(self._session, ready_at=None)
        self._evaluate(utc(at))

    @property
    def readiness_target(self) -> float | None:
        target = self.target_temperature
        return None if target is None else target + self.parameters.values["readiness_offset_c"]

    @property
    def mechanical_timer_ends_at(self):
        return self.mechanical_timer_status["ends_at"]

    @property
    def mechanical_timer_status(self):
        return self.mechanical_timer.status(self._last_at,
            self.parameters.seconds("mechanical_timer_minutes"))

    @property
    def cooling_wait_until(self):
        if self._session is None:
            return None
        return next((d.due_at for d in self._session.deadlines
                     if d.purpose == "person_opportunity"), None)

    @property
    def phase(self) -> str:
        session = self._session
        if session is None or not session.operation_enabled:
            return "aus"
        if session.timeline.active:
            return "saunagang"
        if session.after_run:
            return "nachlauf"
        if session.cooling and session.cooling.started_at is not None:
            return "zwangskühlung"
        if (session.ready_at is not None and self.temperature is not None
                and self.readiness_target is not None
                and self.temperature >= self.readiness_target - self.parameters.values["readiness_hysteresis_c"]):
            return "bereit"
        return "aufheizen"

    def begin_session(self, session_id: str, at: datetime) -> Session:
        if self._session is not None:
            raise ValueError("Bestehende Session darf nicht beiläufig ersetzt werden")
        at = utc(at)
        self._session = replace(Session.create(session_id, at), operation_enabled=True,
                                energy=Energy(accounted_at=at))
        self._session = replace(self._session, heating=heating.report(
            self._session.heating, self.feedback, at, self.parameters.seconds("heat_reset_minutes")))
        self._last_at = at
        self.mechanical_timer = self.mechanical_timer.start(at, session_id)
        self._evaluate(at)
        return self._session

    def set_operation(self, enabled: bool, at: datetime, *, session_id: str | None = None):
        at = utc(at)
        self.advance(at, evaluate=False)
        if enabled:
            if self._session is None:
                return self.begin_session(session_id or uuid4().hex, at)
            if not self._session.operation_enabled:
                self._session = replace(self._session, operation_enabled=True, operation_off_at=None)
                self._cancel("session_gap")
                self.mechanical_timer = self.mechanical_timer.start(at, self._session.session_id)
        elif self._session is not None and self._session.operation_enabled:
            self.process(Event(uuid4().hex, self._session.session_id, Kind.OPERATION_OFF, at, at))
        self._evaluate(at)
        return self._session

    def set_temperature(self, value: float | None, at: datetime):
        self.advance(at, evaluate=False)
        self.temperature = value
        limit = self.parameters.values.get("safety_temperature_c")
        if value is not None and limit is not None and value > limit:
            if self.overtemperature_since is None:
                self.overtemperature_since = utc(at)
        else:
            self.overtemperature_since = None
            self._temperature_cooling_requested = False
        self._evaluate(utc(at))

    def report_heating(self, value: bool | None, at: datetime):
        self.advance(at, evaluate=False)
        self.feedback = value
        if self._session is not None:
            self._session = replace(self._session, heating=heating.report(
                self._session.heating, value, at, self.parameters.seconds("heat_reset_minutes")))
            self._ensure_cooling(utc(at))
        self._evaluate(utc(at))

    def report_power(self, value: float | None, valid_until: datetime | None, at: datetime):
        self.advance(at, evaluate=False)
        self.power_w, self.power_valid_until = value, valid_until

    def _account_heat(self, at):
        if self._session is None:
            return
        state = self._session.heating
        if state.accounted_at is not None and state.accounted_at > at:
            return
        self._session = replace(self._session, heating=heating.advance(
            state, at, self.parameters.seconds("heat_reset_minutes")),
            energy=energy.advance(self._session.energy, at, power_w=self.power_w,
                valid_until=self.power_valid_until, heating=state.reported_heating,
                nominal_kw=self.parameters.values["nominal_power_kw"]))

    def _cancel(self, purpose):
        self._session = replace(self._session, deadlines=tuple(
            d for d in self._session.deadlines if d.purpose != purpose))

    def _schedule(self, purpose, due_at, token=None):
        self.register_deadline(Deadline(self._session.session_id, purpose, token or uuid4().hex, due_at))

    def advance(self, at: datetime, *, evaluate=True, inclusive_confirmation=True):
        at = utc(at)
        if self._last_at is not None and at < self._last_at:
            raise ValueError("Laufzeituhr darf nicht rückwärts laufen")
        while self._session is not None:
            due = sorted((d for d in self._session.deadlines if d.due_at <= at
                          and (inclusive_confirmation or d.purpose not in
                               ("confirmation", "person_opportunity") or d.due_at < at)),
                         key=lambda d: (d.due_at, d.purpose))
            if not due:
                break
            self._account_heat(due[0].due_at)
            self.consume_deadline(due[0], at)
        self._account_heat(at)
        self._last_at = at
        if self._session is not None:
            self._ensure_cooling(at)
        if evaluate:
            self._evaluate(at)
        return self._session

    def process(self, event: Event) -> Result:
        if self._session is None:
            raise ValueError("Ereignis ohne Session")
        previous = self._session
        existing = next((e for e in previous.timeline.processed if e.event_id == event.event_id), None)
        if existing is not None:
            if existing != event:
                raise ValueError("Ereignis-ID mit abweichendem Inhalt wiederverwendet")
            return Result(previous, False, "duplicate", event.event_id)
        if event.session_id != previous.session_id:
            raise ValueError("Ereignis gehört zu einer anderen Session")
        if self._last_at is not None and event.detected_at < self._last_at:
            raise ValueError("Verspätetes Ereignis darf keine aktuelle Betriebsentscheidung ändern")
        # Fehlerhafte Eingänge dürfen nicht beiläufig Timer oder Zustand verändern.
        if event.effective_at < previous.started_at:
            raise ValueError("Ereignis liegt vor dem Sessionbeginn")
        # Eine gerade erkannte Öffnung hat am gleichen Zeitpunkt Vorrang vor
        # dem Heizbudget. Bereits laufende Kühlung wird niemals zurückgenommen.
        if event.kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE):
            apply(previous.timeline, event)  # Erst prüfen, dann Fristen ändern.
            if self.phase in ("aufheizen", "bereit"):
                parameter = None
                if event.kind == Kind.DOOR_OPEN:
                    parameter = "open_door_wait_minutes"
                elif self.cooling_wait_until is not None and self.cooling_wait_until >= event.detected_at:
                    parameter = "person_wait_minutes"
                if parameter:
                    due_at = event.effective_at + timedelta(seconds=self.parameters.seconds(parameter))
                    if due_at > event.detected_at:
                        self._schedule("person_opportunity", due_at, event.event_id)
        self.advance(event.detected_at, evaluate=False, inclusive_confirmation=False)
        previous = self._session
        if previous is None:
            raise ValueError("Ereignis gehört zu einer beendeten Session")
        blocked = None
        if event.kind in GANG_SIGNALS and previous.timeline.active is None:
            if not previous.operation_enabled:
                blocked = "operation_off"
            elif previous.after_run is not None:
                blocked = "after_run"
            elif previous.cooling is not None and previous.cooling.started_at is not None:
                blocked = "forced_cooling"
        if blocked:
            self._session = replace(previous, timeline=replace(previous.timeline,
                processed=previous.timeline.processed + (event,)))
            self._evaluate(event.detected_at)
            return Result(self._session, False, blocked, event.event_id)
        timeline = apply(previous.timeline, event)
        previous_target = self.target_temperature
        self._session = replace(previous, timeline=timeline)
        if self.target_temperature != previous_target:
            self._session = replace(self._session, ready_at=None)
        active = timeline.active
        if active is not None or event.kind == Kind.OPERATION_OFF:
            self._cancel("person_opportunity")
        if active is None or active.infusion_events:
            self._cancel("confirmation")
        elif previous.timeline.active is None:
            self._schedule("confirmation", active.started_at + timedelta(
                seconds=self.parameters.seconds("confirmation_minutes")), active.gang_id)
        if len(timeline.completed) > len(previous.timeline.completed) and timeline.completed[-1].infusion_events:
            self._begin_after_run(timeline.completed[-1].gang_id, event.detected_at)
        if event.kind == Kind.OPERATION_OFF:
            self._session = replace(self._session, operation_enabled=False, operation_off_at=event.detected_at)
            self.mechanical_timer = self.mechanical_timer.pause(event.detected_at)
            self._schedule("session_gap", event.detected_at + timedelta(seconds=self.parameters.seconds("session_gap_minutes")))
        self.advance(event.detected_at)
        return Result(self._session, True, "gang_model_updated", event.event_id)

    def _begin_after_run(self, gang_id, at):
        phase = TimedPhase(gang_id, at, at + timedelta(seconds=self.parameters.seconds("after_run_minutes")))
        self._session = replace(self._session, after_run=phase)
        self._schedule("after_run", phase.ends_at, phase.phase_id)

    def _ensure_cooling(self, at):
        self._ensure_temperature_cooling(at)
        session = self._session
        if session.cooling is None and session.heating.elapsed_seconds >= self.heating_limit_seconds:
            cycle = CoolingCycle(uuid4().hex, at, self.parameters.seconds("forced_cooling_minutes"))
            self._session = replace(session, cooling=cycle)
        session = self._session
        if (session.cooling is not None and session.cooling.started_at is None
                and session.timeline.active is None and session.after_run is None
                and self.cooling_wait_until is None):
            self._start_cooling(at)

    def _ensure_temperature_cooling(self, at):
        if (self.overtemperature_since is None or self._temperature_cooling_requested
                or (at - self.overtemperature_since).total_seconds() <= self.parameters.seconds("overtemperature_minutes")):
            return
        self._temperature_cooling_requested = True
        duration = self.parameters.seconds("forced_cooling_minutes") * self.parameters.values["overtemperature_cooling_factor"]
        cycle = self._session.cooling
        if cycle is None:
            cycle = CoolingCycle(uuid4().hex, at, duration, reason="overtemperature")
        else:
            cycle = replace(cycle, duration_seconds=max(duration, cycle.duration_seconds), reason="overtemperature")
            if cycle.started_at is not None:
                cycle = replace(cycle, ends_at=cycle.started_at + timedelta(seconds=max(0, cycle.duration_seconds - cycle.credited_seconds)))
                self._cancel("forced_cooling")
                self._schedule("forced_cooling", cycle.ends_at, cycle.cycle_id)
        self._session = replace(self._session, cooling=cycle)

    def _start_cooling(self, at):
        cycle = self._session.cooling
        remaining = max(0.0, cycle.duration_seconds - cycle.credited_seconds)
        if remaining == 0:
            # Es existiert kein zusätzlicher Kühlabschnitt; nur der abgeschlossene
            # Kühlvorgang mit seiner bereits verbrauchten Nachlaufanrechnung.
            self._finish_cooling(replace(cycle, ends_at=at), at)
            return
        cycle = replace(cycle, started_at=at, ends_at=at + timedelta(seconds=remaining))
        self._session = replace(self._session, cooling=cycle)
        self._schedule("forced_cooling", cycle.ends_at, cycle.cycle_id)

    def _finish_cooling(self, cycle, at):
        session = self._session
        self._session = replace(session, cooling=None,
            cooling_history=session.cooling_history + (cycle,),
            timeline=replace(session.timeline, anchor=None, preparation=None),
            heating=replace(session.heating, elapsed_seconds=0, last_reset_at=at))
        if cycle.reason == "overtemperature":
            self._temperature_cooling_requested = False
            self.overtemperature_since = at if self.temperature is not None and self.temperature > self.parameters.values["safety_temperature_c"] else None

    def _evaluate(self, at):
        session = self._session
        if session is None:
            decision = thermostat.Decision(at, False, "operation_off")
        else:
            target = self.readiness_target
            if (session.ready_at is None and target is not None and self.temperature is not None
                    and self.temperature >= target and session.operation_enabled):
                self._session = session = replace(session, ready_at=at)
            state, decision = thermostat.evaluate(session.thermostat, now=at, parameters=self.parameters,
                target_temperature=self.target_temperature,
                temperature=self.temperature, enabled=session.operation_enabled, gang=session.timeline.active is not None,
                cooling=session.cooling is not None and session.cooling.started_at is not None,
                after_run=session.after_run is not None, protection=tuple(sorted(self.protection)),
                inhibits=tuple(sorted(self.inhibits)),
                heating_since=(session.heating.intervals[-1].started_at
                    if session.heating.reported_heating is True else None))
            self._session = replace(session, thermostat=state)
        if self.last_decision is None or (decision.heat, decision.reason) != (self.last_decision.heat, self.last_decision.reason):
            self.decisions.append(decision)
        self.last_decision = decision
        phase_key = (session.session_id if session else None, self.phase)
        if phase_key != self._phase_key:
            self._phase_key, self.phase_since = phase_key, at
        return decision

    def register_deadline(self, deadline: Deadline) -> None:
        session = self._session
        if session is None or deadline.session_id != session.session_id:
            raise ValueError("Frist ohne passende Session")
        previous = next((d for d in session.deadlines if d.purpose == deadline.purpose), None)
        if previous is not None and previous.token == deadline.token and previous != deadline:
            raise ValueError("Geänderte Frist benötigt ein neues Token")
        kept = tuple(d for d in session.deadlines if d.purpose != deadline.purpose)
        self._session = replace(session, deadlines=kept + (deadline,))

    def consume_deadline(self, deadline: Deadline, now: datetime) -> bool:
        now = utc(now)
        session = self._session
        if session is None or deadline.session_id != session.session_id or deadline not in session.deadlines:
            return False
        if now < deadline.due_at:
            raise ValueError("Frist ist noch nicht abgelaufen")
        self._cancel(deadline.purpose)
        if deadline.purpose == "confirmation":
            active = session.timeline.active
            if active is not None and active.gang_id == deadline.token and not active.infusion_events:
                event = Event(f"deadline:{deadline.token}", session.session_id,
                              Kind.CONFIRMATION_EXPIRED, deadline.due_at, now)
                self._session = replace(self._session, timeline=apply(session.timeline, event))
        elif deadline.purpose == "after_run":
            phase = session.after_run
            if phase is not None and phase.phase_id == deadline.token:
                self._session = replace(self._session, after_run=None,
                    timeline=replace(session.timeline, anchor=None, preparation=None),
                    after_run_history=session.after_run_history + (phase,))
                if session.cooling is not None:
                    # Genau dieser beendete Nachlauf wird einmal angerechnet.
                    credit = (phase.ends_at - phase.started_at).total_seconds()
                    self._session = replace(self._session, cooling=replace(session.cooling,
                        credited_seconds=session.cooling.credited_seconds + credit))
                    self._start_cooling(phase.ends_at)
        elif deadline.purpose == "forced_cooling":
            if session.cooling is not None and session.cooling.cycle_id == deadline.token:
                self._finish_cooling(session.cooling, deadline.due_at)
        elif deadline.purpose == "session_gap" and not session.operation_enabled:
            self.completed_sessions += (replace(self._session, ended_at=deadline.due_at, deadlines=()),)
            if session.timeline.gang_count:
                self.mechanical_timer = replace(self.mechanical_timer, reset_pending=True)
            self._session = None
        return True
