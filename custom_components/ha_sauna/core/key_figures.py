"""Kennzahlen abgeschlossener Sitzungen nach dem Kennzahlenkatalog.

Die Rechnung kennt keine Home-Assistant-Objekte. Eingang ist ``SessionInput``
mit Zeiten in Sekunden ab Sitzungsbeginn; ``session_input_from_archive`` bildet
ihn aus einer archivierten Sitzung. Ergebnis sind Rohwerte (Sekunden, Anteile,
°C, g/m³, kWh, €, l). Gerundete Anzeigewerte bildet ``key_figure_display``.
Eine Kennzahl ohne Daten ist ``None``, nicht 0.
"""

from __future__ import annotations

import bisect
import statistics
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from itertools import pairwise
from math import inf, isfinite

from .moisture import absolute_humidity
from .seasons import PARTS, period_label, period_of, period_start
from .temperature_program import evenly_distributed
from .timeline import Kind, project_gang_intervals, utc

# Zeitpunktregel des Katalogs: vor dem ersten Messwert gilt dieser, wenn er
# höchstens so viel später kommt.
FIRST_SAMPLE_GRACE_SECONDS = 5.0

DOOR_CLASSES = ("entry", "exit", "final_ventilation", "other")
PARAMETER_KEYS = (
    "readiness_hysteresis_c",
    "readiness_offset_c",
    "nominal_power_kw",
)


@dataclass(frozen=True)
class Series:
    """Messreihe mit aufsteigenden Zeiten in Sekunden ab Sitzungsbeginn."""

    times: tuple[float, ...]
    values: tuple[float, ...]

    def at(self, t: float) -> float | None:
        """Letzter Wert zum Zeitpunkt oder davor (Zeitpunktregel des Katalogs)."""
        index = bisect.bisect_right(self.times, t) - 1
        if index >= 0:
            return self.values[index]
        if self.times and self.times[0] - t <= FIRST_SAMPLE_GRACE_SECONDS:
            return self.values[0]
        return None

    def window(self, start: float, end: float) -> list[tuple[float, float]]:
        """Messpunkte von ``start`` bis ``end`` einschließlich."""
        first = bisect.bisect_left(self.times, start)
        last = bisect.bisect_right(self.times, end)
        return list(zip(self.times[first:last], self.values[first:last], strict=True))

    def first_from(
        self, start: float, condition: Callable[[float], bool], until: float = inf,
    ) -> float | None:
        for t, value in self.window(start, until):
            if condition(value):
                return t
        return None


@dataclass(frozen=True)
class GangInput:
    """Bestätigter Gang; ein nicht belegter Beginn oder ein Ende ist ``None``."""

    start: float | None
    end: float | None
    infusions: tuple[float, ...] = ()
    # Von Hand beendet (GE1): keine Öffnung ist der Austritt (GE1-6).
    manual_end: bool = False


@dataclass(frozen=True)
class InfusionInput:
    """Erkannter Aufguss und Ende seines Anstiegs laut Erkennung, falls bekannt."""

    at: float
    rise_end: float | None = None


@dataclass(frozen=True)
class OutsideInput:
    """Umgebungseintrag (Q-U): Außentemperatur, wie sie ab ``at`` in HA aktuell war.

    ``at`` in Sekunden ab Sitzungsbeginn, der Eintrag bei Sitzungsbeginn bei 0;
    ``measured_at`` ist die Messzeit der Quelle (``None``: ohne Messzeit).
    """

    at: float
    value: float | None
    measured_at: float | None


@dataclass(frozen=True)
class EpisodeInput:
    """Episode verworfener Öffnungskandidaten; ``door``: über die Anwesenheit als Türvorgang übernommen."""

    opened: float
    ended: float
    door: bool
    candidates: tuple[float, ...]


@dataclass(frozen=True)
class SessionInput:
    started_at: datetime
    duration: float
    upper_temperature: Series
    lower_temperature: Series
    upper_humidity: Series
    lower_humidity: Series
    gangs: tuple[GangInput, ...]
    infusions: tuple[InfusionInput, ...]
    doors: tuple[tuple[str, float], ...]  # ("door_open" | "door_close", t)
    phases: tuple[tuple[float, str], ...]
    targets: tuple[tuple[float, float], ...]
    heating: tuple[tuple[float, float], ...]
    energy_kwh: float | None
    measured_kwh: float | None
    oven_cooling: tuple[tuple[float, float], ...]
    parameters: Mapping[str, float | None]
    parameter_origin: str | None
    # Umgebungseinträge nach ``at``; None = keiner (ältere Sitzung oder keine Bindung)
    outside: tuple[OutsideInput, ...] | None = None
    volume_m3: float | None = None
    price_eur_per_kwh: float | None = None
    session_id: str | None = None
    # Gelernter Nachlauf des Temperaturfühlers nach dem Türschließen (K-S21)
    door_sensor_lag: float | None = None
    program: Mapping | None = None  # K-S25
    # Bereit-Verlauf (Zeit, gesetzt) aus ``ready_history``; None = nicht aufgezeichnet
    readiness: tuple[tuple[float, bool], ...] | None = None
    # K-E2: Zeitpunkte der von der Öffnungsform verworfenen Kandidaten und ihre
    # Episoden; None = nicht aufgezeichnet (Sitzung ohne Zustandsverlauf)
    rejected_candidates: tuple[float, ...] | None = None
    rejected_episodes: tuple[EpisodeInput, ...] = ()
    upper_absolute_humidity: Series = field(init=False)
    lower_absolute_humidity: Series = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "started_at", utc(self.started_at))
        object.__setattr__(self, "upper_absolute_humidity",
                           _absolute(self.upper_temperature, self.upper_humidity))
        object.__setattr__(self, "lower_absolute_humidity",
                           _absolute(self.lower_temperature, self.lower_humidity))

    @property
    def manual(self) -> bool | None:
        """Handbetrieb laut Phasenverlauf; ohne Phasen unbekannt."""
        if not self.phases:
            return None
        return any(phase == "manuell" for _, phase in self.phases)


def _absolute(temperature: Series, humidity: Series) -> Series:
    """Absolute Feuchte mit der Temperatur derselben Position zum selben Zeitpunkt."""
    times, values = [], []
    for t, relative in zip(humidity.times, humidity.values, strict=True):
        current = temperature.at(t)
        if current is not None and 0 <= relative <= 100:
            times.append(t)
            values.append(absolute_humidity(current, relative))
    return Series(tuple(times), tuple(values))


def _overlap(start: float, end: float, intervals: Iterable[tuple[float, float]]) -> float:
    return sum(max(0.0, min(end, b) - max(start, a)) for a, b in intervals)


def _subtract(intervals, removed):
    """Intervalle ohne die Bereiche aus ``removed``."""
    result = []
    for a, b in intervals:
        parts = [(a, b)]
        for x, y in removed:
            remaining = []
            for p, q in parts:
                if y <= p or x >= q:
                    remaining.append((p, q))
                    continue
                if x > p:
                    remaining.append((p, x))
                if y < q:
                    remaining.append((y, q))
            parts = remaining
        result.extend(parts)
    return [(a, b) for a, b in result if b > a]


