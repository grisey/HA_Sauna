"""Erkennungskontrolle (B4b, Details · Erkennung) aus gespeicherten Sitzungsdaten.

Alle Werte der Ansicht werden hier einmal gerechnet (G19); das Panel formatiert
nur. Grundlage sind ausschließlich Archivinhalte: Zeitleiste der Sitzung,
Erkennungseinträge (``detection``, ``detection_rejected``) und, ab 0.9.0b6,
Zustandsverlauf, verworfene Kandidaten und ihre Episoden (B2). Erkennung,
Gangmodell und Archiv bleiben unverändert.

K-E1: Gänge, Aufgüsse (in Gängen und außerhalb), Türvorgänge.
K-E2: verworfene Kandidaten wie in den Kennzahlen (``key_figures.rejected_summary``).
K-E3: je Ereignis Zeitpunkt, Entscheidungszeit (Zeitpunkt bis Entscheidung) und
die gespeicherte Sicherheit. Für übernommene Ereignisse speichert das Archiv die
Wahrscheinlichkeit bei der Entscheidung, nicht das Verhältnis zur nächstbesten
Erklärung; für verworfene Kandidaten ist das Verhältnis gespeichert.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime

from .key_figures import gang_confirmed, rejected_summary
from .timeline import Kind, project_door_cycles, project_gang_intervals, utc

STATE_LABELS = {
    "closed": "Ruhe",
    "open": "offen",
    "infusion": "Aufguss",
    "decay": "Abklingen",
    "recovery": "Erholung",
    "shift": "Heizwechsel",
}

EVENT_LABELS = {
    Kind.DOOR_OPEN: "Tür geöffnet",
    Kind.DOOR_CLOSE: "Tür geschlossen",
    Kind.INFUSION: "Aufguss",
    Kind.VENTILATION: "Lüften bestätigt",
    Kind.VENTILATION_COMPLETE: "Vollständiges Lüften bestätigt",
    Kind.PERSON_STRONG: "Person erkannt",
    Kind.PERSON_WEAK: "Person erkannt (schwach)",
    Kind.PRESENCE_ENTRY: "Anwesenheit beim Türschluss",
    Kind.PRESENCE_CONFIRMED: "Präsenz nach Türvorgang",
    Kind.PRESENCE_ENDED: "Abwesenheit und Durchlüften",
    Kind.PRESENCE_CLOSE_ENDED: "Türschluss ohne dauerhafte Anwesenheit",
    Kind.ABSENCE_EXIT: "Türvorgang mit Abwesenheit",
}

REASON_LABELS = {"opening_form": "Öffnungsform passt nicht"}

_RECOGNITION_KINDS = frozenset(EVENT_LABELS)


def _time(value) -> datetime | None:
    if value is None:
        return None
    return utc(value if isinstance(value, datetime) else datetime.fromisoformat(value))


def _iso(value) -> str | None:
    moment = _time(value)
    return moment.isoformat() if moment is not None else None


def _seconds(start, end) -> float | None:
    start, end = _time(start), _time(end)
    if start is None or end is None:
        return None
    return (end - start).total_seconds()


_PRESENCE_KINDS = frozenset({
    Kind.PRESENCE_ENTRY, Kind.PRESENCE_CONFIRMED, Kind.PRESENCE_ENDED,
    Kind.PRESENCE_CLOSE_ENDED, Kind.ABSENCE_EXIT,
})


def _source(event: Mapping) -> str:
    """Herkunft eines Ereignisses aus seiner Kennung; ``detector``: Verfahren vor G8."""
    if event.get("kind") in _PRESENCE_KINDS:
        return "Präsenzsensor"
    return {
        "recognition": "Erkennung",
        "detector": "frühere Erkennung",
    }.get(event["event_id"].split(":", 1)[0], "Steuerung")


def _gangs(timeline: Mapping, intervals: Mapping) -> list[dict]:
    """Bestätigte, nicht aufgehobene Gänge in Zeitleistenfolge, wie in den Kennzahlen."""
    archived = [*(timeline.get("completed") or ()),
                *([timeline["active"]] if timeline.get("active") else [])]
    result = []
    for gang in archived:
        interval = intervals.get(gang.get("gang_id"))
        if interval is None or not gang_confirmed(gang):
            continue
        result.append({
            "number": len(result) + 1,
            "gang_id": gang["gang_id"],
            "started_at": _time(interval["started_at"]),
            "ended_at": _time(interval["ended_at"]),
            "end_source_event_id": interval.get("end_source_event_id"),
        })
    return result


def _gang_at(gangs: list[dict], moment: datetime | None, until: datetime | None):
    if moment is None:
        return None
    for gang in gangs:
        end = gang["ended_at"] or until
        if gang["started_at"] <= moment and (end is None or moment <= end):
            return gang
    return None


def _retraction(processed: list[Mapping], gang: Mapping) -> tuple[str | None, str]:
    """Aufhebung eines vorläufigen Gangs (V-29): Zeitpunkt und Grund, soweit gespeichert."""
    source = gang.get("start_source_event_id")
    for event in processed:
        event_id = event.get("event_id") or ""
        if (event.get("kind") == Kind.CONFIRMATION_EXPIRED and source
                and event_id.startswith("deadline:") and event_id.endswith(f":{source}")):
            return _iso(event.get("effective_at")), "Bestätigungsfrist abgelaufen"
        if (event.get("kind") == Kind.MANUAL_GANG_END
                and event_id == f"manual_gang_end:{gang.get('gang_id')}"):
            return _iso(event.get("effective_at")), "von Hand beendet"
    return None, "aufgehoben, Anlass nicht gespeichert"


def recognition_control(
    session: Mapping,
    *,
    ended_at=None,
    now=None,
    detections: Iterable[Mapping] = (),
    not_taken: Iterable[Mapping] = (),
    states: Iterable[Mapping] | None = None,
    rejected: Iterable[Mapping] | None = None,
    episodes: Iterable[Mapping] = (),
) -> dict:
    """Erkennungskontrolle einer Sitzung.

    ``states``, ``rejected``: ``None``, wenn die Sitzung ohne Zustandsverlauf
    aufgezeichnet wurde (vor B2); dann fehlen Spur und K-E2 ausdrücklich.
    """
    timeline = session.get("timeline") or {}
    processed = list(timeline.get("processed") or ())
    until = _time(ended_at) or _time(now)
    cycles = project_door_cycles(timeline)
    intervals = {item["gang_id"]: item
                 for item in project_gang_intervals(timeline, door_cycles=cycles)}
    gangs = _gangs(timeline, intervals)
    numbers = {gang["gang_id"]: gang["number"] for gang in gangs}
    cycle_of = {event_id: cycle for cycle in cycles for event_id in cycle.get("event_ids") or ()}
    detected = {
        record["event"]["event_id"]: record
        for record in detections
        if isinstance(record.get("event"), Mapping) and record["event"].get("event_id")
    }

    def door_meaning(event) -> str:
        cycle = cycle_of.get(event["event_id"])
        for link in (cycle or {}).get("gang_links") or ():
            number = numbers.get(link.get("gang_id"))
            if number is not None and link.get("role") == "entry":
                return f"Eintritt Gang {number}"
            if number is not None and link.get("role") == "exit":
                return f"Austritt Gang {number}"
        gang = _gang_at(gangs, _time(event.get("effective_at")), until)
        return f"Türvorgang im Gang {gang['number']}" if gang else "Türvorgang ohne Gang"

    def meaning(event) -> str:
        kind = event.get("kind")
        ends = next((gang for gang in gangs
                     if gang["end_source_event_id"] == event["event_id"]), None)
        if kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE):
            return door_meaning(event)
        if ends is not None:
            return f"Gangende Gang {ends['number']}"
        gang = _gang_at(gangs, _time(event.get("effective_at")), until)
        if kind == Kind.INFUSION:
            return f"Aufguss im Gang {gang['number']}" if gang else "Aufguss außerhalb eines Gangs"
        starts = next((g for g in gangs if g["started_at"] == _time(event.get("effective_at"))), None)
        if starts is not None and kind in (Kind.PRESENCE_ENTRY, Kind.PRESENCE_CONFIRMED,
                                           Kind.PERSON_STRONG, Kind.PERSON_WEAK):
            return f"Gangbeginn Gang {starts['number']}"
        return f"im Gang {gang['number']}" if gang else "ohne Gang"

    events = []
    for event in processed:
        kind = event.get("kind")
        if kind not in _RECOGNITION_KINDS:
            continue
        record = detected.get(event["event_id"])
        source = _source(event)
        if record is None and source == "Erkennung" and kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE):
            # G9: verworfene Öffnung, vom Gangmodell über die Anwesenheit übernommen
            source = "verworfene Öffnung, über die Anwesenheit übernommen"
        events.append({
            "event_id": event["event_id"],
            "kind": kind,
            "label": EVENT_LABELS[kind],
            "source": source,
            "effective_at": _iso(event.get("effective_at")),
            "decided_at": _iso(event.get("detected_at")),
            "decision_seconds": _seconds(event.get("effective_at"), event.get("detected_at")),
            "probability": record.get("probability") if record else None,
            "meaning": meaning(event),
        })
    events.sort(key=lambda item: item["effective_at"] or "")

    infusions = [e for e in events if e["kind"] == Kind.INFUSION]
    in_gangs = sum(1 for e in infusions if _gang_at(gangs, _time(e["effective_at"]), until))

    track = None
    if states is not None:
        items = sorted(states, key=lambda item: _time(item["at"]))
        track = [
            {
                "at": _iso(item["at"]),
                "until": _iso(items[index + 1]["at"]) if index + 1 < len(items) else _iso(until),
                "state": item.get("state"),
                "label": STATE_LABELS.get(item.get("state"), item.get("state")),
                "probability": item.get("probability"),
            }
            for index, item in enumerate(items)
        ]

    episodes = list(episodes)
    candidates = None
    if rejected is not None:
        taken = {_iso(at) for episode in episodes if episode.get("result") == "door"
                 for at in episode.get("candidates") or ()}
        held = {_iso(at) for episode in episodes if episode.get("result") != "door"
                for at in episode.get("candidates") or ()}
        candidates = []
        for item in sorted(rejected, key=lambda entry: _time(entry["effective_at"])):
            at = _iso(item["effective_at"])
            candidates.append({
                "effective_at": at,
                "decided_at": _iso(item.get("decided_at")),
                "decision_seconds": _seconds(item["effective_at"], item.get("decided_at")),
                "reason": item.get("reason"),
                "reason_label": REASON_LABELS.get(item.get("reason"), item.get("reason") or "–"),
                "state": STATE_LABELS.get(item.get("state"), item.get("state")),
                "against": STATE_LABELS.get(item.get("against"), item.get("against")),
                "ratio": item.get("ratio"),
                "result": ("als Türvorgang übernommen" if at in taken
                           else "verworfen in einer Episode" if at in held else "verworfen"),
            })

    rejected_counts = (
        rejected_summary(
            [item["effective_at"] for item in candidates],
            [(episode.get("result") == "door",
              tuple(_iso(at) for at in episode.get("candidates") or ()))
             for episode in episodes],
        )
        if candidates is not None else None
    )

    retracted = []
    for gang in timeline.get("retracted") or ():
        at, reason = _retraction(processed, gang)
        retracted.append({
            "gang_id": gang.get("gang_id"),
            "started_at": _iso(gang.get("started_at")),
            "recognition": EVENT_LABELS.get(gang.get("recognition_kind"), "Erkennung"),
            "retracted_at": at,
            "reason": reason,
        })

    return {
        "session_id": timeline.get("session_id"),
        "recorded": states is not None,
        "summary": {
            "gangs": len(gangs),
            "infusions": len(infusions),
            "infusions_in_gangs": in_gangs,
            "infusions_outside": len(infusions) - in_gangs,
            "door_operations": len(cycles),
            "rejected_candidates": rejected_counts,
            "retracted_gangs": len(retracted),
        },
        "states": track,
        "events": events,
        "rejected": candidates,
        "not_taken": [
            {
                "kind": (record.get("event") or {}).get("kind"),
                "label": EVENT_LABELS.get((record.get("event") or {}).get("kind"), "Ereignis"),
                "effective_at": _iso((record.get("event") or {}).get("effective_at")),
                "reason": record.get("reason"),
                "probability": record.get("probability"),
            }
            for record in not_taken
        ],
        "retracted": retracted,
    }
