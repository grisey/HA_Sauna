"""Vorsichtige, reine Anzeige-Schätzung für das Aufheizen."""
from __future__ import annotations

from datetime import datetime
from math import isfinite, sqrt


class WarmupTrend:
    """Sammelt kausal empfangene obere Temperaturen für eine ETA-Anzeige.

    Die Klasse kennt weder Ofenleistung noch Regelung. Sie liefert nur bei
    ausreichend langen, zeitlich verschiedenen und stabil positiv gerichteten
    Werten eine lineare Trendrate. Quantisierung und kleine Messschwankungen
    dürfen den Trend nicht verdecken; eine Plateau- oder Abkühlungsbewegung
    dagegen schon.
    """

    def __init__(self, window_seconds: float) -> None:
        if not isinstance(window_seconds, (int, float)) or not isfinite(window_seconds) or window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.window_seconds = float(window_seconds)
        self._points: list[tuple[datetime, float]] = []

    def reset(self) -> None:
        self._points.clear()

    def accept(self, received_at: datetime, temperature_c: float) -> None:
        """Accept one newly received value; delayed/out-of-order data stay out."""
        if (not isinstance(received_at, datetime) or isinstance(temperature_c, bool)
                or not isinstance(temperature_c, (int, float)) or not isfinite(temperature_c)):
            return
        if self._points and received_at <= self._points[-1][0]:
            return
        self._points.append((received_at, float(temperature_c)))
        cutoff = received_at.timestamp() - self.window_seconds
        self._points = [(at, value) for at, value in self._points if at.timestamp() >= cutoff]

    def rate(self, now: datetime, *, maximum_age_seconds: float | None = None) -> float | None:
        """Return a positive °C/s trend only for current, stable observations."""
        if not isinstance(now, datetime):
            return None
        if maximum_age_seconds is not None and (not isinstance(maximum_age_seconds, (int, float))
                                                or not isfinite(maximum_age_seconds) or maximum_age_seconds < 0):
            return None
        start = now.timestamp() - self.window_seconds
        points = [(at, value) for at, value in self._points
                  if start <= at.timestamp() <= now.timestamp()]
        if len(points) < 3:
            return None
        first, last = points[0], points[-1]
        age = (now - last[0]).total_seconds()
        if age < 0 or (maximum_age_seconds is not None and age > maximum_age_seconds):
            return None
        span = (last[0] - first[0]).total_seconds()
        if span < self.window_seconds / 2:
            return None
        times = [(at - first[0]).total_seconds() for at, _ in points]
        mean_time = sum(times) / len(times)
        mean_temperature = sum(value for _, value in points) / len(points)
        denominator = sum((time - mean_time) ** 2 for time in times)
        if denominator <= 0:
            return None
        slope = sum((time - mean_time) * (value - mean_temperature)
                    for time, (_, value) in zip(times, points)) / denominator
        if not isfinite(slope) or slope <= 0 or last[1] <= first[1]:
            return None

        fitted = [mean_temperature + slope * (time - mean_time) for time in times]
        residual_rms = sqrt(sum((value - estimate) ** 2
                                for (_, value), estimate in zip(points, fitted)) / len(points))
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
        latest_peak = max(index for index, (_, value) in enumerate(points) if value == maximum)
        recent = points[latest_peak:]
        if len(recent) >= 3:
            recent_times = [(at - recent[0][0]).total_seconds() for at, _ in recent]
            recent_mean_time = sum(recent_times) / len(recent_times)
            recent_mean_temperature = sum(value for _, value in recent) / len(recent)
            recent_denominator = sum((time - recent_mean_time) ** 2 for time in recent_times)
            recent_slope = (sum((time - recent_mean_time) * (value - recent_mean_temperature)
                                for time, (_, value) in zip(recent_times, recent)) / recent_denominator
                            if recent_denominator > 0 else 0)
            if recent_slope <= 0:
                return None
        # A local fall that consumes at least half of the fitted rise is an
        # interruption of the warm-up (for example an open door), even if
        # older values would still make the full-window regression positive.
        largest_drop = max((left[1] - right[1]
                            for left, right in zip(points, points[1:])), default=0.0)
        if 2 * largest_drop > trend_rise:
            return None
        return slope
