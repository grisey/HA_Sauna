"""Benachrichtigungen der Sauna (BN): Anlässe, Einstellungen, Texte und Ablauf.

Ohne HA-Abhängigkeit. Die Laufzeit liefert je Zyklus den beobachteten Zustand
(:class:`Facts`); :class:`Notifier` antwortet mit den fälligen Nachrichten und
führt das Protokoll. Steuerung, Erkennung und Gangmodell lesen weder Ablauf
noch Protokoll. Antworten mit Wirkung (Gang beenden, Ausschalten) gibt der
Ablauf nur als Auftrag zurück; die Laufzeit führt sie über die vorhandenen
Bedienwege mit der Steuerberechtigung des Antwortenden aus.

Empfänger (``audience``):

* ``participants``: Teilnehmer der Sitzung (Sitzungsempfänger)
* ``timer``: Ziele des startenden Benutzers, ohne bekannten Benutzer die
  anwesenden Geräte, ohne anwesendes Gerät alle Zugeordneten
* ``assigned``: alle Zugeordneten, auch ohne Sitzung
* ``user``: die eigenen Ziele eines Benutzers (Testnachricht)
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo
from math import isfinite

from . import ready_forecast

TITLE = "Sauna"
ACTION_PREFIX = "HA_SAUNA_NOTIFY_"

# Antwortarten
RECEIVED = "received"
TIMER_SET = "timer_set"
END_GANG = "end_gang"
SWITCH_OFF = "switch_off"
ACTION_LABELS = {
    RECEIVED: "Empfangen",
    TIMER_SET: "Uhr ist gestellt",
    END_GANG: "Gang beenden",
    SWITCH_OFF: "Ausschalten",
}

TEST = "test"
# Anlässe in der Reihenfolge der Einstellungen: (Bezeichnung, Beschreibung)
OCCASIONS = {
    "timer_start": (
        "Zeitschaltuhr beim Start",
        ("Beim Sitzungsbeginn: Zeitschaltuhr aufziehen. Wiederholt ohne Antwort, bis "
         "„Uhr ist gestellt“ beantwortet ist; die Antwort startet die Zählung der Ofenzeit."),
    ),
    "timer_expiry": (
        "Zeitschaltuhr läuft ab",
        "Vor Ablauf der gezählten Ofenzeit seit der letzten Bestätigung.",
    ),
    "ready": (
        "Bereit",
        "Beim Erreichen von Bereit, vor dem ersten und nach jedem Gang.",
    ),
    "next_gang": (
        "Nächster Gang voraussichtlich",
        ("Sobald die Temperatur nach dem Austritt wieder gleichmäßig steigt und die Dauer "
         "bis Bereit abschätzbar ist; ebenso beim Aufheizen vor dem ersten Gang."),
    ),
    "gang_end": (
        "Rückfrage Gangende",
        ("Der Präsenzsensor meldet nach einer Tür im Gang Abwesenheit, der Gang läuft "
         "weiter. „Gang beenden“ wirkt wie die Taste auf der Steuerungsseite."),
    ),
    "idle": (
        "Sauna läuft ohne Nutzung",
        ("Im Bereit nach zwei Abschaltungen an der oberen Regeltemperatur ohne Gang und "
         "ohne Anwesenheit. „Ausschalten“ schaltet den Betrieb aus."),
    ),
    "session_end": (
        "Sauna aus",
        "Beim Sitzungsende mit Gängen, Aufgüssen, Saunazeit, Energie und Kosten.",
    ),
    "safety": (
        "Sicherheit",
        ("Ofen meldet „an“ trotz Befehl „aus“, obere Messung fällt beim Heizen aus, "
         "Übertemperatur. Geht an alle Zugeordneten, auch ohne Sitzung."),
    ),
}
AUDIENCES = {
    "timer_start": "timer", "timer_expiry": "timer", "ready": "participants",
    "next_gang": "participants", "gang_end": "participants", "idle": "participants",
    "session_end": "participants", "safety": "assigned", TEST: "user",
}
# Einstellbare Werte: (Bezeichnung, Einheit, Vorgabe, Beschreibung)
VALUES = {
    "timer_runtime_minutes": (
        "Laufzeit der Zeitschaltuhr", "min", 240.0,
        ("Ofenzeit, nach der die mechanische Zeitschaltuhr abläuft. Gezählt wird die "
         "gebuchte Heizzeit seit der letzten Bestätigung."),
    ),
    "timer_lead_minutes": (
        "Erinnerung vor Ablauf", "min", 15.0,
        "So lange vor Ablauf der gezählten Ofenzeit geht die Erinnerung.",
    ),
    "timer_repeat_minutes": (
        "Wiederholung ohne Antwort", "min", 10.0,
        "Abstand, in dem die Startnachricht wiederholt wird, bis sie beantwortet ist.",
    ),
    "overtemperature_c": (
        "Übertemperatur", "°C", 105.0,
        "Ab dieser oberen Temperatur geht eine Sicherheitsmeldung. Sie schaltet nichts ab.",
    ),
}

# Ergebnisse einer Antwort und ihr Protokolltext
OUTCOMES = {
    "received": "Empfang bestätigt",
    "confirmed": "Zeitschaltuhr bestätigt",
    "effect": "ausgeführt",
    "no_permission": "ohne Steuerberechtigung, keine Wirkung",
    "late": "wirkungslos, der Anlass besteht nicht mehr",
    "duplicate": "bereits beantwortet",
    "unknown": "unbekannte Nachricht",
}


def is_action(value: object) -> bool:
    return isinstance(value, str) and value.startswith(ACTION_PREFIX)


def decimal(value: float, places: int = 1) -> str:
    """Zahl mit Dezimalkomma; ganze Werte ohne Nachkommastellen."""
    rounded = round(float(value), places)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.{places}f}".replace(".", ",")


@dataclass(frozen=True)
class NotificationSettings:
    """Anlässe einzeln ein- und ausschaltbar; Werte für Zeitschaltuhr und Sicherheit."""

    disabled: frozenset[str] = frozenset()
    timer_runtime_minutes: float = VALUES["timer_runtime_minutes"][2]
    timer_lead_minutes: float = VALUES["timer_lead_minutes"][2]
    timer_repeat_minutes: float = VALUES["timer_repeat_minutes"][2]
    overtemperature_c: float = VALUES["overtemperature_c"][2]

    def __post_init__(self) -> None:
        if not isinstance(self.disabled, frozenset) or self.disabled - OCCASIONS.keys():
            raise ValueError("Unbekannter Anlass")
        for key in VALUES:
            value = getattr(self, key)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not isfinite(value) or value <= 0):
                raise ValueError(f"{VALUES[key][0]} muss größer als null sein")
        if self.timer_lead_minutes >= self.timer_runtime_minutes:
            raise ValueError("Die Erinnerung muss vor Ablauf der Laufzeit liegen")

    def enabled(self, occasion: str) -> bool:
        return occasion == TEST or occasion not in self.disabled

    @classmethod
    def from_dict(cls, data: Mapping | None) -> NotificationSettings:
        if data is None:
            return cls()
        if not isinstance(data, Mapping) or set(data) - {"occasions", *VALUES}:
            raise ValueError("Ungültige Benachrichtigungseinstellungen")
        occasions = data.get("occasions", {})
        if (not isinstance(occasions, Mapping) or set(occasions) - OCCASIONS.keys()
                or any(not isinstance(value, bool) for value in occasions.values())):
            raise ValueError("Ungültige Anlässe")
        return cls(
            frozenset(key for key, value in occasions.items() if value is False),
            **{key: float(data[key]) if isinstance(data[key], int) and not isinstance(
                data[key], bool) else data[key] for key in VALUES if key in data},
        )

    def as_dict(self) -> dict:
        return {
            "occasions": {key: key not in self.disabled for key in OCCASIONS},
            **{key: getattr(self, key) for key in VALUES},
        }


def factory_deviations(settings: NotificationSettings) -> dict:
    """E2: Werte und Anlässe, die von der Werkseinstellung abweichen, mit deren Wert.

    Benachrichtigungen haben keinen gespeicherten Standard; Vergleichsstufe ist
    immer die Werkseinstellung.
    """
    factory, current = NotificationSettings().as_dict(), settings.as_dict()
    result = {key: factory[key] for key in VALUES if current[key] != factory[key]}
    occasions = {
        key: factory["occasions"][key]
        for key in OCCASIONS
        if current["occasions"][key] != factory["occasions"][key]
    }
    if occasions:
        result["occasions"] = occasions
    return result


def describe() -> dict:
    """Bezeichnungen, Einheiten und Vorgaben für die Einstellungen im Panel."""
    return {
        "occasions": [
            {"id": key, "label": label, "description": text}
            for key, (label, text) in OCCASIONS.items()
        ],
        "values": [
            {"key": key, "label": label, "unit": unit, "default": default,
             "description": text}
            for key, (label, unit, default, text) in VALUES.items()
        ],
    }


@dataclass(frozen=True)
class GangFact:
    gang_id: str
    started_at: datetime
    number: int
    acting: bool


@dataclass(frozen=True)
class Facts:
    """Beobachteter Zustand am Ende eines Zyklus (nur lesend erhoben)."""

    now: datetime
    session_id: str | None = None
    session_started_at: datetime | None = None
    operation_enabled: bool = False
    ready_marks: tuple[tuple[datetime, bool], ...] = ()
    target_c: float | None = None
    gang: GangFact | None = None
    gang_count: int = 0
    last_gang_end: datetime | None = None
    door_open: bool = False
    door_events: tuple[tuple[str, datetime], ...] = ()  # (door_open|door_close, wirksam)
    presence: tuple[str, datetime] | None = None  # letzte Sensormeldung present/absent
    last_present_at: datetime | None = None
    contactor: bool | None = None
    heating: tuple[tuple[datetime, datetime | None], ...] = ()
    switch_offs: tuple[datetime, ...] = ()  # Abschaltungen an der oberen Regeltemperatur
    temperature: tuple[datetime, float] | None = None  # Regeltemperatur (Empfang, Wert)
    upper_temperature_c: float | None = None
    upper_failed: bool = False
    heating_against_off: bool = False  # Rückmeldung „an“ trotz Befehl „aus“, bestätigt


@dataclass(frozen=True)
class Message:
    seq: int
    occasion: str
    at: datetime
    text: str
    audience: str
    actions: tuple[str, ...] = ()
    session_id: str | None = None
    gang_id: str | None = None
    user_id: str | None = None
    title: str = TITLE


@dataclass
class _Session:
    session_id: str
    started_at: datetime
    timer_sent_at: datetime | None = None
    timer_confirmed_at: datetime | None = None
    timer_confirmed_by: str | None = None
    expiry_sent_for: datetime | None = None
    ready_seen: int = 0
    forecast_key: tuple | None = None
    forecast_sent: set = field(default_factory=set)
    samples: list = field(default_factory=list)
    asked_gangs: set = field(default_factory=set)
    idle_sent: set = field(default_factory=set)


class Notifier:
    """Anlässe der Sauna: Zustand → Nachrichten, Antworten → Bestätigung oder Auftrag."""

    def __init__(self, settings: NotificationSettings | None = None,
                 zone: tzinfo | None = None) -> None:
        self.settings = settings or NotificationSettings()
        self.zone = zone
        self._seq = 0
        self._messages: dict[int, Message] = {}
        self._answered: set[int] = set()
        self._session: _Session | None = None
        self._safety: set[str] = set()
        self.tests: dict[str, dict] = {}
        self.records: list[dict] = []

    # -- Protokoll ---------------------------------------------------------

    def _record(self, event: str, at: datetime, session_id: str | None, **values) -> None:
        self.records.append({"event": event, "at": at, "session_id": session_id, **values})

    def take_records(self) -> list[dict]:
        records, self.records = self.records, []
        return records

    def message(self, seq: int) -> Message | None:
        return self._messages.get(seq)

    # -- Texte -------------------------------------------------------------

    def _clock(self, at: datetime) -> str:
        local = at.astimezone(self.zone) if self.zone is not None else at
        return local.strftime("%H:%M")

    @staticmethod
    def _celsius(value: float | None) -> str:
        return f"{decimal(value)} °C" if value is not None else ""

    def _emit(self, occasion: str, at: datetime, text: str, *, actions=(),
              session_id=None, gang_id=None, user_id=None) -> Message:
        self._seq += 1
        message = Message(self._seq, occasion, at, text, AUDIENCES[occasion],
                          tuple(actions), session_id, gang_id, user_id)
        self._messages[self._seq] = message
        self._record("message", at, session_id, seq=message.seq, occasion=occasion,
                     occasion_label=OCCASIONS.get(occasion, ("Testnachricht",))[0],
                     text=text, actions=[ACTION_LABELS[kind] for kind in actions],
                     gang_id=gang_id)
        return message

    def test(self, at: datetime, user_id: str | None) -> Message:
        """Testnachricht an die eigenen Ziele eines Benutzers (BN-1)."""
        message = self._emit(TEST, at, "Testnachricht der Sauna", actions=(RECEIVED,),
                             session_id=self._session.session_id if self._session else None,
                             user_id=user_id)
        self.tests[user_id] = {"seq": message.seq, "sent_at": at, "targets": 0,
                               "accepted": 0, "replied_at": None, "replied_by": None}
        return message

    def handed_off(self, message: Message, target: Mapping, accepted: bool,
                   evidence: str, message_id: str | None) -> None:
        self._record("handoff", message.at, message.session_id, seq=message.seq,
                     occasion=message.occasion, target=dict(target), accepted=accepted,
                     evidence=evidence, message_id=message_id)
        if message.occasion == TEST and message.user_id in self.tests:
            status = self.tests[message.user_id]
            if status["seq"] == message.seq:
                status["targets"] += 1
                status["accepted"] += 1 if accepted else 0

    def addressed(self, message: Message, count: int, basis: str) -> None:
        """Protokoll der Empfängerwahl einer Nachricht."""
        self._record("recipients", message.at, message.session_id, seq=message.seq,
                     occasion=message.occasion, count=count, basis=basis)
        if message.occasion == TEST and message.user_id in self.tests:
            status = self.tests[message.user_id]
            if status["seq"] == message.seq:
                status["addressed"] = count

    # -- Ablauf ------------------------------------------------------------

    def advance(self, facts: Facts) -> tuple[list[Message], str | None]:
        """Fällige Nachrichten; zweiter Wert: die Sitzung, die eben endete."""
        out: list[Message] = []
        ended = None
        current = self._session
        if current is not None and current.session_id != facts.session_id:
            ended = current.session_id
            self._session = current = None
        if facts.session_id is not None and current is None:
            current = self._session = _Session(
                facts.session_id, facts.session_started_at or facts.now,
                ready_seen=sum(1 for _, ready in facts.ready_marks if ready),
            )
            if facts.operation_enabled and self.settings.enabled("timer_start"):
                out.append(self._timer_start(facts, current))
        if current is not None:
            out += self._timer(facts, current)
            out += self._ready(facts, current)
            out += self._next_gang(facts, current)
            out += self._gang_end(facts, current)
            out += self._idle(facts, current)
        out += self._safety_messages(facts)
        return out, ended

    # Z1/Z2 -----------------------------------------------------------------

    def _timer_start(self, facts: Facts, session: _Session) -> Message:
        repeat = session.timer_sent_at is not None
        session.timer_sent_at = facts.now
        text = "Sauna gestartet – Zeitschaltuhr aufziehen"
        if repeat:
            text = "Sauna läuft – Zeitschaltuhr aufziehen (keine Antwort bisher)"
        return self._emit("timer_start", facts.now, text, actions=(TIMER_SET,),
                          session_id=session.session_id)

    def heating_seconds(self, facts: Facts, since: datetime) -> float:
        """Gebuchte Heizzeit seit ``since`` (Schützrückmeldung wie die Heizzeitbuchung)."""
        total = 0.0
        for start, end in facts.heating:
            end = facts.now if end is None else end
            begin = max(start, since)
            if end > begin:
                total += (end - begin).total_seconds()
        return total

    def _timer(self, facts: Facts, session: _Session) -> list[Message]:
        acting = facts.gang is not None and facts.gang.acting
        if not facts.operation_enabled or acting:
            return []  # BN-2: im Gang nichts an die Teilnehmer; nachgeholt nach dem Gang
        if (session.timer_confirmed_at is None and session.timer_sent_at is not None
                and self.settings.enabled("timer_start")
                and facts.now - session.timer_sent_at
                >= timedelta(minutes=self.settings.timer_repeat_minutes)):
            return [self._timer_start(facts, session)]
        if (session.timer_sent_at is None and session.timer_confirmed_at is None
                and self.settings.enabled("timer_start")):
            # Start ohne Betrieb (Wiederaufnahme) oder Anlass eben eingeschaltet
            return [self._timer_start(facts, session)]
        confirmed = session.timer_confirmed_at
        if (confirmed is None or session.expiry_sent_for == confirmed
                or not self.settings.enabled("timer_expiry")):
            return []
        counted = self.heating_seconds(facts, confirmed)
        runtime = self.settings.timer_runtime_minutes * 60
        if counted < runtime - self.settings.timer_lead_minutes * 60:
            return []
        session.expiry_sent_for = confirmed
        remaining = max(1, round((runtime - counted) / 60))
        return [self._emit("timer_expiry", facts.now,
                           f"Zeitschaltuhr läuft in etwa {remaining} min ab",
                           actions=(TIMER_SET,), session_id=session.session_id)]

    # B1 --------------------------------------------------------------------

    def _ready(self, facts: Facts, session: _Session) -> list[Message]:
        reached = sum(1 for _, ready in facts.ready_marks if ready)
        if reached <= session.ready_seen:
            return []
        session.ready_seen = reached
        if (not self.settings.enabled("ready") or not facts.operation_enabled
                or facts.gang is not None and facts.gang.acting):
            return []
        target = self._celsius(facts.target_c)
        text = (f"Sauna bereit, {target}" if facts.gang_count == 0
                else f"Bereit für Gang {facts.gang_count + 1}, {target}")
        return [self._emit("ready", facts.now, text.rstrip(", "),
                           session_id=session.session_id)]

    # B2 --------------------------------------------------------------------

    def _anchor(self, facts: Facts) -> datetime | None:
        """Austrittsschluss bzw. Heizbeginn: Beginn des Abschnitts, sonst ``None``."""
        if facts.door_open or facts.contactor is not True or not facts.heating:
            return None
        start, end = facts.heating[-1]
        if end is not None:
            return None
        anchor = max(start, facts.last_gang_end or facts.session_started_at or start)
        closes = [at for kind, at in facts.door_events if kind == "door_close"]
        if closes:
            anchor = max(anchor, closes[-1])
        return anchor

    def _next_gang(self, facts: Facts, session: _Session) -> list[Message]:
        key = facts.gang_count
        if (not self.settings.enabled("next_gang") or not facts.operation_enabled
                or facts.gang is not None or key in session.forecast_sent
                or any(ready for _, ready in facts.ready_marks[-1:])
                or facts.target_c is None or facts.temperature is None):
            if facts.gang is not None:
                session.samples.clear()
            return []
        anchor = self._anchor(facts)
        if anchor is None:
            return []
        at, value = facts.temperature
        if session.forecast_key != (key, anchor):
            session.forecast_key = (key, anchor)
            session.samples = [item for item in session.samples if item[0] >= anchor]
        if session.samples and at <= session.samples[-1][0]:
            return []
        session.samples.append((at, value))
        origin = session.samples[0][0]
        times = [(item[0] - origin).total_seconds() for item in session.samples]
        result = ready_forecast.forecast(
            times, [item[1] for item in session.samples], facts.target_c,
            anchor=(anchor - origin).total_seconds(),
        )
        if result is None:
            return []
        session.forecast_sent.add(key)
        session.samples.clear()
        ready_at = origin + timedelta(seconds=result.ready_at)
        minutes = max(1, round((ready_at - facts.now).total_seconds() / 60))
        target = self._celsius(facts.target_c)
        clock = self._clock(ready_at)
        text = (f"Sauna voraussichtlich bereit ab etwa {clock} (in {minutes} min), {target}"
                if key == 0 else
                f"Gang {key + 1} möglich ab etwa {clock} (in {minutes} min), {target}")
        message = self._emit("next_gang", facts.now, text, session_id=session.session_id)
        self._record("forecast", facts.now, session.session_id, seq=message.seq,
                     ready_at=ready_at, anchor=anchor,
                     linear_from=origin + timedelta(seconds=result.linear_from),
                     slope_c_per_min=result.slope_c_per_s * 60,
                     log_evidence=result.log_evidence)
        return [message]

    # G ---------------------------------------------------------------------

    def _gang_end(self, facts: Facts, session: _Session) -> list[Message]:
        gang = facts.gang
        if (gang is None or not self.settings.enabled("gang_end")
                or gang.gang_id in session.asked_gangs or facts.door_open
                or facts.presence is None or facts.presence[0] != "absent"):
            return []
        openings = [at for kind, at in facts.door_events
                    if kind == "door_open" and at >= gang.started_at]
        if not openings or facts.presence[1] < openings[-1]:
            return []
        closes = [at for kind, at in facts.door_events
                  if kind == "door_close" and at >= openings[-1]]
        if not closes:
            return []
        session.asked_gangs.add(gang.gang_id)
        return [self._emit(
            "gang_end", facts.now,
            f"Gang {gang.number} läuft laut Sauna noch – bist du draußen?",
            actions=(END_GANG,), session_id=session.session_id, gang_id=gang.gang_id,
        )]

    # U ---------------------------------------------------------------------

    def _idle(self, facts: Facts, session: _Session) -> list[Message]:
        latched = [at for at, ready in facts.ready_marks if ready]
        if (not self.settings.enabled("idle") or not facts.operation_enabled
                or facts.gang is not None or not latched or not facts.ready_marks[-1][1]):
            return []
        reference = max(latched[-1], facts.last_gang_end or latched[-1])
        if reference in session.idle_sent:
            return []
        if (facts.presence is not None and facts.presence[0] == "present"
                or facts.last_present_at is not None and facts.last_present_at >= reference):
            return []
        if sum(1 for at in facts.switch_offs if at >= reference) < 2:
            return []
        session.idle_sent.add(reference)
        return [self._emit("idle", facts.now,
                           f"Sauna läuft seit {self._clock(reference)} ohne Nutzung",
                           actions=(SWITCH_OFF,), session_id=session.session_id)]

    # E ---------------------------------------------------------------------

    def session_end(self, at: datetime, session_id: str, text: str) -> Message | None:
        if not self.settings.enabled("session_end"):
            return None
        return self._emit("session_end", at, text, session_id=session_id)

    # S ---------------------------------------------------------------------

    def _safety_messages(self, facts: Facts) -> list[Message]:
        checks = {
            "heating_against_off": (
                facts.heating_against_off,
                "Achtung: Ofen meldet „an“, obwohl er aus sein soll",
            ),
            "upper_failed": (
                facts.upper_failed and facts.contactor is True,
                "Achtung: obere Temperaturmessung ausgefallen, während der Ofen heizt",
            ),
            "overtemperature": (
                facts.upper_temperature_c is not None
                and facts.upper_temperature_c >= self.settings.overtemperature_c,
                (f"Achtung: Übertemperatur {self._celsius(facts.upper_temperature_c)} "
                 f"(Grenze {self._celsius(self.settings.overtemperature_c)})"),
            ),
        }
        out = []
        for key, (active, text) in checks.items():
            if not active:
                ended = (key != "overtemperature" or facts.contactor is not True)
                if ended:
                    self._safety.discard(key)
                continue
            if key in self._safety:
                continue
            self._safety.add(key)
            if self.settings.enabled("safety"):
                out.append(self._emit("safety", facts.now, text,
                                      session_id=facts.session_id))
        return out

    # Antworten -------------------------------------------------------------

    def reply(self, seq: int, kind: str, at: datetime, user_id: str | None,
              *, current_session: str | None) -> str:
        """Ergebnis einer Antwort ohne Bedienwirkung, ``effect`` für einen Auftrag."""
        message = self._messages.get(seq)
        if message is None or kind not in message.actions:
            return "unknown"
        if kind == RECEIVED:
            status = self.tests.get(message.user_id)
            if seq in self._answered:
                return "duplicate"
            self._answered.add(seq)
            if status is not None and status["seq"] == seq:
                status.update(replied_at=at, replied_by=user_id)
            return "received"
        if kind == TIMER_SET:
            session = self._session
            if (session is None or message.session_id != session.session_id
                    or current_session != session.session_id):
                return "late"
            if seq in self._answered:
                return "duplicate"
            self._answered.add(seq)
            session.timer_confirmed_at = at
            session.timer_confirmed_by = user_id
            return "confirmed"
        return "effect"

    def replied(self, seq: int, kind: str, at: datetime, user_id: str | None,
                outcome: str, detail: str | None = None) -> None:
        message = self._messages.get(seq)
        self._record("reply", at, message.session_id if message else None, seq=seq,
                     occasion=message.occasion if message else None,
                     action=ACTION_LABELS.get(kind, kind), user_id=user_id,
                     outcome=outcome, outcome_text=detail or OUTCOMES.get(outcome, outcome))

    @property
    def timer(self) -> dict | None:
        session = self._session
        if session is None:
            return None
        return {"sent_at": session.timer_sent_at, "confirmed_at": session.timer_confirmed_at,
                "confirmed_by": session.timer_confirmed_by}

    def configure(self, settings: NotificationSettings) -> None:
        self.settings = settings


def upper_switch_offs(decisions: Iterable[tuple[datetime, bool, str]]) -> tuple[datetime, ...]:
    """Abschaltungen an der oberen Regeltemperatur: Wechsel von Heizen auf
    „Temperatur erreicht“ (Thermostat, auch im manuellen Modus)."""
    out = []
    heating = None
    for at, heat, reason in decisions:
        if heat is False and reason == "temperature_reached" and heating is True:
            out.append(at)
        heating = heat
    return tuple(out)


def summary(figures: Mapping | None, *, heater_off: bool | None) -> str:
    """Text „Sauna aus“ mit den Kennzahlen der Sitzung (Anzeigewerte, B1)."""
    head = "Sauna aus · " + ("Ofen aus bestätigt" if heater_off is True
                             else "Ofenrückmeldung fehlt" if heater_off is None
                             else "Ofen meldet noch „an“")
    if not figures:
        return head

    def shown(key):
        item = figures.get(key)
        return item.get("display") if isinstance(item, Mapping) else None

    parts = []
    gangs = shown("gang_count")
    if gangs is not None:
        parts.append("1 Gang" if gangs == 1 else f"{gangs} Gänge")
    infusions = shown("infusions_total")
    if infusions is not None:
        parts.append("1 Aufguss" if infusions == 1 else f"{infusions} Aufgüsse")
    sauna = shown("sauna_time_seconds")
    if sauna is not None:
        parts.append(f"Saunazeit {decimal(sauna)} min")
    energy = shown("energy_kwh")
    if energy is not None:
        parts.append(f"{decimal(energy)} kWh")
    cost = shown("cost_eur")
    if cost is not None:
        parts.append(f"{cost:.2f} €".replace(".", ","))
    return head + ("\n" + ", ".join(parts) if parts else "")


def replace_settings(settings: NotificationSettings, data: Mapping) -> NotificationSettings:
    """Teiländerung aus dem Panel: nur genannte Anlässe und Werte."""
    current = settings.as_dict()
    occasions = {**current["occasions"], **dict(data.get("occasions", {}))}
    merged = {**current, **{key: value for key, value in data.items() if key != "occasions"},
              "occasions": occasions}
    return NotificationSettings.from_dict(merged)


