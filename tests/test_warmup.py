"""Die ETA ist nur bei aktuellen, stabilen, aufsteigenden Oberwerten erlaubt."""
from datetime import datetime, timedelta, timezone
import unittest

from custom_components.ha_sauna.core.warmup import WarmupTrend, historical_warmup_rate


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
