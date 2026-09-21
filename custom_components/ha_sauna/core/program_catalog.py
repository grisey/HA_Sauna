"""Benannte, unveränderliche Temperaturprogramme ohne Laufzeitkopplung."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite

from .parameters import BY_KEY
from .temperature_program import TemperatureProgram, temperature_steps

MAXIMUM_DISTRIBUTION_GANGS = int(BY_KEY["temperature_gangs"].maximum)


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} muss eine endliche Zahl sein")
    number = float(value)
    if not isfinite(number):
        raise ValueError(f"{name} muss eine endliche Zahl sein")
    return number


def _limits(minimum_c: object, maximum_c: object) -> tuple[float, float]:
    minimum = _finite_number(minimum_c, "Mindesttemperatur")
    maximum = _finite_number(maximum_c, "Höchsttemperatur")
    if minimum > maximum:
        raise ValueError(
            "Die Mindesttemperatur darf die Höchsttemperatur nicht überschreiten"
        )
    return minimum, maximum


def _within_limits(value: float, name: str, minimum: float, maximum: float) -> None:
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} muss zwischen {minimum:g} und {maximum:g} °C liegen")


@dataclass(frozen=True)
class NamedTemperatureProgram:
    """A selectable program and the values needed to calculate its targets."""

    id: str
    name: str
    start_c: float | None = None
    end_c: float | None = None
    distribution_gangs: int | None = None
    temperature_steps: tuple[float, ...] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("Die Programm-ID muss ein nicht leerer Text sein")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Der Programmname muss ein nicht leerer Text sein")
        object.__setattr__(self, "id", self.id.strip())
        object.__setattr__(self, "name", self.name.strip())
        if self.temperature_steps is not None:
            steps = temperature_steps(self.temperature_steps, "Temperaturstufen")
            if len(steps) > MAXIMUM_DISTRIBUTION_GANGS:
                raise ValueError("Die Anzahl der Temperaturstufen ist zu groß")
            supplied = (self.start_c, self.end_c, self.distribution_gangs)
            derived = (steps[0], steps[-1], len(steps))
            if any(value is not None for value in supplied) and supplied != derived:
                raise ValueError(
                    "Start, Ende und Anzahl müssen zu den Temperaturstufen passen"
                )
            object.__setattr__(self, "temperature_steps", steps)
            object.__setattr__(self, "start_c", steps[0])
            object.__setattr__(self, "end_c", steps[-1])
            object.__setattr__(self, "distribution_gangs", len(steps))
            return
        object.__setattr__(
            self, "start_c", _finite_number(self.start_c, "Starttemperatur")
        )
        object.__setattr__(self, "end_c", _finite_number(self.end_c, "Endtemperatur"))
        if (
            isinstance(self.distribution_gangs, bool)
            or not isinstance(self.distribution_gangs, int)
            or self.distribution_gangs < 1
        ):
            raise ValueError(
                "Die Anzahl der Temperaturstufen muss eine positive ganze Zahl sein"
            )

    def temperature_program(
        self, *, minimum_c: float, maximum_c: float
    ) -> TemperatureProgram:
        """Return the existing calculation model after catalog-bound validation."""
        minimum, maximum = _limits(minimum_c, maximum_c)
        _within_limits(self.start_c, "Starttemperatur", minimum, maximum)
        _within_limits(self.end_c, "Endtemperatur", minimum, maximum)
        if self.temperature_steps is not None:
            for step in self.temperature_steps:
                _within_limits(step, "Temperaturstufe", minimum, maximum)
        return TemperatureProgram(
            self.start_c, self.end_c, self.distribution_gangs, self.temperature_steps
        )

    def as_dict(self) -> dict[str, str | float | int | tuple[float, ...]]:
        """Return a JSON-safe representation for config-entry options."""
        result = {
            "id": self.id,
            "name": self.name,
            "start_c": self.start_c,
            "end_c": self.end_c,
            "distribution_gangs": self.distribution_gangs,
        }
        if self.temperature_steps is not None:
            result["temperature_steps"] = self.temperature_steps
        return result

    def option(self) -> dict[str, str]:
        """Return the small representation used by a program selector."""
        return {"id": self.id, "name": self.name}


DEFAULT_PROGRAMS = (
    NamedTemperatureProgram("genusszeit", "Genusszeit", 80, 90, 3),
    NamedTemperatureProgram("gipfelstuermer", "Gipfelstürmer", 84, 100, 3),
    NamedTemperatureProgram("ewigkeit", "Ewigkeit", 80, 96, 5),
    NamedTemperatureProgram("liegewiese", "Liegewiese", 75, 85, 3),
    NamedTemperatureProgram("hoehenwanderung", "Höhenwanderung", 90, 100, 3),
    NamedTemperatureProgram("schnellstarter", "Schnellstarter", 70, 90, 2),
)

RESERVED_PROGRAM_IDS = frozenset(("current", "constant", "progressive"))


def program_options(
    programs: Sequence[NamedTemperatureProgram], *, minimum_c: float, maximum_c: float
) -> list[dict[str, str]]:
    """Validate a catalog and return its selector options in catalog order."""
    return [
        program.option()
        for program in validate_programs(
            programs, minimum_c=minimum_c, maximum_c=maximum_c
        )
    ]


def validate_programs(
    programs: Sequence[NamedTemperatureProgram],
    *,
    minimum_c: float,
    maximum_c: float,
    maximum_gangs: int = MAXIMUM_DISTRIBUTION_GANGS,
) -> tuple[NamedTemperatureProgram, ...]:
    """Check catalog uniqueness and limits, retaining the immutable objects."""
    if isinstance(programs, (str, bytes)) or not isinstance(programs, Sequence):
        raise ValueError("Die Programme müssen als Liste übergeben werden")
    ids: set[str] = set()
    checked = []
    for program in programs:
        if not isinstance(program, NamedTemperatureProgram):
            raise ValueError("Die Programmliste enthält einen ungültigen Eintrag")
        if program.id in RESERVED_PROGRAM_IDS:
            raise ValueError(f"Die Programm-ID {program.id!r} ist reserviert")
        program.temperature_program(minimum_c=minimum_c, maximum_c=maximum_c)
        if program.distribution_gangs > maximum_gangs:
            raise ValueError(
                f"Die Anzahl der Temperaturstufen darf höchstens {maximum_gangs} sein"
            )
        if program.id in ids:
            raise ValueError("Jede Programm-ID darf nur einmal vorkommen")
        ids.add(program.id)
        checked.append(program)
    return tuple(checked)


def load_programs(
    stored: object,
    *,
    minimum_c: float,
    maximum_c: float,
    maximum_gangs: int = MAXIMUM_DISTRIBUTION_GANGS,
) -> tuple[NamedTemperatureProgram, ...]:
    """Load and validate JSON-decoded program option values."""
    if isinstance(stored, (str, bytes)) or not isinstance(stored, Sequence):
        raise ValueError("Die Programme müssen als Liste übergeben werden")
    programs = []
    for value in stored:
        if not isinstance(value, Mapping):
            raise ValueError("Jedes Programm muss ein Objekt sein")
        fields = set(value)
        standard = {"id", "name", "start_c", "end_c", "distribution_gangs"}
        manual = {"id", "name", "temperature_steps"}
        complete_manual = standard | {"temperature_steps"}
        if fields not in (standard, manual, complete_manual):
            raise ValueError("Ein Programm enthält ungültige Felder")
        programs.append(NamedTemperatureProgram(**value))
    return validate_programs(
        programs,
        minimum_c=minimum_c,
        maximum_c=maximum_c,
        maximum_gangs=maximum_gangs,
    )


def migrate_legacy_programs(
    options: Mapping[str, object],
    *,
    minimum_c: float,
    maximum_c: float,
    maximum_gangs: int = MAXIMUM_DISTRIBUTION_GANGS,
) -> tuple[NamedTemperatureProgram, ...]:
    """Append meaningful old profiles while keeping the six shipped programs fixed.

    ``program_1`` and ``program_2`` remain their old IDs so a stored external
    button selection continues to identify the same legacy program.  A profile
    whose old values equal its former defaults is only retained when that button
    explicitly refers to it.
    """
    if not isinstance(options, Mapping):
        raise ValueError("Die gespeicherten Optionen müssen ein Objekt sein")
    values = options.get("parameters", options)
    if not isinstance(values, Mapping):
        raise ValueError("Die gespeicherten Parameter müssen ein Objekt sein")
    button_program = options.get("button_program", "current")
    migrated = list(DEFAULT_PROGRAMS)
    for legacy_id, legacy_name, defaults in _LEGACY_PROGRAMS:
        keys = tuple(f"{legacy_id}_{part}" for part in ("start_c", "end_c", "gangs"))
        legacy_values = tuple(
            values.get(key, default) for key, default in zip(keys, defaults)
        )
        changed = legacy_values != defaults
        referenced = button_program == legacy_id
        if changed or referenced:
            migrated.append(
                NamedTemperatureProgram(
                    legacy_id,
                    legacy_name,
                    legacy_values[0],
                    legacy_values[1],
                    legacy_values[2],
                )
            )
    return validate_programs(
        migrated,
        minimum_c=minimum_c,
        maximum_c=maximum_c,
        maximum_gangs=maximum_gangs,
    )


_LEGACY_PROGRAMS = (
    ("program_1", "Bisheriges Programm 1", (80, 95, 4)),
    ("program_2", "Bisheriges Programm 2", (70, 90, 3)),
)
