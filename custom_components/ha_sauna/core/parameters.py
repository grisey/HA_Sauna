"""Ein Parameterkatalog für Eingabeprüfung, Oberfläche und spätere Verbraucher."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType


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

    def validate(self, value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ParameterError(self.key, "invalid_number")
        try:
            number = float(value)
        except (ValueError, OverflowError):
            raise ParameterError(self.key, "invalid_number") from None
        if not isfinite(number) or (self.unit == "min" and not isfinite(number * 60)):
            raise ParameterError(self.key, "invalid_number")
        if number < 0 or (number == 0 and not self.allow_zero):
            raise ParameterError(self.key, "non_negative" if self.allow_zero else "positive")
        if number > self.maximum:
            raise ParameterError(self.key, "too_large")
        return number


# Keine unvereinbarten Ausgangswerte. Der Nutzer setzt die Werte bei Einrichtung.
DEFINITIONS = (
    ParameterDefinition("session_gap_minutes", "Session-Unterbrechungsfrist", "min"),
    ParameterDefinition("confirmation_minutes", "Aufgussbestätigungsfrist", "min"),
    ParameterDefinition("heating_minutes", "Heizzeit vor erster Kühlung", "min", default=90),
    ParameterDefinition("heating_reduction_minutes", "Einmalige Heizzeitverkürzung", "min", True, default=30),
    ParameterDefinition("heat_reset_minutes", "Heizzeit-Rücksetz-Auszeit", "min"),
    ParameterDefinition("thermostat_cooldown_minutes", "Thermostat-Cooldown", "min", True),
    ParameterDefinition("forced_cooling_minutes", "Zwangskühlungsdauer", "min", default=15),
    ParameterDefinition("after_run_minutes", "Nachlaufdauer", "min"),
    ParameterDefinition("readiness_offset_c", "Bereitschaftsaufschlag", "°C", True, default=5),
    ParameterDefinition("readiness_hysteresis_c", "Bereitschaftshysterese", "°C", default=3),
    # Unbestimmte Schutz-/Betriebswerte bleiben leer. Leer bedeutet Heizsperre,
    # nicht ein vom Code gewählter Ersatzwert oder eine sichere Werkseinstellung.
    ParameterDefinition("target_temperature_c", "Solltemperatur oben", "°C", optional=True),
    ParameterDefinition("safety_temperature_c", "Abschalttemperatur oben", "°C", optional=True),
    ParameterDefinition("sensor_timeout_seconds", "Messwert-Gültigkeitsdauer", "s", optional=True),
    ParameterDefinition("feedback_timeout_seconds", "Rückmeldungsfrist", "s", optional=True),
    ParameterDefinition("cooling_brightness_percent", "Licht bei Zwangskühlung", "%", optional=True, maximum=100),
)
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
        object.__setattr__(self, "values", MappingProxyType(checked))

    def seconds(self, key: str) -> float:
        """Einheitenumrechnung, keine zusätzliche frei gewählte Zeitbeziehung."""
        if key not in BY_KEY or BY_KEY[key].unit != "min":
            raise ParameterError(key, "not_duration")
        return self.values[key] * 60

    def as_dict(self) -> dict[str, float]:
        return dict(self.values)
