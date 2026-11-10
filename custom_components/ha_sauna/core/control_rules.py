"""K-R2 Fristen und Regeln einer Sitzung (B4d, Details · Steuerung).

Quelle ist der gespeicherte Sitzungsstand (Q-S: Ofenkühlungen, Sollverlauf,
Phasen) und die gespeicherte Konfiguration der Sitzung (Q-P). Die heutige
Einstellung gilt nie rückwirkend: Fehlt die Konfiguration der Sitzung, bleiben
die Parameterwerte leer.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from .key_figures import gang_confirmed, session_program
from .timeline import Kind, utc

# Parameter der Sitzung, die als Fristen und Regeln gelten (Schlüssel im Ergebnis → Parameter).
RULE_PARAMETERS = {
    "heating_pause_minutes": "thermostat_cooldown_minutes",
    "minimum_heating_minutes": "minimum_heating_minutes",
    "oven_cooling_minimum_minutes": "after_run_minutes",
    "oven_cooling_maximum_minutes": "oven_cooling_max_minutes",
    "minimum_temperature_c": "sauna_min_temperature_c",
}


def _time(value):
    return utc(datetime.fromisoformat(value)) if isinstance(value, str) else value


def _minutes(started_at, ended_at) -> float | None:
    if not started_at or not ended_at:
        return None
    return (_time(ended_at) - _time(started_at)).total_seconds() / 60


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _confirmed_at(gang: Mapping):
    """Bestätigungszeit wie ``timeline.Gang.confirmed_at`` für gespeicherte Gänge."""
    if gang.get("recognition_kind") == Kind.PRESENCE_CONFIRMED:
        return gang.get("detected_at")
    infusions = gang.get("infusion_events") or ()
    return infusions[0].get("detected_at") if infusions else None


def gang_bookings(timeline: Mapping) -> list[dict]:
    """Gebuchte Gänge der Sitzung: Beginn, Buchung, Bestätigung und Ende je Gang.

    Bestätigte Gänge tragen ihre Nummer wie in den Kennzahlen; ein vorläufiger
    Gang hat keine Nummer.
    """
    retracted = {gang.get("gang_id") for gang in timeline.get("retracted") or ()}
    gangs = [*(timeline.get("completed") or ()),
             *([timeline["active"]] if timeline.get("active") else [])]
    result, number = [], 0
    for gang in gangs:
        if gang.get("gang_id") in retracted:
            continue
        confirmed = gang_confirmed(gang)
        number += confirmed
        result.append({
            "gang_id": gang.get("gang_id"),
            "number": number if confirmed else None,
            "confirmed": confirmed,
            "started_at": gang.get("started_at"),
            "detected_at": gang.get("detected_at"),
            "confirmed_at": _confirmed_at(gang),
            "ended_at": gang.get("ended_at"),
            "end_reason": gang.get("end_reason"),
            "active": gang is timeline.get("active"),
        })
    return result


def control_rules(session: Mapping) -> dict:
    """Ofenkühlungen mit Beginn, Ende, Dauer und Gang; Heizpause, Mindestheizzeit,
    Regelband, Mindesttemperatur und Sollfolge der Sitzung, dazu die gebuchten Gänge.

    Der Gang einer Ofenkühlung ist der bestätigte Gang, dessen Kennung die
    Kühlphase trägt (die Ofenkühlung folgt dem Gang, der sie ausgelöst hat);
    gezählt werden bestätigte Gänge wie in den Kennzahlen.
    """
    configuration = session.get("configuration")
    parameters = (configuration or {}).get("parameters") or {}
    timeline = session.get("timeline") or {}
    gangs = gang_bookings(timeline)
    numbers = {gang["gang_id"]: gang["number"] for gang in gangs if gang["number"]}
    current = session.get("after_run")
    phases = list(session.get("after_run_history") or ())
    if current and all(phase.get("phase_id") != current.get("phase_id") for phase in phases):
        phases.append(current)
    cooling = [
        {
            "started_at": phase.get("started_at"),
            "ended_at": phase.get("ends_at"),
            "minutes": _minutes(phase.get("started_at"), phase.get("ends_at")),
            "gang": numbers.get(phase.get("phase_id")),
            "running": bool(current) and phase.get("phase_id") == current.get("phase_id"),
            "pending": bool(phase.get("pending_start")),
        }
        for phase in sorted(phases, key=lambda item: item.get("started_at") or "")
    ]
    targets = []
    for mark in session.get("target_history") or ():
        value = _number(mark.get("target_c"))
        if value is not None and (not targets or targets[-1] != value):
            targets.append(value)
    below = _number(parameters.get("readiness_hysteresis_c"))
    above = _number(parameters.get("readiness_offset_c"))
    program = session_program(
        session, configuration, [(None, mark.get("phase")) for mark in session.get("base_phases") or ()]
    )
    return {
        "configured": configuration is not None,
        "oven_cooling": cooling,
        **{key: _number(parameters.get(name)) for key, name in RULE_PARAMETERS.items()},
        "band": {"below_c": below, "above_c": above} if below is not None and above is not None else None,
        "targets": targets,
        "program": {"kind": program["kind"], "name": program.get("name")} if program else None,
        "gangs": gangs,
    }
