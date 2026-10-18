"""Compare causal onset expansion to the complete, independently formed bank."""

from dataclasses import replace
from datetime import timedelta
from math import exp, fsum, log
import unittest

from test_detector import trace
from test_foundation import T0
from custom_components.ha_sauna.core.curve_probability import (
    CurveProbability, CurveProfile, OnsetPrior, Response,
)
from custom_components.ha_sauna.core.sequential_evidence import (
    Basis, ChannelModel, EvidenceSequence, Hypothesis, Observation, Regression,
)


def specification(*, split=False, time_scale=1):
    # Mathematical comparison constants, not production recognition settings.
    background = (ChannelModel("temperature", Basis(T0, 10 * time_scale, (0, 1)),
                               Regression((trace(0)[0], 0), ((1, 0), (0, 1)), 2, 0.1)),)
    profiles = tuple(CurveProfile(name, name, 1, (
        Response("temperature", "slope_change", 10 * time_scale, 4, direction),
    )) for name, direction in (("fall", -1), ("rise", 1)))
    nodes = ((0.5, 0.5), (0.5, 0.5)) if split else ((0.5, 1),)
    return background, profiles, OnsetPrior(0.01 / time_scale, nodes)


def observations(*, time_scale=1):
    return tuple(Observation("temperature", "fixture", 0,
                             T0 + timedelta(seconds=second * time_scale),
                             T0 + timedelta(seconds=second * time_scale), trace(second)[0])
                 for second in range(60, 86))


def probabilities(sequence):
    return {item.event: item for item in sequence.probabilities()}


class CurveProbabilityTests(unittest.TestCase):
    def test_online_expansion_equals_complete_static_onset_model(self):
        background, profiles, prior = specification()
        originals = observations()
        first, last = originals[0].at, originals[-1].at
        alternatives = [Hypothesis(
            "background", "background", exp(-prior.rate_per_second * (last - first).total_seconds()),
            background,
        )]
        for number, (before, after) in enumerate(zip(originals, originals[1:])):
            survival = exp(-prior.rate_per_second * (before.at - first).total_seconds())
            for node, (onset, mass) in enumerate(prior.interval(before.at, after.at)):
                for profile in profiles:
                    alternatives.append(Hypothesis(
                        f"{number}:{node}:{profile.name}", profile.event,
                        survival * exp(mass) * profile.prior_weight / fsum(p.prior_weight for p in profiles),
                        profile.bind(background, onset),
                    ))
        self.assertAlmostEqual(fsum(h.prior_weight for h in alternatives), 1, places=14)
        static, online = EvidenceSequence(alternatives), CurveProbability(background, profiles, prior)
        for original in originals:
            static.observe(original)
            online.observe(original)
        expected = probabilities(static)
        for event, actual in probabilities(online).items():
            self.assertAlmostEqual(actual.log_odds, expected[event].log_odds, places=10)
            self.assertAlmostEqual(actual.probability, expected[event].probability, places=12)
        self.assertGreater(expected["fall"].probability, expected["rise"].probability)

    def test_each_original_updates_without_a_candidate_or_confirmation_block(self):
        stream = CurveProbability(*specification())
        previous = None
        for original in observations():
            stream.observe(original)
            current = probabilities(stream)
            self.assertEqual(current["background"].original_count,
                             len([o for o in observations() if o.at <= original.at]))
            if previous is not None:
                self.assertNotEqual(current["background"].log_odds, previous)
            previous = current["background"].log_odds
        self.assertEqual(len(stream.onsets()), (len(observations()) - 1) * 2)

    def test_repeated_reports_and_silence_do_not_create_onsets_or_evidence(self):
        stream = CurveProbability(*specification())
        first = observations()[0]
        stream.observe(first)
        snapshot = stream.probabilities(), stream.onsets(), stream.frontier
        for seconds in (1, 10, 1000):
            stamp = first.received_at + timedelta(seconds=seconds)
            stream.observe(replace(first, received_at=stamp))
            stream.observe(replace(first, at=stamp, received_at=stamp, value=None))
            self.assertEqual((stream.probabilities(), stream.onsets(), stream.frontier), snapshot)

    def test_duplicate_onset_locations_share_prior_mass(self):
        one, split = CurveProbability(*specification()), CurveProbability(*specification(split=True))
        for original in observations():
            one.observe(original)
            split.observe(original)
        for event, result in probabilities(one).items():
            self.assertAlmostEqual(result.log_odds, probabilities(split)[event].log_odds, places=11)

    def test_physical_time_units_preserve_probability_and_onset_location(self):
        seconds = CurveProbability(*specification())
        scaled = CurveProbability(*specification(time_scale=60))
        for a, b in zip(observations(), observations(time_scale=60)):
            seconds.observe(a)
            scaled.observe(b)
        for event, result in probabilities(seconds).items():
            # datetime stores microseconds; the declared onset nodes are rounded
            # in each coordinate system. Bound comparison to that resolution.
            self.assertAlmostEqual(result.probability, probabilities(scaled)[event].probability,
                                   places=6)
        for a, b in zip(seconds.onsets(), scaled.onsets()):
            self.assertAlmostEqual((a.onset_at - T0).total_seconds(),
                                   (b.onset_at - T0).total_seconds() / 60, places=6)

    def test_correction_rebuilds_the_same_onset_comparison(self):
        stream, clean = CurveProbability(*specification()), CurveProbability(*specification())
        originals = observations()
        changed = originals[15]
        new_value = trace(74)[0]
        for original in originals:
            stream.observe(original)
            clean.observe(replace(original, value=new_value) if original is changed else original)
        stream.observe(replace(changed, value=new_value,
                               received_at=originals[-1].received_at + timedelta(seconds=1)))
        self.assertEqual(stream.probabilities(), clean.probabilities())
        self.assertEqual(stream.onsets(), clean.onsets())

    def test_response_parameters_describe_curves_and_are_not_sample_requirements(self):
        for form in ("slope_change", "curvature_change", "relaxation", "pulse", "smooth_braking"):
            response = Response("temperature", form, 10, 4, -1)
            self.assertEqual(response.value(-1), 0)
            self.assertEqual(response.value(0), 0)
            self.assertLess(response.value(1), 0)
            self.assertAlmostEqual(response.value(5), replace(response, duration_seconds=60).value(30))
        pulse = Response("humidity", "pulse", 10, 4, 1)
        self.assertEqual(pulse.value(10), 1)
        self.assertGreater(pulse.value(10), pulse.value(1))
        self.assertGreater(pulse.value(10), pulse.value(100))

    def test_invalid_profiles_and_source_changes_fail_without_partial_evidence(self):
        with self.assertRaises(ValueError):
            OnsetPrior(0, ((0.5, 1),))
        with self.assertRaises(ValueError):
            OnsetPrior(1, ((0, 1),))
        with self.assertRaises(ValueError):
            Response("humidity", "pulse", 0, 1, 1)
        background, profiles, prior = specification()
        with self.assertRaises(ValueError):
            CurveProbability(background, (replace(profiles[0], responses=(
                replace(profiles[0].responses[0], channel="foreign"),)),), prior)
        stream = CurveProbability(background, profiles, prior)
        stream.observe(observations()[0])
        snapshot = stream.probabilities(), stream.onsets(), stream.frontier
        with self.assertRaises(ValueError):
            stream.observe(replace(observations()[1], source="another sensor"))
        self.assertEqual((stream.probabilities(), stream.onsets(), stream.frontier), snapshot)
