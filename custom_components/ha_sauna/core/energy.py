"""Sessionenergie aus Messleistung und berechneten Beiträgen für Messlücken."""

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from math import isfinite

from .models import Energy, EnergyInterval
from .timeline import utc


def _append_interval(intervals, interval):
    if (
        intervals
        and intervals[-1].ended_at == interval.started_at
        and intervals[-1].source == interval.source
        and intervals[-1].power_kw == interval.power_kw
    ):
        return intervals[:-1] + (
            replace(intervals[-1], ended_at=interval.ended_at),
        )
    return intervals + (interval,)


def advance(state, at, *, power_w, valid_until, heating, nominal_kw):
    at = utc(at)
    if state.accounted_at is None:
        return replace(state, accounted_at=at)
    if at < state.accounted_at:
        raise ValueError("Energiezeit darf nicht rückwärts laufen")
    seconds = (at - state.accounted_at).total_seconds()
    measured = 0.0
    if power_w is not None and valid_until is not None:
        measured = max(0.0, (min(at, valid_until) - state.accounted_at).total_seconds())
    remaining = seconds - measured
    # Letzter gültiger Leistungswert gilt bis zum nächsten Empfang, höchstens
    # bis zur konfigurierten Gültigkeitsgrenze. Auch Standbyleistung wird erfasst.
    intervals = state.intervals
    measured_end = state.accounted_at
    if measured > 0:
        measured_end = min(at, valid_until)
        intervals = _append_interval(
            intervals,
            EnergyInterval(
                state.accounted_at, measured_end, "measured", power_w / 1000
            ),
        )
    if remaining > 0:
        intervals = _append_interval(
            intervals,
            EnergyInterval(
                measured_end,
                at,
                "unknown" if heating is None else "estimated",
                None if heating is None else nominal_kw if heating is True else 0,
            ),
        )
    return replace(
        state,
        accounted_at=at,
        measured_kwh=state.measured_kwh + (power_w or 0) * measured / 3600000,
        estimated_kwh=state.estimated_kwh
        + (nominal_kw * remaining / 3600 if heating is True else 0),
        measured_seconds=state.measured_seconds + measured,
        estimated_seconds=state.estimated_seconds
        + (remaining if heating is not None else 0),
        unknown_seconds=state.unknown_seconds + (remaining if heating is None else 0),
        intervals=intervals,
    )


def energy_for_interval(state: Energy | Mapping, started_at, ended_at) -> Energy | None:
    """[A, B) aus gespeicherten Beiträgen schneiden; fehlende Abdeckung ist None."""

    def get(value, key, default=None):
        return (
            value.get(key, default)
            if isinstance(value, Mapping)
            else getattr(value, key, default)
        )

    def time(value):
        return utc(datetime.fromisoformat(value) if isinstance(value, str) else value)

    started_at, ended_at = time(started_at), time(ended_at)
    if ended_at < started_at:
        raise ValueError("Energieintervall darf nicht rückwärts laufen")
    if ended_at == started_at:
        return Energy(accounted_at=ended_at)
    try:
        accounted_at = time(get(state, "accounted_at"))
    except (TypeError, ValueError):
        return None
    if ended_at > accounted_at:
        return None
    intervals = get(state, "intervals")
    if not isinstance(intervals, (tuple, list)) or not intervals:
        return None
    cursor, previous_end = started_at, None
    measured_kwh = estimated_kwh = 0.0
    measured_seconds = estimated_seconds = unknown_seconds = 0.0
    clipped = []
    for interval in intervals:
        try:
            start, end = time(get(interval, "started_at")), time(
                get(interval, "ended_at")
            )
        except (TypeError, ValueError):
            return None
        if end <= start or (previous_end is not None and start < previous_end):
            return None
        previous_end = end
        if end <= started_at:
            continue
        if start >= ended_at:
            break
        start, end = max(start, started_at), min(end, ended_at)
        if start != cursor:
            return None
        source, power_kw = get(interval, "source"), get(interval, "power_kw")
        seconds = (end - start).total_seconds()
        if source == "unknown" and power_kw is None:
            unknown_seconds += seconds
        elif (
            source in ("measured", "estimated")
            and isinstance(power_kw, (int, float))
            and not isinstance(power_kw, bool)
        ):
            try:
                if not isfinite(power_kw):
                    return None
            except OverflowError:
                return None
            if source == "measured":
                measured_kwh += power_kw * seconds / 3600
                measured_seconds += seconds
            else:
                estimated_kwh += power_kw * seconds / 3600
                estimated_seconds += seconds
        else:
            return None
        if (
            clipped
            and clipped[-1].ended_at == start
            and clipped[-1].source == source
            and clipped[-1].power_kw == power_kw
        ):
            clipped[-1] = replace(clipped[-1], ended_at=end)
        else:
            clipped.append(EnergyInterval(start, end, source, power_kw))
        cursor = end
        if cursor == ended_at:
            return Energy(
                measured_kwh=measured_kwh,
                estimated_kwh=estimated_kwh,
                measured_seconds=measured_seconds,
                estimated_seconds=estimated_seconds,
                unknown_seconds=unknown_seconds,
                accounted_at=ended_at,
                intervals=tuple(clipped),
            )
    return None
