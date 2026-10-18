"""Independent conjugacy, likelihood and original-identity contracts.

Uses existing climate fixtures. Small algebraic constants below define exact
mathematical oracles, not recognition thresholds or synthetic sauna sessions.
"""

from dataclasses import replace
from datetime import timedelta
from math import exp, fsum, lgamma, log, pi
import unittest

from test_curve_change import level_trace_points, trace_points
from test_detector import trace
from test_foundation import T0

from custom_components.ha_sauna.core.sequential_evidence import (
    Basis, ChannelModel, EvidenceSequence, Hypothesis, Observation, Regression,
)


def hypotheses(*, noisy=False, scale=1, offset=0, split=False, time_scale=1):
    noise = trace(151)[1] - trace(150)[1]
    rate = (trace(0)[1] if noisy else noise) ** 2 * 4 * scale ** 2
    basis = Basis(T0, time_scale, (0,))

    def item(name, event, mean, weight=1):
        prior = Regression((offset + scale * mean,), ((0.01,),), 4, rate)
        return Hypothesis(name, event, weight, (ChannelModel("humidity", basis, prior),))

    result = [item("original_level", "background", trace(0)[1])]
    if split:
        result.extend(item(name, "lower_level", trace(100)[1], 0.5) for name in ("a", "b"))
    else:
        result.append(item("new_level", "lower_level", trace(100)[1]))
    return tuple(result)


def observation(at, value, *, received=None):
    return Observation("humidity", "existing_fixture", 0, at, received or at, value)


def probabilities(sequence):
    return {item.event: item for item in sequence.probabilities()}


