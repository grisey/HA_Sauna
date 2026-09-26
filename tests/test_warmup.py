"""Die ETA ist nur bei aktuellen, stabilen, aufsteigenden Oberwerten erlaubt."""
from datetime import datetime, timedelta, timezone
import unittest

from custom_components.ha_sauna.core.warmup import (
    WarmupEstimate,
    WarmupTrend,
    historical_warmup_rate,
)


T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def at(seconds):
    return T0 + timedelta(seconds=seconds)


class WarmupTrendTests(unittest.TestCase):
    def test_linear_trend_with_irregular_reports_has_a_manually_known_eta(self):
        trend = WarmupTrend(300)
        for seconds, temperature in ((0, 20), (70, 27), (210, 41)):
            trend.accept(at(seconds), temperature)

        rate = trend.rate(at(210), maximum_age_seconds=180)

        self.assertAlmostEqual(rate, 0.1)
        self.assertIsNone(
            historical_warmup_rate(
                [(at(0), 20), (at(60), 26), (at(120), 32), (at(180), 38)],
                minimum_observation_seconds=300,
            )
        )
        # 100 °C - 41 °C at 0.1 °C/s: independently calculated 590 s.
        self.assertEqual((100 - 41) / rate, 590)

    def test_old_or_future_reports_cannot_supply_a_rate(self):
        trend = WarmupTrend(300)
        for seconds, temperature in ((0, 20), (80, 28), (180, 38)):
            trend.accept(at(seconds), temperature)
        # All three points are still inside the five-minute window, but the
        # last report is older than its sensor-validity period.
        self.assertIsNone(trend.rate(at(250), maximum_age_seconds=60))

        future = WarmupTrend(300)
        for seconds, temperature in ((240, 20), (300, 26), (360, 32)):
            future.accept(at(seconds), temperature)
        self.assertIsNone(future.rate(at(210), maximum_age_seconds=180))

    def test_door_drop_or_unstable_values_leave_the_eta_unknown(self):
        trend = WarmupTrend(300)
        for seconds, temperature in ((0, 20), (90, 29), (180, 38), (240, 30)):
            trend.accept(at(seconds), temperature)

        self.assertIsNone(trend.rate(at(240), maximum_age_seconds=180))

    def test_cooling_values_leave_the_eta_unknown(self):
        trend = WarmupTrend(300)
        for seconds, temperature in ((0, 30), (90, 29), (180, 28), (270, 27)):
            trend.accept(at(seconds), temperature)

        self.assertIsNone(trend.rate(at(270), maximum_age_seconds=180))

    def test_recent_cooling_after_a_longer_rise_leaves_eta_unknown(self):
        trend = WarmupTrend(300)
        for seconds, temperature in ((0, 20), (60, 22), (120, 24), (180, 26),
                                     (240, 28), (250, 27.8), (260, 27.6),
                                     (270, 27.4), (280, 27.2), (290, 27), (300, 26.8)):
            trend.accept(at(seconds), temperature)

        self.assertIsNone(trend.rate(at(300), maximum_age_seconds=180))

    def test_quantized_rise_with_a_small_wobble_has_a_rate(self):
        trend = WarmupTrend(300)
        for seconds, temperature in ((0, 20), (60, 20), (120, 21), (180, 21),
                                     (240, 20.8), (300, 22)):
            trend.accept(at(seconds), temperature)

        self.assertIsNotNone(trend.rate(at(300), maximum_age_seconds=180))

    def test_plateau_remains_unknown_even_when_one_value_is_higher(self):
        trend = WarmupTrend(300)
        for seconds, temperature in ((0, 20), (60, 20.1), (120, 20), (180, 20.1),
                                     (240, 20), (300, 20.1)):
            trend.accept(at(seconds), temperature)

        self.assertIsNone(trend.rate(at(300), maximum_age_seconds=180))

    def test_restart_needs_a_new_half_window_after_the_pause(self):
        trend = WarmupTrend(300)
        for seconds, temperature in ((0, 20), (90, 29), (180, 38)):
            trend.accept(at(seconds), temperature)
        self.assertIsNotNone(trend.rate(at(180), maximum_age_seconds=180))

        trend.reset()
        for seconds, temperature in ((200, 40), (260, 46), (320, 52)):
            trend.accept(at(seconds), temperature)
        self.assertIsNone(trend.rate(at(320), maximum_age_seconds=180))
        trend.accept(at(380), 58)
        self.assertIsNotNone(trend.rate(at(380), maximum_age_seconds=180))

    def test_temperature_before_looks_up_the_latest_stored_value(self):
        trend = WarmupTrend(300)
        trend.accept(at(10), 20)
        trend.accept(at(20), 22)

        self.assertEqual(trend.temperature_before(at(15)), 20)
        # A delayed door event is allowed to fall after the latest packet.
        self.assertEqual(trend.temperature_before(at(30)), 22)
        self.assertIsNone(trend.temperature_before(at(5)))
        self.assertIsNone(trend.temperature_before("not a timestamp"))

    def test_completed_initial_warmup_supplies_a_stable_historical_rate(self):
        rate = historical_warmup_rate(
            [(at(0), 20), (at(60), 26), (at(120), 32), (at(180), 38)],
            minimum_observation_seconds=180,
        )

        self.assertAlmostEqual(rate, 0.1)

    def test_incomplete_or_cooling_history_remains_unknown(self):
        self.assertIsNone(
            historical_warmup_rate(
                [(at(0), 20), (at(60), 26)], minimum_observation_seconds=180
            )
        )
        self.assertIsNone(
            historical_warmup_rate(
                [(at(0), 30), (at(60), 29), (at(120), 28)],
                minimum_observation_seconds=180,
            )
        )


