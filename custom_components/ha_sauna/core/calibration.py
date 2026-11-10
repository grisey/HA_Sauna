"""Kalibrierung mit Anweisungen per Benachrichtigung und Rückmeldung (V31, W-3).

Ohne HA-Abhängigkeit. Module sind Daten; der Ablauf führt je Schritt
Anweisung → Antwort oder Wartezeit und führt das Protokoll. Er bedient die
Sauna nicht: Steuerung, Erkennung und Gangmodell lesen weder Ablauf noch
Protokoll. Die Auswertung vergleicht das Protokoll nachträglich mit
Präsenzmeldungen, gebuchten Erkennungen, Gängen und Gangheizung.

Grundablauf (W3-1, W3-3, W3-6): P1 (leere Kabine beim Aufheizen), Gang 1 nach
Bereit, P2 (leere Kabine in der Pause nach Gang 1), Gang 2 nach „Nächster Gang“,
danach der freiwillige Gang 3 nach „Nächster Gang“ oder „Beenden“. Zusatzmodule
(P, A1, A2, A3, C/B, D) laufen einzeln in einer laufenden Sitzung.

Zeitfenster eines Schritts: Untergrenze ist die Übergabe der Anweisung,
Obergrenze die Antwort (``time_fired``), bei einem Schritt ohne Aktion das Ende
seiner Wartezeit. Die Wartezeiten der Module sind Teil der Anweisungen an die
Person, keine Größe der Steuerung oder Erkennung.

Jeder Teil (eine Ausführung eines Moduls) gilt für sich (W3-2): Eine
Unterbrechung (W-2a) beendet den Lauf und macht nur den laufenden Teil
ungültig; abgeschlossene Teile bleiben gültig und werden ausgewertet. Die
Erkennungen eines Laufs werden seinen Referenzen in deren physikalischer
Reihenfolge zugeordnet (W-2b). ``CalibrationRun.status`` ist der Anzeigezustand
für das Panel (W-2c).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import pairwise

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
FINISH = "finish"
REPEAT = "repeat"
LABELS = {
    DEVIATE: "Abweichend",
    CANCEL: "Abbrechen",
    NEXT: "Nächster Gang",
    FINISH: "Beenden",
    REPEAT: "Wiederholen",
}
# Antworten einer Pause, die auch das Panel geben kann.
PAUSE_ACTIONS = (NEXT, FINISH)

# Unterbrechungen (W-2a, W-2b); jede macht den laufenden Teil ungültig (W3-2).
INTERRUPTIONS = {
    "cancelled": "abgebrochen",
    "operation_off": "Betrieb aus",
    "session_ended": "Sitzung vor dem letzten Schritt beendet",
    "integration_unloaded": "Integration entladen oder neu gestartet",
    "manual_mode": "manueller Modus",
    "heater_override": "manuelle Ofenwahl",
    "presence_unavailable": "Präsenzsensor unbekannt oder nicht verfügbar",
}

# W-2e: Startsperren ohne Unterbrechung. Vor dem ersten Erreichen der
# Mindesttemperatur zählt kein Eintritt (G6a); der Gang des Moduls fehlte.
START_LOCKS = {
    "minimum_temperature_pending": "Mindesttemperatur noch nicht erreicht",
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

# Beginn eines Teils:
#   free:  sobald kein Gang aktiv ist (Zusatzmodul)
#   now:   gleich nach dem Start oder dem vorigen Teil
#   ready: nach Bereit, frühestens nach dem ersten Erreichen der Mindesttemperatur
#   next:  nach „Nächster Gang“ in der Pause nach dem vorigen Teil
BEGINS = ("free", "now", "ready", "next")


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
    begin: str = "free"
    optional: bool = False
    # Name in den Nachrichten; ohne Angabe „Gang A1“ oder „Modul P“.
    name: str | None = None

    @property
    def reference_infusions(self) -> int:
        return sum(INFUSION in step.expects for step in self.steps)

    @property
    def heading(self) -> str:
        if self.name:
            return self.name
        return f"Gang {self.label}" if self.gang else f"Modul {self.label}"


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


# Leere Kabine: Modul P und die Teile P1 und P2 des Grundablaufs (W3-6).
EMPTY_CABIN = (
    Step("empty", "Kabine leer, Tür zu, nicht an die Kabine.",
         "Beginn „leer“", "Kabine ist leer", role="empty_after"),
    Step("door_front",
         "2 min direkt vor der geschlossenen Tür stehen und bewegen.",
         "Person vor der Tür", "Fertig", after_seconds=600, role="empty"),
    Step("away", f"Weggehen, {_span(120)} nicht an die Kabine.", "leer",
         hold_seconds=120, role="empty"),
)


def _short_visit(message: str) -> Step:
    """Türvorgang mit kurzem Betreten ohne Gang (W3-4).

    Die Kennung bleibt die der früheren Öffnung von außen (C/B), damit ältere
    Protokolle weiter ausgewertet werden können.
    """
    return Step("outside_opening", message, "kurzer Türvorgang ohne Gang", "Tür zu",
                expects=(DOOR_OPEN, DOOR_CLOSE))


# Grundablauf (W-3): Teile in ihrer Folge.
PARTS: tuple[Module, ...] = (
    Module("P1", "P1", "Leere Kabine beim Aufheizen", EMPTY_CABIN, gang=False,
           presence_only=True, begin="now", name="P1"),
    Module("G1", "Gang 1", "nach Bereit", (
        Step("entry", "Gang 1: hinein, Tür zu.", "Eintritt", "Drin, Tür zu",
             expects=(DOOR_OPEN, DOOR_CLOSE), role="entry"),
        Step("infusion_1", "Aufguss, eine Kelle.", "Aufguss 1", "Gegossen",
             after_seconds=300, expects=(INFUSION,)),
        Step("short_opening", "Tür 5 s öffnen, drinbleiben, schließen.",
             "kurze Öffnung im Gang", "Tür zu", after_seconds=180,
             expects=(DOOR_OPEN, DOOR_CLOSE)),
        # Der Doppelguss zählt als ein Aufguss (E-17).
        Step("infusion_2", "Zwei Kellen, die zweite 10–15 s nach der ersten.",
             "Aufguss 2, Doppelguss", "Gegossen", after_seconds=120,
             expects=(INFUSION,)),
        Step("exit_open", "Hinaus, Tür offen lassen.", "Austrittsöffnung = Gangende",
             "Draußen, Tür offen", after_seconds=30, expects=(DOOR_OPEN,),
             role="exit_open"),
        Step("exit_close", "Tür schließen.", "Austrittsschluss", "Tür zu",
             after_seconds=45, expects=(DOOR_CLOSE,), role="exit_close"),
    ), begin="ready", name="Gang 1"),
    Module("P2", "P2", "Leere Kabine nach Gang 1", EMPTY_CABIN, gang=False,
           presence_only=True, begin="now", name="P2"),
    Module("G2", "Gang 2", "nach der Pause", (
        _short_visit("Gang 2: Tür öffnen, kurz hinein, etwas ablegen, wieder hinaus, Tür zu."),
        Step("entry", "Hinein, Tür zu.", "Eintritt", "Drin, Tür zu",
             after_seconds=60, expects=(DOOR_OPEN, DOOR_CLOSE), role="entry"),
        Step("infusion_1", "Aufguss, eine Kelle.", "Aufguss 1", "Gegossen",
             after_seconds=300, expects=(INFUSION,)),
        Step("decay_opening", "Tür 15 s öffnen, drinbleiben, schließen.",
             "kurze Öffnung im Abklingen", "Tür zu", after_seconds=60,
             expects=(DOOR_OPEN, DOOR_CLOSE)),
        Step("infusion_2", "Aufguss, eine Kelle.", "Aufguss 2", "Gegossen",
             after_seconds=240, expects=(INFUSION,)),
        Step("exit_open", f"Hinaus, Tür offen lassen und {_span(60)} vor der offenen "
             "Tür stehen.", "Austrittsöffnung = Gangende", "Draußen, Tür offen",
             after_seconds=90, expects=(DOOR_OPEN,), role="exit_open", watch_seconds=60),
        Step("exit_close", "Tür schließen.", "Austrittsschluss", "Tür zu",
             after_seconds=180, expects=(DOOR_CLOSE,), role="exit_close"),
    ), begin="next", name="Gang 2"),
    Module("G3", "Gang 3", "freiwillig", (
        Step("entry", "Gang 3: hinein, Tür zu.", "Eintritt", "Drin, Tür zu",
             expects=(DOOR_OPEN, DOOR_CLOSE), role="entry"),
        Step("infusion_1", "Aufguss, eine Kelle.", "Aufguss 1 kurz nach dem Schluss",
             "Gegossen", after_seconds=60, expects=(INFUSION,)),
        Step("waving", "30 s wedeln.", "Wedeln, keine Tür", "Fertig"),
        Step("resting", "2–3 min ruhig liegen, nicht bewegen.",
             "ruhig liegen, Präsenz durchgehend", "Fertig", after_seconds=240),
        Step("infusion_2", "Aufguss, eine Kelle.", "Aufguss 2", "Gegossen",
             expects=(INFUSION,)),
        # Austrittsöffnung und -schluss mit je einer Antwort wie in Gang 1 und 2
        # (Nutzer 10.11.); damit ist auch der Nachlauf messbar.
        Step("exit_open", "Hinaus, Tür offen lassen.", "Austrittsöffnung = Gangende",
             "Draußen, Tür offen", after_seconds=60, expects=(DOOR_OPEN,),
             role="exit_open"),
        Step("exit_close", "Tür schließen.", "Austrittsschluss", "Tür zu",
             after_seconds=20, expects=(DOOR_CLOSE,), role="exit_close"),
        Step("ventilation_open",
             "Tür öffnen, kurz hinein, Fenster öffnen, hinaus, Tür offen lassen.",
             "Lüften, kurzes Betreten ohne Gang", "Draußen, Tür offen",
             after_seconds=120, expects=(DOOR_OPEN,)),
        # Das Fenster wird immer mit der Tür geschlossen (W3-5).
        Step("ventilation_close", "Kurz hinein, Fenster schließen, hinaus, Tür zu.",
             "Lüften Ende", "Tür zu", after_seconds=60, expects=(DOOR_CLOSE,)),
    ), begin="next", optional=True, name="Gang 3"),
)

# Zusatzmodule: einzeln in einer laufenden Sitzung, sobald kein Gang aktiv ist.
ADDITIONAL: dict[str, Module] = {
    module.module_id: module
    for module in (
        Module("P", "P", "Leere Kabine", EMPTY_CABIN, gang=False, presence_only=True),
        _exit_module("A1", 30, 45),
        _exit_module("A2", 60, 90),
        _exit_module("A3", 90, 180, 60),
        Module("CB", "C/B", "Kurze Öffnungen", (
            _short_visit("Tür öffnen, kurz hinein, etwas ablegen oder holen, wieder "
                         "hinaus, Tür zu."),
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

# Alle bekannten Module für Auswertung und Anzeige gespeicherter Läufe.
MODULES: dict[str, Module] = {
    **{module.module_id: module for module in PARTS},
    **ADDITIONAL,
}


def procedure(*, presence: bool) -> tuple[Module, ...]:
    """Teile des Grundablaufs; ohne Präsenzsensor entfallen P1 und P2."""
    return tuple(m for m in PARTS if presence or not m.presence_only)


def additional_modules(*, presence: bool) -> tuple[Module, ...]:
    """Angebotene Zusatzmodule in fester Folge; ohne Präsenzsensor ohne P."""
    return tuple(m for m in ADDITIONAL.values() if presence or not m.presence_only)


def additional_module(module_id: str, *, presence: bool) -> Module:
    """Ein Zusatzmodul für den Start in einer laufenden Sitzung."""
    module = ADDITIONAL.get(module_id)
    if module is None:
        raise ValueError(f"Unbekanntes Zusatzmodul: {module_id}")
    if module.presence_only and not presence:
        raise ValueError("Modul P braucht einen Präsenzsensor")
    return module


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
    kind: str  # step | part | pause | result | interrupted | invalid | evaluation
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


COMPLETED = "Die Auswertung der Erkennung folgt nach dem Sitzungsende."


@dataclass
class CalibrationRun:
    """Schritt → Nachricht → Antwort oder Wartezeit für eine Folge von Teilen.

    ``kind`` "procedure": Grundablauf in einer eigenen Sitzung, jeder Teil
    beginnt nach seiner Regel (``Module.begin``). ``kind`` "module": ein
    Zusatzmodul in einer laufenden Sitzung, Beginn sobald kein Gang aktiv ist.
    ``records`` ist der Ausgang des Protokolls, ``steps`` die abgeschlossenen
    Schritte. Gültig ist jeder abgeschlossene Teil (W3-2); ``interrupt``
    beendet den Lauf und macht nur den laufenden Teil ungültig.
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
    _pause: int | None = None
    _valid: list[int] = field(default_factory=list)
    _invalid: int | None = None

    def __post_init__(self) -> None:
        if self.kind not in ("procedure", "module"):
            raise ValueError("Kalibrierung ist ein Grundablauf oder ein Zusatzmodul")
        if not self.modules or (self.kind == "module" and len(self.modules) != 1):
            raise ValueError("Ein Zusatzmodul ist genau ein Modul")
        if any(module.begin not in BEGINS for module in self.modules):
            raise ValueError("Unbekannter Beginn eines Kalibrierteils")
        self._record("start", self.started_at, kind=self.kind,
                     modules=[m.module_id for m in self.modules if not m.optional],
                     optional=[m.module_id for m in self.modules if m.optional],
                     user_id=self.user_id, validity="part")

    @property
    def active(self) -> bool:
        return self.phase in ("waiting", "running", "pause")

    @property
    def valid_executions(self) -> tuple[int, ...]:
        """Abgeschlossene, damit gültige Teile (W3-2)."""
        return tuple(self._valid)

    @property
    def invalid_execution(self) -> int | None:
        """Der bei der Unterbrechung laufende, damit ungültige Teil."""
        return self._invalid

    @property
    def evaluable(self) -> bool:
        return bool(self._valid)

    @property
    def valid(self) -> bool:
        """Beendet mit mindestens einem gültigen und keinem ungültigen Teil."""
        return not self.active and bool(self._valid) and self._invalid is None

    @property
    def valid_steps(self) -> list[dict]:
        """Abgeschlossene Schritte der gültigen Teile; nur sie werden ausgewertet."""
        return [step for step in self.steps if step["execution"] in self._valid]

    @property
    def waiting_for(self) -> str | None:
        """Beginn des wartenden Teils: ``free``, ``now`` oder ``ready``."""
        if self.phase != "waiting":
            return None
        if self._repeat_module is not None:
            return "free"
        return self.modules[self._next_module].begin

    @property
    def current(self) -> _Execution | None:
        return self.executions[-1] if self.executions else None

    def owns(self, action_id: str) -> bool:
        return action_id in self._actions

    def _labels(self) -> list[str]:
        begun = {execution.module.module_id for execution in self.executions}
        return [m.label for m in self.modules if not m.optional or m.module_id in begun]

    def _parts(self) -> dict:
        labels = {execution.index: execution.module.label for execution in self.executions}
        return {"valid": [labels[index] for index in self._valid],
                "invalid": labels.get(self._invalid)}

    def pause_actions(self) -> list[dict]:
        """Antworten der offenen Pause ohne „Abbrechen“, für das Panel."""
        if self.phase != "pause" or self._pause is None:
            return []
        return [{"action": kind, "label": LABELS[kind]}
                for kind in self._messages[self._pause]["actions"] if kind in PAUSE_ACTIONS]

    def status(self) -> dict:
        """Display state without user identities (W-2c).

        An active run names its part, step n of m and what it waits for:
        ``ready`` (Bereit), ``gang_end`` (no gang active), ``reply``, ``hold``
        (waiting time) or ``pause`` with the next part and the pause actions.
        An ended run names its validity, reason and the valid and invalid parts.
        """
        state = {"kind": self.kind, "modules": self._labels(),
                 "started_at": self.started_at}
        if not self.active:
            return {**state, "valid": self.valid, "reason": self.end_reason,
                    "reason_text": INTERRUPTIONS.get(self.end_reason),
                    "ended_at": self.ended_at, "parts": self._parts()}
        if self.phase == "waiting":
            module = self._repeat_module or self.modules[self._next_module]
            waiting = {"ready": "ready", "free": "gang_end"}.get(self.waiting_for, "hold")
            return {**state, "module": module.label, "step": None,
                    "steps": len(module.steps), "waiting": waiting}
        execution = self.current
        steps = len(execution.module.steps)
        if self.phase == "pause":
            return {**state, "module": execution.module.label, "step": steps,
                    "steps": steps, "waiting": "pause",
                    "next": self.modules[self._next_module].label,
                    "actions": self.pause_actions()}
        step = execution.module.steps[execution.step]
        waiting = "reply" if execution.sent_at is not None and step.action else "hold"
        return {**state, "module": execution.module.label, "step": execution.step + 1,
                "steps": steps, "waiting": waiting}

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
            "actions": tuple(action for action, _ in actions),
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
                begin = self.waiting_for
                if (begin == "ready" and not ready) or (begin == "free" and gang_active):
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
        self._repeatable = self._pause = None
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
        """A completed part is valid (W3-2); the next one begins after its rule."""
        execution = self.current
        self._valid.append(execution.index)
        self._record("module_end", at, execution=execution.index,
                     module=execution.module.module_id, valid=True, reason=None)
        text = execution_summary(self, execution.index, until=at)
        following = (self.modules[self._next_module]
                     if self.kind == "procedure" and self._next_module < len(self.modules)
                     else None)
        repeat = (((REPEAT, LABELS[REPEAT]),)
                  if self.kind == "module" and execution.module.presence_only else ())
        if following is None:
            self._end(at, "finished")
            closing = f" Kalibrierung abgeschlossen. {COMPLETED}" if self.kind == "procedure" else ""
            instruction = self._emit(at, "result", text + closing, repeat, execution)
            if repeat:
                self._repeatable = instruction.seq
            return [instruction]
        if following.begin == "next":
            self.phase = "pause"
            if following.optional:
                message = (f"{text} Pause. „{LABELS[NEXT]}“ beginnt den freiwilligen "
                           f"{following.heading}, „{LABELS[FINISH]}“ schließt die "
                           "Kalibrierung ab.")
                actions = ((NEXT, LABELS[NEXT]), (FINISH, LABELS[FINISH]))
            else:
                message = f"{text} Pause. {following.heading} beginnt mit „{LABELS[NEXT]}“."
                actions = ((NEXT, LABELS[NEXT]), (CANCEL, LABELS[CANCEL]))
            instruction = self._emit(at, "pause", message, actions, execution)
            self._pause = instruction.seq
            return [instruction]
        self.phase = "waiting"
        after = f" {following.heading} beginnt nach Bereit." if following.begin == "ready" else ""
        return [self._emit(at, "part", text + after, (), execution)]

    def _end(self, at, reason, *, user_id=None):
        self.phase = "ended"
        self.ended_at, self.end_reason = at, reason
        self._pause = None
        self._record("end", at, reason=reason, valid=self.valid, user_id=user_id,
                     valid_executions=list(self._valid))

    def interrupt(self, at: datetime, reason: str, *, user_id=None) -> list[Instruction]:
        """End an active run; only the part running now becomes invalid (W-2a, W3-2).

        An open step is recorded without reference. No instruction follows;
        the message names the invalid part and whether completed parts remain.
        """
        if not self.active:
            return []
        execution = self.current
        if self.phase == "running":
            if execution.sent_at is not None:
                step = execution.module.steps[execution.step]
                self.steps.append(self._record(
                    "step", at, execution=execution.index,
                    module=execution.module.module_id, step=step.key,
                    reference=step.reference, handoff_at=execution.sent_at, closed_at=at,
                    outcome="cancelled", action=None, user_id=user_id, time_fired=None,
                ))
            self._invalid = execution.index
            self._record("module_end", at, execution=execution.index,
                         module=execution.module.module_id, valid=False, reason=reason)
        self._repeatable = None
        self._end(at, reason, user_id=user_id)
        text = INTERRUPTIONS[reason]
        if not self._valid:
            return [self._emit(at, "invalid", f"Kalibrierung ungültig: {text}. "
                                              "Sie wird nicht ausgewertet.")]
        remaining = "die abgeschlossenen Teile werden nach dem Sitzungsende ausgewertet."
        if self._invalid is not None:
            return [self._emit(at, "interrupted", (
                f"Kalibrierung unterbrochen: {text}. {execution.module.heading} ist "
                f"ungültig; {remaining}"))]
        return [self._emit(at, "interrupted", f"Kalibrierung beendet: {text}. "
                                              f"{remaining[0].upper()}{remaining[1:]}")]

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
        return self._answer(seq, kind, at, user_id, message_id=message_id,
                            restart_allowed=restart_allowed)

    def answer_pause(self, kind: str, at: datetime,
                     user_id: str | None) -> tuple[str, list[Instruction]]:
        """Answer the open pause from the panel, like its message would be answered.

        Only the pause actions the message offers are accepted; without an
        open pause the answer is unavailable and changes nothing.
        """
        if kind not in {item["action"] for item in self.pause_actions()}:
            return "unavailable", []
        return self._answer(self._pause, kind, at, user_id, source="panel")

    def _answer(self, seq, kind, at, user_id, *, message_id=None, restart_allowed=True,
                source=None):
        context = self._messages[seq]
        disposition, out = self._apply_reply(
            seq, kind, context, at, user_id, restart_allowed,
        )
        if disposition == "accepted":
            self._answered.add(seq)
        extra = {"source": source} if source else {}
        self._record("reply", at, seq=seq, execution=context["execution"],
                     message_id=message_id, action=kind, time_fired=at,
                     user_id=user_id, disposition=disposition, **extra)
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
                and self.phase == "ended"):
            # A single module repeats under its own start condition.
            self._record("repeat", at, execution=execution.index, user_id=user_id)
            self.phase, self.ended_at, self.end_reason = "waiting", None, None
            self._repeatable, self._repeat_module = None, execution.module
            return "accepted", []
        if self.phase != "pause" or seq != self._pause:
            return stale
        if kind == NEXT:
            self._start_execution(at)
            return "accepted", []
        if kind == FINISH and FINISH in context["actions"]:
            self._end(at, "finished", user_id=user_id)
            return "accepted", [self._emit(
                at, "result", f"Kalibrierung abgeschlossen. {COMPLETED}")]
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


