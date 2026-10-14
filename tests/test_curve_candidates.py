"""Originaleingänge, Kandidatenherkunft und bestehende Ereignisbestätigung."""

from datetime import timedelta
import unittest
from unittest.mock import patch

from test_detector import detection_parameters, measurement, sample, trace
from test_foundation import T0

from custom_components.ha_sauna.core.curve_change import CurveFeatures
from custom_components.ha_sauna.core.detector import Detector
from custom_components.ha_sauna.core.models import Position, Quantity
from custom_components.ha_sauna.core.timeline import Kind


def at(second):
    return T0 + timedelta(seconds=second)


def supported_opening(
    samples, *, policy, onset_at=None, direction=None, onset_not_before=None,
):
    """Controlled math evidence for provenance contracts, never event proof."""
    if len(samples) < 2 * policy.min_side_samples or direction not in (None, -1):
        return None
    if onset_at is None:
        onset_at = next((
            stamp for stamp, _ in samples[policy.min_side_samples - 1:len(samples) - policy.min_side_samples]
            if onset_not_before is None or stamp >= onset_not_before
        ), None)
    if onset_at is None or (onset_not_before is not None and onset_at < onset_not_before):
        return None
    before = sum(stamp <= onset_at for stamp, _ in samples)
    return CurveFeatures(
        evidence="supports", onset_at=onset_at, newest_at=samples[-1][0],
        before_count=before, after_count=len(samples) - before,
        noise_scale=1.0, slope_before=0.0, slope_after=-1.0,
        slope_change=-1.0, signed_strength=-1.0,
        smooth_score=1.0, change_score=0.0, score_gain=1.0,
        smooth_model="linear",
    )