def _mean(values):
    values = list(values)
    return statistics.fmean(values) if values else None


def _complete(values):
    """Werte nur, wenn keiner fehlt: eine Summe ohne einen Teil ist keine Summe."""
    values = list(values)
    return None if any(value is None for value in values) else values


def phase_intervals(session: SessionInput, name: str) -> list[tuple[float, float]]:
    if not session.phases:
        return []
    ends = [t for t, _ in session.phases[1:]] + [session.duration]
    return [(t, end) for (t, phase), end in zip(session.phases, ends, strict=True)
            if phase == name]


def ready_intervals(session: SessionInput) -> list[tuple[float, float]]:
    """Bereit-Intervalle bei eingeschaltetem Betrieb (K-S9, K-S10, K-S19).

    Aus dem Bereit-Verlauf des Sitzungsstands, in Automatik und Handbetrieb;
    ohne Verlauf (ältere Sitzungen) die Phase „bereit“ aus ``base_phases``.
    """
    if session.readiness is None:
        return phase_intervals(session, "bereit")
    intervals, since = [], None
    for t, ready in sorted(session.readiness, key=lambda mark: mark[0]):
        if ready and since is None:
            since = t
        elif not ready and since is not None:
            intervals.append((since, t))
            since = None
    if since is not None:
        intervals.append((since, session.duration))
    clipped = [(max(a, 0.0), min(b, session.duration)) for a, b in intervals]
    return _subtract(clipped, phase_intervals(session, "aus"))


def first_ready(session: SessionInput) -> float | None:
    """Erstes Bereit: aus dem Bereit-Verlauf, sonst erste Phase „bereit“."""
    if session.readiness is None:
        return next((t for t, phase in session.phases if phase == "bereit"), None)
    intervals = ready_intervals(session)
    return intervals[0][0] if intervals else None


def target_at(session: SessionInput, t: float) -> float | None:
    value = None
    for at, target in session.targets:
        if at <= t:
            value = target
    return value


def time_weighted(series: Series, intervals):
    """(Zeit, Wert, Gewicht in s); Gewicht bis zum nächsten Messpunkt im Intervall."""
    result = []
    for a, b in intervals:
        points = series.window(a, b)
        first = series.at(a)
        if first is not None and (not points or points[0][0] > a):
            points = [(a, first), *points]
        for (t, value), following in zip(points, [*points[1:], (b, None)], strict=True):
            weight = min(following[0], b) - t
            if weight > 0:
                result.append((t, value, weight))
    return result


def operation_off_at(session: SessionInput) -> float:
    """Beginn der letzten Phase „aus“, sonst Sitzungsende."""
    if session.phases and session.phases[-1][1] == "aus":
        return session.phases[-1][0]
    return session.duration


def door_pairs(session: SessionInput) -> list[tuple[float, float]]:
    """Öffnen → nächstes Schließen über die ganze Sitzung."""
    events = sorted(session.doors, key=lambda item: item[1])
    return [
        (a, b) for (first, a), (second, b) in pairwise(events)
        if first == "door_open" and second == "door_close"
    ]


def _door_class(a, b, openings, gangs):
    """Klasse eines Türvorgangs; ``None``, wenn eine nötige Gangbegrenzung fehlt."""
    door_class, unresolved = "other", False
    for gang in gangs:
        if gang.start is not None and b == gang.start:
            door_class, unresolved = "entry", False
        if gang.start is not None and gang.end is not None:
            inside = [x for x in openings if gang.start < x <= gang.end]
            if inside and a == inside[-1] and not gang.manual_end:
                door_class, unresolved = "exit", False
        elif (gang.start is None or a > gang.start) and (gang.end is None or a <= gang.end):
            unresolved = True
    if gangs:
        last = gangs[-1]
        if last.end is not None:
            if a > last.end:
                door_class, unresolved = "final_ventilation", False
        elif last.start is None or a > last.start:
            unresolved = True
    return None if unresolved else door_class


def loss_window_end(closed: float, sensor_lag: float | None) -> float | None:
    """Ende des Fensters für den Türverlust (K-S21, Variante C, Nutzer 10.11.).

    Schließen plus gelernter Nachlauf des Fühlers; ohne Nachlauf kein Verlust.
    """
    return None if sensor_lag is None else closed + sensor_lag


def learn_door_sensor_lag(sessions: Iterable[SessionInput]) -> dict | None:
    """Nachlauf des Fühlers lernen (Regel 3): Median der Zeit vom Schließen bis zum Tiefpunkt.

    Gezählt werden Türvorgänge, nach denen T vor dem nächsten Ereignis (nächste
    Öffnung, nächster Aufguss, Betrieb aus) wieder den Wert beim Öffnen
    erreicht. Der Tiefpunkt ist das kleinste T vom Öffnen bis dahin. Ergebnis
    mit Lernmenge; gespeichert und neu geschätzt wird nur auf Bestätigung.
    """
    lags, used = [], []
    for session in sessions:
        temperature = session.upper_temperature
        openings = [t for kind, t in session.doors if kind == "door_open"]
        infusions = [infusion.at for infusion in session.infusions]
        off = operation_off_at(session)
        count = 0
        for a, b in door_pairs(session):
            at_open = temperature.at(a)
            if at_open is None:
                continue
            following = min([x for x in openings if x > a]
                            + [x for x in infusions if x > a] + [off])
            recovered = temperature.first_from(
                b, lambda value, reference=at_open: value >= reference, until=following,
            )
            if recovered is None:
                continue
            low_at = min(temperature.window(a, recovered), key=lambda item: item[1])[0]
            lags.append(low_at - b)
            count += 1
        used.append({"session_id": session.session_id,
                     "started_at": session.started_at.isoformat(), "operations": count})
    if not lags:
        return None
    quartiles = statistics.quantiles(lags, n=4) if len(lags) > 1 else [lags[0]] * 3
    return {
        "value_seconds": statistics.median(lags),
        "quartiles_seconds": [quartiles[0], quartiles[2]],
        "operations": len(lags),
        "sessions": used,
    }


def entry_opening(session: SessionInput, start: float | None) -> float | None:
    """Öffnen der Eintrittstür: Türvorgang, dessen Schließen der Gangbeginn ist."""
    if start is None:
        return None
    return next((a for a, b in door_pairs(session) if b == start), None)


