"""Lernen der Erkennungsgrößen: Belege, Schätzung, Ablehnungen und Nachlernen."""

from datetime import UTC, datetime, timedelta
import math
import unittest

import numpy as np

from custom_components.ha_sauna.core import recognition_learning as learning
from custom_components.ha_sauna.core.recognition import Detection
from custom_components.ha_sauna.core.recognition_model import INFUSION, OPEN
from custom_components.ha_sauna.core.timeline import Kind

T0 = datetime(2026, 1, 10, 18, tzinfo=UTC)


def detection(kind, seconds):
    at = T0 + timedelta(seconds=seconds)
    return Detection(kind, at, at + timedelta(seconds=20), ("Tu", "Tl", "Au", "Al"), trace_at=at + timedelta(seconds=20))


class ExchangeTests(unittest.TestCase):
    def test_two_node_course_returns_its_coefficients(self):
        tt = np.arange(0.0, 600.0, 2.0)
        lam, beta, level0, excess0 = 0.004, 0.002, 80.0, 58.0
        course = learning._course(tt, lam, beta, level0, excess0)
        fitted_lam, fitted_beta = learning.fit_exchange(tt, course, excess0)
        self.assertAlmostEqual(fitted_lam, lam, delta=1e-4)
        self.assertAlmostEqual(fitted_beta, beta, delta=1e-4)
        self.assertAlmostEqual(learning.fit_exchange(tt, course, excess0, beta)[0], lam, delta=1e-5)

    def test_humidity_course_returns_exchange_and_moisture_source(self):
        tt = np.arange(0.0, 600.0, 2.0)
        course = 10.0 + learning._source_basis(tt, 0.004) @ np.array([40.0, 0.03])
        lam, source, _, _ = learning.fit_source_exchange(tt, course, 10.0)
        self.assertAlmostEqual(lam, 0.004, delta=1e-7)
        self.assertAlmostEqual(source, 0.03, delta=1e-7)
        noisy = course + np.random.default_rng(1).normal(0, 0.2, len(tt))
        lam, source, error_lam, error_source = learning.fit_source_exchange(tt, noisy, 10.0)
        self.assertLess(abs(math.log(lam / 0.004)), 3 * error_lam)
        self.assertLess(abs(source - 0.03), 3 * error_source)


class PopulationTests(unittest.TestCase):
    def test_without_errors_it_is_mean_and_standard_deviation(self):
        values = [1.0, 2.0, 4.0, 7.0]
        mu, tau = learning.population(values)
        self.assertAlmostEqual(mu, float(np.mean(values)))
        self.assertAlmostEqual(tau, float(np.std(values)))

    def test_poorly_determined_events_count_little(self):
        rng = np.random.default_rng(2)
        precise = rng.normal(-6.5, 0.5, 200)
        values = list(precise) + [-20.0, -21.0]
        errors = [0.05] * 200 + [1e4, 1e4]
        mu, tau = learning.population(values, errors)
        self.assertAlmostEqual(mu, float(precise.mean()), delta=0.01)
        self.assertAlmostEqual(tau, float(np.sqrt(precise.var() - 0.05 ** 2)), delta=0.02)
        self.assertGreater(learning.population(values)[1], 1.0)   # the same values without their errors


