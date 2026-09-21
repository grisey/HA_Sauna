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

    def temperature_before(self, at: datetime) -> float | None:
        """Return the latest stored upper temperature at or before ``at``.

        Door recognition can be delayed relative to its effective timestamp.
        This is deliberately a lookup in the accepted observations, rather
        than an age check against the newest packet.
        """
        if not isinstance(at, datetime):
            return None
        for received_at, temperature_c in reversed(self._points):
            if received_at <= at:
                return temperature_c
        return None


class WarmupEstimate:
    """Eine geglättete, rein anzeigende Restzeit aus Historie und Live-Trend.

    ``accept`` ist bewusst der einzige schreibende Einstiegspunkt.  Ein
    wiederholtes Abfragen der Anzeige kann damit weder den Mischanteil noch
    eine Korrektur der Restzeit beeinflussen.
    """

    def __init__(self, window_seconds: float) -> None:
        if (
            not isinstance(window_seconds, (int, float))
            or isinstance(window_seconds, bool)
            or not isfinite(window_seconds)
            or window_seconds <= 0
        ):
            raise ValueError("window_seconds must be positive")
        self.window_seconds = float(window_seconds)
        self.trend = WarmupTrend(self.window_seconds)
        self.reset()

    def reset(self) -> None:
        """Forget one session's display estimate and its live observations."""
        self.trend.reset()
        self._accepted_at: datetime | None = None
        self._estimate_started_at: datetime | None = None
        self._temperature_c: float | None = None
        self._target_c: float | None = None
        self._remaining_seconds: float | None = None
        self._fit_started_at: datetime | None = None
        self._last_fit_at: datetime | None = None
        self._stable_fit_seconds = 0.0
        self._fit_was_continuous = False
        self._slower_since: datetime | None = None
        self._reference_live_rate: float | None = None
        self._last_live_rate: float | None = None
        self._source_invalid = False

    def accept(
        self,
        received_at: datetime,
        temperature_c: float,
        *,
        target_c: float,
        historical_rate: float | None,
        door_loss_c: float | None = None,
    ) -> None:
        """Advance the ETA once for one newly received upper temperature.

        ``door_loss_c`` is context supplied by the caller after it has already
        recognised a door episode.  It is never inferred from a falling sensor
        value here.
        """
        if (
            not self._valid_number(target_c)
            or not self._valid_rate_or_none(historical_rate)
            or not self._valid_door_loss(door_loss_c)
        ):
            self._invalidate()
            return
        if not isinstance(received_at, datetime) or not self._valid_number(
            temperature_c
        ):
            self._invalidate()
            return
        if self._accepted_at is not None and received_at <= self._accepted_at:
            return

        previous_at = self._accepted_at
        self._accepted_at = received_at
        if self._estimate_started_at is None:
            self._estimate_started_at = received_at
        self._temperature_c = float(temperature_c)
        self._target_c = float(target_c)
        self._source_invalid = False
        self.trend.accept(received_at, float(temperature_c))
        live_rate = self.trend.rate(received_at)
        if live_rate is not None:
            if self._fit_started_at is None:
                # The first useful fit starts with no live share.
                self._fit_started_at = received_at
            elif self._fit_was_continuous and self._last_fit_at is not None:
                self._stable_fit_seconds += (
                    received_at - self._last_fit_at
                ).total_seconds()
            self._last_fit_at = received_at
            self._fit_was_continuous = True
            self._last_live_rate = live_rate
            if self._reference_live_rate is None:
                # The historical rate is the accepted starting reference until
                # live observations have demonstrated a different one.
                self._reference_live_rate = (
                    historical_rate if historical_rate is not None else live_rate
                )
        else:
            # Keep the accepted blend for a brief missing fit, but do not let
            # that gap count as evidence for a stable rate change.
            self._fit_was_continuous = False

        candidate = self._candidate_seconds(live_rate, historical_rate)
        if candidate is None:
            # A brief fit failure must not turn a previously valid, fresh
            # display back into a history-only estimate or reset its gates.
            return
        if self._remaining_seconds is None:
            self._remaining_seconds = candidate
            return
        if candidate == 0:
            # This zero is backed by the current temperature, never by an old
            # rate being counted down through an unusable fit.
            self._remaining_seconds = 0.0
            return

        elapsed = (
            (received_at - previous_at).total_seconds()
            if previous_at is not None
            else 0.0
        )
        weight = min(1.0, max(0.0, elapsed / self.window_seconds))
        # Count the real elapsed time before reconciling predictions.  Thus a
        # constant, accurate heat-up remains an accurate countdown instead of
        # acquiring one full window of display delay.
        baseline = max(0.0, self._remaining_seconds - elapsed)
        reconciled = baseline + weight * (candidate - baseline)
        if door_loss_c is not None and door_loss_c > 0:
            rate = self._effective_rate(live_rate, historical_rate)
            if rate is not None:
                loss_candidate = baseline + door_loss_c / rate
                self._remaining_seconds = max(reconciled, loss_candidate)
                self._slower_since = None
                return

        if live_rate is None:
            # A missing fit is no evidence that the earlier, slower live
            # rate continued.  During its grace window retain that display;
            # history-only startup still reconciles each rising measurement.
            self._slower_since = None
            if self._last_live_rate is not None:
                return
            self._remaining_seconds = min(self._remaining_seconds, reconciled)
            return
        if self._reference_live_rate is None:
            self._remaining_seconds = min(self._remaining_seconds, reconciled)
            return
        if live_rate >= self._reference_live_rate:
            self._reference_live_rate = live_rate
            self._slower_since = None
            self._remaining_seconds = min(self._remaining_seconds, reconciled)
            return
        if self._slower_since is None:
            self._slower_since = received_at
            self._remaining_seconds = min(self._remaining_seconds, reconciled)
            return
        slower_span = (received_at - self._slower_since).total_seconds()
        if slower_span < self.window_seconds:
            # Before a slower rate is established, only suppress a genuine
            # upward correction.  A less steep descent or a standstill is
            # already reflected by ``reconciled`` when it remains below the
            # previous displayed value.
            self._remaining_seconds = min(self._remaining_seconds, reconciled)
            return
        # The lower measured rate has survived a complete live window.  Move
        # towards its ETA only as fresh reports arrive, over that same window.
        self._remaining_seconds = reconciled

    def remaining_seconds(
        self, now: datetime, *, maximum_age_seconds: float | None = None
    ) -> float | None:
        """Read the cached ETA when its last accepted source remains current."""
        if not isinstance(now, datetime) or not self._valid_age(maximum_age_seconds):
            return None
        if (
            self._source_invalid
            or self._accepted_at is None
            or self._remaining_seconds is None
        ):
            return None
        fit_reference_at = self._last_fit_at or self._estimate_started_at
        if (
            self._temperature_c is not None
            and self._target_c is not None
            and self._temperature_c < self._target_c
            and fit_reference_at is not None
            and (self._accepted_at - fit_reference_at).total_seconds()
            > self.window_seconds
        ):
            # The sensor may be fresh while the rate supporting the ETA is
            # not.  Do not keep counting down a historical or cached fit
            # through a sustained plateau/cooling run.
            return None
        age = (now - self._accepted_at).total_seconds()
        if age < 0 or (maximum_age_seconds is not None and age > maximum_age_seconds):
            return None
        return self._remaining_seconds

    def _candidate_seconds(
        self,
        live_rate: float | None,
        historical_rate: float | None,
    ) -> float | None:
        if self._temperature_c is None or self._target_c is None:
            return None
        remaining_c = max(0.0, self._target_c - self._temperature_c)
        if remaining_c == 0:
            return 0.0
        rate = self._effective_rate(live_rate, historical_rate)
        return remaining_c / rate if rate is not None and rate > 0 else None

    def _effective_rate(
        self,
        live_rate: float | None,
        historical_rate: float | None,
    ) -> float | None:
        # A short failed fit retains its last accepted live rate and blend
        # position.  It never silently falls back to history.
        rate_from_live = live_rate if live_rate is not None else self._last_live_rate
        if rate_from_live is None:
            return historical_rate
        if historical_rate is None or self._fit_started_at is None:
            return rate_from_live
        share = min(1.0, max(0.0, self._stable_fit_seconds / self.window_seconds))
        return historical_rate * (1.0 - share) + rate_from_live * share

    def _invalidate(self) -> None:
        # Invalid input hides the display but does not create a new estimate
        # whose first returning packet could bypass the upward correction gate.
        self._source_invalid = True

    @staticmethod
    def _valid_number(value: object) -> bool:
        return (
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and isfinite(value)
        )

    @classmethod
    def _valid_rate_or_none(cls, value: object) -> bool:
        return value is None or (cls._valid_number(value) and value > 0)

    @classmethod
    def _valid_door_loss(cls, value: object) -> bool:
        return value is None or (cls._valid_number(value) and value >= 0)

    @classmethod
    def _valid_age(cls, value: object) -> bool:
        return value is None or (cls._valid_number(value) and value >= 0)