def door_operations(session: SessionInput) -> list[dict]:
    """Alle Türvorgänge der Sitzung mit Klasse, offen, Verlust und Wiedererreichen."""
    pairs = door_pairs(session)
    openings = [a for a, _ in pairs]
    off = operation_off_at(session)
    temperature = session.upper_temperature
    result = []
    for a, b in pairs:
        door_class = _door_class(a, b, openings, session.gangs)
        at_open = temperature.at(a)
        loss_end = loss_window_end(b, session.door_sensor_lag)
        during = (
            [value for _, value in temperature.window(a, loss_end)]
            if loss_end is not None else []
        )
        recovered = None
        checked = door_class not in (None, "final_ventilation") and at_open is not None
        if checked:
            following = min([x for x in openings if x > a] + [off])
            recovered = temperature.first_from(
                b, lambda value, reference=at_open: value >= reference, until=following,
            )
        result.append({
            "opened": a,
            "closed": b,
            "door_class": door_class,
            "open_seconds": b - a,
            "temperature_at_open_c": at_open,
            "loss_c": at_open - min(during) if during and at_open is not None else None,
            "recovered_seconds": None if recovered is None else recovered - b,
            # Wiedererreichen geprüft (nicht Schlusslüften, Klasse und T beim Öffnen bekannt)
            "recovery_checked": checked,
        })
    return result


def door_summary(operations: list[dict]) -> dict:
    """Je Klasse und für alle: Anzahl, Verlust, offen, Wiedererreichen."""
    summary = {}
    for name in (*DOOR_CLASSES, "all"):
        selected = [x for x in operations if name == "all" or x["door_class"] == name]
        losses = [x["loss_c"] for x in selected if x["loss_c"] is not None]
        recovered = [x["recovered_seconds"] for x in selected
                     if x["recovered_seconds"] is not None]
        summary[name] = {
            "count": len(selected),
            "loss_mean_c": _mean(losses),
            "loss_max_c": max(losses, default=None),
            "open_mean_seconds": _mean(x["open_seconds"] for x in selected),
            "open_max_seconds": max((x["open_seconds"] for x in selected), default=None),
            "recovered_count": len(recovered),
            "recovery_checked_count": sum(1 for x in selected if x["recovery_checked"]),
            "recovered_mean_seconds": _mean(recovered),
        }
    return summary


def infusion_rise_window(session: SessionInput, at: float):
    """Vom Guss bis zum Ende seines Anstiegs laut Erkennung, höchstens bis zum nächsten Aufguss."""
    ordered = sorted(session.infusions, key=lambda item: item.at)
    for index, infusion in enumerate(ordered):
        if infusion.at != at:
            continue
        if infusion.rise_end is None:
            return None
        following = ordered[index + 1].at if index + 1 < len(ordered) else inf
        return at, min(infusion.rise_end, following)
    return None


def _rise(series: Series, window):
    if window is None:
        return None
    base = series.at(window[0])
    peak = [value for _, value in series.window(*window)]
    return max(peak) - base if base is not None and peak else None


def gang_figures(session: SessionInput, gang: GangInput, number: int) -> dict:
    start, end = gang.start, gang.end
    bounded = start is not None and end is not None
    openings = [t for kind, t in session.doors if kind == "door_open"]
    inside = [t for t in openings if start < t < end] if bounded else None
    windows = [infusion_rise_window(session, t) for t in gang.infusions]
    rises = [_rise(session.upper_absolute_humidity, w) for w in windows]
    rises_lower = [_rise(session.lower_absolute_humidity, w) for w in windows]
    complete_rises = _complete(rises)
    rise_mean = _mean(complete_rises) if complete_rises is not None else None
    temperatures = session.upper_temperature.window(start, end) if bounded else []
    humidities = session.upper_humidity.window(start, end) if bounded else []
    humidity_peak_at, humidity_peak = (
        max(humidities, key=lambda item: item[1]) if humidities else (None, None)
    )
    duration = end - start if bounded else None
    entry = entry_opening(session, start)
    exit_opening = inside[-1] if inside and not gang.manual_end else None
    entry_at = entry if entry is not None else start
    exit_at = exit_opening if exit_opening is not None else end if bounded else None
    temperature = session.upper_temperature
    return {
        "number": number,
        "start": start,
        "end": end,
        "duration_seconds": duration,
        "entry_opening": entry,
        "entry_temperature_c": temperature.at(entry_at) if entry_at is not None else None,
        "exit_temperature_c": temperature.at(exit_at) if exit_at is not None else None,
        "highest_temperature_c": max(v for _, v in temperatures) if temperatures else None,
        "infusions": list(gang.infusions),
        "infusion_count": len(gang.infusions),
        "humidity_rises_gm3": rises,
        "humidity_rise_mean_gm3": rise_mean,
        "humidity_rises_lower_gm3": rises_lower,
        "water_mean_l": (
            rise_mean * session.volume_m3 / 1000
            if rise_mean is not None and session.volume_m3 is not None else None
        ),
        "inner_door_openings": (
            len(inside if exit_opening is None else inside[:-1]) if inside is not None else None
        ),
        "exit_opening": exit_opening,
        "highest_humidity_percent": humidity_peak,
        "highest_humidity_at": humidity_peak_at,
        "absolute_humidity_at_highest_gm3": (
            session.upper_absolute_humidity.at(humidity_peak_at)
            if humidity_peak_at is not None else None
        ),
        "heating_share": (
            _overlap(start, end, session.heating) / duration if duration else None
        ),
        "target_c": target_at(session, start) if start is not None else None,
        "target_deviation": target_deviation(session, start, end) if bounded else None,
    }


def target_deviation(session: SessionInput, start: float, end: float) -> dict | None:
    """K-G9: zeitgewichtetes Mittel von T − Soll im Gang, kleinste und größte Abweichung."""
    points = [
        (t, value - target, weight)
        for t, value, weight in time_weighted(session.upper_temperature, [(start, end)])
        if (target := target_at(session, t)) is not None
    ]
    if not points:
        return None
    total = sum(weight for *_, weight in points)
    return {
        "mean_c": sum(d * weight for _, d, weight in points) / total,
        "lowest_c": min(d for _, d, _ in points),
        "highest_c": max(d for _, d, _ in points),
    }


def _maximum_position(t, gangs):
    """Einordnung der Höchsttemperatur: vor Gang 1, in Gang n oder nach Gang n."""
    position = {"relation": "before", "gang": 1 if gangs else None}
    for gang in gangs:
        if gang["start"] is None:
            return None
        if t < gang["start"]:
            break
        if gang["end"] is None:
            return None
        if t <= gang["end"]:
            return {"relation": "in", "gang": gang["number"]}
        position = {"relation": "after", "gang": gang["number"]}
    return position


