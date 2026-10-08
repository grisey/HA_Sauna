"""Berechnung unveränderlicher Temperaturprogramme für gezählte Gänge."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite

from .parameters import BY_KEY
from .temperature_target import whole_temperature

MAXIMUM_TEMPERATURE_C = BY_KEY["target_temperature_c"].maximum


def _temperature(
    value: object, name: str, minimum_c: float = 0,
    maximum_c: float = MAXIMUM_TEMPERATURE_C,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite temperature")
    try:
        number = float(value)
    except OverflowError:
        raise ValueError(f"{name} must be a finite temperature") from None
    if not isfinite(number) or not minimum_c <= number <= maximum_c:
        raise ValueError(f"{name} must be between {minimum_c} and {maximum_c}")
    return number


def _count(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def temperature_steps(
    value: object, name: str = "temperature_steps", *,
    minimum_c: float = 0, maximum_c: float = MAXIMUM_TEMPERATURE_C,
    rounded: bool = True,
) -> tuple[float, ...]:
    """Validate explicit targets shared by free and named programs."""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be a non-empty sequence of temperatures")
    steps = tuple(_temperature(step, name, minimum_c, maximum_c) for step in value)
    if not steps:
        raise ValueError(f"{name} must not be empty")
    if not rounded:
        return steps
    targets = tuple(whole_temperature(step) for step in steps)
    for target in targets:
        _temperature(target, name, minimum_c, maximum_c)
    return targets


def evenly_distributed(
    start_c: object, end_c: object, gangs: object
) -> tuple[float, ...]:
    """Start und Ende liegen auf dem ersten und letzten Verteilungspunkt."""
    start = whole_temperature(_temperature(start_c, "start_c"))
    end = whole_temperature(_temperature(end_c, "end_c"))
    count = _count(gangs, "gangs")
    if count == 1:
        return (start,)
    step = (end - start) / (count - 1)
    return tuple(whole_temperature(start + step * index) for index in range(count))


@dataclass(frozen=True)
class TemperatureProgram:
    """A program has no controller state and never changes a stored end target."""

    start_c: float
    end_c: float
    gangs: int
    steps: tuple[float, ...] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "start_c", whole_temperature(_temperature(self.start_c, "start_c"))
        )
        object.__setattr__(
            self, "end_c", whole_temperature(_temperature(self.end_c, "end_c"))
        )
        object.__setattr__(self, "gangs", _count(self.gangs, "gangs"))
        if self.steps is not None:
            object.__setattr__(self, "steps", temperature_steps(self.steps))

    def target(self, completed: object) -> float:
        """Target after ``completed`` actual gangs."""
        if (
            isinstance(completed, bool)
            or not isinstance(completed, int)
            or completed < 0
        ):
            raise ValueError("completed must be a non-negative integer")
        if self.steps is not None:
            return self.steps[min(completed, len(self.steps) - 1)]
        # A one-gang distribution still begins at its configured start.  When
        # start and end differ, the first actual gang must be able to reach
        # the end instead of leaving it unreachable forever.
        if self.gangs == 1 and self.start_c != self.end_c:
            return self.start_c if completed == 0 else self.end_c
        targets = evenly_distributed(self.start_c, self.end_c, self.gangs)
        return targets[min(completed, self.gangs - 1)]
