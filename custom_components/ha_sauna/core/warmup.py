"""Vorsichtige, reine Anzeige-Schätzung für das Aufheizen."""

from __future__ import annotations

from datetime import datetime
from math import isfinite, sqrt


def historical_warmup_rate(
    points: object, *, minimum_observation_seconds: float
) -> float | None:
    """Return a robust average rise from one completed initial warm-up.

    Archive data is intentionally held to the same standard as live data: a
    short, flat, falling, or malformed series does not turn into an ETA.  The
    completed phase has no sensor-age requirement, but it must still span a
    useful interval and rise consistently.
    """
    if (
        not isinstance(points, (list, tuple))
        or isinstance(minimum_observation_seconds, bool)
        or not isinstance(minimum_observation_seconds, (int, float))
        or not isfinite(minimum_observation_seconds)
        or minimum_observation_seconds <= 0
    ):
        return None
    valid: list[tuple[datetime, float]] = []
    for point in points:
        if not isinstance(point, tuple) or len(point) != 2:
            return None
        received_at, temperature_c = point
        if (
            not isinstance(received_at, datetime)
            or isinstance(temperature_c, bool)
            or not isinstance(temperature_c, (int, float))
            or not isfinite(temperature_c)
        ):
            return None
        if valid and received_at <= valid[-1][0]:
            return None
        valid.append((received_at, float(temperature_c)))
    return _stable_rate(valid, float(minimum_observation_seconds))


def _stable_rate(
    points: list[tuple[datetime, float]], minimum_span_seconds: float
) -> float | None:
    """Calculate one stable rate in linear time from already valid points."""
    if len(points) < 3:
        return None
    first, last = points[0], points[-1]
    span = (last[0] - first[0]).total_seconds()
    if not isfinite(span) or span < minimum_span_seconds:
        return None
    times = [(at - first[0]).total_seconds() for at, _ in points]
    mean_time = sum(times) / len(times)
    mean_temperature = sum(value for _, value in points) / len(points)
    denominator = sum((time - mean_time) ** 2 for time in times)
    if denominator <= 0:
        return None
    slope = (
        sum(
            (time - mean_time) * (value - mean_temperature)
            for time, (_, value) in zip(times, points)
        )
        / denominator
    )
    if not isfinite(slope) or slope <= 0 or last[1] <= first[1]:
        return None

    fitted = [mean_temperature + slope * (time - mean_time) for time in times]
    residual_rms = sqrt(
        sum((value - estimate) ** 2 for (_, value), estimate in zip(points, fitted))
        / len(points)
    )
    trend_rise = slope * span
    # A useful ETA needs a rise that is stronger than the wobble around
    # its fitted line. This leaves a quantized, gradually increasing series
    # intact while a plateau remains indistinguishable from noise.
    if trend_rise <= 2 * residual_rms:
        return None
    # Do not let an older rise mask a current cooling run. Once the latest
    # maximum has been followed by enough reports to form a trend, that
    # newest section must still rise on its own.
    maximum = max(value for _, value in points)
    latest_peak = max(
        index for index, (_, value) in enumerate(points) if value == maximum
    )
    recent = points[latest_peak:]
    if len(recent) >= 3:
        recent_times = [(at - recent[0][0]).total_seconds() for at, _ in recent]
        recent_mean_time = sum(recent_times) / len(recent_times)
        recent_mean_temperature = sum(value for _, value in recent) / len(recent)
        recent_denominator = sum(
            (time - recent_mean_time) ** 2 for time in recent_times
        )
        recent_slope = (
            sum(
                (time - recent_mean_time) * (value - recent_mean_temperature)
                for time, (_, value) in zip(recent_times, recent)
            )
            / recent_denominator
            if recent_denominator > 0
            else 0
        )
        if recent_slope <= 0:
            return None
    # A local fall that consumes at least half of the fitted rise is an
    # interruption of the warm-up (for example an open door), even if
    # older values would still make the full-window regression positive.
    largest_drop = max(
        (left[1] - right[1] for left, right in zip(points, points[1:])), default=0.0
    )
    if 2 * largest_drop > trend_rise:
        return None
    return slope


class WarmupTrend:
    """Sammelt kausal empfangene obere Temperaturen für eine ETA-Anzeige.

    Die Klasse kennt weder Ofenleistung noch Regelung. Sie liefert nur bei
    ausreichend langen, zeitlich verschiedenen und stabil positiv gerichteten
    Werten eine lineare Trendrate. Quantisierung und kleine Messschwankungen
    dürfen den Trend nicht verdecken; eine Plateau- oder Abkühlungsbewegung
    dagegen schon.
    """

    def __init__(self, window_seconds: float) -> None:
        if (
            not isinstance(window_seconds, (int, float))
            or not isfinite(window_seconds)
            or window_seconds <= 0
        ):
            raise ValueError("window_seconds must be positive")
        self.window_seconds = float(window_seconds)
        self._points: list[tuple[datetime, float]] = []
        self._accepted_rate: float | None = None
        self._accepted_at: datetime | None = None

    def reset(self) -> None:
        self._points.clear()
        self._accepted_rate = None
        self._accepted_at = None

    def accept(self, received_at: datetime, temperature_c: float) -> None:
        """Accept one newly received value; delayed/out-of-order data stay out."""
        if (
            not isinstance(received_at, datetime)
            or isinstance(temperature_c, bool)
            or not isinstance(temperature_c, (int, float))
            or not isfinite(temperature_c)
        ):
            return
        if self._points and received_at <= self._points[-1][0]:
            return
        self._points.append((received_at, float(temperature_c)))
        cutoff = received_at.timestamp() - self.window_seconds
        first = 0
        while first < len(self._points) and self._points[first][0].timestamp() < cutoff:
            first += 1
        if first:
            del self._points[:first]
        # The display samples this cached, accepted fit. It changes only when
        # a genuinely new upper measurement arrives, never on UI refreshes.
        self._accepted_rate = _stable_rate(self._points, self.window_seconds / 2)
        self._accepted_at = received_at

    def rate(
        self, now: datetime, *, maximum_age_seconds: float | None = None
    ) -> float | None:
        """Return a positive °C/s trend only for current, stable observations."""
        if not isinstance(now, datetime):
            return None
        if maximum_age_seconds is not None and (
            not isinstance(maximum_age_seconds, (int, float))
            or not isfinite(maximum_age_seconds)
            or maximum_age_seconds < 0
        ):
            return None
        if self._accepted_rate is None or self._accepted_at is None:
            return None
        age = (now - self._accepted_at).total_seconds()
        if age < 0 or (maximum_age_seconds is not None and age > maximum_age_seconds):
            return None
        return self._accepted_rate
