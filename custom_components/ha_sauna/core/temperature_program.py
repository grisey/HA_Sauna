"""Berechnung unveränderlicher Temperaturprogramme für gezählte Gänge."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from .parameters import BY_KEY


MAXIMUM_TEMPERATURE_C = BY_KEY["target_temperature_c"].maximum


def _temperature(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite temperature")
    number = float(value)
    if not isfinite(number) or not 0 <= number <= MAXIMUM_TEMPERATURE_C:
        raise ValueError(f"{name} must be between 0 and {MAXIMUM_TEMPERATURE_C}")
    return number


def _count(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def evenly_distributed(start_c: object, end_c: object, gangs: object) -> tuple[float, ...]:
    """Start und Ende liegen auf dem ersten und letzten Verteilungspunkt."""
    start = _temperature(start_c, "start_c")
    end = _temperature(end_c, "end_c")
    count = _count(gangs, "gangs")
    if count == 1:
        return (start,)
    step = (end - start) / (count - 1)
    return tuple(start + step * index for index in range(count))


@dataclass(frozen=True)
class TemperatureProgram:
    """A program has no controller state and never changes a stored end target."""

    start_c: float
    end_c: float
    gangs: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "start_c", _temperature(self.start_c, "start_c"))
        object.__setattr__(self, "end_c", _temperature(self.end_c, "end_c"))
        object.__setattr__(self, "gangs", _count(self.gangs, "gangs"))

    def target(self, completed: object) -> float:
        """Target after ``completed`` actual gangs."""
        if isinstance(completed, bool) or not isinstance(completed, int) or completed < 0:
            raise ValueError("completed must be a non-negative integer")
        # A one-gang distribution still begins at its configured start.  When
        # start and end differ, the first actual gang must be able to reach
        # the end instead of leaving it unreachable forever.
        if self.gangs == 1 and self.start_c != self.end_c:
            return self.start_c if completed == 0 else self.end_c
        targets = evenly_distributed(self.start_c, self.end_c, self.gangs)
        return targets[min(completed, self.gangs - 1)]
