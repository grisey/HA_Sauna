"""Anzeigewerte der Kennzahlen nach den Anzeigeregeln des Kennzahlenkatalogs.

Jede Kennzahl wird zu ``{"value": Rohwert, "display": Anzeigewert, "unit": Einheit}``.
Der Anzeigewert ist für sich aus dem Rohwert kaufmännisch gerundet: Temperatur,
Feuchte, Minuten und Energie auf 0,1, Kosten und Wasser auf 0,01, Anteile auf
ganze Prozent, Stunden als ganze Minuten für h:mm. Anteile, die zusammen 100 %
ergeben, folgen der Methode der größten Reste. Zeitpunkte werden zu
Zeitstempeln. Eine Kurve (Aufheizen) kommt nur als Anzeigewerte
``{"display": [[min, °C], …], "unit": "curve"}``. Einheit, Dezimalkomma und
Zeitformat setzt die Oberfläche.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from .timeline import utc

# Einheit: Nachkommastellen des Anzeigewerts
PLACES = {
    "°C": 1, "%": 1, "g/m³": 1, "kWh": 1, "€": 2, "l": 2, "°C/kWh": 1, "kWh/h": 1,
    "mean": 1,
}


def rounded(value: float | None, places: int) -> float | int | None:
    """Kaufmännisch gerundet (5 aufwärts) aus der kürzesten Dezimaldarstellung."""
    if value is None:
        return None
    result = Decimal(str(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return int(result) if places == 0 else float(result)


def largest_remainder(shares: list[float | None]) -> list[int] | None:
    """Ganze Prozent, die zusammen 100 ergeben (Methode der größten Reste)."""
    if not shares or any(share is None for share in shares):
        return None
    percent = [share * 100 for share in shares]
    whole = [int(value) for value in percent]
    remaining = 100 - sum(whole)
    order = sorted(range(len(percent)), key=lambda i: percent[i] - whole[i], reverse=True)
    for index in order[:remaining]:
        whole[index] += 1
    return whole


def curve(points: list[tuple[float, float]] | None) -> list[list[float]] | None:
    """Kurve in Anzeigewerten: Minuten (0,1) ab dem ersten Punkt und °C (0,1).

    Ein Punkt steht nur dort, wo sich die angezeigte Temperatur ändert; der
    letzte Punkt steht immer dabei. Nach der Zeitpunktregel (letzter Wert zum
    Zeitpunkt oder davor) geht dabei in Anzeigegenauigkeit nichts verloren.
    """
    if not points:
        return None
    shown = []
    for t, value in points:
        temperature = rounded(value, PLACES["°C"])
        if not shown or temperature != shown[-1][1]:
            shown.append([rounded(t / 60, 1), temperature])
    last = [rounded(points[-1][0] / 60, 1), rounded(points[-1][1], PLACES["°C"])]
    if shown[-1] != last:
        shown.append(last)
    return shown


def figure(value, unit: str, started_at: datetime | None = None) -> dict:
    """Eine Kennzahl als Rohwert und Anzeigewert."""
    if unit == "curve":
        # Die Messreihe selbst bleibt im Archiv; geliefert wird nur die Anzeige.
        return {"display": curve(value), "unit": unit}
    if unit == "time":
        stamp = (
            None if value is None
            else (started_at + timedelta(seconds=value)).isoformat()
        )
        return {"value": stamp, "display": stamp, "unit": unit}
    if unit == "min":
        shown = rounded(value / 60, 1) if value is not None else None
    elif unit in ("h:mm", "clock"):
        # clock: Uhrzeit als Sekunden seit Mitternacht (Ortszeit), ganze Minuten
        shown = rounded(value / 60, 0) if value is not None else None
    elif unit == "share":
        shown = rounded(value * 100, 0) if value is not None else None
    elif unit == "count":
        shown = value
    else:
        shown = rounded(value, PLACES[unit])
    return {"value": value, "display": shown, "unit": unit}


DOOR_CLASS = {
    "count": "count", "loss_mean_c": "°C", "loss_max_c": "°C",
    "open_mean_seconds": "min", "open_max_seconds": "min",
    "recovered_count": "count", "recovered_mean_seconds": "min",
}
DOOR_CLASSES = {name: DOOR_CLASS for name in (
    "entry", "exit", "final_ventilation", "other", "all",
)}
GANG = {
    "start": "time", "end": "time", "duration_seconds": "min",
    "entry_opening": "time", "entry_temperature_c": "°C", "exit_temperature_c": "°C",
    "highest_temperature_c": "°C", "infusions": ["time"], "infusion_count": "count",
    "humidity_rises_gm3": ["g/m³"], "humidity_rise_mean_gm3": "g/m³",
    "humidity_rises_lower_gm3": ["g/m³"], "water_mean_l": "l",
    "inner_door_openings": "count", "exit_opening": "time",
    "highest_humidity_percent": "%", "highest_humidity_at": "time",
    "absolute_humidity_at_highest_gm3": "g/m³", "heating_share": "share", "target_c": "°C",
    "target_deviation": {"mean_c": "°C", "lowest_c": "°C", "highest_c": "°C"},
}
SESSION = {
    "duration_seconds": "h:mm", "gang_count": "count", "sauna_time_seconds": "min",
    "gang_duration_mean_seconds": "min", "infusions_in_gangs": "count",
    "infusions_total": "count", "maximum_temperature_c": "°C",
    "maximum_temperature_at": "time", "maximum_temperature_target_c": "°C",
    "rests_seconds": ["min"], "rest_mean_seconds": "min",
    "heating_started_at": "time", "start_temperature_c": "°C", "ready_at": "time",
    "warmup_seconds": "h:mm", "warmup_end_at": "time", "warmup_end_temperature_c": "°C",
    "warmup_end_target_c": "°C", "warmup_curve": "curve", "targets_c": ["°C"],
    "heating_time_seconds": "h:mm",
    "heating_share": "share", "energy_kwh": "kWh", "energy_measured_kwh": "kWh",
    "energy_check_kwh": "kWh", "cost_eur": "€", "energy_warmup_kwh": "kWh",
    "energy_after_warmup_kwh": "kWh", "heating_phases_after_warmup": "count",
    "heating_in_gangs": "share",
    "oven_cooling": [{"start": "time", "end": "time", "duration_seconds": "min"}],
    "control_in_ready": {
        "window_seconds": "min", "mean_c": "°C", "below": "share", "within": "share",
        "above": "share", "highest_c": "°C", "highest_at": "time", "lowest_c": "°C",
        "lowest_at": "time", "hysteresis_c": "°C", "offset_c": "°C",
    },
    "outside_temperature_c": "°C", "outside_temperature_measured_at": "time",
    "rejected_candidates": {key: "count" for key in (
        "candidates", "rejected", "rejected_in_episodes", "rejected_single",
        "rejected_episodes", "taken_as_door", "door_episodes",
    )},
    "doors": [{
        "opened": "time", "closed": "time", "open_seconds": "min",
        "temperature_at_open_c": "°C", "loss_c": "°C", "recovered_seconds": "min",
    }],
    "door_classes": DOOR_CLASSES, "door_openings_warmup": "count", "rise_per_kwh": "°C/kWh",
    "gangs": [GANG],
}
EARLIER_WARMUP = {"median_seconds": "h:mm"}
STATISTICS = {
    "sessions": {"value": "count"},
    "operating_time_seconds": {"value": "h:mm"},
    "sauna_time_seconds": {"value": "h:mm"},
    "gangs": {"value": "count"},
    "gangs_per_session": {"value": "mean"},
    "infusions": {"value": "count"},
    "infusions_per_gang": {"value": "mean"},
    "infusions_most": {"value": "count"},
    "gang_duration": {"mean_seconds": "min", "range_seconds": ["min"]},
    "gang_infusions": {"mean": "mean", "range": ["count"]},
    "humidity_rise": {"mean_gm3": "g/m³", "water_mean_l": "l"},
    "rest": {"mean_seconds": "min", "range_seconds": ["min"], "longest": {"value": "min"}},
    "habits": {
        "weekday_sessions": ["count"], "start_quartiles_seconds": ["clock"],
        "most_frequent_gang_count_sessions": "count",
    },
    "records": {
        "longest_gang": {"value": "min"},
        "hottest_gang": {"value": "°C"},
        "most_infusions": {"value": "count"},
        "longest_session": {"value": "h:mm"},
        "coldest_outside": {"value": "°C"},
    },
    "energy_kwh": {"value": "kWh"},
    "cost_eur": {"value": "€"},
    "energy_per_sauna_hour_kwh": {"value": "kWh/h"},
    "heating_share": {"value": "share"},
    "by_program": None,
    "warmup": {
        "median_seconds": "h:mm", "range_seconds": ["h:mm"],
        "per_session": [{
            "heating_started_at": "time", "end_at": "time", "seconds": "h:mm",
            "start_temperature_c": "°C", "end_temperature_c": "°C", "end_target_c": "°C",
            "curve": "curve",
        }],
    },
    "door_classes": DOOR_CLASSES,
    "door_entry_exit": {
        "count": "count", "loss_mean_c": "°C", "open_mean_seconds": "min",
        "largest": {"loss_c": "°C", "opened": "time", "open_seconds": "min"},
    },
    "groups": [{
        "sessions": "count", "sauna_time_seconds": {"value": "h:mm"}, "gangs": "count",
        "infusions": "count", "energy_kwh": {"value": "kWh"}, "heating_share": "share",
        "door_loss_entry_exit_mean_c": "°C",
    }],
    "per_session": [{
        "duration_seconds": "h:mm", "gang_count": "count",
        "gangs": [{
            "start": "time", "end": "time", "duration_seconds": "min",
            "infusion_count": "count",
        }],
        "sauna_time_seconds": "min", "infusions_in_gangs": "count", "energy_kwh": "kWh",
        "heating_share": "share", "door_loss_entry_exit_mean_c": "°C",
    }],
}


def present(raw, spec, started_at: datetime | None = None):
    """Rohwerte nach ``spec`` in Kennzahlen mit Anzeigewert umsetzen."""
    if spec is None or raw is None and not isinstance(spec, str):
        return raw
    if isinstance(spec, str):
        return figure(raw, spec, started_at)
    if isinstance(spec, list):
        return [present(item, spec[0], started_at) for item in raw]
    if set(spec) == {"value"} and "value" in raw:
        # Wert mit Bezug (Sitzungszahl, Sitzung, Gang): Kennzahl und Bezug in einer Ebene.
        return {**raw, **figure(raw["value"], spec["value"], started_at)}
    if isinstance(raw.get("started_at"), str):
        started_at = utc(datetime.fromisoformat(raw["started_at"]))
    result = dict(raw)
    for key, part in spec.items():
        if key in raw:
            result[key] = present(raw[key], part, started_at)
    return result


def present_session(raw: dict, earlier: dict | None = None) -> dict:
    """Kennzahlen einer Sitzung mit Anzeigewerten; Regelband-Anteile nach größten Resten."""
    result = present(raw, SESSION)
    control = result.get("control_in_ready")
    if control is not None:
        shares = largest_remainder([raw["control_in_ready"][key]
                                    for key in ("below", "within", "above")])
        for key, shown in zip(("below", "within", "above"), shares, strict=True):
            control[key]["display"] = shown
    if earlier is not None:
        result["earlier_warmup"] = present(earlier, EARLIER_WARMUP)
    return result


BY_PROGRAM = {
    "warmup": {"median_seconds": "h:mm", "range_seconds": ["h:mm"]},
    "per_session": [{
        "warmup_seconds": "h:mm", "control_mean_c": "°C", "control_within": "share",
        "target_deviation_mean_c": ["°C"],
    }],
}


def present_statistics(raw: dict) -> dict:
    result = present(raw, STATISTICS)
    result["by_program"] = {
        kind: present(group, BY_PROGRAM) for kind, group in raw["by_program"].items()
    }
    return result
