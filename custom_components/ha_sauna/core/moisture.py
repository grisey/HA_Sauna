"""Psychrometrische Hilfsfunktionen für abgeleitete Feuchtemesswerte."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from math import exp, isfinite, log

from .models import Measurement, Position, Quantity


# ASHRAE Fundamentals (2017), Gleichungen 5 und 6, in der von PsychroLib
# verwendeten SI-Fassung.  PsychroLib dokumentiert dieselben Koeffizienten in
# GetSatVapPres: https://psychrometrics.github.io/psychrolib/_modules/psychrolib.html#GetSatVapPres
TRIPLE_POINT_WATER_C = 0.01
KELVIN_OFFSET_C = 273.15
WATER_VAPOR_GAS_CONSTANT = 461.52  # J/(kg K), für die Umrechnung zu g/m³
PSYCHROLIB_MIN_TEMPERATURE_C = -100.0
PSYCHROLIB_MAX_TEMPERATURE_C = 200.0


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def saturation_vapor_pressure(temperature_c: float) -> float:
    """Sättigungsdampfdruck in Pa nach den ASHRAE-SI-Gleichungen liefern."""
    temperature_c = _finite_number(temperature_c, "temperature_c")
    if not PSYCHROLIB_MIN_TEMPERATURE_C <= temperature_c <= PSYCHROLIB_MAX_TEMPERATURE_C:
        raise ValueError("temperature_c is outside the ASHRAE SI equation range")
    temperature_k = temperature_c + KELVIN_OFFSET_C
    if temperature_c <= TRIPLE_POINT_WATER_C:
        log_pressure = (
            -5.6745359e3 / temperature_k
            + 6.3925247
            - 9.677843e-3 * temperature_k
            + 6.2215701e-7 * temperature_k**2
            + 2.0747825e-9 * temperature_k**3
            - 9.484024e-13 * temperature_k**4
            + 4.1635019 * log(temperature_k)
        )
    else:
        log_pressure = (
            -5.8002206e3 / temperature_k
            + 1.3914993
            - 4.8640239e-2 * temperature_k
            + 4.1764768e-5 * temperature_k**2
            - 1.4452093e-8 * temperature_k**3
            + 6.5459673 * log(temperature_k)
        )
    return exp(log_pressure)


def absolute_humidity(temperature_c: float, relative_humidity_percent: float) -> float:
    """Absoluten Wassergehalt in g/m³ aus Temperatur in °C und relativer Feuchte in % berechnen."""
    relative_humidity_percent = _finite_number(relative_humidity_percent, "relative_humidity_percent")
    if not 0 <= relative_humidity_percent <= 100:
        raise ValueError("relative_humidity_percent must be between 0 and 100")
    temperature_c = _finite_number(temperature_c, "temperature_c")
    pressure = saturation_vapor_pressure(temperature_c) * relative_humidity_percent / 100
    return pressure * 1000 / (WATER_VAPOR_GAS_CONSTANT * (temperature_c + KELVIN_OFFSET_C))


def current_absolute_humidity(measurements: Mapping[str, Measurement], position: Position,
                              now: datetime, timeout_seconds: float | None) -> float | None:
    """Nur ein vollständiges, frisches Quellenpaar als Wassergehalt ausgeben.

    Die Funktion behält keinen vorherigen Wert. Dadurch wird ein abgeleiteter
    Sensor bei fehlender, alter oder zeitlich zukünftiger Quelle sofort
    unavailable statt einen historischen Berechnungswert als aktuell zu zeigen.
    """
    if not isinstance(position, Position):
        raise ValueError("position must be a Position")
    if timeout_seconds is None:
        return None
    try:
        timeout = _finite_number(timeout_seconds, "timeout_seconds")
    except ValueError:
        return None
    if timeout < 0:
        return None
    temperature = measurements.get(f"{position.value}_temperature")
    humidity = measurements.get(f"{position.value}_humidity")
    if not _current(temperature, position, Quantity.TEMPERATURE, now, timeout):
        return None
    if not _current(humidity, position, Quantity.HUMIDITY, now, timeout):
        return None
    try:
        return absolute_humidity(temperature.value, humidity.value)
    except ValueError:
        return None


def _current(measurement: object, position: Position, quantity: Quantity, now: datetime,
             timeout_seconds: float) -> bool:
    if not isinstance(measurement, Measurement):
        return False
    if measurement.position is not position or measurement.quantity is not quantity:
        return False
    if measurement.value is None:
        return False
    try:
        age_seconds = (now - measurement.received_at).total_seconds()
    except TypeError:
        return False
    return 0 <= age_seconds <= timeout_seconds
