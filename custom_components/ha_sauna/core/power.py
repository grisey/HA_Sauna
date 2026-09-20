"""Normalisierung einer optionalen, unabhängigen Ofen-Leistungsmessung."""

from math import isfinite


def watts(raw, unit):
    """Nur endliche, nicht negative Leistungswerte; keine Schätzung aus Befehlen."""
    if unit not in ("W", "kW") or isinstance(raw, bool):
        return None
    try:
        value = float(raw) * (1000 if unit == "kW" else 1)
    except (ValueError, TypeError, OverflowError):
        return None
    return value if isfinite(value) and value >= 0 else None


def heating(value, threshold):
    if value is None or threshold is None:
        return None
    return value > threshold