def control_in_ready(session: SessionInput) -> dict | None:
    """Regelung im Bereit ohne Gänge, Ofenkühlung und offene Tür, zeitgewichtet."""
    parameters = session.parameters
    hysteresis = parameters.get("readiness_hysteresis_c")
    offset = parameters.get("readiness_offset_c")
    gangs = [(g.start, g.end) for g in session.gangs]
    if hysteresis is None or offset is None or any(None in gang for gang in gangs):
        return None
    window = _subtract(
        ready_intervals(session),
        gangs + list(session.oven_cooling) + door_pairs(session),
    )
    points = [
        (t, value - target, weight)
        for t, value, weight in time_weighted(session.upper_temperature, window)
        if (target := target_at(session, t)) is not None
    ]
    if not points:
        return None
    total = sum(weight for *_, weight in points)
    below = sum(weight for _, d, weight in points if d < -hysteresis) / total
    above = sum(weight for _, d, weight in points if d > offset) / total
    highest = max(points, key=lambda item: item[1])
    lowest = min(points, key=lambda item: item[1])
    return {
        "window_seconds": total,
        "mean_c": sum(d * weight for _, d, weight in points) / total,
        "below": below,
        "within": 1 - below - above,
        "above": above,
        "highest_c": highest[1],
        "highest_at": highest[0],
        "lowest_c": lowest[1],
        "lowest_at": lowest[0],
        "hysteresis_c": hysteresis,
        "offset_c": offset,
    }


def outside_at(session: SessionInput, t: float) -> OutsideInput | None:
    """Q-U zum Zeitpunkt: der letzte Umgebungseintrag zum Zeitpunkt oder davor.

    Zum Sitzungsbeginn ist das der Eintrag bei Sitzungsbeginn. Es gibt keine
    Altersgrenze; die Messzeit steht beim Wert.
    """
    readings = [reading for reading in session.outside or () if reading.at <= t]
    return readings[-1] if readings else None


def rejected_candidates(session: SessionInput) -> dict | None:
    """K-E2: Öffnungskandidaten mit unpassender Kurvenform, davon endgültig verworfen.

    Kandidaten einer über die Anwesenheit übernommenen Episode zählen als
    Türvorgang, nicht als verworfen. Endgültig verworfen sind die übrigen: in
    Episoden ohne Übernahme oder einzeln (ohne gemeldete Episode).
    """
    if session.rejected_candidates is None:
        return None
    episodes = session.rejected_episodes
    taken = {t for episode in episodes if episode.door for t in episode.candidates}
    held = {t for episode in episodes if not episode.door for t in episode.candidates}
    candidates = session.rejected_candidates
    door = sum(1 for t in candidates if t in taken)
    in_episodes = sum(1 for t in candidates if t in held and t not in taken)
    rejected = len(candidates) - door
    return {
        "candidates": len(candidates),
        "rejected": rejected,
        "rejected_in_episodes": in_episodes,
        "rejected_single": rejected - in_episodes,
        "rejected_episodes": sum(1 for episode in episodes if not episode.door),
        "taken_as_door": door,
        "door_episodes": sum(1 for episode in episodes if episode.door),
    }


def warmup_end_of(session: SessionInput, ready: float | None):
    """Ende des Aufheizens: erstes Bereit oder Öffnen der Eintrittstür zum ersten Gang.

    Es gilt das frühere Ereignis, bei Gleichstand Bereit. Ohne Eintrittstür gilt
    der Gangbeginn. Ist der Beginn des ersten Gangs nicht belegt, bleibt das
    Ende offen.
    """
    candidates = [] if ready is None else [(ready, "ready")]
    if session.gangs:
        start = session.gangs[0].start
        if start is None:
            return None, None
        entry = entry_opening(session, start)
        candidates.append((entry if entry is not None else start, "gang"))
    if not candidates:
        return None, None
    return min(candidates, key=lambda item: (item[0], item[1] != "ready"))


def warmup_curve(
    temperature: Series, start: float | None, end: float | None,
) -> list[tuple[float, float]] | None:
    """K-T9 Bild: T vom Heizbeginn bis zum Ende des Aufheizens, Zeit ab Heizbeginn.

    Erster Punkt ist T beim Heizbeginn (K-S11), letzter T am Ende (K-S10),
    dazwischen alle Messpunkte; ohne einen der beiden Werte fehlt die Kurve.
    """
    if start is None or end is None:
        return None
    first, last = temperature.at(start), temperature.at(end)
    if first is None or last is None:
        return None
    inner = [(t - start, v) for t, v in temperature.window(start, end) if start < t < end]
    return [(0.0, first), *inner, (end - start, last)]


def session_figures(session: SessionInput) -> dict:
    """Kennzahlen K-S1 bis K-S25 mit den Gängen K-G1 bis K-G9 und K-E2 als Rohwerte."""
    gangs = [gang_figures(session, gang, index + 1) for index, gang in enumerate(session.gangs)]
    temperature = session.upper_temperature
    parameters = session.parameters
    manual = session.manual
    heating_start = min((a for a, _ in session.heating), default=None)
    ready = first_ready(session)
    warmup_end, warmup_kind = warmup_end_of(session, ready)
    heating_time = sum(min(b, session.duration) - max(a, 0) for a, b in session.heating)
    power = parameters.get("nominal_power_kw")
    energy_warmup = (
        _overlap(heating_start, warmup_end, session.heating) / 3600 * power
        if warmup_end is not None and heating_start is not None and power is not None
        else None
    )
    points = temperature.window(0, session.duration)
    maximum_at, maximum = max(points, key=lambda item: item[1]) if points else (None, None)
    durations = _complete(g["duration_seconds"] for g in gangs)
    sauna_time = sum(durations) if durations is not None else None
    rests = [
        later["start"] - earlier["end"]
        if later["start"] is not None and earlier["end"] is not None else None
        for earlier, later in pairwise(gangs)
    ]
    complete_rests = _complete(rests)
    doors = door_operations(session)
    openings = [t for kind, t in session.doors if kind == "door_open"]
    oven = [
        {
            "start": a,
            "end": b,
            "duration_seconds": b - a,
            "gang": next((g["number"] for g in reversed(gangs)
                          if g["start"] is not None and g["start"] < a), None),
        }
        for a, b in session.oven_cooling
    ]
    gang_time = sum(durations) if durations else None
    energy = session.energy_kwh
    start_temperature = temperature.at(heating_start) if heating_start is not None else None
    end_temperature = temperature.at(warmup_end) if warmup_end is not None else None
    outside = outside_at(session, 0.0)
    return {
        "session_id": session.session_id,
        "started_at": session.started_at.isoformat(),
        "duration_seconds": session.duration,
        "manual": manual,
        "program": dict(session.program) if session.program is not None else None,
        "parameter_origin": session.parameter_origin,
        "gangs": gangs,
        "gang_count": len(gangs),
        "sauna_time_seconds": sauna_time,
        "gang_duration_mean_seconds": (
            sauna_time / len(gangs) if sauna_time is not None and gangs else None
        ),
        "infusions_in_gangs": sum(g["infusion_count"] for g in gangs),
        "infusions_total": len(session.infusions),
        "maximum_temperature_c": maximum,
        "maximum_temperature_at": maximum_at,
        "maximum_temperature_position": (
            _maximum_position(maximum_at, gangs) if maximum_at is not None else None
        ),
        "maximum_temperature_target_c": (
            target_at(session, maximum_at) if maximum_at is not None else None
        ),
        "rests_seconds": rests,
        "rest_mean_seconds": _mean(complete_rests) if complete_rests is not None else None,
        "heating_started_at": heating_start,
        "start_temperature_c": start_temperature,
        "ready_at": ready,
        "warmup_seconds": (
            warmup_end - heating_start
            if warmup_end is not None and heating_start is not None else None
        ),
        "warmup_end_at": warmup_end,
        "warmup_end_kind": warmup_kind,
        "warmup_end_temperature_c": end_temperature,
        "warmup_end_target_c": (
            target_at(session, warmup_end) if warmup_end is not None else None
        ),
        "warmup_curve": warmup_curve(temperature, heating_start, warmup_end),
        "targets_c": [target for at, target in session.targets
                      if at < operation_off_at(session)],
        "heating_time_seconds": heating_time,
        "heating_share": heating_time / session.duration if session.duration else None,
        "energy_kwh": energy,
        "energy_measured_kwh": session.measured_kwh,
        "energy_upper_bound": manual,
        "energy_check_kwh": heating_time / 3600 * power if power is not None else None,
        # Q-P: Nennleistung der Gegenprobe, wie gespeichert (reine Ausgabe)
        "nominal_power_kw": power,
        "cost_eur": (
            energy * session.price_eur_per_kwh
            if energy is not None and session.price_eur_per_kwh is not None else None
        ),
        "energy_warmup_kwh": energy_warmup,
        "energy_after_warmup_kwh": (
            energy - energy_warmup
            if energy is not None and energy_warmup is not None else None
        ),
        "heating_phases_after_warmup": (
            sum(1 for a, _ in session.heating if a > warmup_end)
            if warmup_end is not None else None
        ),
        "heating_in_gangs": (
            sum(_overlap(g["start"], g["end"], session.heating) for g in gangs) / gang_time
            if gang_time else None
        ),
        "oven_cooling": oven,
        "control_in_ready": control_in_ready(session),
        "outside_temperature_c": outside.value if outside is not None else None,
        "outside_temperature_measured_at": outside.measured_at if outside is not None else None,
        "rejected_candidates": rejected_candidates(session),
        "doors": doors,
        "door_classes": door_summary(doors),
        # K-T10 je Sitzung: Mittel des Verlusts über Ein- und Austritt
        "door_loss_entry_exit_mean_c": _mean(_entry_exit_losses([{"doors": doors}])),
        "door_openings_warmup": (
            sum(1 for t in openings if heating_start <= t < warmup_end)
            if heating_start is not None and warmup_end is not None else None
        ),
        "rise_per_kwh": (
            (end_temperature - start_temperature) / energy_warmup
            if energy_warmup and start_temperature is not None
            and end_temperature is not None else None
        ),
    }


