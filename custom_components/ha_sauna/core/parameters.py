"""Ein Parameterkatalog für Eingabeprüfung, Oberfläche und spätere Verbraucher."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType

from .defaults import section
from .temperature_target import whole_temperature


class ParameterError(ValueError):
    """Fehler mit Feldbezug für die Konfigurationsoberfläche."""

    def __init__(self, key: str, code: str) -> None:
        self.key, self.code = key, code
        super().__init__(f"{key}: {code}")


@dataclass(frozen=True)
class ParameterDefinition:
    key: str
    label: str
    unit: str
    allow_zero: bool
    optional: bool
    maximum: float
    default: float | None
    minimum: float | None
    integer: bool
    description: str
    group: str
    expert: bool
    step: float | str
    number_step: float
    settings_group: str
    settings_subgroup: str | None
    order: int

    def validate(self, value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ParameterError(self.key, "invalid_number")
        try:
            number = float(value)
        except (ValueError, OverflowError):
            raise ParameterError(self.key, "invalid_number") from None
        if not isfinite(number) or (self.unit == "min" and not isfinite(number * 60)):
            raise ParameterError(self.key, "invalid_number")
        if self.minimum is not None:
            if number < self.minimum:
                raise ParameterError(self.key, "too_small")
        elif number < 0 or (number == 0 and not self.allow_zero):
            raise ParameterError(
                self.key, "non_negative" if self.allow_zero else "positive"
            )
        if self.integer and not number.is_integer():
            raise ParameterError(self.key, "integer_required")
        if number > self.maximum:
            raise ParameterError(self.key, "too_large")
        return int(number) if self.integer else number


# Einstellbare Standardwerte; bereits gespeicherte Werte haben Vorrang.
LIVE_TEMPERATURE_KEYS = frozenset(
    {"target_temperature_c", "final_temperature_c", "temperature_gangs"}
)
# Read compatibility only: obsolete values in saved options and archives have
# no defaults, validation constraints, editable fields, or control effect.
LEGACY_PARAMETER_KEYS = frozenset(
    {
        "button_hold_brightness_percent",
        "door_request_minutes",
        "forced_cooling_minutes",
        "heating_minutes",
        "heating_reduction_minutes",
        "mechanical_timer_minutes",
        "mechanical_timer_warning_minutes",
        "open_door_wait_minutes",
        "overtemperature_cooling_factor",
        "overtemperature_minutes",
        "person_wait_minutes",
        "safety_temperature_c",
    }
)
# Current settings and read-compatibility metadata are deliberately separate.
EDITABLE_DEFINITIONS = tuple(
    ParameterDefinition(**spec)
    for spec in sorted(section("parameters"), key=lambda spec: spec["order"])
)
DEFINITIONS = EDITABLE_DEFINITIONS + tuple(
    ParameterDefinition(**spec) for spec in section("legacy_parameters")
)
BY_KEY = MappingProxyType({definition.key: definition for definition in DEFINITIONS})
SAUNA_TEMPERATURE_MAXIMUM_C = BY_KEY["target_temperature_c"].maximum


@dataclass(frozen=True)
class Parameters:
    """Validierter, unveränderlicher Stand; gespeicherte Werte sind keine Defaults."""

    values: Mapping[str, float]

    def __post_init__(self) -> None:
        if not isinstance(self.values, Mapping):
            raise ParameterError("base", "invalid_parameters")
        unknown = set(self.values) - BY_KEY.keys() - LEGACY_PARAMETER_KEYS
        if unknown:
            raise ParameterError("base", "unknown_parameter")
        # Configurations from before the adaptive cooling limit only contain the
        # former fixed duration.  Preserve an explicitly saved value above the
        # new default cap by supplying an equal cap on first load.  The next
        # normal configuration write persists that effective value.
        supplied = self.values
        values = dict(supplied)
        base = values.get("after_run_minutes")
        max_key = "oven_cooling_max_minutes"
        if (
            max_key not in values
            and isinstance(base, (int, float))
            and not isinstance(base, bool)
            and base > BY_KEY[max_key].default
        ):
            values[max_key] = base

        checked = {}
        for definition in DEFINITIONS:
            if definition.key not in values:
                if definition.default is not None:
                    checked[definition.key] = definition.validate(definition.default)
                    continue
                if definition.optional:
                    continue
                raise ParameterError(definition.key, "required")
            checked[definition.key] = definition.validate(values[definition.key])
        if checked["oven_cooling_max_minutes"] < checked["after_run_minutes"]:
            raise ParameterError("oven_cooling_max_minutes", "too_small")

        sauna_minimum = checked["sauna_min_temperature_c"]
        for key in (
            "preset_start_c",
            "target_temperature_c",
            "final_temperature_c",
        ):
            if checked[key] < sauna_minimum:
                raise ParameterError(key, "too_small")
            # Check the original input before rounding: e.g. 59.9 must not
            # enter a 60-degree minimum by rounding into the permitted range.
            checked[key] = whole_temperature(checked[key])
            if checked[key] < sauna_minimum:
                raise ParameterError(key, "too_small")
            BY_KEY[key].validate(checked[key])
        for route in ("strong", "weak"):
            if checked[f"{route}_window_seconds"] % checked["person_step_seconds"]:
                raise ParameterError(f"{route}_window_seconds", "window_not_divisible")
        object.__setattr__(self, "values", MappingProxyType(checked))

    def seconds(self, key: str) -> float:
        """Einheitenumrechnung, keine zusätzliche frei gewählte Zeitbeziehung."""
        if key not in BY_KEY or BY_KEY[key].unit != "min":
            raise ParameterError(key, "not_duration")
        return self.values[key] * 60

    def minimum_for(self, key: str) -> float:
        """Return the effective UI and validation minimum for a parameter."""
        if key not in BY_KEY:
            raise ParameterError(key, "unknown_parameter")
        if key in {
            "preset_start_c",
            "target_temperature_c",
            "final_temperature_c",
        }:
            return self.values["sauna_min_temperature_c"]
        if key == "oven_cooling_max_minutes":
            return self.values["after_run_minutes"]
        definition = BY_KEY[key]
        return definition.minimum if definition.minimum is not None else 0

    def as_dict(self) -> dict[str, float]:
        return dict(self.values)