def _sequence(groups, modules):
    """Referenced expectations of one run in their physical order (W-2b).

    Steps follow the protocol, within a step the opening comes before its
    closing. Each entry is (kind, protocol step, reference operation).
    """
    references = []
    for group in groups:
        module = modules[group[0]["module"]]
        operations = _operations(module)
        roles = {step.key: step for step in module.steps}
        for item in filter(_referenced, group):
            references += [(kind, item, (item["execution"], operations[item["step"], kind]))
                           for kind in roles[item["step"]].expects]
    return references


def _assign(references, events):
    """Assign recognitions along the physical order (W-2b); {recognition: reference}.

    ``references`` from `_sequence`, ``events`` as (kind, time) in time
    order, both as positions. Each recognition goes on its own to at most one
    reference of its kind, each reference takes at most one recognition, and
    no pairs cross the order over all kinds. A recognition is eligible after
    the window of the previous and before the window of the next reference of
    its kind. Chosen are the most pairs, then the smallest sum of distances to
    the windows; on a full tie the earlier recognition and the earlier
    reference.
    """
    bounds = {}
    for kind in {reference[0] for reference in references}:
        order = [index for index, reference in enumerate(references) if reference[0] == kind]
        for position, index in enumerate(order):
            bounds[index] = (
                references[order[position - 1]][1]["closed_at"] if position else None,
                references[order[position + 1]][1]["handoff_at"]
                if position + 1 < len(order) else None,
            )

    def eligible(index, kind, at):
        lower, upper = bounds[index]
        return (references[index][0] == kind and (lower is None or at > lower)
                and (upper is None or at < upper))

    # Per first free reference the best result of the later recognitions;
    # options are compared from the end.
    following = [(0, timedelta(), None)] * (len(references) + 1)
    tables = []
    for kind, at in reversed(events):
        table = []
        for free in range(len(references) + 1):
            options = [(following[index + 1][0] + 1,
                        following[index + 1][1] + _distance(at, references[index][1]), index)
                       for index in range(free, len(references)) if eligible(index, kind, at)]
            options.append((*following[free][:2], None))
            table.append(min(options, key=lambda option: (-option[0], option[1])))
        tables.append(table)
        following = table
    assignment, free = {}, 0
    for position, table in enumerate(reversed(tables)):
        index = table[free][2]
        if index is not None:
            assignment[position], free = index, index + 1
    return assignment