def earlier_warmup(earlier: Iterable[dict], kind: str | None = None) -> dict:
    """K-S10 „frühere Sitzungen“: Median von K-S9 mit Art und Betriebsart der Sitzungen.

    Vergleiche nach Programmart getrennt (Katalog): es zählen nur frühere
    Sitzungen derselben Programmart ``kind`` (K-S25; ohne Angabe ``None``).
    """
    selected = [s for s in earlier
                if s["warmup_seconds"] is not None and program_kind(s) == kind]
    return {
        "program_kind": kind,
        "median_seconds": (
            statistics.median(s["warmup_seconds"] for s in selected) if selected else None
        ),
        "kinds": sorted({s["warmup_end_kind"] for s in selected}),
        "all_manual": all(s["manual"] for s in selected) if selected else None,
        "sessions": len(selected),
    }


def program_kind(figures: dict) -> str | None:
    """K-S25 Art: manual, constant oder program; ohne Angabe Handbetrieb laut Phasen."""
    if figures.get("program") is not None:
        return figures["program"]["kind"]
    return "manual" if figures.get("manual") else None


def _warmup_summary(sessions: list[dict]) -> dict:
    values = [s["warmup_seconds"] for s in sessions if s["warmup_seconds"] is not None]
    return {
        "median_seconds": statistics.median(values) if values else None,
        "range_seconds": (min(values), max(values)) if values else None,
        "sessions": len(values),
    }


def _by_program(sessions: list[dict]) -> dict:
    """Sitzungsbezogene Vergleiche getrennt nach Programmart (Aufheizen, Regelung, Sollabweichung)."""
    groups = {}
    for s in sessions:
        groups.setdefault(program_kind(s) or "unknown", []).append(s)
    return {
        kind: {
            "sessions": len(group),
            "warmup": _warmup_summary(group),
            "per_session": [
                {
                    "session_id": s["session_id"],
                    "started_at": s["started_at"],
                    "warmup_seconds": s["warmup_seconds"],
                    "warmup_end_kind": s["warmup_end_kind"],
                    "control_mean_c": (s["control_in_ready"] or {}).get("mean_c"),
                    "control_within": (s["control_in_ready"] or {}).get("within"),
                    "target_deviation_mean_c": [
                        (g["target_deviation"] or {}).get("mean_c") for g in s["gangs"]
                    ],
                }
                for s in group
            ],
        }
        for kind, group in groups.items()
    }


def _record(items, key, choose=max):
    """Rekord mit Sitzung und Gang; bei Gleichstand der erste."""
    items = [item for item in items if item[key] is not None]
    return choose(items, key=lambda item: item[key]) if items else None


ENTRY_EXIT = ("entry", "exit")
DAY_SECONDS = 24 * 3600.0


def _local(started_at: str, zone: tzinfo) -> datetime:
    return datetime.fromisoformat(started_at).astimezone(zone)


def _seconds_of_day(local: datetime) -> float:
    return local.hour * 3600 + local.minute * 60 + local.second + local.microsecond / 1e6


def _entry_exit_losses(sessions: list[dict]) -> list[float]:
    return [d["loss_c"] for s in sessions for d in s["doors"]
            if d["door_class"] in ENTRY_EXIT and d["loss_c"] is not None]


def start_quartiles(starts: Iterable[float]) -> list[float] | None:
    """K-T6 „meist zwischen“: 1. und 3. Quartil der Beginnzeiten auf dem Tageskreis.

    ``starts`` sind Sekunden seit Mitternacht (Ortszeit). Die Tagesgrenze liegt
    in der größten Lücke zwischen zwei aufeinanderfolgenden Beginnzeiten, die
    Lücke über Mitternacht eingeschlossen; bei gleich großen Lücken in der
    ersten. Liegt sie nicht um Mitternacht, zählen die Beginnzeiten bis zu ihr
    zum Vortag. Die Quartile werden zwischen den Beginnzeiten interpoliert und
    als Sekunden seit Mitternacht geliefert.
    """
    ordered = sorted(starts)
    if not ordered:
        return None
    if len(ordered) == 1:
        return [ordered[0], ordered[0]]
    gaps = [later - earlier for earlier, later in pairwise(ordered)]
    gaps.append(ordered[0] + DAY_SECONDS - ordered[-1])
    first = (gaps.index(max(gaps)) + 1) % len(ordered)
    unrolled = ordered[first:] + [t + DAY_SECONDS for t in ordered[:first]]
    quartiles = statistics.quantiles(unrolled, n=4, method="inclusive")
    return [quartiles[0] % DAY_SECONDS, quartiles[2] % DAY_SECONDS]