def original_sample(detector, second, *, values=None, measured=None, source=None):
    values = trace(second) if values is None else values
    for quantity, value in zip((Quantity.TEMPERATURE, Quantity.HUMIDITY), values, strict=True):
        detector.accept(measurement(
            Position.UPPER, quantity, value, second, source=source, measured=measured,
        ))
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
            self.assertEqual(original_sample(
                detector, second, values=trace(index),
                measured=at(second) if measured else None, source=source,
            ), [])
        return second

    def test_real_curve_candidate_moves_the_beginning_without_moving_confirmation(self):
        observed = []
        detector = self._detector(observer=observed.append)
        events = []
        for second in range(90):
            events += sample(detector, second, *trace(second), detector.positions)

        reference = self._detector()
        baseline = []
        with patch("custom_components.ha_sauna.core.detector.curve_change", return_value=None):
            for second in range(90):
                baseline += sample(reference, second, *trace(second), reference.positions)

        self.assertEqual(
            [(event.kind, event.detected_at, event.trace_at, event.channels) for event in events],
            [(event.kind, event.detected_at, event.trace_at, event.channels) for event in baseline],
        )
        openings = [event for event in events if event.kind == Kind.DOOR_OPEN]
        self.assertEqual(len(openings), 1)
        opening = openings[0]
        old_opening = next(event for event in baseline if event.kind == Kind.DOOR_OPEN)
        self.assertLess(opening.effective_at, old_opening.effective_at)
        confirmed = [
            status
            for row in observed if row["at"] == opening.trace_at
            for status in row["curve_candidates"]["door_open"]["upper"].values()
            if status["status"] == "confirmed"
        ]
        self.assertTrue(confirmed)
        self.assertEqual(opening.effective_at, min(status["onset_at"] for status in confirmed))
        for status in confirmed:
            self.assertLess(status["first_observed_at"], opening.detected_at)
            self.assertEqual(status["confirmed_at"], opening.detected_at)
            history = [
                previous
                for row in observed
                for previous in row["curve_candidates"].get("door_open", {}).get("upper", {}).values()
                if previous.get("candidate_id") == status["candidate_id"]
            ]
            self.assertEqual({previous["onset_at"] for previous in history}, {status["onset_at"]})

    def test_ticks_repeated_reports_and_a_correction_do_not_add_original_evidence(self):
        detector = self._detector()
        with patch(
            "custom_components.ha_sauna.core.detector.curve_change", side_effect=supported_opening,
        ) as fitted:
            last = self._seed(detector)
            initial = dict(candidate_statuses(detector)[Quantity.TEMPERATURE.value])
            count = initial["before_count"] + initial["after_count"]
            calls = fitted.call_count
            detector.advance(at(last + detector.grid_seconds), enabled=True)
            self.assertEqual(fitted.call_count, calls)

            repeated_at = last + 2 * detector.grid_seconds
            original_sample(detector, repeated_at, values=trace(last), measured=at(last))
            self.assertEqual(fitted.call_count, calls)
            repeated = candidate_statuses(detector)[Quantity.TEMPERATURE.value]
            self.assertEqual(repeated["candidate_id"], initial["candidate_id"])
            self.assertEqual(repeated["newest_at"], at(last))

            corrected = trace(71)[0]
            detector.accept(measurement(
                Position.UPPER, Quantity.TEMPERATURE, corrected,
                repeated_at + detector.grid_seconds, measured=at(last),
            ))
            detector.advance(at(repeated_at + detector.grid_seconds), enabled=True)
            self.assertGreater(fitted.call_count, calls)
            current = candidate_statuses(detector)[Quantity.TEMPERATURE.value]
            self.assertEqual(current["before_count"] + current["after_count"], count)
            self.assertEqual(current["candidate_id"], initial["candidate_id"])
            self.assertEqual(current["first_observed_at"], initial["first_observed_at"])
            self.assertEqual(current["onset_at"], initial["onset_at"])
            points = fitted.call_args.args[0]
            self.assertEqual(len(points), len({stamp for stamp, _ in points}))
            self.assertEqual(points[-1], (at(last), corrected))
            self.assertEqual(fitted.call_args.kwargs["onset_at"], initial["onset_at"])
            self.assertEqual(fitted.call_args.kwargs["direction"], -1)
            self.assertEqual(fitted.call_args.kwargs["onset_not_before"], at(0))

    def test_source_time_basis_missing_value_and_gap_retire_the_previous_candidate(self):
        for boundary in ("source", "time_basis", "missing", "gap"):
            with self.subTest(boundary=boundary), patch(
                "custom_components.ha_sauna.core.detector.curve_change", side_effect=supported_opening,
            ):
                detector = self._detector()
                last = self._seed(detector)
                previous = dict(candidate_statuses(detector)[Quantity.TEMPERATURE.value])
                reset = last + detector.grid_seconds
                if boundary == "gap":
                    reset += detector.p["sensor_timeout_seconds"]
                source = "sensor.replaced" if boundary == "source" else None
                measured = boundary != "time_basis"
                original_sample(
                    detector, reset,
                    values=(None, trace(0)[1]) if boundary == "missing" else trace(0),
                    source=source, measured=at(reset) if measured else None,
                )
                self.assertTrue(all(
                    status.get("candidate_id") != previous["candidate_id"]
                    for status in candidate_statuses(detector).values()
                ))

                self._seed(detector, start=reset + detector.grid_seconds, measured=measured, source=source)
                current = candidate_statuses(detector)[Quantity.TEMPERATURE.value]
                self.assertEqual(current["status"], "supported")
                self.assertNotEqual(current["candidate_id"], previous["candidate_id"])
                self.assertGreaterEqual(current["onset_at"], at(reset))
                self.assertGreater(current["first_observed_at"], previous["first_observed_at"])
                self.assertEqual(current["time_basis"], "measured" if measured else "received")

    def test_catchup_records_actual_decision_time_as_first_observation(self):
        detector = self._detector()
        count = 2 * detector.curve_policy.min_side_samples + 1
        for second in range(count):
            for quantity, value in zip((Quantity.TEMPERATURE, Quantity.HUMIDITY), trace(second), strict=True):
                detector.accept(measurement(Position.UPPER, quantity, value, second))
        decision_at = at(count + detector.grid_seconds)
        with patch(
            "custom_components.ha_sauna.core.detector.curve_change", side_effect=supported_opening,
        ):
            self.assertEqual(detector.advance(at(count - 1), enabled=True, detected_at=decision_at), [])
        statuses = candidate_statuses(detector)
        self.assertTrue(statuses)
        for status in statuses.values():
            self.assertEqual(status["first_observed_at"], decision_at)
            self.assertLess(status["onset_at"], status["first_observed_at"])
            self.assertLessEqual(status["newest_at"], detector.diagnostic["at"])

    def test_simultaneous_opening_routes_cannot_attribute_a_temperature_only_mixed_start(self):
        values = detection_parameters().values
        selected = detection_parameters(door_heating_hold_seconds=values["door_open_hold_seconds"])
        observed = []
        detector = Detector(selected, T0, (Position.UPPER,), observer=observed.append)
        reference = Detector(selected, T0, detector.positions)
        toggles = {}
        sustain = detector._sustain

        def observed_sustain(name, *args, **kwargs):
            result = sustain(name, *args, **kwargs)
            if name in ("door", "door_heating"):
                toggles.setdefault(detector.index, {})[name] = result
            return result

        def temperature_only(
            points, *, policy, onset_at=None, direction=None, onset_not_before=None,
        ):
            # Reuse the supplied fixture channels in opposite input roles:
            # its large RH fall then precedes the shared minimum T loss.
            onset_at = onset_at or at(70)
            if (
                not points or points[0][1] != trace(0)[1]
                or onset_at not in {stamp for stamp, _ in points}
                or sum(stamp > onset_at for stamp, _ in points) < policy.min_side_samples
            ):
                return None
            return supported_opening(
                points, policy=policy, onset_at=onset_at,
                direction=direction, onset_not_before=onset_not_before,
            )

        events, baseline = [], []
        with (
            patch.object(detector, "_sustain", side_effect=observed_sustain),
            patch("custom_components.ha_sauna.core.detector.curve_change", side_effect=temperature_only),
        ):
            for second in range(90):
                now = at(second)
                detector.report_heating(True, now)
                events += sample(detector, second, *reversed(trace(second)), detector.positions)
        with patch("custom_components.ha_sauna.core.detector.curve_change", return_value=None):
            for second in range(90):
                reference.report_heating(True, at(second))
                baseline += sample(reference, second, *reversed(trace(second)), reference.positions)

        openings = [event for event in events if event.kind == Kind.DOOR_OPEN]
        self.assertEqual(len(openings), 1)
        opening = openings[0]
        index = int((opening.trace_at - T0).total_seconds() / detector.grid_seconds)
        self.assertEqual(toggles[index], {"door": True, "door_heating": True})
        row = next(row for row in observed if row["at"] == opening.trace_at)
        candidates = row["curve_candidates"]["door_open"]["upper"]
        self.assertEqual(candidates[Quantity.TEMPERATURE.value]["status"], "supported")
        self.assertEqual(candidates[Quantity.TEMPERATURE.value]["onset_at"], at(70))
        self.assertNotEqual(candidates[Quantity.HUMIDITY.value]["status"], "supported")
        self.assertEqual(events, baseline)
        self.assertGreater(opening.effective_at, at(70))