class SequentialEvidenceTests(unittest.TestCase):
    def test_scalar_conjugacy_and_student_density_have_independent_closed_form(self):
        prior = Regression((0,), ((1,),), 2, 3)
        prediction = prior.predict((1,))
        self.assertEqual(prediction.mean, 0)
        self.assertEqual(prediction.degrees_of_freedom, 4)
        self.assertEqual(prediction.scale_squared, 3)
        expected = (lgamma(2.5) - lgamma(2) - log(12 * pi) / 2
                    - 2.5 * log(1 + 4 / 12))
        posterior, density = prior.observe((1,), 2)
        self.assertAlmostEqual(density, expected, places=13)
        self.assertEqual(posterior.mean, (1,))
        self.assertEqual(posterior.covariance, ((0.5,),))
        self.assertEqual((posterior.shape, posterior.rate, posterior.count), (2.5, 4, 1))
        self.assertEqual(prior.count, 0)

    def test_sequential_posterior_and_density_equal_independent_batch_integral(self):
        points = trace_points(90)
        origin, center = points[0]
        span = (points[-1][0] - origin).total_seconds()
        rows = [((1, (at - origin).total_seconds() / span), value - center)
                for at, value in points]
        prior = Regression((0, 0), ((4, 0), (0, 9)), 2, 3)
        # Independent 2x2 batch inverse and marginal-likelihood normalization.
        a = 1 / 4 + len(rows)
        b = fsum(x[1] for x, _ in rows)
        d = 1 / 9 + fsum(x[1] ** 2 for x, _ in rows)
        determinant = a * d - b * b
        inverse = ((d / determinant, -b / determinant), (-b / determinant, a / determinant))
        vector = (fsum(y for _, y in rows), fsum(x[1] * y for x, y in rows))
        mean = tuple(fsum(c * v for c, v in zip(row, vector)) for row in inverse)
        shape = prior.shape + len(rows) / 2
        rate = prior.rate + (fsum(y * y for _, y in rows)
                             - fsum(m * v for m, v in zip(mean, vector))) / 2
        marginal = (lgamma(shape) - lgamma(prior.shape)
                    + prior.shape * log(prior.rate) - shape * log(rate)
                    + (log(1 / 36) - log(determinant)) / 2 - len(rows) * log(2 * pi) / 2)
        for ordered in (rows, list(reversed(rows)), rows[::2] + rows[1::2]):
            state, terms = prior, []
            for design, value in ordered:
                state, density = state.observe(design, value)
                terms.append(density)
            for got, expected in zip(state.mean, mean):
                self.assertAlmostEqual(got, expected, places=10)
            for got, expected in zip(state.covariance, inverse):
                for actual, target in zip(got, expected):
                    self.assertAlmostEqual(actual, target, places=12)
            self.assertAlmostEqual(state.rate, rate, places=9)
            self.assertEqual(state.shape, shape)
            self.assertAlmostEqual(fsum(terms), marginal, places=9)

    def test_probability_uses_pre_update_predictive_densities(self):
        items = hypotheses()
        sequence = EvidenceSequence(items)
        at, value = level_trace_points()[0]
        log_likelihoods = [h.channels[0].prior.predict((1,)).log_density(value) for h in items]
        expected = 1 / (1 + exp(log_likelihoods[0] - log_likelihoods[1]))
        sequence.observe(observation(at, value))
        actual = probabilities(sequence)["lower_level"]
        self.assertAlmostEqual(actual.probability, expected, places=14)
        self.assertAlmostEqual(actual.log_odds, log_likelihoods[1] - log_likelihoods[0], places=12)
        self.assertEqual(actual.original_count, 1)

    def test_decision_depends_on_information_not_seven_new_originals(self):
        clear = EvidenceSequence(hypotheses())
        uncertain = EvidenceSequence(hypotheses(noisy=True))
        at, value = level_trace_points()[0]
        clear.observe(observation(at, value))
        self.assertTrue(probabilities(clear)["lower_level"].confirmed(0.95))
        for at, value in level_trace_points()[:14]:
            uncertain.observe(observation(at, value))
            self.assertFalse(probabilities(uncertain)["lower_level"].confirmed(0.95))

    def test_duplicates_and_unknowns_add_no_probability(self):
        sequence = EvidenceSequence(hypotheses())
        at, value = level_trace_points()[0]
        first = observation(at, value)
        sequence.observe(first)
        snapshot = sequence.probabilities()
        for delay in (1, 10, 100):
            received = at + timedelta(seconds=delay)
            self.assertFalse(sequence.observe(replace(first, received_at=received)))
            self.assertFalse(sequence.observe(observation(received, None)))
            self.assertEqual(sequence.probabilities(), snapshot)

    def test_correction_replaces_original_contribution_and_records_arrival(self):
        sequence = EvidenceSequence(hypotheses())
        expected = EvidenceSequence(hypotheses())
        points = level_trace_points()[:8]
        first = observation(*points[0])
        for index, (at, value) in enumerate(points):
            sequence.observe(observation(at, trace(0)[1] if index == 0 else value))
            expected.observe(observation(at, value))
        received = points[-1][0] + timedelta(seconds=1)
        sequence.observe(replace(first, received_at=received))
        self.assertEqual(sequence.probabilities(), expected.probabilities())
        self.assertEqual(sequence.revisions[first.identity], received)
        self.assertEqual(sequence.last_received_at, received)
        self.assertEqual(probabilities(sequence)["lower_level"].original_count, len(points))
        with self.assertRaises(ValueError):
            sequence.observe(observation(received - timedelta(microseconds=1), None))

    def test_source_epoch_and_time_basis_cannot_reuse_another_comparison(self):
        sequence = EvidenceSequence(hypotheses())
        first = observation(*level_trace_points()[0])
        sequence.observe(first)
        snapshot = sequence.probabilities()
        for edit in ({"source": "other"}, {"epoch": 1}, {"time_basis": "receipt"}):
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                sequence.observe(replace(first, **edit))
            self.assertEqual(sequence.probabilities(), snapshot)

    def test_splitting_one_event_prior_does_not_manufacture_votes(self):
        whole = EvidenceSequence(hypotheses())
        split = EvidenceSequence(hypotheses(split=True))
        for at, value in level_trace_points()[:8]:
            whole.observe(observation(at, value))
            split.observe(observation(at, value))
            for event, expected in probabilities(whole).items():
                actual = probabilities(split)[event]
                self.assertAlmostEqual(actual.probability, expected.probability, places=14)
                self.assertAlmostEqual(actual.log_odds, expected.log_odds, places=11)

    def test_value_units_and_time_coordinates_preserve_event_probability(self):
        baseline = EvidenceSequence(hypotheses())
        transformed = EvidenceSequence(hypotheses(scale=2, offset=70, time_scale=60))
        for at, value in level_trace_points()[:10]:
            baseline.observe(observation(at, value))
            moved = T0 + (at - T0) * 60
            transformed.observe(observation(moved, 70 + 2 * value))
            left, right = probabilities(baseline), probabilities(transformed)
            for event in left:
                self.assertAlmostEqual(left[event].log_odds, right[event].log_odds, places=10)

    def test_prior_alone_is_not_an_observed_event(self):
        items = hypotheses()
        sequence = EvidenceSequence((replace(items[0], prior_weight=1),
                                     replace(items[1], prior_weight=1000)))
        posterior = probabilities(sequence)["lower_level"]
        self.assertGreater(posterior.probability, 0.99)
        self.assertFalse(posterior.confirmed(0.99))
        for invalid in (0, 0.5, 1, float("nan")):
            with self.assertRaises(ValueError):
                posterior.threshold_margin(invalid)

    def test_actual_irregular_times_define_fixed_basis_without_repeated_samples(self):
        points = level_trace_points()
        knot = points[len(points) // 2][0]
        basis = Basis(points[0][0], 10, (0, 1), ((knot, 1),))
        for at in (points[0][0], knot, points[-1][0] + timedelta(hours=1)):
            self.assertEqual(basis.design(at), (
                1, (at - points[0][0]).total_seconds() / 10,
                max(0, (at - knot).total_seconds() / 10),
            ))
        prior = Regression((0, 0, 0), ((1, 0, 0), (0, 1, 0), (0, 0, 1)), 2, 3)
        posterior, _ = prior.observe(basis.design(points[0][0]), trace(0)[1])
        self.assertEqual(posterior.count, 1)  # proper uncertainty, no rank/sample gate
        self.assertEqual(posterior.covariance[1:], prior.covariance[1:])

    def test_improper_priors_and_incomparable_models_fail_explicitly(self):
        for covariance in (((0,),), ((-1,),), ((float("nan"),),)):
            with self.assertRaises(ValueError):
                Regression((0,), covariance, 2, 3)
        with self.assertRaises(ValueError):
            Regression((0, 0), ((1, 2), (2, 1)), 2, 3)
        items = hypotheses()
        other = replace(items[1], channels=(replace(items[1].channels[0], channel="temperature"),))
        with self.assertRaises(ValueError):
            EvidenceSequence((items[0], other))
        self.assertAlmostEqual(fsum(x.probability for x in EvidenceSequence(items).probabilities()), 1)
