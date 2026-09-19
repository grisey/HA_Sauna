"""Ein Parameterkatalog für Eingabeprüfung, Oberfläche und spätere Verbraucher."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType

from .detection_parameters import SPECS
from .parameter_text import PARAMETER_TEXT


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
    allow_zero: bool = False
    optional: bool = False
    maximum: float = 1000000
    default: float | None = None
    minimum: float | None = None
    integer: bool = False
    description: str = ""
    group: str = ""

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
            raise ParameterError(self.key, "non_negative" if self.allow_zero else "positive")
        if self.integer and not number.is_integer():
            raise ParameterError(self.key, "integer_required")
        if number > self.maximum:
            raise ParameterError(self.key, "too_large")
        return int(number) if self.integer else number


def definition(key, unit, allow_zero=False, **kwargs):
    label, description, group = PARAMETER_TEXT[key]
    return ParameterDefinition(key, label, unit, allow_zero=allow_zero,
        description=description, group=group, **kwargs)


# Einstellbare Standardwerte; bereits gespeicherte Werte haben Vorrang.
LIVE_TEMPERATURE_KEYS = frozenset({"target_temperature_c", "temperature_increase_c", "final_temperature_c"})
DEFINITIONS = (
    definition("session_gap_minutes", "min", default=15),
    definition("confirmation_minutes", "min", default=12),
    definition("heating_minutes", "min", default=90),
    definition("heating_reduction_minutes", "min", True, default=30),
    definition("heat_reset_minutes", "min", default=10),
    definition("thermostat_cooldown_minutes", "min", True, default=5),
    definition("minimum_heating_minutes", "min", True, default=10),
    definition("mechanical_timer_minutes", "min", default=240),
    definition("mechanical_timer_warning_minutes", "min", optional=True),
    definition("forced_cooling_minutes", "min", default=15),
    definition("person_wait_minutes", "min", default=4),
    definition("open_door_wait_minutes", "min", default=10),
    definition("after_run_minutes", "min", default=8),
    definition("readiness_offset_c", "°C", True, default=5),
    definition("readiness_hysteresis_c", "°C", default=3),
    definition("preset_start_c", "°C", default=70),
    definition("preset_step_c", "°C", default=5),
    definition("preset_count", "Anzahl", default=6, minimum=1, maximum=20, integer=True),
    definition("target_temperature_c", "°C", default=80),
    definition("temperature_increase_c", "°C", default=5),
    definition("final_temperature_c", "°C", optional=True),
    definition("safety_temperature_c", "°C", default=105),
    definition("overtemperature_minutes", "min", default=10),
    definition("overtemperature_cooling_factor", "×", default=2, minimum=1),
    definition("fault_confirmation_seconds", "s", default=60),
    definition("sensor_timeout_seconds", "s", default=180),
    definition("feedback_timeout_seconds", "s", default=10),
    definition("power_heating_threshold_w", "W", True, default=50),
    definition("nominal_power_kw", "kW", default=4.5),
    definition("operation_brightness_percent", "%", default=35, maximum=100),
    definition("after_run_brightness_percent", "%", default=15, maximum=100),
    definition("cooling_brightness_percent", "%", default=5, maximum=100),
    definition("session_light_minutes", "min", True, default=10),
    definition("session_light_brightness_percent", "%", True, default=50, maximum=100),
) + tuple(definition(key, unit, allow_zero=minimum <= 0,
        default=default, minimum=minimum, maximum=maximum, integer=integer)
    for key, label, unit, default, minimum, maximum, integer in SPECS)
BY_KEY = MappingProxyType({definition.key: definition for definition in DEFINITIONS})


@dataclass(frozen=True)
class Parameters:
    """Validierter, unveränderlicher Stand; gespeicherte Werte sind keine Defaults."""

    values: Mapping[str, float]

    def __post_init__(self) -> None:
        if not isinstance(self.values, Mapping):
            raise ParameterError("base", "invalid_parameters")
        unknown = set(self.values) - BY_KEY.keys()
        if unknown:
            raise ParameterError("base", "unknown_parameter")
        checked = {}
        for definition in DEFINITIONS:
            if definition.key not in self.values:
                if definition.default is not None:
                    checked[definition.key] = definition.validate(definition.default)
                    continue
                if definition.optional:
                    continue
                raise ParameterError(definition.key, "required")
            checked[definition.key] = definition.validate(self.values[definition.key])
        if checked["heating_reduction_minutes"] >= checked["heating_minutes"]:
            raise ParameterError("heating_reduction_minutes", "reduction_too_large")
        if ("final_temperature_c" in checked and "target_temperature_c" in checked
                and checked["final_temperature_c"] < checked["target_temperature_c"]):
            raise ParameterError("final_temperature_c", "below_start_temperature")
        for route in ("strong", "weak"):
            if checked[f"{route}_window_seconds"] % checked["person_step_seconds"]:
                raise ParameterError(f"{route}_window_seconds", "window_not_divisible")
        object.__setattr__(self, "values", MappingProxyType(checked))

    def seconds(self, key: str) -> float:
        """Einheitenumrechnung, keine zusätzliche frei gewählte Zeitbeziehung."""
        if key not in BY_KEY or BY_KEY[key].unit != "min":
            raise ParameterError(key, "not_duration")
        return self.values[key] * 60

    def as_dict(self) -> dict[str, float]:
        return dict(self.values)
