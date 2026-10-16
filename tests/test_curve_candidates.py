"""Originaleingänge, feste Kandidaten und Bestätigung durch neue Originale."""

from datetime import timedelta
from math import ceil
import unittest
from unittest.mock import patch

from test_detector import detection_parameters, measurement, sample, trace
from test_foundation import T0

from custom_components.ha_sauna.core.curve_change import (
    CurveFeatures,
    CurveTrendFeatures,
)
from custom_components.ha_sauna.core.detector import Detector
from custom_components.ha_sauna.core.models import Position, Quantity
from custom_components.ha_sauna.core.timeline import Kind


def at(second):
    return T0 + timedelta(seconds=second)


def supported_opening(
    samples,
    *,
    policy,
    onset_at=None,
    direction=None,
    onset_not_before=None,
):
    """Controlled math evidence for provenance contracts, never event proof."""
    if len(samples) < 2 * policy.min_side_samples or direction not in (None, -1):
        return None
    if onset_at is None:
        onset_at = next(
            (
                stamp
                for stamp, _ in samples[
                    policy.min_side_samples - 1 : len(samples) - policy.min_side_samples
                ]
                if onset_not_before is None or stamp >= onset_not_before
            ),
            None,
        )
    if onset_at is None or (
        onset_not_before is not None and onset_at < onset_not_before
    ):
        return None
    if onset_at not in {stamp for stamp, _ in samples}:
        return None
    before = sum(stamp <= onset_at for stamp, _ in samples)
    if min(before, len(samples) - before) < policy.min_side_samples:
        return None
    return CurveFeatures(
        evidence="supports",
        onset_at=onset_at,
        newest_at=samples[-1][0],
        before_count=before,
        after_count=len(samples) - before,
        noise_scale=1.0,
        slope_before=0.0,
        slope_after=-1.0,
        slope_change=-1.0,
        signed_strength=-1.0,
        smooth_score=1.0,
        change_score=0.0,
        score_gain=1.0,
        smooth_model="linear",
        after_trend=CurveTrendFeatures(
            direction=-1,
            slope=-1.0,
            noise_scale=1.0,
            level_score=1.0,
            trend_score=0.0,
            score_gain=1.0,
            exact_constant=False,
            exact_linear=False,
        ),
    )


def original_sample(detector, second, *, values=None, measured=None, source=None):
    values = trace(second) if values is None else values
    for quantity, value in zip(
        (Quantity.TEMPERATURE, Quantity.HUMIDITY), values, strict=True
    ):
        detector.accept(
            measurement(
                Position.UPPER,
                quantity,
                value,
                second,
                source=source,
                measured=measured,
            )
        )
    return detector.advance(at(second), enabled=True)


def candidate_statuses(detector):
    return detector.diagnostic["curve_candidates"].get("door_open", {}).get("upper", {})


