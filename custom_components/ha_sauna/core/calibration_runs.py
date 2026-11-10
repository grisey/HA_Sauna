"""Kalibrierläufe aus dem Archiv (B4e, Details · Kalibrierung).

Grundlage ist nur das gespeicherte Protokoll (Datensatzart ``calibration``)
mit der gespeicherten Auswertung (``evaluation``). Ablauf, Bewertung und
Zuordnung (``calibration.evaluate``) bleiben unverändert; hier werden ihre
Ergebnisse je Schritt für die Anzeige zusammengestellt (G19: das Panel
formatiert nur).

* ``summary``: ein Lauf mit Art, Modulen, Beginn, Ende, Gültigkeit und, nach
  der Auswertung, ihren Summen.
* ``result``: dazu je Ausführung eines Moduls eine Zelle je Schritt mit dem
  Ergebnis jeder erwarteten Erkennung (im Fenster gefunden, außerhalb
  zugeordnet mit Lage zum Fenster, fehlt) sowie die zusätzlichen Erkennungen in
  Zeitfolge zwischen den Schritten ihrer Ausführung. Die Kennung des zugehörigen
  Ereignisses der Sitzung erlaubt den Sprung in die Erkennungskontrolle.

Das Ergebnis des zuletzt beendeten Laufs steht damit auch nach einem Neustart
oder Neuladen der Integration zur Verfügung (L-W2c-1).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import datetime

from .calibration import (
    DOOR_CLOSE,
    DOOR_OPEN,
    INFUSION,
    INTERRUPTIONS,
    MODULES,
    Module,
    Step,
)
from .timeline import utc

KIND_TEXT = {"session": "Kalibriersitzung", "module": "Einzelnes Modul"}
EXPECTATION_LABELS = {DOOR_OPEN: "Tür auf", DOOR_CLOSE: "Tür zu", INFUSION: "Aufguss"}
STATUS_TEXT = {
    "found": "im Fenster",
    "outside": "außerhalb des Fensters",
    "missing": "fehlt",
    "additional": "zusätzlich",
}
CELL_TEXT = {
    "none": "keine Erkennung erwartet",
    "pending": "Auswertung nach dem Sitzungsende",
    "unevaluated": "nicht ausgewertet",
    "excluded": "abweichend, ohne Referenz",
    "cancelled": "bei der Unterbrechung offen",
    "not_reached": "nicht erreicht",
}
WITHOUT_END = "Protokoll ohne Ende"


def _time(value) -> datetime | None:
    if value is None:
        return None
    return utc(value if isinstance(value, datetime) else datetime.fromisoformat(value))


def _iso(value) -> str | None:
    moment = _time(value)
    return moment.isoformat() if moment is not None else None


def group(records: Iterable[tuple[str | None, Mapping]]) -> dict[str, tuple[str | None, list]]:
    """Protokolleinträge je Lauf in Archivfolge: {run_id: (session_id, [Einträge])}."""
    runs: dict[str, tuple[str | None, list]] = {}
    for session_id, record in records:
        run_id = record.get("run_id") if isinstance(record, Mapping) else None
        if not run_id:
            continue
        runs.setdefault(run_id, (session_id, []))[1].append(record)
    return runs


def _last(records, event):
    return next((record for record in reversed(records) if record.get("event") == event), None)


def summary(run_id: str, session_id: str | None, records: list, *,
            modules: Mapping[str, Module] = MODULES) -> dict:
    """Ein Lauf für die Auswahl: Art, Module, Zeiten, Gültigkeit und Summen."""
    start = _last(records, "start") or {}
    end = _last(records, "end")
    evaluation = _last(records, "evaluation")
    valid = end is not None and end.get("valid") is True
    reason = end.get("reason") if end else None
    kind = start.get("kind")
    return {
        "run_id": run_id,
        "session_id": session_id,
        "kind": kind,
        "kind_text": KIND_TEXT.get(kind, "Kalibrierung"),
        "modules": [modules[item].label if item in modules else item
                    for item in start.get("modules") or ()],
        "started_at": _iso(start.get("at") or (records[0].get("at") if records else None)),
        "ended_at": _iso(end.get("at")) if end else None,
        "valid": valid,
        "reason": reason,
        "reason_text": None if valid else (INTERRUPTIONS.get(reason, reason) if end else WITHOUT_END),
        "evaluated": evaluation is not None,
        "totals": (evaluation.get("result") or {}).get("totals") if evaluation else None,
    }


def summaries(runs: Mapping[str, tuple[str | None, list]], **options) -> list[dict]:
    """Alle Läufe, der zuletzt begonnene zuerst."""
    items = [summary(run_id, session_id, records, **options)
             for run_id, (session_id, records) in runs.items()]
    return sorted(items, key=lambda item: item["started_at"] or "", reverse=True)


def view(records: Iterable[tuple[str | None, Mapping]], *, run_id: str | None = None,
         active_run_id: str | None = None,
         processed: Callable[[str | None], Iterable[Mapping]] = lambda _session_id: ()) -> dict | None:
    """Auswahl der Läufe und Ergebnis des gewählten (Endpunkt ``archive/calibration``).

    ``runs``: alle Läufe außer dem laufenden (``active_run_id``), der zuletzt
    begonnene zuerst; ``run``: der gewählte, ohne Wahl der zuerst genannte, mit
    dem Ergebnis je Schritt. ``processed`` liefert die gebuchten Ereignisse einer
    Sitzung. Eine unbekannte Wahl ergibt ``None``.
    """
    runs = group(records)
    runs.pop(active_run_id, None)
    listed = summaries(runs)
    if run_id is not None and run_id not in runs:
        return None
    chosen = run_id or (listed[0]["run_id"] if listed else None)
    if chosen is None:
        return {"runs": listed, "run": None}
    session_id, items = runs[chosen]
    return {"runs": listed,
            "run": result(chosen, session_id, items, processed=processed(session_id))}


def _event_ids(processed: Iterable[Mapping]) -> dict[tuple[str, str], str]:
    """Kennung der gebuchten Erkennung je (Art, Zeitpunkt), wie die Auswertung sie liest."""
    ids = {}
    for event in processed:
        event_id = event.get("event_id") or ""
        if event_id.startswith("recognition:") and event.get("effective_at"):
            ids.setdefault((event.get("kind"), _iso(event["effective_at"])), event_id)
    return ids


def _cell_status(statuses: list[str]) -> str:
    """Gemeinsamer Ausgang der Erwartungen einer Zelle, sonst „teils“ (``mixed``)."""
    if not statuses:
        return "none"
    return statuses[0] if len(set(statuses)) == 1 else "mixed"


def result(run_id: str, session_id: str | None, records: list, *,
           processed: Iterable[Mapping] = (),
           modules: Mapping[str, Module] = MODULES) -> dict:
    """Ein Lauf mit seinen Ausführungen und dem Ergebnis je Schritt.

    ``processed``: gebuchte Ereignisse der Sitzung (Zeitleiste) für die
    Kennungen der zugeordneten und zusätzlichen Erkennungen.
    """
    head = summary(run_id, session_id, records, modules=modules)
    evaluation = (_last(records, "evaluation") or {}).get("result")
    ids = _event_ids(processed)

    def event_id(kind, at):
        return ids.get((kind, _iso(at)))

    steps = [record for record in records if record.get("event") == "step"]
    started = [(record.get("execution"), record.get("module"))
               for record in records if record.get("event") == "module"]
    evaluated = {item.get("execution"): item for item in (evaluation or {}).get("executions") or ()
                 if item.get("run_id") == run_id}
    executions = []
    for index, (number, module_id) in enumerate(started):
        closed = {step.get("step"): step for step in steps if step.get("execution") == number}
        found = evaluated.get(number) or {}
        recognition = {item.get("step"): item for item in found.get("recognition") or ()}
        presence = found.get("presence")
        empty = {item.get("step"): item for item in (presence or {}).get("empty") or ()}
        module = modules.get(module_id)
        cells = []
        for step in _steps(module, closed.values(), recognition):
            item = closed.get(step.key)
            cell = {
                "type": "step",
                "step": step.key,
                "reference": step.reference,
                "expects": [EXPECTATION_LABELS.get(kind, kind) for kind in step.expects],
                "outcome": item.get("outcome") if item else None,
                "window": [_iso(item.get("handoff_at")), _iso(item.get("closed_at"))] if item else None,
                "expected": [],
            }
            if item is None:
                cell["status"] = "not_reached"
            elif item.get("outcome") == "deviate":
                cell["status"] = "excluded"
            elif item.get("outcome") == "cancelled":
                cell["status"] = "cancelled"
            elif not head["valid"]:
                cell["status"] = "unevaluated" if step.expects else "none"
            elif evaluation is None:
                cell["status"] = "pending" if step.expects else "none"
            else:
                expected = [{
                    "kind": entry.get("kind"),
                    "label": EXPECTATION_LABELS.get(entry.get("kind"), entry.get("kind")),
                    "status": entry.get("status"),
                    "status_text": STATUS_TEXT.get(entry.get("status"), entry.get("status")),
                    "at": _iso(entry.get("at")),
                    "offset_s": entry.get("offset_s"),
                    "event_id": event_id(entry.get("kind"), entry.get("at")),
                } for entry in (recognition.get(step.key) or {}).get("expected") or ()]
                cell["expected"] = expected
                cell["status"] = _cell_status([entry["status"] for entry in expected])
            if step.key in empty:
                cell["presence"] = {"reports": empty[step.key].get("reports"),
                                    "present_s": empty[step.key].get("present_s")}
            cell["status_text"] = (STATUS_TEXT.get(cell["status"])
                                   or CELL_TEXT.get(cell["status"]))
            cells.append(cell)
        executions.append({
            "execution": number,
            "module": module_id,
            "label": module.label if module else module_id,
            "title": module.title if module else "",
            "repeat": any(other == module_id for _, other in started[:index]),
            "cells": cells,
            "gang": found.get("gang"),
            "presence": None if presence is None else {
                key: presence.get(key) for key in ("dropouts", "after_run_s", "door_watch")
            },
            "merged": [],
        })
    # Module der Auswahl, die der Lauf nicht mehr begonnen hat
    for module_id in (_last(records, "start") or {}).get("modules") or ():
        if any(other == module_id for _, other in started):
            continue
        module = modules.get(module_id)
        # Ohne bekanntes Modul (geänderter Ablauf) steht es als ein nicht erreichter Schritt.
        known = list(module.steps) if module else [Step(None, "", "nicht begonnen")]
        executions.append({
            "execution": None, "module": module_id,
            "label": module.label if module else module_id,
            "title": module.title if module else "", "repeat": False,
            "cells": [{"type": "step", "step": step.key, "reference": step.reference,
                       "expects": [EXPECTATION_LABELS.get(kind, kind) for kind in step.expects],
                       "outcome": None, "window": None, "expected": [],
                       "status": "not_reached", "status_text": CELL_TEXT["not_reached"]}
                      for step in known],
            "gang": None, "presence": None, "merged": [],
        })
    unplaced = _place_additional(executions, evaluation, run_id, event_id)
    _attach_merged(executions, evaluation, run_id)
    return {
        **head,
        "presence_measured": any(item["presence"] is not None for item in executions)
        if evaluation is not None else None,
        "executions": executions,
        "additional": unplaced,
    }


def _steps(module: Module | None, closed: Iterable[Mapping], recognition: Mapping) -> list[Step]:
    """Schritte einer Ausführung in Ablauffolge.

    Ein bekanntes Modul liefert alle Schritte, auch die nicht erreichten. Kennt der
    aktuelle Stand das Modul eines gespeicherten Laufs nicht mehr (geänderter
    Ablauf), gelten die abgeschlossenen Schritte des Protokolls mit den
    Erwartungen ihrer Auswertung.
    """
    if module is not None:
        return list(module.steps)
    return [Step(item.get("step"), "", item.get("reference") or item.get("step"),
                 expects=tuple(entry.get("kind") for entry in
                               (recognition.get(item.get("step")) or {}).get("expected") or ()))
            for item in sorted(closed, key=lambda record: _time(record.get("handoff_at")))]


def _place_additional(executions, evaluation, run_id, event_id) -> list[dict]:
    """Zusätzliche Erkennungen in Zeitfolge in die Ausführung, in deren Zeitraum sie liegen.

    Eine Ausführung reicht wie in der Auswertung von ihrer ersten Übergabe bis zur
    ersten Übergabe der nächsten, die letzte bis zu ihrem letzten Fenster. In der
    Ausführung steht die Erkennung hinter dem letzten Schritt, dessen Fenster
    vorher begann.
    """
    spans = []
    started = [item for item in executions if item["execution"] is not None]
    for index, execution in enumerate(started):
        windows = [cell["window"] for cell in execution["cells"] if cell["window"]]
        if not windows:
            continue
        following = next((cell["window"][0] for later in started[index + 1:]
                          for cell in later["cells"] if cell["window"]), None)
        spans.append((_time(windows[0][0]), _time(following or windows[-1][1]), execution))
    unplaced = []
    for item in sorted((entry for entry in (evaluation or {}).get("additional") or ()
                        if entry.get("run_id") == run_id), key=lambda entry: _time(entry["at"])):
        at = _time(item["at"])
        cell = {"type": "additional", "kind": item.get("kind"),
                "label": EXPECTATION_LABELS.get(item.get("kind"), item.get("kind")),
                "at": _iso(at), "window": [_iso(at), _iso(at)],
                "status": "additional", "status_text": STATUS_TEXT["additional"],
                "event_id": event_id(item.get("kind"), at)}
        owner = next((execution for begin, end, execution in reversed(spans)
                      if begin <= at <= end), None)
        if owner is None:
            unplaced.append(cell)
            continue
        cells = owner["cells"]
        position = max((index + 1 for index, other in enumerate(cells)
                        if other["window"] and _time(other["window"][0]) <= at), default=0)
        cells.insert(position, cell)
    return unplaced


def _attach_merged(executions, evaluation, run_id) -> None:
    """Von der Erkennung zusammengelegte Türvorgänge bei der Ausführung der Öffnung."""
    by_number = {item["execution"]: item for item in executions if item["execution"] is not None}

    def reference(part):
        execution = by_number.get(part.get("execution"))
        cell = next((cell for cell in (execution or {}).get("cells") or ()
                     if cell.get("step") == part.get("step")), None)
        return {"reference": cell["reference"] if cell else part.get("step"),
                "module": execution["label"] if execution else None,
                "at": _iso(part.get("at"))}

    for item in (evaluation or {}).get("merged") or ():
        if item.get("run_id") != run_id:
            continue
        owner = by_number.get((item.get("opening") or {}).get("execution"))
        if owner is not None:
            owner["merged"].append({"opening": reference(item.get("opening") or {}),
                                    "closing": reference(item.get("closing") or {})})
