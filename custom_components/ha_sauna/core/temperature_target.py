"""Whole-degree setpoints, separate from measured temperatures and offsets."""

from math import floor


def whole_temperature(value: float) -> int:
    """Round an already validated non-negative target, with .5 rounded up."""
    integer = floor(value)
    return integer + int(value - integer >= 0.5)
