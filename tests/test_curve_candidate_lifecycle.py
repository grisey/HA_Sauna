"""Historische Vollsätze: ein R04-Nachweis und isolierte Zustandsverträge."""

from dataclasses import replace
from datetime import datetime, timedelta
import gzip
import json
from math import ceil
from pathlib import Path
from time import perf_counter
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from test_curve_candidates import at, original_sample, supported_opening
from test_detector import detection_parameters, measurement, trace
from test_foundation import T0
from test_settings import fixture

from custom_components.ha_sauna.core.detector import Detector
from custom_components.ha_sauna.core.models import Position, Quantity
from custom_components.ha_sauna.core.timeline import Kind


QUANTITIES = (Quantity.TEMPERATURE, Quantity.HUMIDITY)


def local_features(samples, *, policy, onset_at=None, direction=None, onset_not_before=None, target=None):
    """Controlled local evidence; the R04 method below uses real mathematics."""
    onset_at = onset_at or target
    result = supported_opening(
        samples, policy=policy, onset_at=onset_at,
        direction=-1, onset_not_before=onset_not_before,
    )
    if result is None or min(result.before_count, result.after_count) < policy.min_side_samples:
        return None
    if direction == 1:
        return replace(result, slope_before=-2.0, slope_after=-1.0,
                       slope_change=1.0, signed_strength=1.0)
    return result


