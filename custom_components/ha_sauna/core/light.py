"""Reine Zielwerte fuer die temperatur- und tageslichtabhaengige Lichtkurve."""
from __future__ import annotations

from math import isfinite

from .parameters import Parameters


def _number(value: float | None) -> float | None:
    """Gibt endliche Messwerte zurueck; ungueltige Werte gelten als fehlend."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if isfinite(value) else None


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def linear(start: float, target: float, elapsed: float, duration: float) -> float:
    """Interpoliert von ``start`` nach ``target`` ohne zeitlichen Zustand."""
    start, target = float(start), float(target)
    elapsed, duration = _number(elapsed), _number(duration)
    if duration is None or duration <= 0:
        return target
    if elapsed is None or elapsed <= 0:
        return start
    if elapsed >= duration:
        return target
    return start + (target - start) * elapsed / duration


def normal_brightness(elevation: float | None, parameters: Parameters) -> float:
    """Tag/Nacht-Helligkeit, mit linearer buergerlicher Daemmerung von 0 bis -6 Grad."""
    values = parameters.values
    night = values["night_brightness_percent"]
    day = values["operation_brightness_percent"]
    elevation = _number(elevation)
    if elevation is None:
        return night
    return linear(night, day, _clamp((elevation + 6) / 6, 0, 1), 1)


def temperature_brightness(temperature: float | None, readiness_target: float | None,
                           parameters: Parameters, normal_brightness: float) -> float:
    """Temperaturkurve vom Kaltpunkt bis zum Bereitschaftsziel."""
    values = parameters.values
    minimum = values["cooling_brightness_percent"]
    temperature, readiness_target = _number(temperature), _number(readiness_target)
    if temperature is None:
        return minimum
    if readiness_target is None:
        return minimum
    cold = values["light_reference_temperature_c"]
    if readiness_target <= cold:
        return normal_brightness if temperature >= readiness_target else minimum
    fraction = _clamp((temperature - cold) / (readiness_target - cold), 0, 1)
    return linear(minimum, normal_brightness, fraction, 1)


def phase_target(phase: str, temperature: float | None, readiness_target: float | None,
                 parameters: Parameters, normal_brightness: float) -> float:
    """Leitet nur aus der fuehrenden Phase den Zielwert ab, ohne eigenen Zustand."""
    if phase == "saunagang":
        return normal_brightness
    temperature, readiness_target = _number(temperature), _number(readiness_target)
    hysteresis = parameters.values["readiness_hysteresis_c"]
    if (phase == "bereit" and temperature is not None
            and readiness_target is not None and temperature >= readiness_target - hysteresis):
        return normal_brightness
    return temperature_brightness(temperature, readiness_target, parameters, normal_brightness)
