"""Analytical distribution identities and directed existing curve fixtures."""

from datetime import timedelta
from math import atan, exp, fsum, log, pi, sqrt
import unittest

from test_detector import trace
from test_foundation import T0
from custom_components.ha_sauna.core.probability import student_log_survival
from custom_components.ha_sauna.core.sequential_evidence import (
    Basis, ChannelModel, DirectedRegression, EvidenceSequence, HalfSpace,
    Hypothesis, Observation, Prediction, Regression,
)


class DirectedEvidenceTests(unittest.TestCase):
    def test_student_tail_matches_cauchy_and_two_degree_closed_forms(self):
        for value in (0, 1e-10, 0.1, 1, 3, 100, 1e12, 1e150):
            cauchy = atan(1 / value) / pi if value else 0.5
            # Cancellation-free form of (1 - t/sqrt(t²+2))/2.
            root = sqrt(value * value + 2)
            two = 1 / root / (root + value)
            for degrees, expected in ((1, cauchy), (2, two)):
                actual = student_log_survival(value, degrees)
                self.assertAlmostEqual(actual, log(expected), places=11)
                self.assertAlmostEqual(exp(actual) + exp(student_log_survival(-value, degrees)),
                                       1, places=14)

    def test_student_tail_matches_independent_numerical_density_integral(self):
        # Numerical quadrature tests a different representation of the CDF.
        for degrees in (0.5, 3, 4.5, 25, 100):
            prediction = Prediction(0, 1, degrees)
            for boundary in (0.1, 1, 3):
                steps = 4096
                step = boundary / steps
                density = lambda i: exp(prediction.log_density(i * step))
                integral = step / 3 * (
                    density(0) + density(steps)
                    + 4 * fsum(density(i) for i in range(1, steps, 2))
                    + 2 * fsum(density(i) for i in range(2, steps, 2))
                )
                self.assertAlmostEqual(exp(student_log_survival(boundary, degrees)),
                                       0.5 - integral, places=11)

    def test_coefficient_probability_excludes_measurement_variance(self):
        prior = Regression((0,), ((4,),), 2, 3)
        coefficient = prior.coefficient_contrast((1,))
        observation = prior.predict((1,))
        self.assertEqual(coefficient.scale_squared, 6)
        self.assertEqual(observation.scale_squared, 7.5)
        self.assertEqual(HalfSpace((1,)).log_mass(prior), -log(2))

    def test_partitioning_a_prior_preserves_its_full_predictive_distribution(self):
        base = Regression((0,), ((1,),), 2, 3)
        positive = DirectedRegression(base, HalfSpace((1,)))
        negative = DirectedRegression(base, HalfSpace((-1,)))
        weights = [0.5, 0.5]
        for second in range(150, 180):
            value = trace(second)[1] - trace(150)[1]
            base, full = base.observe((1,), value)
            positive, plus = positive.observe((1,), value)
            negative, minus = negative.observe((1,), value)
            mixture = fsum((weights[0] * exp(plus), weights[1] * exp(minus)))
            self.assertAlmostEqual(log(mixture), full, places=12)
            weights = [weights[0] * exp(plus) / mixture, weights[1] * exp(minus) / mixture]
            self.assertAlmostEqual(weights[0], exp(HalfSpace((1,)).log_mass(base)), places=12)

    def test_direction_is_model_evidence_and_cannot_flip_with_the_fitted_coefficient(self):
        basis = Basis(T0, 10, (0, 1), ((T0 + timedelta(seconds=70), 1),))
        prior = Regression((trace(0)[0], 0, 0),
                           ((1, 0, 0), (0, 1, 0), (0, 0, 1)), 2, 0.01)
        models = tuple(Hypothesis(name, name, 0.5, (ChannelModel(
            "temperature", basis, DirectedRegression(prior, HalfSpace((0, 0, sign))),
        ),)) for name, sign in (("negative_kink", -1), ("positive_kink", 1)))
        sequence = EvidenceSequence(models)
        before = None
        for second in range(40, 86):
            at = T0 + timedelta(seconds=second)
            sequence.observe(Observation("temperature", "fixture", 0, at, at, trace(second)[0]))
            if second == 69:
                before = sequence.probabilities()
        self.assertAlmostEqual(before[0].probability, 0.5, places=12)
        result = {p.event: p for p in sequence.probabilities()}
        self.assertTrue(result["negative_kink"].confirmed(0.99))
        self.assertFalse(result["positive_kink"].confirmed(0.99))

    def test_directional_evidence_accumulates_to_exact_endpoint_mass_ratio(self):
        base = Regression((0, 0), ((1, 0), (0, 1)), 2, 3)
        constraint = HalfSpace((0, -1))
        directed = DirectedRegression(base, constraint)
        initial = constraint.log_mass(base)
        ordinary_terms, directed_terms = [], []
        for second in range(70, 86):
            design = (1, (second - 70) / 10)
            value = trace(second)[0] - trace(70)[0]
            base, ordinary = base.observe(design, value)
            directed, restricted = directed.observe(design, value)
            ordinary_terms.append(ordinary)
            directed_terms.append(restricted)
        self.assertAlmostEqual(fsum(directed_terms) - fsum(ordinary_terms),
                               constraint.log_mass(base) - initial, places=12)

    def test_nonphysical_distribution_input_fails_explicitly(self):
        for degrees in (0, -1, float("inf"), True):
            with self.assertRaises(ValueError):
                student_log_survival(0, degrees)
        for scale in (0, -1, float("nan")):
            with self.assertRaises(ValueError):
                Prediction(0, scale, 2)
        with self.assertRaises(ValueError):
            HalfSpace((0, 0))
        with self.assertRaises(ValueError):
            DirectedRegression(Regression((0,), ((1,),), 2, 3), HalfSpace((1, 1)))