class WarmupEstimateTests(unittest.TestCase):
    def _accept(self, estimate, seconds, temperature, *, target=100, history=0.1,
                door_loss=None):
        estimate.accept(
            at(seconds), temperature, target_c=target, historical_rate=history,
            door_loss_c=door_loss,
        )

    def test_history_yields_to_live_rate_from_zero_share_over_one_window(self):
        estimate = WarmupEstimate(300)
        for seconds, temperature in ((0, 20), (60, 23), (120, 26), (180, 29)):
            self._accept(estimate, seconds, temperature)
        # The first live fit has zero share and cannot raise the display.
        self.assertLess(
            estimate.remaining_seconds(at(180)), (100 - 29) / 0.1
        )
        for seconds, temperature in ((240, 32), (300, 35), (360, 38),
                                     (420, 41), (480, 44)):
            self._accept(estimate, seconds, temperature)
        # A sustained 0.05 °C/s run has not jumped the display upward yet.
        before = estimate.remaining_seconds(at(480))
        self._accept(estimate, 540, 47)
        self.assertGreater(estimate.remaining_seconds(at(540)), before)
        self.assertLess(estimate.remaining_seconds(at(540)), (100 - 47) / 0.05)

    def test_short_fit_gap_does_not_restart_the_blend_or_raise_eta(self):
        estimate = WarmupEstimate(300)
        for seconds, temperature in ((0, 20), (60, 26), (120, 32), (180, 38),
                                     (240, 44)):
            self._accept(estimate, seconds, temperature)
        before = estimate.remaining_seconds(at(240))
        # A noisy packet can make the regression unavailable for this update.
        self._accept(estimate, 300, 43.9)
        self.assertLessEqual(estimate.remaining_seconds(at(300)), before)
        self._accept(estimate, 360, 56)
        self.assertLess(estimate.remaining_seconds(at(360)), before)

    def test_fast_25_15_25_jitter_is_smoothed_and_never_raises_without_cause(self):
        estimate = WarmupEstimate(300)
        for seconds, temperature in ((0, 20), (60, 25), (120, 30), (180, 35),
                                     (240, 40)):
            self._accept(estimate, seconds, temperature)
        first = estimate.remaining_seconds(at(240))
        self._accept(estimate, 300, 45)
        down = estimate.remaining_seconds(at(300))
        self._accept(estimate, 360, 44.8)
        self.assertLess(down, first)
        self.assertLessEqual(estimate.remaining_seconds(at(360)), down)

    def test_recognized_door_loss_permits_a_smoothed_upward_correction(self):
        estimate = WarmupEstimate(300)
        for seconds, temperature in ((0, 20), (60, 26), (120, 32), (180, 38),
                                     (240, 44)):
            self._accept(estimate, seconds, temperature)
        before = estimate.remaining_seconds(at(240))
        self._accept(estimate, 300, 37, door_loss=7)
        after = estimate.remaining_seconds(at(300))
        self.assertGreater(after, before)
        self.assertLess(after - before, 200)

    def test_invalid_stale_and_reset_estimates_are_not_positive(self):
        estimate = WarmupEstimate(300)
        self._accept(estimate, 0, 20)
        self.assertIsNotNone(estimate.remaining_seconds(at(0), maximum_age_seconds=60))
        self.assertIsNone(estimate.remaining_seconds(at(61), maximum_age_seconds=60))
        estimate.accept(at(120), float("nan"), target_c=100, historical_rate=0.1)
        self.assertIsNone(estimate.remaining_seconds(at(120)))
        estimate.reset()
        self.assertIsNone(estimate.remaining_seconds(at(120)))

    def test_exact_linear_warmup_keeps_an_exact_countdown_near_target(self):
        estimate = WarmupEstimate(300)
        # 1 °C/min from 60 to 80 with reports every 30 s.  A five-minute
        # smoother must not leave a five-minute ETA lag at the end.
        for seconds in range(0, 1141, 30):
            self._accept(estimate, seconds, 60 + seconds / 60, target=80,
                         history=1 / 60)
        self.assertAlmostEqual(estimate.remaining_seconds(at(1140)), 60, delta=2)
        self._accept(estimate, 1170, 79.5, target=80, history=1 / 60)
        self.assertAlmostEqual(estimate.remaining_seconds(at(1170)), 30, delta=2)

    def test_slower_live_rate_can_reduce_the_descent_before_eta_is_raised(self):
        estimate = WarmupEstimate(300)
        # History initially promises 2 °C/min; the stable live trend is
        # 1 °C/min.  Before its upward correction is admitted, reconciliation
        # must still stop the old forced 1-s/s countdown.
        for seconds in range(0, 1141, 30):
            self._accept(
                estimate, seconds, 60 + seconds / 60, target=80, history=2 / 60
            )
            if seconds == 300:
                # With the observed slower rise, the initial 600-s estimate
                # must not simply have lost 300 s of wall-clock time.
                self.assertGreater(estimate.remaining_seconds(at(seconds)), 600 - seconds)
        # At 79 °C the analytical ETA is 60 s.  The gated display may remain
        # optimistic, but must remain positive until the target is reached.
        remaining = estimate.remaining_seconds(at(1140))
        self.assertGreater(remaining, 0)
        self.assertLess(remaining, 60)

    def test_plateau_or_cooling_expires_a_cached_live_fit_without_zero(self):
        estimate = WarmupEstimate(300)
        for seconds in range(0, 601, 60):
            self._accept(estimate, seconds, 20 + seconds / 10)
        for seconds in range(660, 1141, 60):
            self._accept(estimate, seconds, 80)
        self.assertGreater(estimate.remaining_seconds(at(1140)), 0)
        self._accept(estimate, 1200, 80)
        self.assertIsNone(estimate.remaining_seconds(at(1200)))

    def test_history_only_plateau_expires_after_one_window(self):
        estimate = WarmupEstimate(300)
        for seconds in range(0, 361, 60):
            self._accept(estimate, seconds, 20)
        self.assertIsNone(estimate.remaining_seconds(at(360)))
