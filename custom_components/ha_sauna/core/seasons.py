"""Zeiträume der Statistik: Saison, Jahreszeit und Sommer nach kalendarischem Beginn.

Die Jahreszeiten beginnen astronomisch: Frühling mit der Tagundnachtgleiche im
März, Sommer mit der Sonnenwende im Juni, Herbst mit der Tagundnachtgleiche im
September, Winter mit der Sonnenwende im Dezember. Die Zeitpunkte rechnet
``solar_term`` nach J. Meeus, Astronomical Algorithms, 2. Aufl., Kapitel 27
(mittlere Zeitpunkte nach Tafel 27.B, periodische Glieder nach Tafel 27.C;
Genauigkeit etwa eine Minute) und bringt sie mit ΔT nach Espenak und Meeus
(NASA, Polynomial Expressions for Delta T) von dynamischer Zeit auf Weltzeit.

Eine Saison umfasst Herbst, Winter und Frühling eines Jahrespaars und heißt
nach ihm („2026/27“); der Sommer dazwischen ist ein eigener Zeitraum
(„Sommer 2027“). Verglichen wird der Zeitpunkt des Sitzungsbeginns mit dem
Zeitpunkt des Jahreszeitbeginns, unabhängig von der Zeitzone.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import cache
from math import cos, radians

# Tafel 27.B: mittlerer Zeitpunkt (JDE) für die Jahre 1000 bis 3000, Y = (Jahr − 2000) / 1000
_MEAN = {
    "spring": (2451623.80984, 365242.37404, 0.05169, -0.00411, -0.00057),
    "summer": (2451716.56767, 365241.62603, 0.00325, 0.00888, -0.00030),
    "autumn": (2451810.21715, 365242.01767, -0.11575, 0.00337, 0.00078),
    "winter": (2451900.05952, 365242.74049, -0.06223, -0.00823, 0.00032),
}
# Tafel 27.C: periodische Glieder A, B (°), C (° je julianischem Jahrhundert)
_PERIODIC = (
    (485, 324.96, 1934.136), (203, 337.23, 32964.467), (199, 342.08, 20.186),
    (182, 27.85, 445267.112), (156, 73.14, 45036.886), (136, 171.52, 22518.443),
    (77, 222.54, 65928.934), (74, 296.72, 3034.906), (70, 243.58, 9037.513),
    (58, 119.81, 33718.147), (52, 297.17, 150.678), (50, 21.02, 2281.226),
    (45, 247.54, 29929.562), (44, 325.15, 31555.956), (29, 60.93, 4443.417),
    (18, 155.12, 67555.328), (17, 288.79, 4562.452), (16, 198.04, 62894.029),
    (14, 199.76, 31436.921), (12, 95.39, 14577.848), (12, 287.11, 31931.756),
    (12, 320.81, 34777.259), (9, 227.73, 1222.114), (8, 15.45, 16859.074),
)
_J2000 = 2451545.0
_J2000_TIME = datetime(2000, 1, 1, 12, tzinfo=UTC)

PARTS = ("autumn", "winter", "spring")
LABELS = {"autumn": "Herbst", "winter": "Winter", "spring": "Frühling", "summer": "Sommer"}


def _delta_t_seconds(year: float) -> float:
    """ΔT = TD − UT nach den Polynomen von Espenak und Meeus."""
    if 1986 <= year < 2005:
        t = year - 2000
        return (63.86 + 0.3345 * t - 0.060374 * t**2 + 0.0017275 * t**3
                + 0.000651814 * t**4 + 0.00002373599 * t**5)
    if 2005 <= year < 2050:
        t = year - 2000
        return 62.92 + 0.32217 * t + 0.005589 * t**2
    if 2050 <= year < 2150:
        return -20 + 32 * ((year - 1820) / 100) ** 2 - 0.5628 * (2150 - year)
    return -20 + 32 * ((year - 1820) / 100) ** 2


@cache
def solar_term(year: int, term: str) -> datetime:
    """Beginn der Jahreszeit ``term`` (spring, summer, autumn, winter) im Jahr, in UTC."""
    if not 1000 <= year <= 3000:
        raise ValueError("Jahreszeiten sind für die Jahre 1000 bis 3000 berechnet")
    y = (year - 2000) / 1000
    a, b, c, d, e = _MEAN[term]
    mean = a + b * y + c * y**2 + d * y**3 + e * y**4
    t = (mean - _J2000) / 36525
    w = radians(35999.373 * t - 2.47)
    spread = 1 + 0.0334 * cos(w) + 0.0007 * cos(2 * w)
    terms = sum(amplitude * cos(radians(phase + speed * t))
                for amplitude, phase, speed in _PERIODIC)
    dynamical = mean + 0.00001 * terms / spread
    return _J2000_TIME + timedelta(days=dynamical - _J2000,
                                   seconds=-_delta_t_seconds(year + 0.5))


def season_label(season: int) -> str:
    return f"{season}/{(season + 1) % 100:02d}"


def period_of(at: datetime) -> dict:
    """Saison und Jahreszeit eines Zeitpunkts.

    Ergebnis: ``{"season": Anfangsjahr, "part": autumn|winter|spring}`` oder
    ``{"summer": Jahr}``.
    """
    at = at.astimezone(UTC)
    year = at.year
    if at >= solar_term(year, "winter"):
        return {"season": year, "part": "winter"}
    if at >= solar_term(year, "autumn"):
        return {"season": year, "part": "autumn"}
    if at >= solar_term(year, "summer"):
        return {"summer": year}
    if at >= solar_term(year, "spring"):
        return {"season": year - 1, "part": "spring"}
    return {"season": year - 1, "part": "winter"}


def period_start(period: dict) -> datetime:
    """Beginn eines Zeitraums (Saison, Jahreszeit oder Sommer), für die Reihenfolge."""
    if "summer" in period:
        return solar_term(period["summer"], "summer")
    part = period.get("part") or "autumn"
    year = period["season"] + (1 if part == "spring" else 0)
    return solar_term(year, part)


def period_label(period: dict) -> str:
    if "summer" in period:
        return f"{LABELS['summer']} {period['summer']}"
    if period.get("part"):
        return LABELS[period["part"]]
    return season_label(period["season"])
