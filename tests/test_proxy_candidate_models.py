"""Independent causal and mathematical contracts for exploratory climate forms.

Small fixture-derived windows exercise the analysis APIs, never a full replay.
Synthetic transformations specify mathematical test cases, not event labels.
"""

from copy import deepcopy
from datetime import timedelta
import json
from math import ceil, log
from pathlib import Path
import unittest
from unittest.mock import patch

from analysis import proxy_candidate_models as candidates


TESTS = Path(__file__).resolve().parent


class ProxyCandidateModelsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = candidates.validate_policy(json.loads(
            (TESTS / "analysis/proxy_candidate_policy.json").read_text()
        ))
        source = json.loads(
            (TESTS / "fixtures/scenarios/source_curves.json").read_text()
        )["curves"]["occupied_infusion"]
        column = source["roles"].index("upper_humidity") + 1
        cls.records = [
            {"t": row[0], "value": row[column], "origin": "samples",
             "valid": True, "fit_eligible": True}
            for row in source["rows"]
        ]
        cls.source = source
        cls.step = cls.records[1]["t"] - cls.records[0]["t"]
        count = cls.policy["numerics"]["max_points"]
        cls.samples = [
            (candidates.EPOCH + timedelta(seconds=row["t"]), row["value"])
            for row in cls.records[:count]
        ]
        cls.baseline = cls.samples[0][1]
        cls.amplitude = max(value for _, value in cls.samples) - min(
            value for _, value in cls.samples
        )
        cls.reader, ah, cls.owner, _ = candidates.load_owners(cls.policy)
        cls.ah = staticmethod(ah)

    def setUp(self):
        self.owner.begin_anchor()

    def _pulse(self, *, elevated_tail=False):
        """Three observed segments, with exactly one feasible knot pair.

        Each segment owns m originals: before includes the first knot, the
        rising segment excludes that knot and includes the peak, and the
        falling segment excludes the peak. No fitted derivative is the oracle.
        """
        side = self.policy["numerics"]["min_side_samples"]
        onset, peak = side - 1, 2 * side - 1
        new_level = self.baseline + (self.amplitude if elevated_tail else 0)
        peak_level = self.baseline + side * self.amplitude
        points = []
        for index in range(3 * side):
            if index <= onset:
                value = self.baseline
            elif index <= peak:
                value = self.baseline + (index - onset) * self.amplitude
            else:
                value = peak_level - (index - peak) * (
                    peak_level - new_level
                ) / side
            points.append((
                candidates.EPOCH + timedelta(seconds=index * self.step), value,
            ))
        return points, (onset, peak), new_level

    def test_future_values_and_invalid_future_originals_cannot_change_a_prefix(self):
        window = self.policy["sampling"]["windows_s"][0]
        minimum = 3 * self.policy["numerics"]["min_side_samples"]
        prefix = [row for row in self.records if row["t"] <= window]
        changed = deepcopy(self.records)
        for row in changed:
            if row["t"] > window:
                row.update(value=None, valid=False, fit_eligible=False,
                           invalid_reason="invalid_future_original")
        results = []
        for records in (prefix, self.records, changed):
            original = candidates.OriginalChannel("upper_humidity", records)
            points, quality = original.support(
                window, window, self.policy["sampling"], minimum,
            )
            self.assertTrue(quality["eligible"], quality)
            self.assertTrue(all(stamp <= candidates.EPOCH + timedelta(
                seconds=window,
            ) for stamp, _ in points))
            self.owner.begin_anchor()
            results.append((points, quality, candidates.common_models(
                self.owner, points,
            )))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0], results[2])

        # An invalid original inside that same prefix is a real context break.
        broken = deepcopy(prefix)
        middle = len(broken) - minimum - 1
        self.assertGreaterEqual(middle, 0)
        broken[middle].update(value=None, valid=False, fit_eligible=False,
                              invalid_reason="invalid_original")
        original = candidates.OriginalChannel("upper_humidity", broken)
        points, quality = original.support(
            window, window, self.policy["sampling"], minimum,
        )
        self.assertEqual(points, [])
        self.assertFalse(quality["eligible"])
        self.assertEqual(quality["invalid_originals"], 1)
        later, quality = original.support(
            window, window, self.policy["sampling"], minimum,
            start=broken[middle + 1]["t"],
        )
        self.assertTrue(quality["eligible"], quality)
        self.assertTrue(later)

    def test_direct_competitors_share_support_and_scale_under_affine_units(self):
        calls = []
        original_fit = self.owner.module._fit

        def observe(rows, values, noise, policy):
            coordinates = (tuple(row[1] for row in rows)
                           if len(rows[0]) > 1 else None)
            calls.append((len(rows), tuple(values), noise, coordinates))
            return original_fit(rows, values, noise, policy)

        with patch.object(self.owner.module, "_fit", side_effect=observe):
            original = candidates.common_models(self.owner, self.samples)
        self.assertEqual(original["status"], "resolved", original)
        self.assertTrue(calls)
        coordinates = [item[3] for item in calls if item[3] is not None]
        self.assertTrue(coordinates)
        self.assertTrue(all(item == coordinates[0] for item in coordinates))
        for count, values, noise, _ in calls:
            self.assertEqual(count, original["selected_count"])
            self.assertEqual(values, calls[0][1])
            self.assertEqual(noise, original["noise_scale"])

        # Positive affine maps preserve shape, normalized losses and ordering.
        value_factor, time_factor = 3, 2
        time_offset = self.policy["sampling"]["anchor_step_s"]
        transformed = [
            (candidates.EPOCH + timedelta(seconds=time_offset + time_factor * (
                stamp - candidates.EPOCH
            ).total_seconds()), self.baseline + value_factor * value)
            for stamp, value in self.samples
        ]
        self.owner.begin_anchor()
        mapped = candidates.common_models(self.owner, transformed)
        self.assertEqual(mapped["status"], "resolved", mapped)
        self.assertEqual(mapped["best_model"], original["best_model"])
        self.assertEqual(mapped["selected_times_s"], [
            time_offset + time_factor * at for at in original["selected_times_s"]
        ])
        self.assertAlmostEqual(
            mapped["noise_scale"], value_factor * original["noise_scale"],
            places=7,
        )
        before = {item["model"]: item for item in original["models"]}
        after = {item["model"]: item for item in mapped["models"]}
        self.assertEqual(set(before), set(after))
        self.assertTrue({"L", "P1", "P2", "P3"}.issubset(before))
        for name, model in before.items():
            with self.subTest(model=name):
                self.assertAlmostEqual(after[name]["score"], model["score"], places=6)

    def test_two_pulse_knots_cost_six_parameters_and_one_feasible_pair(self):
        points, _, _ = self._pulse()
        result = candidates.common_models(self.owner, points)
        self.assertEqual(result["status"], "resolved", result)
        pulse = next(item for item in result["models"] if item["model"] == "PULS")
        # With 3m originals and m required in each segment, only the two
        # segment boundaries are feasible together, regardless of grid size.
        self.assertGreater(result["single_knots_tested"], 1)
        self.assertEqual(result["pulse_pairs_tested"], 1)
        self.assertEqual(pulse["search_count"], 1)
        self.assertEqual(pulse["search_cost"], 2 * log(1))
        # Four continuous hinge coefficients plus two estimated knot times.
        self.assertEqual(pulse["parameters"], 6)
        self.assertAlmostEqual(
            pulse["score"] - pulse["loss"], 6 * log(len(points)), places=7,
        )

    def test_a_favorable_pulse_score_still_requires_observed_ordered_flanks(self):
        points, (onset, peak), _ = self._pulse()
        score_candidate = {"models": [{
            "model": "PULS",
            "gain_over_other": self.policy["competition"]["minimum_score_gain"] + 1,
            "knots_s": [(points[index][0] - candidates.EPOCH).total_seconds()
                        for index in (onset, peak)],
            "search_count": 1, "has_rise_onset": True,
        }]}
        assessment = candidates.pulse_assessment(
            self.owner, points, score_candidate, self.policy, influence=False,
        )
        self.assertTrue(assessment["supported"], assessment)
        for direction in (-1, 0, 1):
            with self.subTest(observed_direction=direction):
                monotone = [
                    (stamp, self.baseline + direction * index * self.amplitude)
                    for index, (stamp, _) in enumerate(points)
                ]
                assessment = candidates.pulse_assessment(
                    self.owner, monotone, score_candidate, self.policy, influence=False,
                )
                self.assertFalse(assessment["supported"], assessment)

    def test_missing_owner_comparison_and_short_support_produce_no_model_ranking(self):
        minimum = 3 * self.policy["numerics"]["min_side_samples"]
        short = candidates.common_models(self.owner, self.samples[:minimum - 1])
        self.assertNotEqual(short["status"], "resolved")
        self.assertFalse(short.get("models"))
        # None is the owner's contract for an unresolved comparison, including
        # a score tie. The consumer must preserve absence, not invent certainty.
        with patch.object(self.owner.module, "_level_models", return_value=None):
            missing = candidates.common_models(self.owner, self.samples)
        self.assertNotEqual(missing["status"], "resolved")
        self.assertFalse(missing.get("models"))
        self.assertIsNone(missing.get("best_model"))

    def test_a_new_level_is_free_but_an_invalid_bridge_interrupts_continuity(self):
        points, (onset, peak), new_level = self._pulse(elevated_tail=True)
        self.assertNotEqual(new_level, points[0][1])
        end = (points[-1][0] - candidates.EPOCH).total_seconds()
        hypotheses = self.policy["hypotheses"]
        interval = max(
            self.policy["sampling"]["anchor_step_s"],
            hypotheses["minimum_new_level_points"] * self.step,
        )
        first = end + hypotheses["tail_window_s"] + 2 * self.step
        anchors = [first + index * interval
                   for index in range(hypotheses["tail_level_confirmations"])]
        plateau = [(candidates.EPOCH + timedelta(seconds=end + index * self.step),
                    new_level)
                   for index in range(1, ceil((anchors[-1] - end) / self.step) + 1)]
        records = [{"t": (stamp - candidates.EPOCH).total_seconds(), "value": value,
                    "origin": "samples", "valid": True, "fit_eligible": True}
                   for stamp, value in points + plateau]
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted):
                inputs = deepcopy(records)
                if interrupted:
                    inputs[len(points)].update(
                        value=None, valid=False, fit_eligible=False,
                        invalid_reason="invalid_original",
                    )
                original = candidates.OriginalChannel("upper_humidity", inputs)
                trace = candidates.PulseTrace("upper_humidity", self.policy)
                trace.accept({
                    "knots_s": [(points[index][0] - candidates.EPOCH).total_seconds()
                                for index in (onset, peak)],
                    "has_rise_onset": True, "context_start_s": 0,
                    "newest_original_s": end, "confirmed_at_s": end,
                    "first_assessment": {"has_rise_onset": True},
                }, hypotheses)
                # Establish the accepted pulse's causal anchor before the
                # later bridge observation can interrupt its continuation.
                trace.advance(end, original, [], self.owner)
                for anchor in anchors:
                    self.owner.begin_anchor()
                    trace.advance(anchor, original, [], self.owner)
                active = trace.active
                self.assertIsNotNone(active["level_confirmed_at_s"], active)
                self.assertAlmostEqual(active["fitted_new_level"], new_level, places=7)
                self.assertEqual(bool(active["continuity_interrupted_at_s"]), interrupted)
                self.assertIsNone(active["event_probability"])
                self.assertIsNone(active["event_kind"])
                self.assertIsNone(active["physical_event_time_s"])

    def test_derived_ah_cannot_add_an_independent_climate_vote_or_event_probability(self):
        row = self.source["rows"][0]
        roles = self.source["roles"]
        upper_t = row[roles.index("upper_temperature") + 1]
        lower_t = row[roles.index("lower_temperature") + 1]
        rh = row[roles.index("upper_humidity") + 1]
        self.assertGreater(upper_t, lower_t)
        self.assertGreater(self.ah(upper_t, rh), self.ah(lower_t, rh))
        measured = {
            "upper_temperature": [{"tail_trend": {"direction": -1}}],
            "upper_humidity": [{"tail_trend": {"direction": 0},
                                "tail_level": {"evidence": "supports"}}],
        }
        expected = candidates.climate_interpretations(measured)
        self.assertTrue(expected)
        for ah_direction in (-1, 1):
            extended = {**measured, "upper_absolute_humidity": [{
                "tail_trend": {"direction": ah_direction},
                "tail_level": {"evidence": "supports"},
            }]}
            self.assertEqual(candidates.climate_interpretations(extended), expected)
        self.assertIsNone(candidates.probability_requirements()["event_probability"])


if __name__ == "__main__":
    unittest.main()
