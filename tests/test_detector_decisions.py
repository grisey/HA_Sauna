"""Decision diagnostics retain the numeric gate and its original evidence."""

from dataclasses import replace
from datetime import timedelta
import unittest
from unittest.mock import patch

import test_detector as detector_fixtures
from test_curve_candidates import supported_opening
from test_curve_change import recovery_trace_points
from test_foundation import T0

from custom_components.ha_sauna.core.curve_change import (
    CurveFormFeatures,
    CurveFormReference,
    curve_form,
)
from custom_components.ha_sauna.core.detector import Detector
from custom_components.ha_sauna.core.models import Position, Quantity


MATH = "custom_components.ha_sauna.core.detector"


class DetectorDecisionTests(unittest.TestCase):
    def setUp(self):
        self.parameters = detector_fixtures.detection_parameters()
        self.p = self.parameters.values
        # Consume the existing lifecycle inputs without inheriting its tests.
        self.flow = detector_fixtures.InfusionLifecycleTests()
        self.flow.setUp()

    def _originals(
        self,
        detector,
        points,
        *,
        source=None,
        received_at=None,
        position=Position.UPPER,
    ):
        """Batch real accepted originals; fitting is explicitly invoked below."""
        for stamp, humidity in points:
            received = stamp if received_at is None else received_at
            second = (received - T0).total_seconds()
            for quantity, value in (
                (Quantity.TEMPERATURE, self.flow.temperature),
                (Quantity.HUMIDITY, humidity),
            ):
                self.assertTrue(
                    detector.accept(
                        detector_fixtures.measurement(
                            position,
                            quantity,
                            value,
                            second,
                            source=source if quantity == Quantity.HUMIDITY else None,
                            measured=stamp,
                        )
                    )
                )
            detector._consume(received)

    @staticmethod
    def _background_form(
        points,
        *,
        policy,
        form,
        reference=None,
        current_not_before=None,
        evidence="supports",
    ):
        """Declared owner evidence for lifetime tests, not a geometry oracle."""
        if len(points) < 2 * policy.min_side_samples:
            return None
        reference = reference or CurveFormReference(
            form=form,
            model="quadratic",
            start_at=points[0][0],
            turn_at=points[len(points) // 2][0],
            turn_kind="minimum",
            knot_at=None,
            hinge_order=0,
            parameter_count=3,
            search_count=1,
            initial_newest_at=points[-1][0],
            current_not_before=current_not_before,
        )
        return CurveFormFeatures(
            evidence=evidence,
            form_evidence=evidence,
            reference_evidence=evidence,
            influence_evidence=evidence,
            reason="declared_lifetime_evidence",
            form=form,
            start_at=points[0][0],
            newest_at=points[-1][0],
            count=len(points),
            original_count=len(points),
            noise_scale=1.0,
            positive_score=0.0,
            counter_score=1.0,
            score_gain=1.0,
            positive_model="quadratic",
            counter_model="linear",
            turn_at=reference.turn_at,
            reference=reference,
            reference_score=0.0,
            reference_score_gain=1.0,
            replacement_gain=None,
            replacement_cost=None,
            fresh_original_count=sum(
                stamp > reference.initial_newest_at for stamp, _ in points
            ),
            before_trend=None,
            after_trend=None,
            before_count=len(points) // 2,
            after_count=len(points) - len(points) // 2,
            removed_at=None,
            influence_score_gain=1.0,
            candidate_models=(),
            score_resolution=0.0,
            reference_score_resolution=0.0,
            influence_score_resolution=0.0,
        )

    def _form_points(self, form, start):
        """Translate the established noisy U; reflection gives a known peak.

        Its span stays inside the catalogue window, including later originals.
        The oracle is the fixture geometry, not the product's winning model.
        """
        source = recovery_trace_points()
        span = (source[-1][0] - source[0][0]).total_seconds()
        scale = self.p["curve_window_seconds"] / (2 * span)
        sign = -1 if form == "peak" else 1
        center = self.flow.peak if form == "peak" else self.flow.baseline
        return [
            (
                start + (stamp - source[0][0]) * scale,
                center + sign * (value - self.flow.temperature),
            )
            for stamp, value in source
        ]

    def test_moisture_gate_results_use_the_reported_operator_and_signed_margin(self):
        detector = Detector(self.parameters, T0, (Position.UPPER,))
        threshold = self.p["strong_humidity_upper"]
        positive_water = self.p["humidity_change_absolute_tolerance"] * 2
        for humidity, water, expected in (
            (threshold, positive_water, True),
            (threshold / 2, positive_water, False),
            (threshold, 0.0, False),
            (threshold, -positive_water, False),
        ):
            with self.subTest(humidity=humidity, water=water):
                gates = {}
                actual = detector._moisture_rise(
                    "strong",
                    {Position.UPPER: (humidity, water)},
                    gates=gates,
                )
                humidity_gate = gates["upper:humidity_slope"]
                water_gate = gates["upper:water_increase"]
                self.assertIs(actual, expected)
                self.assertEqual(humidity_gate["operator"], ">=")
                self.assertEqual(humidity_gate["unit"], "%RH/min")
                self.assertEqual(humidity_gate["threshold"], threshold)
                self.assertEqual(humidity_gate["value"], humidity)
                self.assertIs(humidity_gate["result"], humidity >= threshold)
                self.assertEqual(humidity_gate["margin"], humidity - threshold)
                self.assertEqual(water_gate["operator"], ">")
                self.assertEqual(water_gate["unit"], "g/m³")
                self.assertEqual(water_gate["value"], water)
                self.assertEqual(water_gate["threshold"], 0.0)
                self.assertIs(water_gate["result"], water > 0)
                self.assertEqual(water_gate["margin"], water)
                self.assertIs(actual, humidity_gate["result"] and water_gate["result"])

    def test_missing_and_unresolved_evidence_are_distinct_from_counterevidence(self):
        detector = Detector(self.parameters, T0, (Position.UPPER,))
        gates = {}
        self.assertIsNone(
            detector._moisture_rise(
                "strong", {Position.UPPER: (None, None)}, gates=gates
            )
        )
        for gate in gates.values():
            self.assertIsNone(gate["result"])
            self.assertIsNone(gate["value"])
            self.assertNotIn("margin", gate)
            self.assertIn("unavailable", gate["reason"])

        count = 2 * detector.curve_policy.min_side_samples
        points = [
            (T0 + timedelta(seconds=index * detector.grid_seconds), self.flow.baseline)
            for index in range(count)
        ]
        self._originals(detector, points)
        feature = supported_opening(points, policy=detector.curve_policy)
        self.assertIsNotNone(feature)
        key = ("door_open", Position.UPPER, Quantity.HUMIDITY)
        cases = (("unresolved", "insufficient"), ("rejects", "rejected"))
        for evidence, expected in cases:
            with (
                self.subTest(evidence=evidence),
                patch(
                    f"{MATH}.curve_change",
                    return_value=replace(feature, evidence=evidence),
                ),
            ):
                # An independent owner response needs an independent evaluation.
                detector._curve_evaluated.clear()
                detector._curve_fit_cache.clear()
                detector._curve_candidate(
                    *key, points[-1][0], points[-1][0], T0, ("diagnostic",)
                )
                status = detector._curve_status[key]
                self.assertEqual(status["status"], expected)
                self.assertEqual(status["gates"]["model_evidence"]["value"], evidence)
                self.assertIs(
                    status["gates"]["model_evidence"]["result"],
                    None if evidence == "unresolved" else False,
                )
                direction = status["gates"]["change_direction"]
                self.assertEqual(direction["operator"], "<")
                self.assertEqual(direction["unit"], "%RH/s")
                self.assertIs(direction["result"], feature.slope_change < 0)
                self.assertEqual(direction["margin"], -feature.slope_change)

        empty = Detector(self.parameters, T0, (Position.UPPER,))
        self.assertEqual(empty.advance(T0, enabled=True), [])
        decision = empty.diagnostic["decisions"]["infusion_decay"]
        self.assertFalse(decision["evaluated"])
        self.assertIsNone(decision["result"])

    def test_native_peak_requires_fresh_originals_in_its_source_epoch(self):
        detector = Detector(self.parameters, T0, (Position.UPPER,))
        points = self._form_points("peak", T0)
        initial = points[: -detector.curve_policy.min_side_samples]
        key = ("infusion_decay", Position.UPPER, Quantity.HUMIDITY)
        self._originals(detector, initial)

        def evaluate(now):
            detector._curve_candidate(*key, now, now, T0, ("infusion-owner",))

        # This isolates the complementary native peak path. Its form evidence
        # and every later constrained recheck use the real mathematical owner.
        with (
            patch(f"{MATH}.curve_change", return_value=None),
            patch(f"{MATH}.curve_form", wraps=curve_form) as fitted,
        ):
            evaluate(initial[-1][0])
            first = detector._curve_candidates[key]
            self.assertIsInstance(first.features, CurveFormFeatures)
            self.assertEqual(first.features.form, "peak")
            self.assertEqual(detector._curve_status[key]["status"], "supported")
            self.assertIsNone(first.fresh_newest_at)
            reference = first.features.reference
            detector._remember_curve_proposal(
                "infusion_decay",
                detector.positions,
                (Quantity.HUMIDITY,),
                floor=T0,
                context=("infusion-owner",),
                observed_at=initial[-1][0],
            )
            proposal_key = ("infusion_decay", detector.positions, (Quantity.HUMIDITY,))
            proposal = detector._curve_proposal_status[proposal_key]
            self.assertTrue(proposal["assembly_result"])
            coherence = proposal["assembly_gates"]["onsets_coherent"]
            self.assertEqual(coherence["operator"], "<=")
            self.assertEqual(coherence["unit"], "s")
            self.assertEqual(coherence["threshold"], self.p["infusion_window_seconds"])
            self.assertIs(
                coherence["result"], coherence["value"] <= coherence["threshold"]
            )
            self.assertEqual(
                coherence["margin"], coherence["threshold"] - coherence["value"]
            )
            calls = fitted.call_count
            tick = initial[-1][0] + timedelta(seconds=detector.grid_seconds)
            evaluate(tick)
            self._originals(detector, [initial[-1]], received_at=tick)
            evaluate(tick)
            self.assertEqual(fitted.call_count, calls)
            self.assertIsNone(detector._curve_candidates[key].fresh_newest_at)

            self._originals(detector, points[len(initial) :], received_at=points[-1][0])
            evaluate(points[-1][0])
            fresh = detector._curve_candidates[key]
            self.assertEqual(fresh.candidate_id, first.candidate_id)
            self.assertEqual(fresh.features.reference, reference)
            self.assertEqual(fitted.call_args.kwargs["reference"], reference)
            self.assertEqual(fresh.fresh_newest_at, points[-1][0])
            status = detector._curve_status[key]
            onset = status["gates"]["peak_onset_in_context"]
            self.assertEqual(onset["operator"], ">=")
            self.assertEqual(onset["unit"], "s")
            self.assertIs(onset["result"], fresh.onset_at >= T0)
            self.assertEqual(onset["margin"], (fresh.onset_at - T0).total_seconds())

            changed_at = points[-1][0] + timedelta(seconds=detector.grid_seconds)
            self._originals(
                detector,
                [(changed_at, points[-1][1])],
                source="sensor.replaced_humidity",
            )
            evaluate(changed_at)
        self.assertNotIn(key, detector._curve_candidates)
        self.assertEqual(
            detector._curve_status[key]["source"], "sensor.replaced_humidity"
        )
        self.assertEqual(detector._curve_status[key]["status"], "insufficient")
        self.assertIsNone(detector._curve_status[key]["fresh_newest_at"])

    def test_recovery_background_ends_without_counting_an_infusion(self):
        detector = Detector(self.parameters, T0, (Position.UPPER,))
        points, shapes, _, _ = self.flow._scenario(merged=False)
        events = []
        confirmed = self.flow.begin + 2 * self.flow.width
        with self.flow._patch_math(shapes):
            self.flow._feed(detector, confirmed, points, events)
        self.assertEqual(len(self.flow._infusions(events)), 1)
        episode = detector._infusion_episode
        self.assertEqual(episode["phase"], "active")
        confirmed_at = episode["confirmed_at"]
        # Let the old mathematical window expire without a source interruption.
        start = self.flow._at(
            confirmed + self.p["curve_window_seconds"] + self.flow.step
        )
        recovery = self._form_points("recovery", start)
        initial = recovery[: -detector.curve_policy.min_side_samples]
        self._originals(detector, initial)
        floor = detector._curve_infusion_since

        def advance(now):
            trace = {"conditions": {}, "decisions": {}}
            ready = detector._advance_infusion_episode(
                detector.positions, now, now, floor, trace
            )
            # True is the caller's sole gate for emitting a new INFUSION.
            self.assertFalse(ready)
            return trace

        first = advance(initial[-1][0])
        self.assertEqual(episode["phase"], "active")
        self.assertFalse(first["decisions"]["infusion_background"]["result"])
        reference = episode["backgrounds"][Position.UPPER]["reference"]
        self.assertIsNotNone(reference)
        self._originals(detector, recovery[len(initial) :])
        fresh = advance(recovery[-1][0])
        self.assertFalse(fresh["conditions"]["infusion_level"])
        self.assertTrue(fresh["decisions"]["infusion_background"]["result"])
        later = fresh["decisions"]["infusion_background"]["gates"]["upper"]["gates"][
            "later_original"
        ]
        self.assertEqual(later["operator"], ">")
        self.assertEqual(later["unit"], "originals")
        self.assertIs(later["result"], later["value"] > later["threshold"])
        self.assertGreater(later["margin"], 0)
        self.assertEqual(episode["backgrounds"][Position.UPPER]["reference"], reference)
        self.assertEqual(episode["phase"], "ended")
        self.assertEqual(episode["end_basis"], "fixed_recovery_background")
        self.assertEqual(
            detector._infusion_segment_start[Position.UPPER], recovery[-1][0]
        )
        self.assertEqual(episode["confirmed_at"], confirmed_at)
        self.assertEqual(len(self.flow._infusions(events)), 1)

    def test_expired_background_reference_can_bind_to_new_originals(self):
        detector = Detector(self.parameters, T0, (Position.UPPER,))
        count = 2 * detector.curve_policy.min_side_samples
        points = [
            (T0 + timedelta(seconds=index * detector.grid_seconds), self.flow.baseline)
            for index in range(count)
        ]
        self._originals(detector, points)
        episode = {
            "positions": detector.positions,
            "backgrounds": {},
            "background_start": {},
        }

        def check(now):
            trace = {"decisions": {}}
            result = detector._infusion_background(episode, T0, now, trace)
            self.assertIs(result, trace["decisions"]["infusion_background"]["result"])
            return result

        with patch(f"{MATH}.curve_form", side_effect=self._background_form) as owner:
            self.assertFalse(check(points[-1][0]))
            first = episode["backgrounds"][Position.UPPER]["reference"]
            self.assertIsNotNone(first)
            calls = owner.call_count
            expired = points[-1][0] + timedelta(
                seconds=self.p["curve_window_seconds"] + detector.grid_seconds
            )
            self.assertFalse(check(expired))
            background = episode["backgrounds"][Position.UPPER]
            self.assertIsNone(background["reference"])
            self.assertIsNone(background["fresh_newest_at"])
            self.assertEqual(
                background["diagnostic"]["reason"],
                "recovery_reference_expired_or_context_changed",
            )
            self.assertEqual(owner.call_count, calls)

            rebound_start = expired + timedelta(seconds=detector.grid_seconds)
            rebound_points = [
                (
                    rebound_start + timedelta(seconds=index * detector.grid_seconds),
                    self.flow.baseline,
                )
                for index in range(count)
            ]
            self._originals(detector, rebound_points)
            rebound_at = rebound_points[-1][0]
            self.assertFalse(check(rebound_at))
            self.assertIsNone(owner.call_args.kwargs["reference"])
            rebound = episode["backgrounds"][Position.UPPER]["reference"]
            current = detector._curve_points(
                Position.UPPER, Quantity.HUMIDITY, rebound_at
            )
            self.assertEqual(rebound.start_at, current[0][0])
            self.assertGreater(rebound.start_at, first.start_at)
            self.assertEqual(rebound.initial_newest_at, rebound_at)

            fresh_at = rebound_at + timedelta(seconds=detector.grid_seconds)
            self._originals(detector, [(fresh_at, self.flow.baseline)])
            self.assertTrue(check(fresh_at))
            self.assertEqual(
                episode["backgrounds"][Position.UPPER]["reference"], rebound
            )

    def test_optional_background_correction_preserves_a_level_end(self):
        detector = Detector(self.parameters, T0, (Position.UPPER,))
        points, shapes, _, _ = self.flow._scenario(merged=False)
        events = []
        ended = (
            self.flow.begin
            + 2 * self.flow.width
            + self.p["infusion_window_seconds"]
            + self.flow.step
        )
        with (
            self.flow._patch_math(shapes),
            patch(
                f"{MATH}.curve_level",
                side_effect=self.flow._math_level,
            ),
            patch(f"{MATH}.curve_form", return_value=None),
        ):
            self.flow._feed(detector, ended, points, events)
        episode = detector._infusion_episode
        self.assertEqual(episode["phase"], "ended")
        self.assertEqual(episode["end_basis"], "decay_and_level")
        level_end = episode["level_end_at"]
        confirmed_at = episode["confirmed_at"]
        level = episode["levels"][Position.UPPER]["features"]
        background = episode["backgrounds"][Position.UPPER]
        self.assertIsNone(background["reference"])
        last = detector._curve_series[Position.UPPER, Quantity.HUMIDITY]["points"][-1]
        self.assertIn(last, background["original_points"])
        correction = last[1] + (
            detector_fixtures.trace(151)[1] - detector_fixtures.trace(150)[1]
        )
        # The independent level witness remains supported. The optional,
        # unbound background was never the cause of this episode's end.
        with (
            patch(f"{MATH}.curve_level", return_value=level),
            patch(
                f"{MATH}.curve_form",
            ) as owner,
        ):
            self._originals(
                detector,
                [(last[0], correction)],
                received_at=last[0] + timedelta(seconds=detector.grid_seconds),
            )
        owner.assert_not_called()
        self.assertEqual(episode["phase"], "ended")
        self.assertEqual(episode["end_basis"], "decay_and_level")
        self.assertEqual(episode["level_end_at"], level_end)
        self.assertEqual(episode["confirmed_at"], confirmed_at)
        self.assertNotIn(Position.UPPER, episode["backgrounds"])
        self.assertEqual(len(self.flow._infusions(events)), 1)

    def test_background_completion_requires_current_original_support(self):
        detector = Detector(self.parameters, T0)
        count = 2 * detector.curve_policy.min_side_samples
        first_at = T0 + timedelta(seconds=(count - 1) * detector.grid_seconds)
        humidity = {
            Position.UPPER: self.flow.baseline,
            Position.LOWER: self.flow.baseline / 2,
        }
        for position, value in humidity.items():
            self._originals(
                detector,
                [
                    (T0 + timedelta(seconds=index * detector.grid_seconds), value)
                    for index in range(count)
                ],
                received_at=first_at,
                position=position,
            )
        episode = {
            "positions": detector.positions,
            "backgrounds": {},
            "background_start": {},
        }
        evidence = {position: "supports" for position in detector.positions}

        def owner(points, **kwargs):
            position = (
                Position.UPPER
                if points[0][1] == humidity[Position.UPPER]
                else Position.LOWER
            )
            return self._background_form(points, evidence=evidence[position], **kwargs)

        def check(now):
            trace = {"decisions": {}}
            result = detector._infusion_background(episode, T0, now, trace)
            return result, trace["decisions"]["infusion_background"]

        step = timedelta(seconds=detector.grid_seconds)
        with patch(f"{MATH}.curve_form", side_effect=owner):
            self.assertFalse(check(first_at)[0])
            upper_at = first_at + step
            self._originals(detector, [(upper_at, humidity[Position.UPPER])])
            self.assertFalse(check(upper_at)[0])
            self.assertEqual(
                episode["backgrounds"][Position.UPPER]["fresh_newest_at"], upper_at
            )

            unresolved_at = upper_at + step
            evidence[Position.UPPER] = "unresolved"
            self._originals(detector, [(unresolved_at, humidity[Position.UPPER])])
            self.assertFalse(check(unresolved_at)[0])
            self.assertIsNone(episode["backgrounds"][Position.UPPER]["fresh_newest_at"])

            lower_at = unresolved_at + step
            self._originals(
                detector,
                [(lower_at, humidity[Position.LOWER])],
                position=Position.LOWER,
            )
            self.assertFalse(check(lower_at)[0])
            self.assertEqual(
                episode["backgrounds"][Position.LOWER]["fresh_newest_at"], lower_at
            )

            correction_at = lower_at + step
            evidence[Position.UPPER] = "supports"
            correction = (
                humidity[Position.UPPER]
                + detector_fixtures.trace(151)[1]
                - detector_fixtures.trace(150)[1]
            )
            self._originals(
                detector, [(unresolved_at, correction)], received_at=correction_at
            )
            complete, decision = check(correction_at)
            self.assertFalse(complete)
            self.assertFalse(decision["gates"]["new_original_at_this_check"]["result"])
            self.assertIsNone(episode["backgrounds"][Position.UPPER]["reference"])

            rebound_at = correction_at + step
            self._originals(detector, [(rebound_at, humidity[Position.UPPER])])
            self.assertFalse(check(rebound_at)[0])
            fresh_at = rebound_at + step
            self._originals(detector, [(fresh_at, humidity[Position.UPPER])])
            complete, decision = check(fresh_at)
            self.assertTrue(complete)
            self.assertTrue(decision["gates"]["new_original_at_this_check"]["result"])