class ProfileTests(unittest.TestCase):
    def test_profile_maximum_is_the_vertex_in_log_kappa(self):
        grid = np.log(learning.KAPPA_GRID)
        target = math.log(0.37)
        total = -3.0 * (grid - target) ** 2
        self.assertAlmostEqual(learning._profile_maximum(total), 0.37, delta=1e-9)
        self.assertEqual(learning._profile_maximum(-grid), learning.KAPPA_GRID[0])

    def test_labelled_infusion_yields_turbulence_profiles(self):
        from test_recognition_model import ROOM, cabin, parameters

        times, values = cabin(n=400, infusion=500.0, seed=6)
        params = learning.base_parameters() | parameters()
        ev = learning.collect_evidence(times, values, ("Tu", "Tl", "Au", "Al"),
                                       {"doors": [], "infusions": [{"t": 500.0, "source": "x"}]},
                                       outside=list(ROOM.values()), session="s")
        learning.likelihood_evidence(ev, params, times, values)
        names = sorted((m, k) for m, k, _, _ in ev["turbulence_profile"])
        self.assertEqual(names, [("decay", 2), ("decay", 3), ("infusion", 2), ("infusion", 3)])
        for _, _, profile, source in ev["turbulence_profile"]:
            self.assertEqual(len(profile), len(learning.KAPPA_GRID))
            self.assertEqual(source, "x")
            self.assertTrue(all(math.isfinite(v) for v in profile))
        self.assertGreater(sum(map(sum, ev["innovations"][INFUSION])), 0)


class SourceProfileTests(unittest.TestCase):
    def test_labelled_door_yields_a_profile_of_the_source_wandering(self):
        from test_recognition_model import ROOM, cabin, parameters

        times, values = cabin(n=500, door=(400.0, 700.0), seed=7)
        params = learning.base_parameters() | parameters()
        params["source"] = [[0.0, 0.0], [0.0, 0.0], [0.3, 0.5], [0.3, 0.5]]
        ev = learning.collect_evidence(times, values, ("Tu", "Tl", "Au", "Al"),
                                       {"doors": [{"open": 400.0, "close": 700.0, "source": "d"}],
                                        "infusions": []},
                                       outside=list(ROOM.values()), session="s")
        learning.likelihood_evidence(ev, params, times, values)
        self.assertEqual(len(ev["source_profile"]), 1)
        profile, source = ev["source_profile"][0]
        self.assertEqual(source, "d")
        self.assertEqual(len(profile), len(learning.SOURCE_Q_GRID))
        self.assertTrue(all(math.isfinite(v) for v in profile))

    def test_wandering_is_the_maximum_of_the_summed_profiles(self):
        ev = learning.empty_evidence()
        ev["sessions"] = 1
        grid = np.log(learning.SOURCE_Q_GRID)
        ev["source_profile"] = [[(-2.0 * (grid - math.log(3e-5)) ** 2).tolist(), "a"],
                                [(-1.0 * (grid - math.log(3e-5)) ** 2).tolist(), "b"]]
        params = learning.estimate(ev, base=learning.base_parameters())
        self.assertAlmostEqual(params["source_q"], 3e-5, delta=1e-12)
        self.assertEqual(learning.estimate(learning.empty_evidence(), base=learning.base_parameters())["source_q"], 0.0)


class CorrelationTests(unittest.TestCase):
    def test_event_correlation_recovers_a_shared_cause(self):
        rng = np.random.default_rng(11)
        rho = 0.85
        entries = []
        for n in range(300):
            a = rng.normal()
            b = rho * a + math.sqrt(1 - rho * rho) * rng.normal()
            entries += [(2, 1.0 + 0.4 * a, f"s@{n}"), (3, 0.5 + 0.3 * b, f"s@{n}")]
        r = learning.event_correlation(entries, 4)
        self.assertAlmostEqual(r[2, 3], rho, delta=0.08)
        self.assertEqual(r[0, 1], 0.0)
        self.assertGreater(np.linalg.eigvalsh(r).min(), 0.0)

    def test_too_few_shared_events_stay_uncorrelated(self):
        entries = [(0, float(n), f"e{n}") for n in range(5)] + [(1, float(n), f"e{n}") for n in range(5)]
        np.testing.assert_allclose(learning.event_correlation(entries, 2), np.eye(2))