def _group_figures(selected: list[dict]) -> dict:
    """K-T12 und K-T11 je Gruppe; Türverlust als Mittel über Ein- und Austritt (K-T10)."""
    sauna = [s["sauna_time_seconds"] for s in selected if s["sauna_time_seconds"] is not None]
    energy = [s["energy_kwh"] for s in selected if s["energy_kwh"] is not None]
    duration = sum(s["duration_seconds"] for s in selected)
    return {
        "sessions": len(selected),
        "sauna_time_seconds": {"value": sum(sauna) if sauna else None, "sessions": len(sauna)},
        "gangs": sum(s["gang_count"] for s in selected),
        "infusions": sum(g["infusion_count"] for s in selected for g in s["gangs"]),
        "energy_kwh": {"value": sum(energy) if energy else None, "sessions": len(energy)},
        "heating_share": (
            sum(s["heating_time_seconds"] for s in selected) / duration if duration else None
        ),
        "door_loss_entry_exit_mean_c": _mean(_entry_exit_losses(selected)),
    }


def _groups(sessions: list[dict], grouping: str | None) -> list[dict] | None:
    """Sitzungsbezogene Listen zusammengefasst: je Saison und Sommer oder je Jahreszeit."""
    if grouping is None:
        return None
    found = {}
    for s in sessions:
        period = period_of(datetime.fromisoformat(s["started_at"]))
        if grouping == "season" and "season" in period:
            period = {"season": period["season"]}
        key = tuple(sorted(period.items()))
        found.setdefault(key, (period, []))[1].append(s)
    return [
        {**period, "label": period_label(period), **_group_figures(selected)}
        for period, selected in sorted(found.values(), key=lambda item: period_start(item[0]))
    ]


def statistics_figures(
    sessions: list[dict], zone: tzinfo, volume_m3: float | None = None,
    grouping: str | None = None,
) -> dict:
    """Kennzahlen K-T1 bis K-T12 über die Sitzungen; je Wert die Zahl seiner Sitzungen.

    ``grouping`` fasst die sitzungsbezogenen Listen zusammen: ``season`` je
    Saison und Sommer (Zeitraum gesamt), ``part`` je Jahreszeit (eine Saison),
    ``None`` je Sitzung.
    """
    # Summen (K-T1 bis K-T4, K-T8) zählen alle Sitzungen. Mittel, Vergleiche und
    # Rekorde verwenden ``compared``; ein Kennzeichen „Testbetrieb“ (Folgepaket)
    # nimmt Sitzungen nur dort aus.
    compared = sessions

    def basis(selected):
        return len({item["session_id"] for item in selected})

    def total(key):
        selected = [s for s in sessions if s[key] is not None]
        value = sum(s[key] for s in selected) if selected else None
        return {"value": value, "sessions": basis(selected)}

    def gangs_of(selected):
        return [{**g, "session_id": s["session_id"]} for s in selected for g in s["gangs"]]

    all_gangs = gangs_of(sessions)
    gangs = gangs_of(compared)
    timed = [g for g in gangs if g["duration_seconds"] is not None]
    rests = [{"value": r, "session_id": s["session_id"]}
             for s in compared for r in s["rests_seconds"] if r is not None]
    rises = [{"value": r, "session_id": g["session_id"]}
             for g in gangs for r in g["humidity_rises_gm3"] if r is not None]
    doors = [{**d, "session_id": s["session_id"], "started_at": s["started_at"]}
             for s in compared for d in s["doors"]]
    entry_exit = [d for d in doors if d["door_class"] in ENTRY_EXIT]
    warmed = [s for s in compared if s["warmup_seconds"] is not None]
    outside = [s for s in compared if s["outside_temperature_c"] is not None]
    energy_and_sauna = [s for s in sessions
                        if s["energy_kwh"] is not None and s["sauna_time_seconds"]]
    infusions_most = max((g["infusion_count"] for g in gangs), default=None)
    gang_counts = Counter(s["gang_count"] for s in compared)
    most_frequent = max(gang_counts.values(), default=0)
    locals_ = [(s, _local(s["started_at"], zone)) for s in compared]
    weekdays = Counter(local.isoweekday() for _, local in locals_)
    starts = [_seconds_of_day(local) for _, local in locals_]
    longest_rest = _record(rests, "value")
    largest_loss = _record([d for d in entry_exit if d["loss_c"] is not None], "loss_c")

    def spread(values):
        values = list(values)
        return (min(values), max(values)) if values else None

    def gang_ref(gang, key):
        return None if gang is None else {
            "session_id": gang["session_id"], "gang": gang["number"], "value": gang[key],
        }

    return {
        "sessions": {"value": len(sessions), "sessions": len(sessions)},
        "operating_time_seconds": total("duration_seconds"),
        "sauna_time_seconds": total("sauna_time_seconds"),
        "gangs": {"value": len(all_gangs), "sessions": len(sessions)},
        "gangs_per_session": {
            "value": len(gangs) / len(compared) if compared else None,
            "sessions": len(compared),
        },
        "infusions": {"value": sum(g["infusion_count"] for g in all_gangs),
                      "sessions": len(sessions)},
        "infusions_per_gang": {
            "value": sum(g["infusion_count"] for g in gangs) / len(gangs) if gangs else None,
            "sessions": basis(gangs),
        },
        "infusions_most": {
            "value": infusions_most,
            "where": [{"session_id": g["session_id"], "gang": g["number"]}
                      for g in gangs if g["infusion_count"] == infusions_most],
            "sessions": basis(gangs),
        },
        "gang_duration": {
            "mean_seconds": _mean(g["duration_seconds"] for g in timed),
            "range_seconds": spread(g["duration_seconds"] for g in timed),
            "gangs": len(timed),
            "sessions": basis(timed),
        },
        "gang_infusions": {
            "mean": _mean(g["infusion_count"] for g in gangs),
            "range": spread(g["infusion_count"] for g in gangs),
            "gangs": len(gangs),
            "sessions": basis(gangs),
        },
        "humidity_rise": {
            "mean_gm3": (rise_mean := _mean(r["value"] for r in rises)),
            "water_mean_l": (
                rise_mean * volume_m3 / 1000
                if rise_mean is not None and volume_m3 is not None else None
            ),
            "infusions": len(rises),
            "sessions": basis(rises),
        },
        "rest": {
            "mean_seconds": _mean(r["value"] for r in rests),
            "range_seconds": spread(r["value"] for r in rests),
            "longest": longest_rest,
            "rests": len(rests),
            "sessions": basis(rests),
        },
        "habits": {
            "sessions": [
                {
                    "session_id": s["session_id"],
                    "weekday": local.isoweekday(),
                    "local_start": local.time().isoformat(),
                    "gang_count": s["gang_count"],
                }
                for s, local in locals_
            ],
            "weekday_sessions": [weekdays.get(day, 0) for day in range(1, 8)],
            # Mittlere Hälfte der Beginnzeiten (Ortszeit) auf dem Tageskreis.
            "start_quartiles_seconds": start_quartiles(starts),
            "most_frequent_gang_count": sorted(
                count for count, n in gang_counts.items() if n == most_frequent
            ) if compared else None,
            "most_frequent_gang_count_sessions": most_frequent if compared else None,
        },
        "records": {
            "longest_gang": gang_ref(_record(timed, "duration_seconds"), "duration_seconds"),
            "hottest_gang": gang_ref(_record(gangs, "highest_temperature_c"),
                                     "highest_temperature_c"),
            "most_infusions": gang_ref(_record(gangs, "infusion_count"), "infusion_count"),
            "longest_session": (
                {"session_id": (best := max(compared, key=lambda s: s["duration_seconds"]))
                 ["session_id"], "value": best["duration_seconds"]} if compared else None
            ),
            "coldest_outside": (
                {"session_id": (cold := min(outside, key=lambda s: s["outside_temperature_c"]))
                 ["session_id"], "value": cold["outside_temperature_c"]} if outside else None
            ),
            "coldest_outside_sessions": len(outside),
        },
        "energy_kwh": total("energy_kwh"),
        "cost_eur": total("cost_eur"),
        "energy_per_sauna_hour_kwh": {
            "value": (
                sum(s["energy_kwh"] for s in energy_and_sauna)
                / (sum(s["sauna_time_seconds"] for s in energy_and_sauna) / 3600)
                if energy_and_sauna else None
            ),
            "sessions": len(energy_and_sauna),
        },
        "heating_share": {
            "value": (
                sum(s["heating_time_seconds"] for s in sessions)
                / sum(s["duration_seconds"] for s in sessions)
                if sessions and sum(s["duration_seconds"] for s in sessions) else None
            ),
            "sessions": len(sessions),
        },
        "by_program": _by_program(compared),
        "warmup": {
            **_warmup_summary(compared),
            "per_session": [
                {
                    "session_id": s["session_id"],
                    "started_at": s["started_at"],
                    "program_kind": program_kind(s),
                    "heating_started_at": s["heating_started_at"],
                    "end_at": s["warmup_end_at"],
                    "kind": s["warmup_end_kind"],
                    "seconds": s["warmup_seconds"],
                    "start_temperature_c": s["start_temperature_c"],
                    "end_temperature_c": s["warmup_end_temperature_c"],
                    "end_target_c": s["warmup_end_target_c"],
                    "curve": s["warmup_curve"],
                }
                for s in warmed
            ],
        },
        "door_classes": {**door_summary(doors), "sessions": basis(doors)},
        "door_entry_exit": {
            "count": len(entry_exit),
            "loss_mean_c": _mean(d["loss_c"] for d in entry_exit if d["loss_c"] is not None),
            "open_mean_seconds": _mean(d["open_seconds"] for d in entry_exit),
            "largest": None if largest_loss is None else {
                "loss_c": largest_loss["loss_c"],
                "door_class": largest_loss["door_class"],
                "session_id": largest_loss["session_id"],
                "started_at": largest_loss["started_at"],
                "opened": largest_loss["opened"],
                "open_seconds": largest_loss["open_seconds"],
            },
            "sessions": basis(entry_exit),
        },
        "groups": _groups(compared, grouping),
        "per_session": [
            {
                "session_id": s["session_id"],
                "started_at": s["started_at"],
                "program_kind": program_kind(s),
                "duration_seconds": s["duration_seconds"],
                "gang_count": s["gang_count"],
                "gangs": [
                    {key: g[key] for key in (
                        "number", "start", "end", "duration_seconds", "infusion_count",
                    )}
                    for g in s["gangs"]
                ],
                "sauna_time_seconds": s["sauna_time_seconds"],
                "infusions_in_gangs": s["infusions_in_gangs"],
                "energy_kwh": s["energy_kwh"],
                "heating_share": s["heating_share"],
                "door_loss_entry_exit_mean_c": s["door_loss_entry_exit_mean_c"],
            }
            for s in compared
        ],
    }


