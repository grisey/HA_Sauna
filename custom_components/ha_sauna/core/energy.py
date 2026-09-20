"""Sessionenergie aus Leistung; sichtbar geschätzter Ersatz für Messlücken."""

from dataclasses import replace
from .timeline import utc


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
    )
