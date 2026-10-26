"""Mathematische Eigenschaften der Erkennung: Verteilungen, Knickmodell, Matrixexponential."""

import math
import unittest

import numpy as np

from custom_components.ha_sauna.core.moisture import absolute_humidity
from custom_components.ha_sauna.core.recognition import (
    _Episode,
    falling_probability,
    flattening_probability,
    t_cdf,
)
from custom_components.ha_sauna.core.recognition_model import expm, saturation_humidity


def brute_force_flattening(x, y, m, rise):
    """Reference: explicit least squares per kink, evidence and contrasts per kink."""
    x = np.asarray(x, float) - x[0]
    y = np.asarray(y, float)
    n = len(x)
    weights, probs, taus = [], [], []
    for k in range(m, n - m):
        tau = x[k]
        X = np.column_stack([np.ones(n), x - tau, np.maximum(x - tau, 0)])
        xtx = X.T @ X
        coef = np.linalg.solve(xtx, X.T @ y)
        rss = float(((y - X @ coef) ** 2).sum())
        dof = n - 3
        weights.append(-0.5 * math.log(np.linalg.det(xtx)) - 0.5 * dof * math.log(rss))
        cov = rss / dof * np.linalg.inv(xtx)

        def p(vec):
            vec = np.asarray(vec, float)
            return float(t_cdf(np.array([coef @ vec / math.sqrt(vec @ cov @ vec)]), dof)[0])

        low = p([0, 1 - 1 / math.e, 1])
        high = p([0, -1 - rise, -1])
        fall = p([0, -1, 0])
        probs.append(max(low + high - 1, 0) * fall)
        taus.append(tau)
    w = np.exp(np.array(weights) - max(weights))
    w /= w.sum()
    return float((w * np.array(probs)).sum()), float(taus[int(np.argmax(w))])


class StudentTests(unittest.TestCase):
    def test_cdf_matches_closed_forms(self):
        t = np.array([-7.0, -1.5, -0.2, 0.0, 0.3, 2.0, 9.0])
        np.testing.assert_allclose(t_cdf(t, 1.0), 0.5 + np.arctan(t) / math.pi, atol=1e-10)
        np.testing.assert_allclose(t_cdf(t, 2.0), 0.5 + t / (2 * np.sqrt(2 + t * t)), atol=1e-10)
        normal = 0.5 * (1 + np.vectorize(math.erf)(t / math.sqrt(2)))
        np.testing.assert_allclose(t_cdf(t, 1e7), normal, atol=1e-6)

    def test_falling_probability_follows_the_slope(self):
        x = np.arange(30.0)
        rng = np.random.default_rng(1)
        noise = rng.normal(0, 0.05, len(x))
        self.assertGreater(falling_probability(x, -0.1 * x + noise), 0.999)
        self.assertLess(falling_probability(x, 0.1 * x + noise), 0.001)
        self.assertEqual(falling_probability(x[:3], x[:3]), 0.0)


class FlatteningTests(unittest.TestCase):
    def test_vectorised_evidence_equals_explicit_regressions(self):
        rng = np.random.default_rng(7)
        for shape in ("flat", "falling", "rising_again"):
            with self.subTest(shape=shape):
                x = np.cumsum(rng.uniform(1.5, 2.5, 80))
                kink = x[40]
                after = {"flat": -0.002, "falling": -0.05, "rising_again": 0.03}[shape]
                y = np.where(x < kink, -0.05 * (x - kink), after * (x - kink)) + rng.normal(0, 0.02, len(x))
                p, tau = flattening_probability(x, y, 20, 0.05)
                p_ref, tau_ref = brute_force_flattening(x, y, 20, 0.05)
                self.assertAlmostEqual(p, p_ref, places=8)
                self.assertAlmostEqual(tau, tau_ref, places=8)

    def test_shapes_are_distinguished(self):
        rng = np.random.default_rng(3)
        x = np.arange(0.0, 160.0, 2.0)
        kink = 80.0
        base = -0.05 * (x - kink)
        flat = np.where(x < kink, base, -0.001 * (x - kink)) + rng.normal(0, 0.01, len(x))
        steady = base + rng.normal(0, 0.01, len(x))
        v_shape = np.where(x < kink, base, 0.05 * (x - kink)) + rng.normal(0, 0.01, len(x))
        p_flat, tau = flattening_probability(x, flat, 20, 0.05)
        self.assertGreater(p_flat, 0.9)
        self.assertAlmostEqual(tau, kink, delta=6)
        self.assertLess(flattening_probability(x, steady, 20, 0.05)[0], 0.1)
        # a fall that turns directly into a rise is a V, not a flattening
        self.assertLess(flattening_probability(x, v_shape, 20, 0.05)[0], 0.1)

    def test_too_few_points_give_no_probability(self):
        x = np.arange(10.0)
        self.assertEqual(flattening_probability(x, x, 20, 0.05), (0.0, None))


class ExponentialTests(unittest.TestCase):
    def test_generator_exponential_is_a_transition_matrix(self):
        rng = np.random.default_rng(5)
        g = rng.uniform(0, 0.2, (6, 6))
        np.fill_diagonal(g, 0)
        np.fill_diagonal(g, -g.sum(1))
        for dt in (0.1, 2.0, 37.0, 900.0):
            with self.subTest(dt=dt):
                t = expm(g * dt)
                values, vectors = np.linalg.eig(g * dt)
                reference = (vectors @ np.diag(np.exp(values)) @ np.linalg.inv(vectors)).real
                np.testing.assert_allclose(t, reference, atol=1e-9)
                np.testing.assert_allclose(t.sum(1), 1.0, atol=1e-9)
                self.assertGreaterEqual(t.min(), -1e-12)
        np.testing.assert_allclose(expm(np.zeros((3, 3))), np.eye(3))

    def test_saturation_matches_the_humidity_formula(self):
        for temperature in (20.0, 60.0, 95.0):
            self.assertAlmostEqual(saturation_humidity(temperature), absolute_humidity(temperature, 100.0), places=9)


class EpisodeTests(unittest.TestCase):
    def test_candidate_confirmation_and_symmetric_leaving(self):
        episode = _Episode(0.9)
        sequence = [0.0, 0.05, 0.3, 0.6, 0.95, 0.97, 0.8, 0.5, 0.05, 0.01]
        results = [episode.feed(s, p) for s, p in enumerate(sequence)]
        self.assertEqual(results[4], ("start", 2))           # candidate began when 0.1 was left
        self.assertEqual(results[8], ("end", 6))             # leaving began when 0.9 was left
        self.assertTrue(all(r is None for i, r in enumerate(results) if i not in (4, 8)))

    def test_candidate_returning_to_rest_is_forgotten(self):
        episode = _Episode(0.9)
        for s, p in enumerate([0.0, 0.5, 0.05, 0.2, 0.95]):
            result = episode.feed(s, p)
        self.assertEqual(result, ("start", 3))


if __name__ == "__main__":
    unittest.main()