def in_period(started_at: str, period: Mapping | None = None) -> bool:
    """Sitzungsbeginn im gewählten Zeitraum: Saison, Jahreszeit einer Saison oder Sommer."""
    if not period:
        return True
    found = period_of(datetime.fromisoformat(started_at))
    return all(found.get(key) == value for key, value in period.items())


def periods(started: Iterable[str]) -> list[dict]:
    """Saisons (mit ihren Jahreszeiten) und Sommer, in denen Sitzungen liegen."""
    found = {}
    for value in started:
        period = period_of(datetime.fromisoformat(value))
        if "summer" in period:
            found.setdefault(("summer", period["summer"]), {"summer": period["summer"]})
        else:
            entry = found.setdefault(("season", period["season"]),
                                     {"season": period["season"], "parts": set()})
            entry["parts"].add(period["part"])
    result = []
    for entry in sorted(found.values(), key=period_start):
        if "summer" in entry:
            result.append({**entry, "label": period_label(entry)})
        else:
            result.append({
                "season": entry["season"],
                "label": period_label({"season": entry["season"]}),
                "parts": [
                    {"part": part, "label": period_label({"season": entry["season"], "part": part})}
                    for part in PARTS if part in entry["parts"]
                ],
            })
    return result


# ---------- Eingang aus dem Archiv ----------


def _seconds(start: datetime, value) -> float | None:
    if value is None:
        return None
    return (utc(datetime.fromisoformat(value)) - start).total_seconds()


def _confirmed(gang: Mapping) -> bool:
    """Bestätigung wie ``timeline.Gang.confirmation`` für archivierte Gänge."""
    return bool(gang.get("infusion_events")) or (
        gang.get("recognition_kind") == Kind.PRESENCE_CONFIRMED
    )


def _series(points) -> Series:
    points = sorted(points, key=lambda item: item[0])
    return Series(tuple(t for t, _ in points), tuple(v for _, v in points))


def session_program(session: Mapping, configuration: Mapping | None, phases) -> dict | None:
    """K-S25 Programmart aus Sitzungsstand und Konfiguration der Sitzung."""
    configuration = configuration or {}
    manual = (any(phase == "manuell" for _, phase in phases) if phases
              else configuration.get("control_mode") == "manual")
    if manual:
        return {"kind": "manual"}
    mode = session.get("temperature_program_mode") or configuration.get("program_mode")
    if mode == "constant":
        return {"kind": "constant"}
    if mode != "progressive":
        return None
    selected = configuration.get("selected_program_id")
    program = next((item for item in configuration.get("temperature_programs") or ()
                    if item.get("id") == selected), None)
    steps = session.get("temperature_program_steps") or configuration.get("temperature_steps")
    parameters = configuration.get("parameters") or {}
    try:
        if steps is None and program is not None:
            steps = evenly_distributed(
                program["start_c"], program["end_c"], program["distribution_gangs"]
            )
        elif steps is None:
            steps = evenly_distributed(
                parameters.get("target_temperature_c"), parameters.get("final_temperature_c"),
                session.get("temperature_program_gangs") or parameters.get("temperature_gangs"),
            )
    except (KeyError, ValueError):
        steps = None
    return {
        "kind": "program",
        "id": selected if program is not None else None,
        "name": program.get("name") if program is not None else None,
        "steps": list(steps) if steps is not None else None,
    }