def _merged(references, events, assignment):
    """Reference door operations merged by the recognition (W-2b).

    An opening assigned to one reference door operation is followed among the
    door recognitions by a closing assigned to a later one. The assignment
    stays as it is.
    """
    doors = [position for position, (kind, _) in enumerate(events)
             if kind in (DOOR_OPEN, DOOR_CLOSE)]
    merged = []
    for opening, closing in pairwise(doors):
        if (events[opening][0] != DOOR_OPEN or events[closing][0] != DOOR_CLOSE
                or opening not in assignment or closing not in assignment):
            continue
        first, second = references[assignment[opening]], references[assignment[closing]]
        if second[2] > first[2]:
            merged.append({
                "opening": {"at": events[opening][1], "execution": first[1]["execution"],
                            "step": first[1]["step"]},
                "closing": {"at": events[closing][1], "execution": second[1]["execution"],
                            "step": second[1]["step"]},
            })
    return merged


def _assign_run(groups, events, modules):
    """Assignment of one run: found recognition per referenced expectation,
    the additional recognitions and the merged door operations.

    Considered are recognitions from the first handoff to the last window of
    the run, except inside the window of a step without reference.
    """
    items = [item for group in groups for item in group]
    excluded = [(item["handoff_at"], item["closed_at"])
                for item in items if not _referenced(item)]
    observed = [event for event in events
                if items[0]["handoff_at"] <= event[1] <= items[-1]["closed_at"]
                and not any(start <= event[1] <= end for start, end in excluded)]
    references = _sequence(groups, modules)
    assignment = _assign(references, observed)
    found = {}
    for position, index in assignment.items():
        kind, item, _ = references[index]
        found[item["execution"], item["step"], kind] = observed[position][1]
    additional = [event for position, event in enumerate(observed) if position not in assignment]
    return found, additional, _merged(references, observed, assignment)


