"""Kalibrierung mit Anweisungen per Benachrichtigung und Rückmeldung (V31).

Ohne HA-Abhängigkeit. Module sind Daten; der Ablauf führt je Schritt
Anweisung → Antwort oder Wartezeit und führt das Protokoll. Er bedient die
Sauna nicht: Steuerung, Erkennung und Gangmodell lesen weder Ablauf noch
Protokoll. Die Auswertung vergleicht das Protokoll nachträglich mit
Präsenzmeldungen, gebuchten Erkennungen, Gängen und Gangheizung.

Zeitfenster eines Schritts: Untergrenze ist die Übergabe der Anweisung,
Obergrenze die Antwort (``time_fired``), bei einem Schritt ohne Aktion das Ende
seiner Wartezeit. Die Wartezeiten der Module sind Teil der Anweisungen an die
Person, keine Größe der Steuerung oder Erkennung.

Eine Kalibrierung zählt nur ununterbrochen (W-2a): Jede Unterbrechung beendet
den Lauf sofort als ungültig, ein ungültiger Lauf wird nicht ausgewertet.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import combinations, product

from .notification_recipients import (
    RecipientAssignment,
    RecipientResolution,
    TargetIdentity,
)

ACTION_PREFIX = "HA_SAUNA_CALIBRATION_"
TITLE = "Sauna-Kalibrierung"

# Antwortarten der angebotenen Aktionen.
REPLY = "reply"
DEVIATE = "deviate"
CANCEL = "cancel"
NEXT = "next"
REPEAT = "repeat"
LABELS = {
    DEVIATE: "Abweichend",
    CANCEL: "Abbrechen",
    NEXT: "Nächster Gang",
    REPEAT: "Wiederholen",
}

# Unterbrechungen (W-2a); jede macht den Lauf ungültig.
INTERRUPTIONS = {
    "cancelled": "abgebrochen",
    "operation_off": "Betrieb aus",
    "session_ended": "Sitzung vor dem letzten Schritt beendet",
    "integration_unloaded": "Integration entladen oder neu gestartet",
}

# Bewertete Erkennungen; Lüften gehört zum Gangende.
DOOR_OPEN = "door_open"
DOOR_CLOSE = "door_close"
INFUSION = "infusion"
EVALUATED_KINDS = (DOOR_OPEN, DOOR_CLOSE, INFUSION)

# Präsenzrollen der Schritte:
#   empty_after: Kabine leer vom Abschluss bis zur nächsten Anweisung
#   empty:       Kabine leer während des Fensters
#   entry:       Eintritt; besetzt vom Abschluss bis zur Anweisung zum Austritt
#   exit_open:   Austrittsöffnung, Referenz des Gangendes
#   exit_close:  Austrittsschluss, Bezug des Nachlaufs
EMPTY_ROLES = ("empty_after", "empty")


@dataclass(frozen=True)
class Step:
    key: str
    message: str
    reference: str
    action: str | None = None
    after_seconds: float = 0.0
    hold_seconds: float = 0.0
    expects: tuple[str, ...] = ()
    role: str | None = None
    watch_seconds: float = 0.0


@dataclass(frozen=True)
class Module:
    module_id: str
    label: str
    title: str
    steps: tuple[Step, ...]
    gang: bool = True
    presence_only: bool = False

    @property
    def reference_infusions(self) -> int:
        return sum(INFUSION in step.expects for step in self.steps)


def _span(seconds: float) -> str:
    return f"{seconds / 60:g} min" if seconds % 60 == 0 else f"{seconds:g} s"


def _exit_module(module_id, out_after, open_seconds, watch_seconds=0.0) -> Module:
    exit_message = "Hinaus, Tür offen lassen."
    if watch_seconds:
        exit_message = (
            f"Hinaus, Tür offen lassen und {_span(watch_seconds)} "
            "vor der offenen Tür stehen."
        )
    return Module(module_id, module_id, "Austritt nach Aufguss", (
        Step("entry", f"Gang {module_id}: hinein, Tür zu.", "Eintritt",
             "Drin, Tür zu", expects=(DOOR_OPEN, DOOR_CLOSE), role="entry"),
        Step("infusion", "Aufguss, eine Kelle.", "Aufguss", "Gegossen",
             after_seconds=360, expects=(INFUSION,)),
        Step("exit_open", exit_message, "Austrittsöffnung = Gangende",
             "Draußen, Tür offen", after_seconds=out_after, expects=(DOOR_OPEN,),
             role="exit_open", watch_seconds=watch_seconds),
        Step("exit_close", "Tür schließen.", "Austrittsschluss", "Tür zu",
             after_seconds=open_seconds, expects=(DOOR_CLOSE,), role="exit_close"),
    ))


MODULES: dict[str, Module] = {
    module.module_id: module
    for module in (
        Module("P", "P", "Leere Kabine", (
            Step("empty", "Kabine leer, Tür zu, nicht an die Kabine.",
                 "Beginn „leer“", "Kabine ist leer", role="empty_after"),
            Step("door_front",
                 "2 min direkt vor der geschlossenen Tür stehen und bewegen.",
                 "Person vor der Tür", "Fertig", after_seconds=600, role="empty"),
            Step("away", f"Weggehen, {_span(120)} nicht an die Kabine.", "leer",
                 hold_seconds=120, role="empty"),
        ), gang=False, presence_only=True),
        _exit_module("A1", 30, 45),
        _exit_module("A2", 60, 90),
        _exit_module("A3", 90, 180, 60),
        Module("CB", "C/B", "Kurze Öffnungen", (
            Step("outside_opening",
                 "Tür von außen 10 s öffnen, nicht eintreten, schließen.",
                 "Öffnung ohne Gang", "Tür zu", expects=(DOOR_OPEN, DOOR_CLOSE),
                 role="empty"),
            Step("entry", "Hinein, Tür zu.", "Eintritt", "Drin, Tür zu",
                 after_seconds=60, expects=(DOOR_OPEN, DOOR_CLOSE), role="entry"),
            Step("short_opening", "Tür 5 s öffnen, drinbleiben, schließen.",
                 "kurze Öffnung vor dem Aufguss", "Tür zu", after_seconds=180,
                 expects=(DOOR_OPEN, DOOR_CLOSE)),
            Step("infusion", "Aufguss, eine Kelle.", "Aufguss", "Gegossen",
                 after_seconds=120, expects=(INFUSION,)),
            Step("decay_opening", "Tür 15 s öffnen, drinbleiben, schließen.",
                 "kurze Öffnung im Abklingen", "Tür zu", after_seconds=60,
                 expects=(DOOR_OPEN, DOOR_CLOSE)),
            Step("exit_open", "Hinaus, Tür offen lassen.", "Austrittsöffnung",
                 "Draußen, Tür offen", after_seconds=120, expects=(DOOR_OPEN,),
                 role="exit_open"),
            Step("exit_close", "Tür schließen.", "Austrittsschluss", "Tür zu",
                 after_seconds=60, expects=(DOOR_CLOSE,), role="exit_close"),
        )),
        Module("D", "D", "Aufgüsse", (
            Step("entry", "Hinein, Tür zu.", "Eintritt", "Drin, Tür zu",
                 expects=(DOOR_OPEN, DOOR_CLOSE), role="entry"),
            Step("infusion_1", "Eine Kelle.", "Aufguss 1, erster Guss", "Gegossen",
                 after_seconds=180, expects=(INFUSION,)),
            # Der Doppelguss zählt als ein Aufguss (E-17).
            Step("infusion_1_second", "Zweite Kelle, 10–15 s nach der ersten.",
                 "Aufguss 1, zweiter Guss", "Gegossen"),
            Step("infusion_2", "Eine Kelle.", "Aufguss 2", "Gegossen",
                 after_seconds=300, expects=(INFUSION,)),
            Step("exit_open", "Hinaus, Tür offen lassen.", "Austrittsöffnung",
                 "Draußen, Tür offen", after_seconds=60, expects=(DOOR_OPEN,),
                 role="exit_open"),
            Step("exit_close", "Tür schließen.", "Austrittsschluss", "Tür zu",
                 after_seconds=60, expects=(DOOR_CLOSE,), role="exit_close"),
        )),
    )
}


def select_modules(requested: Iterable[str] | None, *, presence: bool) -> tuple[Module, ...]:
    """Modules in their fixed order; without a presence sensor P is omitted."""
    if requested is None:
        return tuple(m for m in MODULES.values() if presence or not m.presence_only)
    requested = tuple(requested)
    unknown = [item for item in requested if item not in MODULES]
    if unknown:
        raise ValueError(f"Unbekanntes Kalibriermodul: {', '.join(unknown)}")
    if len(set(requested)) != len(requested):
        raise ValueError("Ein Kalibriermodul ist mehrfach angegeben")
    if not requested:
        raise ValueError("Mindestens ein Kalibriermodul ist erforderlich")
    if not presence and any(MODULES[item].presence_only for item in requested):
        raise ValueError("Modul P braucht einen Präsenzsensor")
    return tuple(m for m in MODULES.values() if m.module_id in requested)


def reply_targets(
    user_id: str | None,
    assignments: Iterable[RecipientAssignment],
    resolutions: Iterable[RecipientResolution],
) -> tuple[TargetIdentity, ...]:
    """Companion targets with replies assigned to this user, in a fixed order."""
    if user_id is None:
        return ()
    references = {
        reference
        for assignment in assignments
        if assignment.user_id == user_id
        for reference in assignment.recipients
    }
    return tuple(sorted(
        {
            item.recipient.target
            for item in resolutions
            if item.reference in references
            and item.recipient is not None
            and item.recipient.capability.interactive
        },
        key=lambda target: (target.transport, target.endpoint),
    ))


def is_action(value: object) -> bool:
    return isinstance(value, str) and value.startswith(ACTION_PREFIX)


@dataclass(frozen=True)
class Instruction:
    """One message of the run; every target receives its own copy."""

    seq: int
    at: datetime
    execution: int | None
    module: str | None
    step: str | None
    kind: str  # step | pause | result | invalid | evaluation
    title: str
    message: str
    actions: tuple[tuple[str, str], ...] = ()


@dataclass
class _Execution:
    index: int
    module: Module
    started_at: datetime
    step: int = 0
    due_at: datetime | None = None
    sent_at: datetime | None = None
    seq: int | None = None
    deviating: bool = False


@dataclass
class CalibrationRun:
    """Schritt → Nachricht → Antwort oder Wartezeit für eine Folge von Modulen.

    ``kind`` "session": eigene Kalibriersitzung, das erste Modul beginnt nach
    Bereit, weitere nach „Nächster Gang“. ``kind`` "module": ein Modul in einer
    laufenden Sitzung, Beginn sobald kein Gang aktiv ist. ``records`` ist der
    Ausgang des Protokolls, ``steps`` die abgeschlossenen Schritte. Gültig ist
    nur ein Lauf, der alle Schritte abgeschlossen hat; ``interrupt`` beendet
    ihn sonst als ungültig.
    """

    run_id: str
    kind: str
    modules: tuple[Module, ...]
    user_id: str | None
    session_id: str
    started_at: datetime
    has_presence: bool = True
    presence: list[tuple[datetime, bool | None]] = field(default_factory=list)
    phase: str = "waiting"
    executions: list[_Execution] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    records: list[dict] = field(default_factory=list)
    ended_at: datetime | None = None
    end_reason: str | None = None
    _next_module: int = 0
    _seq: int = 0
    _actions: dict[str, tuple[int, str]] = field(default_factory=dict)
    _messages: dict[int, dict] = field(default_factory=dict)
    _answered: set[int] = field(default_factory=set)
    _repeatable: int | None = None
    _repeat_module: Module | None = None

    def __post_init__(self) -> None:
        if self.kind not in ("session", "module"):
            raise ValueError("Kalibrierung ist eine Sitzung oder ein einzelnes Modul")
        if not self.modules or (self.kind == "module" and len(self.modules) != 1):
            raise ValueError("Ein einzelnes Kalibriermodul ist genau ein Modul")
        self._record("start", self.started_at, kind=self.kind,
                     modules=[m.module_id for m in self.modules],
                     user_id=self.user_id)

    @property
    def active(self) -> bool:
        return self.phase in ("waiting", "running", "pause")

    @property
    def valid(self) -> bool:
        return self.phase == "finished"

    @property
    def waiting_for(self) -> str | None:
        if self.phase != "waiting":
            return None
        return "ready" if self.kind == "session" and not self.executions else "free"

    @property
    def current(self) -> _Execution | None:
        return self.executions[-1] if self.executions else None

    def owns(self, action_id: str) -> bool:
        return action_id in self._actions

    # -- protocol -----------------------------------------------------------

    def _record(self, event, at, **values) -> dict:
        record = {"event": event, "run_id": self.run_id, "at": at, **values}
        self.records.append(record)
        return record

    def take_records(self) -> list[dict]:
        records, self.records = self.records, []
        return records

    def observe_presence(self, at: datetime, state: bool | None) -> None:
        self.presence.append((at, state))
        self.presence.sort(key=lambda item: item[0])

    # -- messages -------------------------------------------------------------

    def _emit(self, at, kind, message, actions=(), execution=None, step=None):
        self._seq += 1
        module = execution.module if execution is not None else None
        title = f"{TITLE} {module.label}" if module is not None else TITLE
        instruction = Instruction(
            self._seq, at, execution.index if execution else None,
            module.module_id if module else None, step.key if step else None,
            kind, title, message, tuple(actions),
        )
        self._messages[self._seq] = {
            "kind": kind,
            "execution": execution.index if execution else None,
            "step": execution.step if execution and step else None,
        }
        return instruction

    def native_actions(self, instruction: Instruction, token: str) -> list[dict]:
        """Companion actions for one target copy; identifiers are unique per copy."""
        actions = []
        for kind, label in instruction.actions:
            action_id = f"{ACTION_PREFIX}{token}_{kind.upper()}"
            self._actions[action_id] = (instruction.seq, kind)
            actions.append({"action": action_id, "title": label})
        return actions

    def handed_off(self, instruction: Instruction, message_id, target, accepted, evidence):
        self._record(
            "message", instruction.at, seq=instruction.seq,
            execution=instruction.execution, module=instruction.module,
            step=instruction.step, message_kind=instruction.kind,
            message_id=message_id,
            target={"transport": target.transport, "endpoint": target.endpoint},
            handoff_at=instruction.at, accepted=accepted, evidence=evidence,
        )

    # -- progression ----------------------------------------------------------

    def advance(self, now: datetime, *, ready: bool, gang_active: bool) -> list[Instruction]:
        out: list[Instruction] = []
        while True:
            if self.phase == "waiting":
                if not (ready if self.waiting_for == "ready" else not gang_active):
                    return out
                module, self._repeat_module = self._repeat_module, None
                self._start_execution(now, module)
            if self.phase != "running":
                return out
            execution = self.current
            step = execution.module.steps[execution.step]
            if execution.sent_at is None:
                if now < execution.due_at:
                    return out
                actions = ((REPLY, step.action),) if step.action else ()
                actions += ((DEVIATE, LABELS[DEVIATE]), (CANCEL, LABELS[CANCEL]))
                execution.sent_at = now
                instruction = self._emit(
                    execution.sent_at, "step", step.message, actions, execution, step,
                )
                execution.seq = instruction.seq
                out.append(instruction)
            if step.action is not None:
                return out
            closes = execution.sent_at + timedelta(seconds=step.hold_seconds)
            if now < closes:
                return out
            out.extend(self._close(closes, "deviate" if execution.deviating else "hold"))

    def _start_execution(self, at, module=None):
        if module is None:
            module = self.modules[self._next_module]
            self._next_module += 1
        execution = _Execution(
            len(self.executions) + 1, module, at,
            due_at=at + timedelta(seconds=module.steps[0].after_seconds),
        )
        self.executions.append(execution)
        self.phase = "running"
        self._repeatable = None
        self._record("module", at, execution=execution.index, module=module.module_id)

    def _close(self, at, outcome, *, action=None, user_id=None, time_fired=None):
        execution = self.current
        step = execution.module.steps[execution.step]
        record = self._record(
            "step", at, execution=execution.index, module=execution.module.module_id,
            step=step.key, reference=step.reference, handoff_at=execution.sent_at,
            closed_at=at, outcome=outcome, action=action, user_id=user_id,
            time_fired=time_fired,
        )
        self.steps.append(record)
        execution.step += 1
        execution.sent_at = execution.seq = None
        execution.deviating = False
        if execution.step < len(execution.module.steps):
            following = execution.module.steps[execution.step]
            execution.due_at = at + timedelta(seconds=following.after_seconds)
            return []
        return self._finish_execution(at)

    def _finish_execution(self, at):
        execution = self.current
        text = execution_summary(self, execution.index, until=at)
        more = self.kind == "session" and self._next_module < len(self.modules)
        repeat = ((REPEAT, LABELS[REPEAT]),) if execution.module.presence_only else ()
        if more:
            self.phase = "pause"
            instruction = self._emit(
                at, "pause", f"{text} Pause. Weiter mit „{LABELS[NEXT]}“.",
                (*repeat, (NEXT, LABELS[NEXT]), (CANCEL, LABELS[CANCEL])), execution,
            )
        else:
            self._end(at, "finished")
            closing = (" Kalibrierung abgeschlossen. Die Auswertung der Erkennung folgt "
                       "nach Sitzungsende.") if self.kind == "session" else ""
            instruction = self._emit(at, "result", text + closing, repeat, execution)
        if repeat:
            self._repeatable = instruction.seq
        return [instruction]

    def _end(self, at, reason, *, user_id=None):
        valid = reason == "finished"
        self.phase = "finished" if valid else "invalid"
        self.ended_at, self.end_reason = at, reason
        self._record("end", at, reason=reason, valid=valid, user_id=user_id)

    def interrupt(self, at: datetime, reason: str, *, user_id=None) -> list[Instruction]:
        """End an active run as invalid; return the message saying so (W-2a).

        An open step is recorded without reference. No instruction follows.
        """
        if not self.active:
            return []
        execution = self.current
        if self.phase == "running" and execution.sent_at is not None:
            step = execution.module.steps[execution.step]
            self.steps.append(self._record(
                "step", at, execution=execution.index,
                module=execution.module.module_id, step=step.key,
                reference=step.reference, handoff_at=execution.sent_at, closed_at=at,
                outcome="cancelled", action=None, user_id=user_id, time_fired=None,
            ))
        self._repeatable = None
        self._end(at, reason, user_id=user_id)
        return [self._emit(at, "invalid", f"Kalibrierung ungültig: {INTERRUPTIONS[reason]}. "
                                          "Sie wird nicht ausgewertet.")]

    def reply(self, action_id: str, at: datetime, user_id: str | None, *,
              message_id: str | None = None,
              restart_allowed: bool = True) -> tuple[str, list[Instruction]]:
        """Apply one reply; return its disposition and immediately due messages.

        A reply to a message that was already answered is a duplicate; a reply
        to a message whose step, pause or run has ended otherwise is late.
        Neither changes the run. ``restart_allowed`` is False while another run
        of the session is active (one module at a time).
        """
        entry = self._actions.get(action_id)
        if entry is None:
            return "unknown", []
        seq, kind = entry
        context = self._messages[seq]
        disposition, out = self._apply_reply(
            seq, kind, context, at, user_id, restart_allowed,
        )
        if disposition == "accepted":
            self._answered.add(seq)
        self._record("reply", at, seq=seq, execution=context["execution"],
                     message_id=message_id, action=kind, time_fired=at,
                     user_id=user_id, disposition=disposition)
        # A following step ("sofort" counts from this reply) is sent by advance.
        return disposition, out

    def _apply_reply(self, seq, kind, context, at, user_id, restart_allowed):
        stale = ("duplicate" if seq in self._answered else "late"), []
        if kind == CANCEL:
            out = self.interrupt(at, "cancelled", user_id=user_id)
            return ("accepted", out) if out else stale
        execution = self.current
        if context["kind"] == "step":
            if (self.phase != "running" or execution.seq != seq
                    or execution.index != context["execution"]):
                return stale
            step = execution.module.steps[execution.step]
            if step.action is None:
                if kind != DEVIATE or execution.deviating:
                    return stale
                execution.deviating = True
                return "accepted", []
            outcome = "reply" if kind == REPLY else "deviate"
            label = step.action if kind == REPLY else LABELS[DEVIATE]
            return "accepted", self._close(
                at, outcome, action=label, user_id=user_id, time_fired=at,
            )
        if (kind == REPEAT and seq == self._repeatable and restart_allowed
                and self.phase in ("pause", "finished")):
            self._record("repeat", at, execution=execution.index, user_id=user_id)
            if self.phase == "finished":
                # A single module repeats under its own start condition.
                self.phase, self.ended_at, self.end_reason = "waiting", None, None
                self._repeatable, self._repeat_module = None, execution.module
            else:
                self._start_execution(at, execution.module)
            return "accepted", []
        if (kind == NEXT and self.phase == "pause" and context["kind"] == "pause"
                and context["execution"] == execution.index):
            self._start_execution(at)
            return "accepted", []
        return stale

    def evaluation_message(self, at: datetime, text: str) -> Instruction:
        """Closing message after the session end; it offers no action."""
        return self._emit(at, "evaluation", text)


# ---------------------------------------------------------------------------
# Auswertung


def _segments(presence, start, end):
    """Presence state over [start, end] as (from, to, state); None is unknown."""
    if end <= start:
        return []
    state = None
    for at, value in presence:
        if at > start:
            break
        state = value
    marks = [(start, state)] + [(at, value) for at, value in presence if start < at < end]
    result = []
    for (begin, value), (following, _) in zip(marks, [*marks[1:], (end, None)]):
        if result and result[-1][2] == value:
            result[-1] = (result[-1][0], following, value)
        elif following > begin:
            result.append((begin, following, value))
    return result


def _seconds(value: timedelta) -> float:
    return round(value.total_seconds(), 3)


def _present(presence, start, end):
    segments = [item for item in _segments(presence, start, end) if item[2] is True]
    return {"from": start, "to": end, "reports": len(segments),
            "present_s": _seconds(sum((b - a for a, b, _ in segments), timedelta()))}


def _by_execution(steps):
    grouped: dict[tuple, list[dict]] = {}
    for step in steps:
        grouped.setdefault((step["run_id"], step["execution"]), []).append(step)
    for items in grouped.values():
        items.sort(key=lambda item: item["handoff_at"])
    return grouped


def _execution_ends(grouped):
    """An execution ends at the next handoff of its run, the last one with its window."""
    ends = {}
    keys = sorted(grouped, key=lambda key: (key[0], grouped[key][0]["handoff_at"]))
    for index, key in enumerate(keys):
        following = next((grouped[other][0]["handoff_at"] for other in keys[index + 1:]
                          if other[0] == key[0]), None)
        ends[key] = following or grouped[key][-1]["closed_at"]
    return ends, keys


def _referenced(item):
    return item["outcome"] in ("reply", "hold")


def _offset(at, item):
    """Seconds before (negative) or after (positive) the window; 0 inside it."""
    if at < item["handoff_at"]:
        return _seconds(at - item["handoff_at"])
    return 0.0 if at <= item["closed_at"] else _seconds(at - item["closed_at"])


def _distance(at, item):
    return max(item["handoff_at"] - at, at - item["closed_at"], timedelta())


def _operations(module):
    """Reference operation of each expectation: a door opening and the door
    closing that follows it form one door operation; each infusion is its own."""
    operations, number = {}, -1
    for step in module.steps:
        for kind in step.expects:
            if kind != DOOR_CLOSE:
                number += 1
            operations[step.key, kind] = number
    return operations


def _units(events):
    """Recognitions in time order; an opening and the closing that follows it
    form one recognized door operation."""
    units = []
    for kind, at in events:
        if kind == DOOR_CLOSE and units and set(units[-1]) == {DOOR_OPEN}:
            units[-1][DOOR_CLOSE] = at
        else:
            units.append({kind: at})
    return units


def _assign(references, units):
    """Assign recognized units to references (W-2a); {(reference, kind): unit}.

    ``references`` in protocol order and ``units`` in time order, each a
    mapping kind → protocol step or time. Per kind the order is kept and no
    pairs cross; every reference and every recognition is in at most one
    pair, and the parts of one unit go to the same reference or to none. A
    recognition is eligible after the window of the previous and before the
    window of the next reference of its kind. Chosen are the most pairs, then
    the smallest sum of distances to the windows; on a full tie the earlier
    recognition and the earlier reference.
    """
    kinds = sorted({kind for reference in references for kind in reference})
    bounds = {}
    for kind in kinds:
        order = [index for index, reference in enumerate(references) if kind in reference]
        for position, index in enumerate(order):
            bounds[index, kind] = (
                references[order[position - 1]][kind]["closed_at"] if position else None,
                references[order[position + 1]][kind]["handoff_at"]
                if position + 1 < len(order) else None,
            )

    def eligible(index, kind, at):
        lower, upper = bounds[index, kind]
        return (lower is None or at > lower) and (upper is None or at < upper)

    # Last assigned reference per kind; options are compared from the end.
    states = list(product(range(-1, len(references)), repeat=len(kinds)))
    following = {state: (0, timedelta(), None) for state in states}
    tables = []
    for unit in reversed(units):
        table = {}
        for state in states:
            options = []
            for index, reference in enumerate(references):
                fitting = [kind for kind in unit if kind in reference
                           and index > state[kinds.index(kind)]
                           and eligible(index, kind, unit[kind])]
                for size in range(len(fitting), 0, -1):
                    for parts in combinations(fitting, size):
                        after = tuple(index if kind in parts else last
                                      for kind, last in zip(kinds, state, strict=True))
                        pairs, distance, _ = following[after]
                        distance += sum((_distance(unit[kind], reference[kind])
                                         for kind in parts), timedelta())
                        options.append((pairs + size, distance, (index, parts, after)))
            pairs, distance, _ = following[state]
            options.append((pairs, distance, (None, (), state)))
            table[state] = min(options, key=lambda option: (-option[0], option[1]))
        tables.append(table)
        following = table
    assignment, state = {}, (-1,) * len(kinds)
    for position, table in enumerate(reversed(tables)):
        index, parts, state = table[state][2]
        assignment.update({(index, kind): position for kind in parts})
    return assignment


def _assign_run(groups, events, modules):
    """Found recognitions per referenced expectation of one run, and the additional ones.

    Considered are recognitions from the first handoff to the last window of
    the run, except inside the window of a step without reference.
    """
    items = [item for group in groups for item in group]
    excluded = [(item["handoff_at"], item["closed_at"])
                for item in items if not _referenced(item)]
    observed = [event for event in events
                if items[0]["handoff_at"] <= event[1] <= items[-1]["closed_at"]
                and not any(start <= event[1] <= end for start, end in excluded)]
    found, additional = {}, []
    for kinds in ((DOOR_OPEN, DOOR_CLOSE), (INFUSION,)):
        references = {}
        for group in groups:
            module = modules[group[0]["module"]]
            operations = _operations(module)
            roles = {step.key: step for step in module.steps}
            for item in filter(_referenced, group):
                for kind in roles[item["step"]].expects:
                    if kind in kinds:
                        operation = (item["execution"], operations[item["step"], kind])
                        references.setdefault(operation, {})[kind] = item
        references = list(references.values())
        units = _units([event for event in observed if event[0] in kinds])
        assignment = _assign(references, units)
        for (index, kind), unit in assignment.items():
            item = references[index][kind]
            found[item["execution"], item["step"], kind] = units[unit][kind]
        assigned = {(unit, kind) for (_, kind), unit in assignment.items()}
        additional += [(kind, at) for position, unit in enumerate(units)
                       for kind, at in unit.items() if (position, kind) not in assigned]
    return found, sorted(additional, key=lambda event: event[1])


def _state_at(presence, at):
    state = None
    for moment, value in presence:
        if moment > at:
            break
        state = value
    return state


def _presence_result(module, items, presence, bound):
    roles = {step.key: step for step in module.steps}
    result = {"empty": [], "dropouts": None, "after_run_s": None, "after_run_off": None,
              "door_watch": None}
    for position, item in enumerate(items):
        step = roles[item["step"]]
        if not _referenced(item):
            continue
        if step.role == "empty":
            result["empty"].append({"step": step.key, "reference": step.reference,
                                    **_present(presence, item["handoff_at"], item["closed_at"])})
        elif step.role == "empty_after" and position + 1 < len(items):
            result["empty"].append({"step": step.key, "reference": "leer",
                                    **_present(presence, item["closed_at"],
                                               items[position + 1]["handoff_at"])})
    entry = next((item for item in items if roles[item["step"]].role == "entry"), None)
    exit_open = next((item for item in items if roles[item["step"]].role == "exit_open"), None)
    exit_close = next((item for item in items if roles[item["step"]].role == "exit_close"),
                      None)
    if entry and exit_open and all(
            _referenced(item) for item in items[items.index(entry):items.index(exit_open)]):
        # Occupied for certain: from [Drin, Tür zu] to the instruction to leave.
        dropouts = [(a, b) for a, b, state in _segments(
            presence, entry["closed_at"], exit_open["handoff_at"]) if state is False]
        result["dropouts"] = {"from": entry["closed_at"], "to": exit_open["handoff_at"],
                              "count": len(dropouts),
                              "durations_s": [_seconds(b - a) for a, b in dropouts]}
    if exit_open and _referenced(exit_open) and roles[exit_open["step"]].watch_seconds:
        watch_end = exit_open["closed_at"] + timedelta(
            seconds=roles[exit_open["step"]].watch_seconds)
        result["door_watch"] = _present(presence, exit_open["closed_at"], watch_end)
    if entry and exit_close and _referenced(entry) and _referenced(exit_close):
        # After run: presence off minus [Tür zu]; negative if it went off before.
        reference = exit_close["closed_at"]
        if _state_at(presence, reference) is True:
            off = next((at for at, state in presence
                        if reference < at <= bound and state is not True), None)
        else:
            present = [s for s in _segments(presence, entry["closed_at"], reference)
                       if s[2] is True]
            off = present[-1][1] if present else None
        result["after_run_off"] = off
        result["after_run_s"] = _seconds(off - reference) if off is not None else None
    return result


def _gang_result(module, items, gangs, gang_heat, until, end):
    roles = {step.key: step for step in module.steps}
    entry = next((item for item in items if roles[item["step"]].role == "entry"), None)
    exit_open = next((item for item in items if roles[item["step"]].role == "exit_open"), None)
    if entry is None or not _referenced(entry):
        return None
    begin = items[0]["handoff_at"]
    inside = [g for g in gangs if begin <= g["start"] < end]
    limit = exit_open["handoff_at"] if exit_open is not None else end
    own = [g for g in inside if entry["handoff_at"] <= g["start"] < limit]
    gang = own[0] if own else None
    additional = [g for g in inside if g is not gang]
    infusions = sum(INFUSION in roles[item["step"]].expects
                    for item in items if _referenced(item))
    result = {"found": gang is not None, "additional": len(additional),
              "additional_starts": [g["start"] for g in additional],
              "reference_infusions": infusions}
    if gang is None:
        return result
    result.update(
        start=gang["start"], start_offset_s=_offset(gang["start"], entry),
        confirmed=gang.get("confirmed"), end=gang.get("end"),
        end_reason=gang.get("end_reason"), infusions=gang.get("infusions"),
    )
    if exit_open is not None and _referenced(exit_open):
        stop = gang.get("end") or until
        result["end_offset_s"] = (
            _offset(gang["end"], exit_open) if gang.get("end") else None
        )
        result["gang_heat_after_exit_s"] = _seconds(sum(
            (min(b, stop) - max(a, exit_open["closed_at"]) for a, b in gang_heat
             if min(b, stop) > max(a, exit_open["closed_at"])), timedelta()))
    return result


def evaluate(steps, *, presence, events, gangs, gang_heat, until, has_presence=True,
             modules: Mapping[str, Module] = MODULES) -> dict:
    """Compare closed protocol steps of uninterrupted runs with observations.

    ``events``: booked recognitions (kind, effective time), assigned per run
    to the referenced steps by `_assign` (W-2a); a found recognition carries
    its offset to the window, unassigned ones of the run are additional.
    ``gangs``: non-retracted gangs as mappings with start, confirmed, end,
    end_reason and infusions. ``gang_heat``: intervals of booked heating while
    the heating decision is the gang demand.
    """
    events = sorted((e for e in events if e[0] in EVALUATED_KINDS), key=lambda e: e[1])
    grouped = _by_execution(steps)
    ends, keys = _execution_ends(grouped)
    found, additional = {}, []
    for run_id in dict.fromkeys(key[0] for key in keys):
        run_found, run_additional = _assign_run(
            [grouped[key] for key in keys if key[0] == run_id], events, modules)
        found.update({(run_id, *key): at for key, at in run_found.items()})
        additional += [{"run_id": run_id, "kind": kind, "at": at}
                       for kind, at in run_additional]
    executions = []
    totals = {"gangs": {"reference": 0, "found": 0, "missing": 0, "additional": 0},
              "events": {"reference": 0, "found": 0, "missing": 0,
                         "additional": len(additional)},
              "empty_cabin_reports": 0, "excluded_steps": 0}
    for key in keys:
        items = grouped[key]
        module = modules[items[0]["module"]]
        roles = {step.key: step for step in module.steps}
        recognition = []
        for item in items:
            if not _referenced(item):
                totals["excluded_steps"] += 1
                continue
            expected = []
            for kind in roles[item["step"]].expects:
                at = found.get((*key, item["step"], kind))
                expected.append({"kind": kind, "found": at is not None, "at": at,
                                 "offset_s": None if at is None else _offset(at, item)})
            recognition.append({"step": item["step"], "reference": item["reference"],
                                "window": [item["handoff_at"], item["closed_at"]],
                                "expected": expected})
            totals["events"]["reference"] += len(expected)
            totals["events"]["found"] += sum(e["found"] for e in expected)
            totals["events"]["missing"] += sum(not e["found"] for e in expected)
        following = next((grouped[k][0]["handoff_at"] for k in keys[keys.index(key) + 1:]
                          if k[0] == key[0]), until)
        presence_result = (_presence_result(module, items, presence, following)
                           if has_presence else None)
        if presence_result:
            totals["empty_cabin_reports"] += sum(e["reports"] for e in presence_result["empty"])
        gang = (_gang_result(module, items, gangs, gang_heat, until, ends[key])
                if module.gang else None)
        if gang is not None:
            totals["gangs"]["reference"] += 1
            totals["gangs"]["found"] += gang["found"]
            totals["gangs"]["missing"] += not gang["found"]
            totals["gangs"]["additional"] += gang["additional"]
        executions.append({"run_id": key[0], "execution": key[1], "module": module.module_id,
                           "recognition": recognition, "presence": presence_result,
                           "gang": gang})
    return {"executions": executions, "additional": additional, "totals": totals}


def protocol_steps(records: Iterable[Mapping]) -> tuple[list[dict], list[str]]:
    """Closed steps of uninterrupted runs and the runs passed over (W-2a).

    A run counts only if its last ``end`` record is valid; a run without an
    end, as after an unclean stop, was interrupted as well.
    """
    records = [record for record in records if "run_id" in record]
    valid = {}
    for record in records:
        if record.get("event") == "end":
            valid[record["run_id"]] = record.get("valid") is True
    steps = [dict(record) for record in records
             if record.get("event") == "step" and valid.get(record["run_id"])]
    runs = dict.fromkeys(record["run_id"] for record in records)
    return steps, [run_id for run_id in runs if not valid.get(run_id)]


def gang_heat_intervals(decisions, heating, until):
    """Booked heating while the heating decision is the gang demand.

    ``decisions``: (at, heat, reason) in order; ``heating``: (start, end or None).
    """
    demand = []
    marks = list(decisions)
    for (at, heat, reason), following in zip(marks, [*marks[1:], (until, None, None)]):
        if heat and reason == "gang_heat_demand" and following[0] > at:
            demand.append((at, following[0]))
    result = []
    for start, stop in heating:
        stop = stop or until
        for a, b in demand:
            if min(b, stop) > max(a, start):
                result.append((max(a, start), min(b, stop)))
    return sorted(result)


def execution_summary(run: CalibrationRun, index: int, *, until: datetime) -> str:
    """Live result of one module from presence; doors and infusions follow later."""
    items = [step for step in run.steps if step["execution"] == index]
    module = next(e.module for e in run.executions if e.index == index)
    if not run.has_presence:
        return f"Modul {module.label} beendet, ohne Präsenzmessung."
    result = _presence_result(module, items, run.presence, until)
    if module.presence_only:
        reports = sum(e["reports"] for e in result["empty"])
        seconds = sum(e["present_s"] for e in result["empty"])
        if not reports:
            return f"Modul {module.label}: keine Anwesenheit bei leerer Kabine."
        return (f"Modul {module.label}: {reports}-mal Anwesenheit bei leerer Kabine, "
                f"zusammen {seconds:g} s.")
    parts = [f"Gang {module.label} beendet."]
    dropouts = result["dropouts"]
    if dropouts is not None:
        parts.append(f"Präsenz: {dropouts['count']} Aussetzer"
                     + (f", zusammen {sum(dropouts['durations_s']):g} s."
                        if dropouts["count"] else "."))
    if result["door_watch"] is not None:
        parts.append(f"Vor der offenen Tür {result['door_watch']['reports']}-mal anwesend.")
    if result["after_run_s"] is not None:
        parts.append(f"Nachlauf {result['after_run_s']:g} s.")
    elif any(step["step"] == "exit_close" and _referenced(step) for step in items):
        parts.append("Präsenz meldet noch Anwesenheit.")
    return " ".join(parts)


def evaluation_summary(result: dict) -> str:
    totals = result["totals"]
    gangs, events = totals["gangs"], totals["events"]
    parts = ["Kalibrierung ausgewertet."]
    if gangs["reference"] or gangs["additional"]:
        parts.append(f"Gänge {gangs['found']} von {gangs['reference']} gefunden, "
                     f"{gangs['additional']} zusätzlich.")
    parts.append(f"Türen und Aufgüsse {events['found']} von {events['reference']} gefunden, "
                 f"{events['additional']} zusätzlich.")
    parts.append(f"Anwesenheit bei leerer Kabine: {totals['empty_cabin_reports']}-mal.")
    parts.append("Einzelheiten im Archiv.")
    return " ".join(parts)