def outside_readings(
    start: datetime, environment: Iterable[tuple[str, Mapping]],
) -> tuple[OutsideInput, ...] | None:
    """Q-U aus den archivierten Umgebungseinträgen (Eintragszeit, Inhalt) der Sitzung.

    Ohne Umgebungseintrag (ältere Sitzung oder keine gebundene Quelle) ``None``.
    """
    readings = []
    for received_at, record in environment:
        value = next((item.get("value") for item in record.get("values") or ()
                      if item.get("key") == "temperature"), None)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(value):
            value = None
        measured_at = record.get("measurement_time")
        readings.append(OutsideInput(
            0.0 if record.get("reason") == "session_start" else _seconds(start, received_at),
            None if value is None else float(value),
            _seconds(start, measured_at) if isinstance(measured_at, str) else None,
        ))
    return tuple(sorted(readings, key=lambda reading: reading.at)) if readings else None


def session_input_from_archive(
    session: Mapping,
    *,
    ended_at: str,
    measurements: Iterable[tuple[str, str, str, float | None]],
    detections: Iterable[Mapping],
    infusion_updates: Iterable[Mapping],
    configuration: Mapping | None,
    parameter_origin: str | None,
    volume_m3: float | None,
    price_eur_per_kwh: float | None,
    door_sensor_lag: float | None,
    environment: Iterable[tuple[str, Mapping]] = (),
    rejected: Iterable[Mapping] | None = None,
    episodes: Iterable[Mapping] = (),
) -> SessionInput:
    """Eingang einer abgeschlossenen archivierten Sitzung.

    ``measurements`` sind (Empfangszeit, Position, Größe, Wert) der Sitzung,
    ``detections`` die archivierten Erkennungsereignisse, ``infusion_updates``
    die Aufgussenden. Gänge stammen aus der Zeitleiste, Steuerung und Energie
    aus dem letzten Sitzungsstand, Parameter aus ``configuration`` (Q-P).
    ``environment`` sind die Umgebungseinträge (Eintragszeit, Inhalt),
    ``rejected`` die verworfenen Kandidaten (``None``: nicht aufgezeichnet),
    ``episodes`` ihre Episoden.
    """
    parameters = (configuration or {}).get("parameters")
    timeline = session["timeline"]
    start = utc(datetime.fromisoformat(timeline["session_started_at"]))
    duration = _seconds(start, ended_at)
    roles = {}
    for received_at, position, quantity, value in measurements:
        if value is None or isinstance(value, bool) or not isfinite(value):
            continue
        roles.setdefault(f"{position}_{quantity}", []).append(
            (_seconds(start, received_at), float(value))
        )

    def series(role):
        return _series(roles.get(role, ()))

    events = [d["event"] for d in detections if isinstance(d.get("event"), Mapping)]
    doors = tuple(sorted(
        ((e["kind"], _seconds(start, e["effective_at"])) for e in events
         if e.get("kind") in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE)),
        key=lambda item: item[1],
    ))
    ends = {}
    for update in infusion_updates:
        end = update.get("end")
        ends[update.get("source_event_id")] = (
            _seconds(start, end["effective_at"]) if isinstance(end, Mapping) else None
        )
    infusions = tuple(sorted(
        (InfusionInput(_seconds(start, e["effective_at"]), ends.get(e["event_id"]))
         for e in events if e.get("kind") == Kind.INFUSION),
        key=lambda item: item.at,
    ))
    intervals = {item["gang_id"]: item for item in project_gang_intervals(timeline)}
    archived = [*(timeline.get("completed") or ()),
                *([timeline["active"]] if timeline.get("active") else [])]
    gangs = tuple(
        GangInput(
            _seconds(start, intervals[g["gang_id"]]["started_at"]),
            _seconds(start, intervals[g["gang_id"]]["ended_at"]),
            tuple(sorted(_seconds(start, e["effective_at"])
                         for e in g.get("infusion_events") or ())),
            g.get("end_reason") == "manual",
        )
        for g in archived if g.get("gang_id") in intervals and _confirmed(g)
    )
    phases = tuple((_seconds(start, p["at"]), p["phase"])
                   for p in session.get("base_phases") or ())
    heating = tuple(
        (max(0.0, _seconds(start, i["started_at"])),
         min(duration, _seconds(start, i["ended_at"]) if i.get("ended_at") else duration))
        for i in (session.get("heating") or {}).get("intervals", ()) if i.get("started_at")
    )
    energy = session.get("energy") or {}
    estimated, measured = energy.get("estimated_kwh"), energy.get("measured_kwh")
    cooling = tuple(
        (_seconds(start, c["started_at"]), _seconds(start, c["ends_at"]))
        for c in session.get("after_run_history") or ()
        if c.get("started_at") and c.get("ends_at")
    )
    return SessionInput(
        started_at=start,
        duration=duration,
        upper_temperature=series("upper_temperature"),
        lower_temperature=series("lower_temperature"),
        upper_humidity=series("upper_humidity"),
        lower_humidity=series("lower_humidity"),
        gangs=gangs,
        infusions=infusions,
        doors=doors,
        phases=phases,
        targets=tuple((_seconds(start, t["at"]), t["target_c"])
                      for t in session.get("target_history") or ()),
        heating=heating,
        energy_kwh=(
            (estimated or 0) + (measured or 0)
            if estimated is not None or measured is not None else None
        ),
        measured_kwh=measured,
        oven_cooling=cooling,
        parameters={key: (parameters or {}).get(key) for key in PARAMETER_KEYS},
        parameter_origin=parameter_origin if parameters is not None else None,
        outside=outside_readings(start, environment),
        volume_m3=volume_m3,
        price_eur_per_kwh=price_eur_per_kwh,
        session_id=timeline.get("session_id"),
        door_sensor_lag=door_sensor_lag,
        program=session_program(session, configuration, phases),
        readiness=(
            tuple((_seconds(start, mark["at"]), mark["ready"])
                  for mark in session["ready_history"])
            if session.get("ready_history") is not None else None
        ),
        rejected_candidates=(
            tuple(sorted(_seconds(start, item["effective_at"]) for item in rejected))
            if rejected is not None else None
        ),
        rejected_episodes=tuple(
            EpisodeInput(
                _seconds(start, episode["opened_at"]),
                _seconds(start, episode["ended_at"]),
                episode.get("result") == "door",
                tuple(_seconds(start, at) for at in episode.get("candidates") or ()),
            )
            for episode in episodes
        ),
    )