class CurveCandidateLifecycleTests(unittest.TestCase):
    def _detector(self):
        return Detector(detection_parameters(), T0, (Position.UPPER,))

    def _remember(self, detector, route="door_open"):
        now = at(detector.index * detector.grid_seconds)
        context = (detector._curve_door_context, T0)
        floor = T0
        if route == "door_close":
            self.assertTrue(detector.open)
            floor = detector._curve_open_effective_at or detector.opened_at or detector.origin
            if detector._curve_ventilation_at is not None:
                floor = max(floor, detector._curve_ventilation_at)
            context = (detector._curve_door_context, detector.door_episode["started_at"], floor)
        detector._remember_curve_proposal(
            route, detector.positions, QUANTITIES, floor=floor,
            context=context, observed_at=now,
        )
        return (route, detector.positions, QUANTITIES)

    def _feed_until(self, detector, last, *, route="door_open"):
        while (detector.index + 1) * detector.grid_seconds <= last:
            second = (detector.index + 1) * detector.grid_seconds
            self.assertEqual(original_sample(detector, second, measured=at(second)), [])
            self._remember(detector, route)

    def test_r04_second_close_consumes_the_complete_previously_observed_package(self):
        started = perf_counter()
        zone = ZoneInfo("Europe/Berlin")
        reference = datetime(2000, 1, 1, 16, 2, 40, tzinfo=zone)
        opening_reference = datetime(2000, 1, 1, 16, 2, 15, tzinfo=zone)
        confirmation = datetime(2000, 1, 1, 16, 3, 19, tzinfo=zone)
        parameters = detection_parameters()
        values = parameters.values
        warmup = max(
            values["curve_window_seconds"],
            values["vent_baseline_seconds"] + max(values["door_window_seconds"], values["door_humidity_seconds"]),
        )
        begin = opening_reference - timedelta(seconds=warmup)
        origin = datetime.fromisoformat(fixture("session")["origin"])
        inputs = []
        path = Path(__file__).parent / "fixtures/replay/R04.samples.jsonl.gz"
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                received = origin + timedelta(seconds=row["t"])
                if received > confirmation:
                    break
                if received < begin or row["role"] == "presence":
                    continue
                position, quantity = row["role"].split("_", 1)
                inputs.append(replace(
                    measurement(Position(position), Quantity(quantity), row["value"], 0,
                                source=f"sensor.fixture_{row['role']}"),
                    received_at=received,
                ))
        observed = []
        detector = Detector(parameters, begin, observer=observed.append)
        cursor = 0
        events = []
        now = begin
        while now <= confirmation:
            while cursor < len(inputs) and inputs[cursor].received_at <= now:
                self.assertTrue(detector.accept(inputs[cursor]))
                cursor += 1
            events.extend(detector.advance(now, enabled=True))
            now += timedelta(seconds=detector.grid_seconds)
        closes = [event for event in events if event.kind == Kind.DOOR_CLOSE]
        self.assertEqual(len(closes), 1)
        close = closes[0]
        self.assertEqual((close.trace_at, close.detected_at), (confirmation, confirmation))
        self.assertLessEqual(abs((close.effective_at - reference).total_seconds()), 10)
        consumed = [
            proposal for row in observed if row["at"] == confirmation
            for proposal in row["curve_proposals"].get("door_close", ())
            if proposal["status"] == "consumed"
        ]
        self.assertEqual(len(consumed), 1)
        package = consumed[0]
        self.assertEqual(package["positions"], ("upper", "lower"))
        self.assertEqual(package["quantities"], ("temperature", "humidity"))
        self.assertEqual(len(package["witnesses"]), 4)
        self.assertEqual(package["first_observed_at"], datetime(2000, 1, 1, 16, 2, 51, tzinfo=zone))
        self.assertEqual(package["confirmed_at"], confirmation)
        self.assertEqual(package["consumed_onset_at"], close.effective_at)
        before = [
            proposal for row in observed if row["at"] < confirmation
            for proposal in row["curve_proposals"].get("door_close", ())
            if proposal["proposal_id"] == package["proposal_id"] and proposal["status"] == "available"
        ]
        self.assertTrue(before)
        self.assertEqual(before[0]["first_observed_at"], package["first_observed_at"])
        expected = {
            ("upper", "temperature"): datetime(2000, 1, 1, 16, 2, 40, 955857, tzinfo=zone),
            ("upper", "humidity"): datetime(2000, 1, 1, 16, 2, 37, 824282, tzinfo=zone),
            ("lower", "temperature"): datetime(2000, 1, 1, 16, 2, 35, 759028, tzinfo=zone),
            ("lower", "humidity"): datetime(2000, 1, 1, 16, 2, 34, 689834, tzinfo=zone),
        }
        for witness in package["witnesses"]:
            key = (witness["position"], witness["quantity"])
            self.assertEqual(witness["onset_at"], expected[key])
            self.assertLessEqual(witness["first_observed_at"], package["first_observed_at"])
            self.assertLessEqual(witness["newest_at"], package["first_observed_at"])
            self.assertLessEqual(witness["point_count"], values["curve_max_points"])
            self.assertTrue(any(
                item.position.value == key[0] and item.quantity.value == key[1]
                and item.source == witness["source"] and item.received_at == witness["onset_at"]
                for item in inputs
            ))
        print(f"R04 lifecycle slice: samples={cursor} ticks={len(observed)} wall_seconds={perf_counter() - started:.6f}")

    def test_window_expiry_preserves_fixed_support_until_an_accepted_correction_rejects_it(self):
        detector = self._detector()
        last = (2 * detector.curve_policy.min_side_samples - 1) * detector.grid_seconds
        with patch("custom_components.ha_sauna.core.detector.curve_change", side_effect=local_features) as fitted:
            self._feed_until(detector, last)
            key = self._remember(detector)
            package = dict(detector._curve_proposal_status[key])
            witness = detector._curve_proposals[key].candidates[0][1]
            originals = witness.original_points
            calls = fitted.call_count
            later = last + detector.p["curve_window_seconds"] + detector.grid_seconds
            self.assertLess(later - last, detector.p["sensor_timeout_seconds"])
            self.assertEqual(detector.advance(at(later), enabled=True), [])
            self.assertEqual(fitted.call_count, calls)
            self.assertEqual(detector._curve_proposal_status[key], package)
            self.assertEqual(detector._curve_proposals[key].candidates[0][1].original_points, originals)
            self.assertLess(witness.onset_at, at(later - detector.p["curve_window_seconds"]))
        corrected = trace(71)[0]
        with patch("custom_components.ha_sauna.core.detector.curve_change", return_value=None) as checked:
            detector.accept(measurement(Position.UPPER, Quantity.TEMPERATURE, corrected,
                                        later + detector.grid_seconds, measured=at(last)))
            detector.advance(at(later + detector.grid_seconds), enabled=True)
        fixed = [call for call in checked.call_args_list if call.kwargs["onset_at"] == witness.onset_at]
        self.assertEqual(len(fixed), 1)
        self.assertEqual(tuple(fixed[0].args[0]), tuple(
            (stamp, corrected if stamp == at(last) else value) for stamp, value in originals
        ))
        self.assertEqual(fixed[0].kwargs["policy"], detector.curve_policy)
        self.assertNotIn(key, detector._curve_proposals)
        self.assertEqual(detector._curve_proposal_status[key]["reason"], "original_corrected")

    def test_compatible_packages_do_not_drift_and_complete_competition_stays_ambiguous(self):
        detector = self._detector()
        grid = detector.grid_seconds
        side = detector.curve_policy.min_side_samples
        window = max(detector.p["door_window_seconds"], detector.p["door_humidity_seconds"])
        shift = (ceil(window / (2 * grid)) + 1) * grid
        original = (side - 1) * grid
        target = [at(original)]

        def evidence(samples, **kwargs):
            result = local_features(samples, target=target[0], **kwargs)
            if result is not None and kwargs["onset_at"] not in (None, target[0]):
                return replace(result, evidence="rejects")
            return result

        with patch("custom_components.ha_sauna.core.detector.curve_change", side_effect=evidence):
            self._feed_until(detector, (2 * side - 1) * grid)
            key = self._remember(detector)
            first = dict(detector._curve_proposal_status[key])
            target[0] = at(original + shift)
            self._feed_until(detector, original + shift + side * grid)
            self.assertEqual(detector._curve_proposal_status[key], first)
            target[0] = at(original + 2 * shift)
            self._feed_until(detector, original + 2 * shift + side * grid)
            ambiguous = dict(detector._curve_proposal_status[key])
            self.assertEqual(ambiguous["status"], "ambiguous")
            self.assertEqual(ambiguous["proposal_id"], first["proposal_id"])
            self.assertEqual(ambiguous["onsets"], first["onsets"])
            target[0] = at(original)
            self._feed_until(detector, detector.index * grid + 2 * grid)
            self.assertEqual(detector._curve_proposal_status[key], ambiguous)
        fallback = at(detector.index * grid)
        self.assertEqual(detector._curve_beginning(
            "door_open", detector.positions, QUANTITIES, fallback,
            floor=T0, kind=Kind.DOOR_OPEN, now=fallback, decision_at=fallback,
        ), fallback)

    def test_only_simultaneous_complete_support_can_bind_within_owner_and_ventilation_bounds(self):
        detector = self._detector()
        allowed = [{Quantity.TEMPERATURE}]
        events = []
        with patch("custom_components.ha_sauna.core.detector.curve_change", return_value=None):
            for second in range(90):
                events.extend(original_sample(detector, second, measured=at(second)))
        self.assertEqual(sum(event.kind == Kind.DOOR_OPEN for event in events), 1)
        self.assertTrue(detector.open)

        def evidence(samples, **kwargs):
            quantity = (Quantity.TEMPERATURE if samples and samples[0][1] == trace(0)[0]
                        else Quantity.HUMIDITY)
            return local_features(samples, **kwargs) if quantity in allowed[0] else None

        with patch("custom_components.ha_sauna.core.detector.curve_change", side_effect=evidence):
            self._feed_until(detector, detector.index * detector.grid_seconds + detector.grid_seconds, route="door_close")
            key = ("door_close", detector.positions, QUANTITIES)
            self.assertNotIn(key, detector._curve_proposals)
            allowed[0] = {Quantity.HUMIDITY}
            self._feed_until(detector, detector.index * detector.grid_seconds + detector.grid_seconds, route="door_close")
            self.assertNotIn(key, detector._curve_proposals)
            allowed[0] = set(QUANTITIES)
            self._feed_until(detector, detector.index * detector.grid_seconds + detector.grid_seconds, route="door_close")
        package = detector._curve_proposal_status[key]
        now = at(detector.index * detector.grid_seconds)
        self.assertEqual(package["first_observed_at"], now)
        self.assertEqual(package["status"], "available")
        self.assertEqual(detector._curve_beginning(
            "door_close", detector.positions, QUANTITIES, now,
            floor=package["onset_at"] + timedelta(seconds=detector.grid_seconds),
            kind=Kind.DOOR_CLOSE, now=now, decision_at=now,
            boundary_reason="ventilation_after_onset",
        ), now)
        self.assertEqual(detector._curve_proposal_status[key]["reason"], "ventilation_after_onset")
        self._remember(detector, "door_close")
        detector._clear_curve_route("door_close", reason="owner_context")
        self.assertNotIn(key, detector._curve_proposals)
        self.assertEqual(detector._curve_proposal_status[key]["reason"], "owner_context")

    def test_earlier_local_infusion_support_cannot_backdate_a_later_confirmation(self):
        selected = detection_parameters()
        old_onset = at(180)
        observed = []
        detector = Detector(selected, T0, (Position.UPPER,), observer=observed.append)

        def infusion_evidence(samples, **kwargs):
            # The unmodified fixture has no qualifying RH jump at 200.
            # Its real +3 RH flank at 240 drives the later sustain/emit.
            if kwargs["direction"] != 1 or not samples or samples[-1][0] != at(200):
                return None
            result = local_features(samples, target=old_onset, **kwargs)
            return None if result is None else replace(result, slope_before=0.0, slope_after=1.0)

        actual = []
        with patch("custom_components.ha_sauna.core.detector.curve_change", side_effect=infusion_evidence):
            for second in range(261):
                actual.extend(original_sample(detector, second))
        reference = Detector(selected, T0, (Position.UPPER,))
        baseline = []
        with patch("custom_components.ha_sauna.core.detector.curve_change", return_value=None):
            for second in range(261):
                baseline.extend(original_sample(reference, second))
        earlier = next(row for row in observed if row["at"] == at(200))
        local = earlier["curve_candidates"]["infusion"]["upper"]["humidity"]
        self.assertEqual(local["status"], "supported")
        self.assertEqual(local["onset_at"], old_onset)
        self.assertFalse(any(
            proposal["status"] in ("available", "consumed")
            for proposal in earlier["curve_proposals"].get("infusion", ())
        ))
        infusions = [event for event in actual if event.kind == Kind.INFUSION]
        self.assertEqual(len(infusions), 1)
        self.assertGreaterEqual(infusions[0].trace_at, at(240))
        self.assertEqual(infusions[0].effective_at, infusions[0].trace_at)
        self.assertEqual(
            [(event.kind, event.trace_at, event.detected_at) for event in actual],
            [(event.kind, event.trace_at, event.detected_at) for event in baseline],
        )