class CurveCandidateTests(unittest.TestCase):
    def _detector(self, **kwargs):
        return Detector(detection_parameters(), T0, (Position.UPPER,), **kwargs)

    def _seed(self, detector, *, start=0, measured=True, source=None):
        count = 2 * detector.curve_policy.min_side_samples
        for index in range(count):
            second = start + index * detector.grid_seconds
            self.assertEqual(
                original_sample(
                    detector,
                    second,
                    values=trace(index),
                    measured=at(second) if measured else None,
                    source=source,
                ),
                [],
            )
            if (
                candidate_statuses(detector)
                .get(
                    Quantity.TEMPERATURE.value,
                    {},
                )
                .get("status")
                == "supported"
            ):
                # A reset may already contain its first original. Stop at the
                # first support, before a later original can confirm the event.
                return second
        return second

    def test_real_curve_confirms_the_fixed_original_beginning_with_new_evidence(self):
        observed = []
        detector = self._detector(observer=observed.append)
        events = []
        for second in range(90):
            events += sample(detector, second, *trace(second), detector.positions)

        openings = [event for event in events if event.kind == Kind.DOOR_OPEN]
        self.assertEqual(len(openings), 1)
        opening = openings[0]
        # The trace starts falling at 70. A bounded knot search need only
        # resolve that beginning to one search interval across its input span.
        # This limit comes from the catalogue before observing the fitted time.
        resolution = max(
            detector.grid_seconds,
            ceil(
                detector.p["curve_window_seconds"]
                / max(1, detector.curve_policy.max_candidates - 1)
            ),
        )
        self.assertLessEqual(
            abs((opening.effective_at - at(70)).total_seconds()),
            resolution,
        )
        self.assertLess(opening.effective_at, opening.detected_at)
        self.assertEqual(opening.detected_at, opening.trace_at)
        self.assertEqual(opening.channels, (Position.UPPER.value,))
        consumed = [
            proposal
            for row in observed
            if row["at"] == opening.trace_at
            for proposal in row["curve_proposals"]["door_open"]
            if proposal["status"] == "consumed"
        ]
        self.assertEqual(len(consumed), 1)
        proposal = consumed[0]
        self.assertEqual(opening.effective_at, proposal["consumed_onset_at"])
        self.assertEqual(opening.effective_at, min(proposal["onsets"]))
        self.assertLess(proposal["first_observed_at"], opening.detected_at)
        self.assertEqual(proposal["confirmed_at"], opening.detected_at)
        confirmations = {
            item["candidate_id"]: item for item in proposal["confirmation"]
        }
        for witness in proposal["witnesses"]:
            self.assertLessEqual(
                witness["first_observed_at"], proposal["first_observed_at"]
            )
            self.assertLessEqual(witness["newest_at"], proposal["first_observed_at"])
            self.assertIn(witness["onset_at"], {at(second) for second in range(90)})
            confirmation = confirmations[witness["candidate_id"]]
            self.assertTrue(confirmation["supported"])
            self.assertGreater(
                confirmation["fresh_newest_at"], witness["first_newest_at"]
            )
            self.assertLessEqual(confirmation["newest_at"], opening.trace_at)
            history = [
                previous
                for row in observed
                for previous in row["curve_candidates"]
                .get("door_open", {})
                .get("upper", {})
                .values()
                if previous.get("candidate_id") == witness["candidate_id"]
            ]
            self.assertEqual(
                {previous["onset_at"] for previous in history}, {witness["onset_at"]}
            )

    def test_ticks_repeated_reports_and_a_correction_do_not_add_original_evidence(self):
        detector = self._detector()
        with patch(
            "custom_components.ha_sauna.core.detector.curve_change",
            side_effect=supported_opening,
        ) as fitted:
            last = self._seed(detector)
            initial = dict(candidate_statuses(detector)[Quantity.TEMPERATURE.value])
            count = initial["before_count"] + initial["after_count"]
            self.assertEqual(initial["first_newest_at"], at(last))
            self.assertIsNone(initial["fresh_newest_at"])
            calls = fitted.call_count
            detector.advance(at(last + detector.grid_seconds), enabled=True)
            self.assertEqual(fitted.call_count, calls)

            repeated_at = last + 2 * detector.grid_seconds
            original_sample(
                detector, repeated_at, values=trace(last), measured=at(last)
            )
            self.assertEqual(fitted.call_count, calls)
            repeated = candidate_statuses(detector)[Quantity.TEMPERATURE.value]
            self.assertEqual(repeated["candidate_id"], initial["candidate_id"])
            self.assertEqual(repeated["newest_at"], at(last))
            self.assertIsNone(repeated["fresh_newest_at"])

            corrected = trace(71)[0]
            detector.accept(
                measurement(
                    Position.UPPER,
                    Quantity.TEMPERATURE,
                    corrected,
                    repeated_at + detector.grid_seconds,
                    measured=at(last),
                )
            )
            detector.advance(at(repeated_at + detector.grid_seconds), enabled=True)
            self.assertGreater(fitted.call_count, calls)
            current = candidate_statuses(detector)[Quantity.TEMPERATURE.value]
            self.assertEqual(current["before_count"] + current["after_count"], count)
            self.assertEqual(current["candidate_id"], initial["candidate_id"])
            self.assertEqual(current["first_observed_at"], initial["first_observed_at"])
            self.assertEqual(current["onset_at"], initial["onset_at"])
            self.assertIsNone(current["fresh_newest_at"])
            self.assertFalse(
                detector._curve_confirmation(
                    "door_open",
                    detector.positions,
                    (Quantity.TEMPERATURE, Quantity.HUMIDITY),
                    floor=T0,
                )
            )
            points = fitted.call_args.args[0]
            self.assertEqual(len(points), len({stamp for stamp, _ in points}))
            self.assertEqual(points[-1], (at(last), corrected))
            self.assertEqual(fitted.call_args.kwargs["onset_at"], initial["onset_at"])
            self.assertEqual(fitted.call_args.kwargs["direction"], -1)
            self.assertEqual(fitted.call_args.kwargs["onset_not_before"], at(0))

    def test_source_time_basis_missing_value_and_gap_retire_the_previous_candidate(
        self,
    ):
        for boundary in ("source", "time_basis", "missing", "gap"):
            with (
                self.subTest(boundary=boundary),
                patch(
                    "custom_components.ha_sauna.core.detector.curve_change",
                    side_effect=supported_opening,
                ),
            ):
                detector = self._detector()
                last = self._seed(detector)
                previous = dict(
                    candidate_statuses(detector)[Quantity.TEMPERATURE.value]
                )
                quantities = (Quantity.TEMPERATURE, Quantity.HUMIDITY)
                key = ("door_open", detector.positions, quantities)
                detector._remember_curve_proposal(
                    "door_open",
                    detector.positions,
                    quantities,
                    floor=T0,
                    context=(
                        detector._curve_door_context,
                        detector.door_episode["started_at"],
                        T0,
                    ),
                    observed_at=at(last),
                )
                self.assertEqual(
                    detector._curve_proposal_status[key]["status"], "available"
                )
                reset = last + detector.grid_seconds
                if boundary == "gap":
                    reset += detector.p["sensor_timeout_seconds"]
                source = "sensor.replaced" if boundary == "source" else None
                measured = boundary != "time_basis"
                original_sample(
                    detector,
                    reset,
                    values=(None, trace(0)[1]) if boundary == "missing" else trace(0),
                    source=source,
                    measured=at(reset) if measured else None,
                )
                self.assertTrue(
                    all(
                        status.get("candidate_id") != previous["candidate_id"]
                        for status in candidate_statuses(detector).values()
                    )
                )
                self.assertNotIn(key, detector._curve_proposals)
                self.assertEqual(
                    detector._curve_proposal_status[key]["status"], "invalid"
                )

                self._seed(
                    detector,
                    start=reset + detector.grid_seconds,
                    measured=measured,
                    source=source,
                )
                current = candidate_statuses(detector)[Quantity.TEMPERATURE.value]
                self.assertEqual(current["status"], "supported")
                self.assertNotEqual(current["candidate_id"], previous["candidate_id"])
                self.assertGreaterEqual(current["onset_at"], at(reset))
                self.assertGreater(
                    current["first_observed_at"], previous["first_observed_at"]
                )
                self.assertEqual(
                    current["time_basis"], "measured" if measured else "received"
                )

    def test_catchup_records_actual_decision_time_as_first_observation(self):
        detector = self._detector()
        count = 2 * detector.curve_policy.min_side_samples + 1
        for second in range(count):
            for quantity, value in zip(
                (Quantity.TEMPERATURE, Quantity.HUMIDITY), trace(second), strict=True
            ):
                detector.accept(measurement(Position.UPPER, quantity, value, second))
        decision_at = at(count + detector.grid_seconds)
        with patch(
            "custom_components.ha_sauna.core.detector.curve_change",
            side_effect=supported_opening,
        ):
            # Stop at the first supported package: later pending originals must
            # not be mistaken for evidence already observed at that first tick.
            self.assertEqual(
                detector.advance(
                    at(count - 2),
                    enabled=True,
                    detected_at=decision_at,
                ),
                [],
            )
        statuses = candidate_statuses(detector)
        self.assertTrue(statuses)
        for status in statuses.values():
            self.assertEqual(status["first_observed_at"], decision_at)
            self.assertLess(status["onset_at"], status["first_observed_at"])
            self.assertLessEqual(status["newest_at"], detector.diagnostic["at"])

    def test_heated_temperature_route_owns_its_start_without_a_mixed_humidity_claim(
        self,
    ):
        selected = detection_parameters()
        observed = []
        detector = Detector(selected, T0, (Position.UPPER,), observer=observed.append)

        def temperature_only(
            points,
            *,
            policy,
            onset_at=None,
            direction=None,
            onset_not_before=None,
        ):
            # Reuse the supplied fixture channels in opposite input roles:
            # its large RH fall then precedes the shared minimum T loss.
            onset_at = onset_at or at(70)
            if (
                not points
                or points[0][1] != trace(0)[1]
                or onset_at not in {stamp for stamp, _ in points}
                or sum(stamp > onset_at for stamp, _ in points)
                < policy.min_side_samples
            ):
                return None
            return supported_opening(
                points,
                policy=policy,
                onset_at=onset_at,
                direction=direction,
                onset_not_before=onset_not_before,
            )

        events = []
        with patch(
            "custom_components.ha_sauna.core.detector.curve_change",
            side_effect=temperature_only,
        ):
            for second in range(90):
                now = at(second)
                detector.report_heating(True, now)
                events += sample(
                    detector, second, *reversed(trace(second)), detector.positions
                )
        openings = [event for event in events if event.kind == Kind.DOOR_OPEN]
        self.assertEqual(len(openings), 1)
        opening = openings[0]
        row = next(row for row in observed if row["at"] == opening.trace_at)
        self.assertTrue(row["conditions"]["door_heating"])
        self.assertFalse(row["conditions"]["door_open"])
        candidates = row["curve_candidates"]["door_open"]["upper"]
        self.assertEqual(candidates[Quantity.TEMPERATURE.value]["status"], "supported")
        self.assertEqual(candidates[Quantity.TEMPERATURE.value]["onset_at"], at(70))
        self.assertNotEqual(candidates[Quantity.HUMIDITY.value]["status"], "supported")
        self.assertTrue(
            any(
                proposal["status"] == "available"
                and proposal["quantities"] == ("temperature",)
                for previous in observed
                if previous["at"] < opening.trace_at
                for proposal in previous["curve_proposals"].get("door_open_heating", ())
            )
        )
        consumed = [
            proposal
            for proposal in row["curve_proposals"].get("door_open_heating", ())
            if proposal["status"] == "consumed"
        ]
        self.assertEqual(len(consumed), 1)
        self.assertEqual(consumed[0]["quantities"], ("temperature",))
        self.assertTrue(all(item["supported"] for item in consumed[0]["confirmation"]))
        self.assertEqual(opening.effective_at, at(70))

        # A heater starting after the observed fall cannot make the earlier,
        # unheated original segment into a temperature-only heating witness.
        late = Detector(selected, T0, detector.positions)
        late_events = []
        with patch(
            "custom_components.ha_sauna.core.detector.curve_change",
            side_effect=temperature_only,
        ):
            for second in range(90):
                if second >= 71:
                    late.report_heating(True, at(second))
                late_events += sample(
                    late, second, *reversed(trace(second)), late.positions
                )
        self.assertFalse(any(event.kind == Kind.DOOR_OPEN for event in late_events))
