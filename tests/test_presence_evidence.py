"""Belegung aus dem Präsenzverlauf: gelernte Sensorzeiten und Beweisverhältnis."""

import math
import unittest
from statistics import NormalDist

from custom_components.ha_sauna.core import presence_evidence as evidence
from custom_components.ha_sauna.core.presence_evidence import (
    PresenceTiming,
    occupancy_log_ratio,
)
from custom_components.ha_sauna.recognition_parameters import shipped_parameters

THRESHOLD = math.log(shipped_parameters()["decision"]["evidence_ratio"])
TIMING = PresenceTiming.from_parameters(shipped_parameters())
CLOSE = 100.0


def entry(states, at):
    return occupancy_log_ratio(TIMING, states, start=CLOSE, at=at, close=CLOSE)


class DistributionTests(unittest.TestCase):
    def test_gamma_tail_matches_closed_forms(self):
        for scale in (0.5, 7.0, 40.0):
            for x in (1.0, 3.0, 30.0, 300.0):
                with self.subTest(scale=scale, x=x):
                    self.assertAlmostEqual(evidence.gamma_logsf(x, 1.0, scale), -x / scale, places=9)
                    self.assertAlmostEqual(
                        evidence.gamma_logsf(x, 2.0, scale),
                        math.log1p(x / scale) - x / scale, places=9,
                    )
                    self.assertAlmostEqual(
                        evidence.gamma_logpdf(x, 1.0, scale), -x / scale - math.log(scale), places=12,
                    )

    def test_lognormal_matches_the_normal_distribution(self):
        normal = NormalDist(4.0, 0.8)
        for x in (2.0, 55.0, 900.0):
            with self.subTest(x=x):
                self.assertAlmostEqual(
                    evidence.lognormal_logsf(x, 4.0, 0.8), math.log(1 - normal.cdf(math.log(x))), places=9,
                )
                self.assertAlmostEqual(
                    evidence.lognormal_logpdf(x, 4.0, 0.8),
                    math.log(normal.pdf(math.log(x)) / x), places=9,
                )

    def test_durations_below_the_report_resolution_count_as_one_second(self):
        self.assertEqual(evidence.lognormal_logsf(0.2, 4.0, 0.8), evidence.lognormal_logsf(1.0, 4.0, 0.8))
        self.assertEqual(evidence.gamma_logpdf(0.0, 1.8, 7.0), evidence.gamma_logpdf(1.0, 1.8, 7.0))


class LearnedSensorTimesTests(unittest.TestCase):
    def test_shipped_times_come_with_their_counts(self):
        block = shipped_parameters()["presence"]
        for key in ("hold", "stay", "dropout", "vacancy"):
            with self.subTest(key=key):
                self.assertEqual(len(block[key]), 2)
                self.assertGreater(block["counts"][key], 0)
        self.assertGreater(TIMING.hold_shape * TIMING.hold_scale, 0)

    def test_presence_within_the_sensor_hold_establishes_no_stay(self):
        # The longest observed hold after an exit was 29 s.
        self.assertLess(entry([(0.0, True)], CLOSE + 29), THRESHOLD)
        self.assertGreater(entry([(0.0, True)], CLOSE + 29), -THRESHOLD)

    def test_presence_outlasting_the_hold_establishes_the_stay(self):
        ratios = [entry([(0.0, True)], CLOSE + seconds) for seconds in range(0, 121, 10)]
        self.assertEqual(ratios, sorted(ratios))
        self.assertGreaterEqual(ratios[-1], THRESHOLD)

    def test_short_overhang_proves_an_empty_cabin(self):
        for overhang in (5.0, 10.0, 20.0):
            with self.subTest(overhang=overhang):
                states = [(0.0, True), (CLOSE + overhang, False)]
                self.assertLess(entry(states, CLOSE + overhang), 0)
                decided = next(seconds for seconds in range(0, 7200, 10)
                               if entry(states, CLOSE + overhang + seconds) <= -THRESHOLD)
                self.assertGreater(decided, 0)

    def test_presence_returning_behind_the_closed_door_supports_a_dropout(self):
        states = [(0.0, True), (CLOSE + 60, False), (CLOSE + 200, True)]
        dropout = entry(states, CLOSE + 300) - entry(states[:2], CLOSE + 200)
        self.assertGreater(dropout, 0)

    def test_presence_returning_through_the_open_door_proves_nothing(self):
        opening, close = 100.0, 160.0
        states = [(0.0, True), (110.0, False), (130.0, True), (150.0, False)]
        with_return = occupancy_log_ratio(TIMING, states, start=opening, at=close - 1)
        only_absence = occupancy_log_ratio(
            TIMING, [(0.0, True), (150.0, False)], start=opening, at=close - 1,
        )
        self.assertAlmostEqual(with_return, only_absence)

    def test_unknown_state_closes_the_section_with_its_observed_length(self):
        interrupted = entry([(0.0, True), (CLOSE + 20, None), (CLOSE + 500, True)], CLOSE + 900)
        observed = entry([(0.0, True)], CLOSE + 20)
        self.assertAlmostEqual(interrupted, observed)

    def test_absence_before_the_opening_continues_as_a_dropout(self):
        opening = 100.0
        states = [(0.0, True), (40.0, False)]
        carried = occupancy_log_ratio(TIMING, states, start=opening, at=opening + 300)
        expected = (evidence.lognormal_logsf(360.0, *TIMING.dropout)
                    - evidence.lognormal_logsf(60.0, *TIMING.dropout)
                    - evidence.lognormal_logsf(300.0, *TIMING.vacancy))
        self.assertAlmostEqual(carried, expected)


class LearningTests(unittest.TestCase):
    def test_lognormal_fit_with_censored_durations(self):
        normal = NormalDist(5.0, 0.7)
        values = [math.exp(normal.inv_cdf((i + 0.5) / 400)) for i in range(400)]
        observed = [v for v in values if v < 400.0]
        censored = [400.0] * (len(values) - len(observed))
        mu, sigma = evidence.fit_lognormal(observed, censored)
        self.assertAlmostEqual(mu, 5.0, delta=0.03)
        self.assertAlmostEqual(sigma, 0.7, delta=0.03)

    def test_hold_fit_separates_persons_still_seen_after_the_exit(self):
        shape, scale = 2.0, 6.0
        # Deterministic gamma(2) quantiles through its closed-form tail.
        hold = []
        for i in range(60):
            target = (i + 0.5) / 60
            low, high = 0.0, 400.0
            for _ in range(80):
                middle = (low + high) / 2
                if 1 - math.exp(evidence.gamma_logsf(middle, shape, scale)) < target:
                    low = middle
                else:
                    high = middle
            hold.append(max(low, 1.0))
        stay = (8.0, 1.0)
        seen = [600.0, 900.0, 1500.0, 2400.0, 4000.0, 7000.0]
        fitted_shape, fitted_scale, share = evidence.fit_hold(hold + seen, [], stay)
        self.assertAlmostEqual(fitted_shape * fitted_scale, shape * scale, delta=1.5)
        self.assertAlmostEqual(share, len(seen) / (len(hold) + len(seen)), delta=0.04)


if __name__ == "__main__":
    unittest.main()