class LabelTests(unittest.TestCase):
    def test_detections_become_doors_and_infusions(self):
        detections = [
            detection(Kind.DOOR_OPEN, 100), detection(Kind.VENTILATION, 160),
            detection(Kind.VENTILATION_COMPLETE, 230), detection(Kind.DOOR_CLOSE, 300),
            detection(Kind.INFUSION, 900), detection(Kind.DOOR_CLOSE, 1000),
        ]
        labels = learning.labels_from_detections(detections, T0, lambda d: f"id:{d.kind.value}")
        self.assertEqual(labels["doors"], [{"open": 100.0, "close": 300.0, "ventilated": 160.0, "exchanged": 230.0,
                                            "source": "id:door_open"}])
        self.assertEqual(labels["infusions"], [{"t": 900.0, "source": "id:infusion"}])


class EstimateTests(unittest.TestCase):
    def evidence(self):
        ev = learning.empty_evidence()
        ev["sessions"] = 1
        ev["dt"] = [2.0]
        ev["prior"]["infusion"] = [[2, 1.0, "a", "s@1"], [3, 0.5, "a", "s@1"], [2, 3.0, "b", "s@2"],
                                   [3, 2.5, "b", "s@2"]]
        ev["exchange"] = [[0, -6.0, 0.1, "a", "s@1"], [0, -7.0, 0.1, "b", "s@2"]]
        ev["source"] = [[2, 0.02, 0.001, "a", "s@1"], [2, 0.04, 0.001, "b", "s@2"]]
        return ev

    def test_rejected_events_leave_the_estimate(self):
        base = learning.base_parameters()
        all_events = learning.estimate(self.evidence(), base=base)
        without_b = learning.estimate(self.evidence(), base=base, rejected=frozenset({"b"}))
        self.assertAlmostEqual(all_events["prior"][INFUSION][2][0], 2.0)
        self.assertAlmostEqual(without_b["prior"][INFUSION][2][0], 1.0)
        self.assertAlmostEqual(without_b["prior"][OPEN][0][0], -6.0)
        self.assertAlmostEqual(all_events["source"][2][0], 0.03, delta=1e-6)
        self.assertAlmostEqual(without_b["source"][2][0], 0.02, delta=1e-6)
        self.assertEqual(all_events["source"][0], [0.0, 0.0])        # temperature: wall node, no source

    def test_compact_evidence_keeps_seven_significant_digits(self):
        ev = {"a": [1.23456789012, -0.000123456789, 3], "b": {"c": (2.5, float("inf"))}, "d": "x"}
        self.assertEqual(learning.compact(ev), {"a": [1.234568, -0.0001234568, 3], "b": {"c": [2.5, float("inf")]},
                                                "d": "x"})

    def test_merge_keeps_every_evidence_entry(self):
        merged = learning.merge_evidence(self.evidence(), self.evidence())
        self.assertEqual(merged["sessions"], 2)
        self.assertEqual(len(merged["prior"]["infusion"]), 8)
        self.assertEqual(len(merged["exchange"]), 4)
        self.assertEqual(len(merged["source"]), 4)

    def test_exchange_evidence_replaces_courses(self):
        ev = learning.empty_evidence()
        tt = np.arange(0.0, 300.0, 2.0)
        humid = 12.0 + learning._source_basis(tt, 0.005) @ np.array([30.0, 0.02])
        ev["_courses"] = [[0, tt, learning._course(tt, 0.003, 0.002, 80.0, 58.0), 58.0, "a", "s@1"],
                          [2, tt, humid, 30.0, "a", "s@1"]]
        learning.fit_exchange_evidence(ev, np.array([0.002, 0.0, 0.0, 0.0]))
        self.assertNotIn("_courses", ev)
        self.assertEqual(len(ev["exchange"]), 2)
        self.assertAlmostEqual(math.exp(ev["exchange"][0][1]), 0.003, delta=1e-5)
        self.assertLess(ev["exchange"][0][2], 1e-3)               # exact course: tiny uncertainty
        self.assertAlmostEqual(math.exp(ev["exchange"][1][1]), 0.005, delta=1e-6)
        self.assertEqual(len(ev["source"]), 1)
        self.assertAlmostEqual(ev["source"][0][1], 0.02, delta=1e-6)


if __name__ == "__main__":
    unittest.main()