def _state_at(presence, at):
    state = None
    for moment, value in presence:
        if moment > at:
            break
        state = value
    return state


def _after_run_bound(modules, following, until):
    """End of the window in which the after-run presence goes off (W-2, W-3a).

    The first handoff of the next execution of the run, without one ``until``.
    Begins the next execution with „Kabine ist leer“ (P2 after Gang 1), the
    window reaches that reply (Nutzer 10.11.): until then the person leaves the
    cabin. Assignment and the other results keep their windows.
    """
    if following is None:
        return until
    first = following[0]
    module = modules.get(first["module"])
    if module is not None:
        step = next((step for step in module.steps if step.key == first["step"]), None)
        if step is not None and step.role == "empty_after" and _referenced(first):
            return first["closed_at"]
    return first["handoff_at"]


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


def _gang_result(module, items, gangs, gang_heat, until, end, entry_close):
    """Gang of one execution: the gang whose start is the recognition assigned
    to the entry closing (W-2b), otherwise the first with its start from the
    instruction to enter to the instruction to leave (W-2)."""
    roles = {step.key: step for step in module.steps}
    entry = next((item for item in items if roles[item["step"]].role == "entry"), None)
    exit_open = next((item for item in items if roles[item["step"]].role == "exit_open"), None)
    if entry is None or not _referenced(entry):
        return None
    begin = items[0]["handoff_at"]
    inside = [g for g in gangs if begin <= g["start"] < end]
    limit = exit_open["handoff_at"] if exit_open is not None else end
    own = [g for g in gangs if entry_close is not None and g["start"] == entry_close]
    own = own or [g for g in inside if entry["handoff_at"] <= g["start"] < limit]
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
    to the referenced steps by `_assign` (W-2b). Each reference is found in
    its window, assigned outside it with its offset, or missing; unassigned
    recognitions of the run are additional, and merged door operations are
    noted. ``gangs``: non-retracted gangs as mappings with start, confirmed,
    end, end_reason and infusions. ``gang_heat``: intervals of booked heating
    while the heating decision is the gang demand.
    """
    events = sorted((e for e in events if e[0] in EVALUATED_KINDS), key=lambda e: e[1])
    grouped = _by_execution(steps)
    ends, keys = _execution_ends(grouped)
    found, additional, merged = {}, [], []
    for run_id in dict.fromkeys(key[0] for key in keys):
        run_found, run_additional, run_merged = _assign_run(
            [grouped[key] for key in keys if key[0] == run_id], events, modules)
        found.update({(run_id, *key): at for key, at in run_found.items()})
        additional += [{"run_id": run_id, "kind": kind, "at": at}
                       for kind, at in run_additional]
        merged += [{"run_id": run_id, **item} for item in run_merged]
    executions = []
    totals = {"gangs": {"reference": 0, "found": 0, "missing": 0, "additional": 0},
              "events": {"reference": 0, "found": 0, "outside": 0, "missing": 0,
                         "additional": len(additional), "merged": len(merged)},
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
                status = ("missing" if at is None else
                          "found" if item["handoff_at"] <= at <= item["closed_at"] else
                          "outside")
                expected.append({"kind": kind, "status": status, "at": at,
                                 "offset_s": None if at is None else _offset(at, item)})
                totals["events"][status] += 1
            recognition.append({"step": item["step"], "reference": item["reference"],
                                "window": [item["handoff_at"], item["closed_at"]],
                                "expected": expected})
            totals["events"]["reference"] += len(expected)
        following = _after_run_bound(
            modules, next((grouped[k] for k in keys[keys.index(key) + 1:] if k[0] == key[0]),
                          None), until)
        presence_result = (_presence_result(module, items, presence, following)
                           if has_presence else None)
        if presence_result:
            totals["empty_cabin_reports"] += sum(e["reports"] for e in presence_result["empty"])
        entry = next((step.key for step in module.steps if step.role == "entry"), None)
        gang = (_gang_result(module, items, gangs, gang_heat, until, ends[key],
                             found.get((*key, entry, DOOR_CLOSE)))
                if module.gang else None)
        if gang is not None:
            totals["gangs"]["reference"] += 1
            totals["gangs"]["found"] += gang["found"]
            totals["gangs"]["missing"] += not gang["found"]
            totals["gangs"]["additional"] += gang["additional"]
        executions.append({"run_id": key[0], "execution": key[1], "module": module.module_id,
                           "recognition": recognition, "presence": presence_result,
                           "gang": gang})
    return {"executions": executions, "additional": additional, "merged": merged,
            "totals": totals}


def protocol_steps(records: Iterable[Mapping]) -> tuple[list[dict], list[dict]]:
    """Closed steps of valid parts and the parts passed over (W-2a, W3-2).

    A run with part validity (``validity`` "part" in its start record) keeps
    every part it completed (``module_end`` valid); the part running at an
    interruption or at an unclean stop is passed over. An older run without
    part validity counts only as a whole: its last ``end`` record must be
    valid. Each passed-over entry names the run and, for a part, its
    execution and module.
    """
    records = [record for record in records if "run_id" in record]
    validity, whole, parts = {}, {}, {}
    for record in records:
        run_id, event = record["run_id"], record.get("event")
        if event == "start":
            validity[run_id] = record.get("validity")
        elif event == "end":
            whole[run_id] = record.get("valid") is True
        elif event == "module_end":
            parts[run_id, record.get("execution")] = record.get("valid") is True

    def valid(run_id, execution):
        if validity.get(run_id) == "part":
            return parts.get((run_id, execution), False)
        return whole.get(run_id, False)

    steps = [dict(record) for record in records
             if record.get("event") == "step" and valid(record["run_id"], record["execution"])]
    skipped = []
    for run_id in dict.fromkeys(record["run_id"] for record in records):
        if validity.get(run_id) != "part":
            if not whole.get(run_id):
                skipped.append({"run_id": run_id, "execution": None, "module": None})
            continue
        skipped += [{"run_id": run_id, "execution": record["execution"],
                     "module": record.get("module")}
                    for record in records
                    if record["run_id"] == run_id and record.get("event") == "module"
                    and not valid(run_id, record["execution"])]
    return steps, skipped


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
    """Live result of one part from presence; doors and infusions follow later."""
    items = [step for step in run.steps if step["execution"] == index]
    module = next(e.module for e in run.executions if e.index == index)
    if not run.has_presence:
        return f"{module.name or f'Modul {module.label}'} beendet, ohne Präsenzmessung."
    result = _presence_result(module, items, run.presence, until)
    if module.presence_only:
        name = module.name or f"Modul {module.label}"
        reports = sum(e["reports"] for e in result["empty"])
        seconds = sum(e["present_s"] for e in result["empty"])
        if not reports:
            return f"{name}: keine Anwesenheit bei leerer Kabine."
        return (f"{name}: {reports}-mal Anwesenheit bei leerer Kabine, "
                f"zusammen {seconds:g} s.")
    parts = [f"{module.heading} beendet."]
    dropouts = result["dropouts"]
    if dropouts is not None:
        parts.append(f"Präsenz: {dropouts['count']} Aussetzer"
                     + (f", zusammen {sum(dropouts['durations_s']):g} s."
                        if dropouts["count"] else "."))
    if result["door_watch"] is not None:
        parts.append(f"Vor der offenen Tür {result['door_watch']['reports']}-mal anwesend.")
    roles = {step.key: step.role for step in module.steps}
    if result["after_run_s"] is not None:
        parts.append(f"Nachlauf {result['after_run_s']:g} s.")
    elif any(roles.get(step["step"]) == "exit_close" and _referenced(step) for step in items):
        parts.append("Präsenz meldet noch Anwesenheit.")
    return " ".join(parts)


def evaluation_summary(result: dict) -> str:
    totals = result["totals"]
    gangs, events = totals["gangs"], totals["events"]
    parts = ["Kalibrierung ausgewertet."]
    if gangs["reference"] or gangs["additional"]:
        parts.append(f"Gänge {gangs['found']} von {gangs['reference']} gefunden, "
                     f"{gangs['additional']} zusätzlich.")
    parts.append(f"Türen und Aufgüsse {events['found']} von {events['reference']} im Fenster "
                 f"gefunden, {events['outside']} außerhalb des Fensters zugeordnet, "
                 f"{events['additional']} zusätzlich.")
    parts.append(f"Anwesenheit bei leerer Kabine: {totals['empty_cabin_reports']}-mal.")
    parts.append("Einzelheiten im Archiv.")
    return " ".join(parts)
