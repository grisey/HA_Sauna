"""Historische Vorschläge, frische Formbestätigung und isolierte Schutzregeln."""

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

from custom_components.ha_sauna.core.curve_change import CurveTrendFeatures
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
        floor = detector._curve_closed_since
        context = (
            detector._curve_door_context, detector.door_episode["started_at"], floor,
        )
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
        # These tests isolate proposal retention and invalidation. The separate
        # fresh-original test below leaves the real confirmation path enabled.
        with patch.object(detector, "_curve_confirmation", return_value=False):
            while (detector.index + 1) * detector.grid_seconds <= last:
                second = (detector.index + 1) * detector.grid_seconds
                self.assertEqual(original_sample(detector, second, measured=at(second)), [])
                if detector.door_episode is not None:
                    self._remember(detector, route)

    def test_r04_short_close_confirms_the_physical_recovery_before_the_old_trigger(self):
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
        openings = [event for event in events if event.kind == Kind.DOOR_OPEN]
        self.assertEqual(len(openings), 1, msg=f"R04 opening evidence missing: {events!r}")
        self.assertEqual(len(closes), 1)
        close = closes[0]
        self.assertLess(close.detected_at, confirmation)
        self.assertEqual(close.trace_at, close.detected_at)
        self.assertLess(openings[0].trace_at, close.trace_at)
        self.assertLessEqual(abs((close.effective_at - reference).total_seconds()), 10)
        consumed = [
            proposal for row in observed if row["at"] == close.trace_at
            for proposal in row["curve_proposals"].get("door_close", ())
            if proposal["status"] == "consumed"
        ]
        self.assertEqual(len(consumed), 1)
        package = consumed[0]
        self.assertEqual(package["positions"], ("upper", "lower"))
        self.assertEqual(package["quantities"], ("temperature", "humidity"))
        self.assertEqual(len(package["witnesses"]), 4)
        self.assertLess(package["first_observed_at"], close.detected_at)
        self.assertEqual(package["confirmed_at"], close.detected_at)
        self.assertEqual(package["consumed_onset_at"], close.effective_at)
        before = [
            proposal for row in observed if row["at"] < close.trace_at
            for proposal in row["curve_proposals"].get("door_close", ())
            if proposal["proposal_id"] == package["proposal_id"] and proposal["status"] == "available"
        ]
        self.assertTrue(before)
        self.assertEqual(before[0]["first_observed_at"], package["first_observed_at"])
        confirmations = {
            item["candidate_id"]: item for item in package["confirmation"]
        }
        for witness in package["witnesses"]:
            key = (witness["position"], witness["quantity"])
            self.assertLessEqual(
                abs((witness["onset_at"] - reference).total_seconds()), 10,
            )
            self.assertLessEqual(witness["first_observed_at"], package["first_observed_at"])
            self.assertLessEqual(witness["newest_at"], package["first_observed_at"])
            self.assertLessEqual(witness["point_count"], values["curve_max_points"])
            fresh = confirmations[witness["candidate_id"]]
            self.assertTrue(fresh["supported"])
            self.assertGreater(fresh["fresh_newest_at"], witness["first_newest_at"])
            self.assertLessEqual(fresh["newest_at"], close.trace_at)
            self.assertTrue(any(
                item.position.value == key[0] and item.quantity.value == key[1]
                and item.source == witness["source"] and item.received_at == witness["onset_at"]
                for item in inputs
            ))
        print(f"R04 lifecycle slice: samples={cursor} ticks={len(observed)} wall_seconds={perf_counter() - started:.6f}")

    def test_window_expiry_cannot_confirm_a_historical_package(self):
        detector = self._detector()
        last = (2 * detector.curve_policy.min_side_samples - 1) * detector.grid_seconds
        with patch("custom_components.ha_sauna.core.detector.curve_change", side_effect=local_features) as fitted:
            self._feed_until(detector, last)
            key = self._remember(detector)
            package = dict(detector._curve_proposal_status[key])
            witness = detector._curve_proposals[key].candidates[0][1]
            calls = fitted.call_count
            later = last + detector.p["curve_window_seconds"] + detector.grid_seconds
            self.assertLess(later - last, detector.p["sensor_timeout_seconds"])
            self.assertEqual(detector.advance(at(later), enabled=True), [])
            self.assertEqual(fitted.call_count, calls)
            self.assertLess(witness.onset_at, at(later - detector.p["curve_window_seconds"]))
            self.assertNotIn(key, detector._curve_proposals)
            expired = detector._curve_proposal_status[key]
            self.assertEqual(expired["proposal_id"], package["proposal_id"])
            self.assertEqual(expired["status"], "invalid")
            self.assertEqual(expired["reason"], "candidate_expired")
            self.assertFalse(detector._curve_confirmation(
                "door_open", detector.positions, QUANTITIES, floor=T0,
            ))

    def test_an_accepted_correction_rechecks_and_can_reject_saved_original_support(self):
        detector = self._detector()
        last = (2 * detector.curve_policy.min_side_samples - 1) * detector.grid_seconds
        with patch(
            "custom_components.ha_sauna.core.detector.curve_change", side_effect=local_features,
        ):
            self._feed_until(detector, last)
        key = self._remember(detector)
        witness = detector._curve_proposals[key].candidates[0][1]
        originals = witness.original_points
        corrected = trace(71)[0]
        with patch("custom_components.ha_sauna.core.detector.curve_change", return_value=None) as checked:
            detector.accept(measurement(Position.UPPER, Quantity.TEMPERATURE, corrected,
                                        last + detector.grid_seconds, measured=at(last)))
            detector.advance(at(last + detector.grid_seconds), enabled=True)
        fixed = [call for call in checked.call_args_list if call.kwargs["onset_at"] == witness.onset_at]
        # The saved witness and still-current candidate may both be checked.
        # Every such comparison must preserve the same corrected originals.
        self.assertGreaterEqual(len(fixed), 1)
        for call in fixed:
            self.assertEqual(tuple(call.args[0]), tuple(
                (stamp, corrected if stamp == at(last) else value)
                for stamp, value in originals
            ))
            self.assertEqual(call.kwargs["policy"], detector.curve_policy)
        self.assertNotIn(key, detector._curve_proposals)
        self.assertEqual(detector._curve_proposal_status[key]["reason"], "original_corrected")

    def test_each_source_needs_a_new_original_after_the_first_complete_package(self):
        detector = self._detector()
        grid = detector.grid_seconds
        last = (2 * detector.curve_policy.min_side_samples - 1) * grid
        with patch(
            "custom_components.ha_sauna.core.detector.curve_change",
            side_effect=supported_opening,
        ):
            for second in range(0, last + grid, grid):
                self.assertEqual(original_sample(detector, second, measured=at(second)), [])
            key = self._remember(detector)
            first = dict(detector._curve_proposal_status[key])
            self.assertFalse(detector._curve_confirmation(
                "door_open", detector.positions, QUANTITIES, floor=T0,
            ))
            self.assertEqual(detector.advance(at(last + grid), enabled=True), [])
            self.assertEqual(original_sample(
                detector, last + 2 * grid, values=trace(last), measured=at(last),
            ), [])
            detector.accept(measurement(
                Position.UPPER, Quantity.TEMPERATURE, trace(71)[0],
                last + 3 * grid, measured=at(last),
            ))
            self.assertEqual(detector.advance(at(last + 3 * grid), enabled=True), [])
            self.assertFalse(detector._curve_confirmation(
                "door_open", detector.positions, QUANTITIES, floor=T0,
            ))
            detector.accept(measurement(
                Position.UPPER, Quantity.TEMPERATURE, trace(71)[0],
                last + 4 * grid, measured=at(last + 4 * grid),
            ))
            self.assertEqual(detector.advance(at(last + 4 * grid), enabled=True), [])
            self.assertFalse(detector._curve_confirmation(
                "door_open", detector.positions, QUANTITIES, floor=T0,
            ))
            detector.accept(measurement(
                Position.UPPER, Quantity.HUMIDITY, trace(last)[1],
                last + 5 * grid, measured=at(last + 5 * grid),
            ))
            events = detector.advance(at(last + 5 * grid), enabled=True)
        openings = [event for event in events if event.kind == Kind.DOOR_OPEN]
        self.assertEqual(len(openings), 1)
        self.assertEqual(openings[0].effective_at, first["onset_at"])
        self.assertEqual(openings[0].detected_at, at(last + 5 * grid))
        consumed = detector._curve_proposal_status[key]
        self.assertEqual(consumed["status"], "consumed")
        self.assertEqual(consumed["proposal_id"], first["proposal_id"])
        fresh = {
            item["candidate_id"]: item for item in consumed["confirmation"]
        }
        for witness in first["witnesses"]:
            confirmed = fresh[witness["candidate_id"]]
            self.assertTrue(confirmed["supported"])
            self.assertGreater(confirmed["fresh_newest_at"], witness["first_newest_at"])

    def test_compatible_packages_do_not_drift_and_complete_competition_stays_ambiguous(self):
        detector = self._detector()
        grid = detector.grid_seconds
        side = detector.curve_policy.min_side_samples
        window = max(detector.p["door_window_seconds"], detector.p["door_humidity_seconds"])
        shift = (ceil(window / (2 * grid)) + 1) * grid
        original = (side - 1) * grid
        with patch(
            "custom_components.ha_sauna.core.detector.curve_change",
            side_effect=local_features,
        ):
            self._feed_until(detector, (2 * side - 1) * grid)
            key = self._remember(detector)
            first = dict(detector._curve_proposal_status[key])
            self._feed_until(detector, original + 2 * shift + side * grid)
            self.assertEqual(detector._curve_proposal_status[key], first)

        def supported_hypothesis(onset):
            # The frozen candidate search cannot move without rejecting its
            # old witness. Supply a separate, still-supported complete
            # hypothesis directly to isolate the proposal ambiguity contract.
            # Its onset and all support points remain observed originals.
            now = at(detector.index * grid)
            for candidate_key in tuple(detector._curve_candidates):
                if candidate_key[0] != "door_open":
                    continue
                candidate = detector._curve_candidates[candidate_key]
                features = local_features(
                    candidate.original_points, policy=detector.curve_policy,
                    onset_at=onset, direction=-1, onset_not_before=T0,
                )
                self.assertIsNotNone(features)
                replacement = replace(
                    candidate, candidate_id=f"{candidate.candidate_id}:{onset.isoformat()}",
                    onset_at=onset, first_observed_at=now, features=features,
                    first_newest_at=features.newest_at, fresh_newest_at=None,
                    fresh_observed_at=None,
                )
                detector._curve_candidates[candidate_key] = replacement
                detector._curve_status[candidate_key] = {
                    **detector._curve_status[candidate_key],
                    "candidate_id": replacement.candidate_id,
                    "onset_at": onset, "first_observed_at": now,
                    "before_count": features.before_count,
                    "after_count": features.after_count,
                    "first_newest_at": features.newest_at,
                    "fresh_newest_at": None, "fresh_observed_at": None,
                }
            self._remember(detector)

        supported_hypothesis(at(original + shift))
        self.assertEqual(detector._curve_proposal_status[key], first)
        supported_hypothesis(at(original + 2 * shift))
        ambiguous = dict(detector._curve_proposal_status[key])
        self.assertEqual(ambiguous["status"], "ambiguous")
        self.assertEqual(ambiguous["proposal_id"], first["proposal_id"])
        self.assertEqual(ambiguous["onsets"], first["onsets"])
        supported_hypothesis(at(original))
        self.assertEqual(detector._curve_proposal_status[key], ambiguous)
        self.assertFalse(detector._curve_confirmation(
            "door_open", detector.positions, QUANTITIES, floor=T0,
        ))
        fallback = at(detector.index * grid)
        self.assertEqual(detector._curve_beginning(
            "door_open", detector.positions, QUANTITIES, fallback,
            floor=T0, kind=Kind.DOOR_OPEN, now=fallback, decision_at=fallback,
        ), fallback)

    def test_counterevidence_retires_the_old_package_before_a_new_candidate_replaces_it(self):
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
        replacement = detector._curve_proposal_status[key]
        self.assertEqual(replacement["status"], "available")
        self.assertNotEqual(replacement["proposal_id"], first["proposal_id"])
        self.assertEqual(replacement["onset_at"], target[0])
        self.assertTrue(set(first["candidate_ids"]).isdisjoint(replacement["candidate_ids"]))
        self.assertFalse(detector._curve_confirmation(
            "door_open", detector.positions, QUANTITIES, floor=T0,
        ))

    def test_only_simultaneous_complete_support_can_bind_within_owner_and_ventilation_bounds(self):
        detector = self._detector()
        allowed = [{Quantity.TEMPERATURE}]
        events = []
        with patch(
            "custom_components.ha_sauna.core.detector.curve_change",
            side_effect=supported_opening,
        ):
            # Establish the opening through the actual first/fresh-original
            # transition, so the short-close RH route belongs to that opening.
            for second in range(2 * detector.curve_policy.min_side_samples + 1):
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

    def test_fresh_temperature_close_keeps_its_history_until_current_recovery(self):
        for boundary in (None, "source", "correction", "expiry", "humidity_source"):
            with self.subTest(boundary=boundary):
                detector = self._detector()
                grid = detector.grid_seconds
                side = detector.curve_policy.min_side_samples
                opening_at = 2 * side * grid
                bound_at = opening_at + 2 * grid
                events = []
                with patch(
                    "custom_components.ha_sauna.core.detector.curve_change",
                    side_effect=supported_opening,
                ):
                    for second in range(0, opening_at + grid, grid):
                        events.extend(original_sample(detector, second, measured=at(second)))
                self.assertEqual([item.kind for item in events], [Kind.DOOR_OPEN])
                events.clear()
                temperature, humidity = trace(0)

                def historical(samples, **kwargs):
                    if not samples or samples[0][1] < (temperature + humidity) / 2:
                        return None
                    features = local_features(samples, **kwargs)
                    if features is None:
                        return None
                    corrected = any(
                        stamp == at(bound_at) and value != temperature - .2
                        for stamp, value in samples
                    )
                    return replace(
                        features, evidence=(
                            "rejects" if corrected or samples[-1][0] > at(bound_at)
                            else "supports"
                        ),
                    )

                def recovery(samples, *, policy):
                    if len(samples) < policy.min_side_samples:
                        return None
                    rising = all(right > left for (_, left), (_, right) in zip(samples, samples[1:]))
                    return CurveTrendFeatures(
                        direction=1 if rising else -1, slope=.1 if rising else -.1,
                        noise_scale=1.0, level_score=1.0, trend_score=0.0, score_gain=1.0,
                        exact_constant=False, exact_linear=False,
                    )

                source = humidity_source = None

                def send(second, value, *, measured=None):
                    for quantity, observed in (
                        (Quantity.TEMPERATURE, value), (Quantity.HUMIDITY, humidity),
                    ):
                        self.assertTrue(detector.accept(measurement(
                            Position.UPPER, quantity, observed, second,
                            source=(source if quantity == Quantity.TEMPERATURE
                                    else humidity_source),
                            measured=at(second if measured is None else measured),
                        )))
                    events.extend(detector.advance(at(second), enabled=True))

                with patch(
                    "custom_components.ha_sauna.core.detector.curve_change", side_effect=historical,
                ), patch(
                    "custom_components.ha_sauna.core.detector.curve_trend", side_effect=recovery,
                ):
                    send(opening_at + grid, temperature - .1)
                    send(bound_at, temperature - .2)
                    key = ("door_close", detector.positions, (Quantity.TEMPERATURE,))
                    proposal = detector._curve_proposals[key]
                    witness = proposal.candidates[0][1]
                    self.assertIsNotNone(witness.fresh_newest_at)
                    self.assertGreater(witness.fresh_newest_at, witness.first_newest_at)
                    self.assertEqual(detector._curve_proposal_status[key]["status"], "bound")
                    self.assertEqual(detector._curve_proposal_status[key]["evidence_bound_at"], at(bound_at))
                    originals = witness.original_points
                    self.assertEqual(events, [])
                    recovery_start = bound_at + side * grid
                    through = recovery_start + detector.p["door_window_seconds"]
                    if boundary == "source":
                        source = "sensor.replaced_temperature"
                        send(bound_at + grid, temperature - .3)
                    elif boundary == "humidity_source":
                        # Broken RH provenance disables only the supplementary
                        # RH route; original T recovery remains independent.
                        humidity_source = "sensor.replaced_humidity"
                        send(bound_at + grid, temperature - .3)
                    elif boundary == "correction":
                        send(bound_at + grid, temperature - 4, measured=bound_at)
                    elif boundary == "expiry":
                        expired = witness.onset_at + timedelta(
                            seconds=detector.p["curve_window_seconds"] + grid,
                        )
                        events.extend(detector.advance(expired, enabled=True))
                        recovery_start = int((expired - T0).total_seconds())
                        through = recovery_start + detector.p["door_window_seconds"]
                    else:
                        events.extend(detector.advance(at(bound_at + grid), enabled=True))
                    if boundary not in (None, "humidity_source"):
                        self.assertNotIn(key, detector._curve_proposals)
                        self.assertEqual(detector._curve_proposal_status[key]["status"], "invalid")
                    for second in range((detector.index + 1) * grid, through + grid, grid):
                        value = temperature - .1 * (second - opening_at) / grid
                        if second > recovery_start:
                            bottom = temperature - .1 * (recovery_start - opening_at) / grid
                            value = bottom + .1 * (second - recovery_start) / grid
                        send(second, value)
                        if boundary in (None, "humidity_source") and second < through:
                            saved = detector._curve_proposals[key].candidates[0][1]
                            self.assertEqual(saved.original_points, originals)
                            self.assertEqual(saved.candidate_id, witness.candidate_id)
                            self.assertEqual(events, [])
                closes = [item for item in events if item.kind == Kind.DOOR_CLOSE]
                if boundary in (None, "humidity_source"):
                    self.assertEqual(len(closes), 1, msg=f"actual detections: {events!r}")
                    self.assertEqual(closes[0].effective_at, witness.onset_at)
                    self.assertEqual(closes[0].trace_at, at(through))
                    self.assertEqual(detector._curve_proposal_status[key]["status"], "consumed")
                    confirmed = detector._curve_proposal_status[key]["confirmation"][0]
                    self.assertEqual(confirmed["fresh_newest_at"], at(bound_at))
                    self.assertEqual(confirmed["recovery_newest_at"], at(through))
                    self.assertEqual(confirmed["recovery_observed_at"], at(through))
                else:
                    self.assertEqual(closes, [])

    def test_late_humidity_recovery_needs_the_current_bound_temperature_identity(self):
        for current_temperature in ("supported", "held", "insufficient", "rejected", "replaced"):
            with self.subTest(current_temperature=current_temperature):
                observed = []
                detector = Detector(
                    detection_parameters(), T0, (Position.UPPER, Position.LOWER),
                    observer=observed.append,
                )
                grid = detector.grid_seconds
                side = detector.curve_policy.min_side_samples
                opening_at = 2 * side * grid
                bound_at = opening_at + 2 * grid
                humidity = trace(0)[1]
                temperatures = {
                    Position.UPPER: trace(0)[0], Position.LOWER: trace(85)[0],
                }
                falling_step = trace(280)[0] - trace(281)[0]
                humidity_step = trace(151)[1] - trace(150)[1]
                events = []

                def send(second, *, pulse_start=None, held_temperature=False):
                    for position, temperature in temperatures.items():
                        values = (
                            (Quantity.TEMPERATURE,
                             temperature - falling_step * max(0, second - opening_at) / grid),
                            (Quantity.HUMIDITY,
                             humidity + humidity_step * max(0, second - pulse_start) / grid
                             if pulse_start is not None else humidity),
                        )
                        for quantity, value in values:
                            if (held_temperature and position == Position.LOWER
                                    and quantity == Quantity.TEMPERATURE):
                                continue
                            self.assertTrue(detector.accept(measurement(
                                position, quantity, value, second, measured=at(second),
                            )))
                    events.extend(detector.advance(at(second), enabled=True))

                with patch(
                    "custom_components.ha_sauna.core.detector.curve_change",
                    side_effect=supported_opening,
                ):
                    for second in range(0, opening_at + grid, grid):
                        send(second)
                self.assertEqual([item.kind for item in events], [Kind.DOOR_OPEN])
                self.assertTrue(detector.door_episode["opening_humidity"])
                self.assertEqual(detector.door_episode["positions"], detector.positions)
                opening_humidity_sources = {
                    position: (
                        detector._curve_series[position, Quantity.HUMIDITY]["source"],
                        detector._curve_series[position, Quantity.HUMIDITY]["epoch"],
                    ) for position in detector.positions
                }
                events.clear()

                def current_evidence(samples, **kwargs):
                    if not samples or samples[0][1] not in temperatures.values():
                        # No RH hinge is supplied: the new route must use the
                        # independently directed current original RH segment.
                        return None
                    lower = samples[0][1] == temperatures[Position.LOWER]
                    later = samples[-1][0] > at(bound_at)
                    if lower and later and current_temperature == "insufficient":
                        return None
                    replacement = at(bound_at)
                    rejected = lower and later and (
                        current_temperature == "rejected"
                        or (current_temperature == "replaced"
                            and kwargs["onset_at"] not in (None, replacement))
                    )
                    result = local_features(
                        samples, target=(replacement if lower and later
                                         and current_temperature == "replaced" else None),
                        **kwargs,
                    )
                    return replace(result, evidence="rejects") if rejected and result else result

                def directed_originals(samples, *, policy):
                    if len(samples) < policy.min_side_samples:
                        return None
                    rising = all(right > left for (_, left), (_, right) in zip(samples, samples[1:]))
                    return CurveTrendFeatures(
                        direction=1 if rising else -1,
                        slope=humidity_step if rising else -falling_step,
                        noise_scale=1.0, level_score=1.0, trend_score=0.0, score_gain=1.0,
                        exact_constant=False, exact_linear=False,
                    )

                with patch(
                    "custom_components.ha_sauna.core.detector.curve_change",
                    side_effect=current_evidence,
                ), patch(
                    "custom_components.ha_sauna.core.detector.curve_trend",
                    side_effect=directed_originals,
                ):
                    send(opening_at + grid)
                    send(bound_at)
                    key = ("door_close", detector.positions, (Quantity.TEMPERATURE,))
                    first = dict(detector._curve_proposal_status[key])
                    self.assertEqual(first["status"], "bound")
                    self.assertEqual(first["evidence_bound_at"], at(bound_at))
                    self.assertEqual(events, [])
                    bound_ids = {
                        candidate_key[1]: candidate.candidate_id
                        for candidate_key, candidate in detector._curve_proposals[key].candidates
                    }
                    pulse_start = bound_at + side * grid
                    through = pulse_start + detector.p["door_humidity_seconds"]
                    self.assertGreater(through - bound_at, detector.p["door_window_seconds"])
                    self.assertLess(through - bound_at, detector.p["sensor_timeout_seconds"])
                    self.assertLess(through - opening_at, detector.p["curve_window_seconds"])
                    for second in range(bound_at + grid, through + grid, grid):
                        send(
                            second, pulse_start=pulse_start,
                            held_temperature=current_temperature == "held",
                        )
                        if second < through:
                            self.assertEqual(events, [], msg=repr(detector.diagnostic))

                diagnostic = repr({
                    "current_temperature": current_temperature,
                    "events": [(item.kind, item.effective_at, item.trace_at) for item in events],
                    "bound_ids": bound_ids,
                    "candidates": detector.diagnostic["curve_candidates"].get("door_close", {}),
                    "proposals": detector.diagnostic["curve_proposals"].get("door_close", ()),
                })
                closes = [item for item in events if item.kind == Kind.DOOR_CLOSE]
                if current_temperature == "supported":
                    self.assertEqual(len(closes), 1, msg=diagnostic)
                    self.assertEqual(closes[0].effective_at, first["onset_at"], msg=diagnostic)
                    self.assertEqual(closes[0].trace_at, at(through), msg=diagnostic)
                    self.assertEqual(detector._curve_proposal_status[key]["status"], "consumed")
                    self.assertEqual(
                        detector._curve_proposal_status[key]["candidate_ids"],
                        first["candidate_ids"], msg=diagnostic,
                    )
                    for confirmed in detector._curve_proposal_status[key]["confirmation"]:
                        position = next(
                            position for position, identity in bound_ids.items()
                            if identity == confirmed["candidate_id"]
                        )
                        source, epoch = opening_humidity_sources[position]
                        self.assertTrue(confirmed["supported"], msg=diagnostic)
                        self.assertEqual(confirmed["recovery_quantity"], "humidity")
                        self.assertEqual(confirmed["recovery_source"], source)
                        self.assertEqual(confirmed["recovery_time_basis"], "measured")
                        self.assertEqual(confirmed["recovery_source_epochs"], (epoch,))
                        self.assertEqual(confirmed["recovery_newest_at"], at(through))
                        self.assertEqual(confirmed["recovery_observed_at"], at(through))
                        self.assertTrue(confirmed["current_temperature_supported"])
                        self.assertEqual(confirmed["current_temperature_newest_at"], at(through))
                else:
                    self.assertEqual(closes, [], msg=diagnostic)
                    self.assertTrue(detector.open, msg=diagnostic)
                    self.assertEqual(detector._curve_proposal_status[key]["status"], "bound")
                    lower_key = ("door_close", Position.LOWER, Quantity.TEMPERATURE)
                    status = detector._curve_status[lower_key]
                    if current_temperature == "held":
                        self.assertEqual(status["status"], "supported", msg=diagnostic)
                        self.assertEqual(status["newest_at"], at(bound_at), msg=diagnostic)
                        self.assertEqual(status["candidate_id"], bound_ids[Position.LOWER])
                    elif current_temperature == "replaced":
                        self.assertEqual(status["status"], "supported", msg=diagnostic)
                        self.assertNotEqual(status["candidate_id"], bound_ids[Position.LOWER])
                    else:
                        self.assertEqual(status["status"], current_temperature, msg=diagnostic)

    def test_temperature_recovery_cache_expires_without_a_tick_refit(self):
        detector = self._detector()
        grid = detector.grid_seconds
        side = detector.curve_policy.min_side_samples
        events = []
        with patch(
            "custom_components.ha_sauna.core.detector.curve_change",
            side_effect=supported_opening,
        ):
            for second in range(0, (2 * side + 1) * grid, grid):
                events.extend(original_sample(detector, second, measured=at(second)))
        self.assertEqual([item.kind for item in events], [Kind.DOOR_OPEN])
        positive = CurveTrendFeatures(
            direction=1, slope=.1, noise_scale=0.0,
            level_score=None, trend_score=None, score_gain=None,
            exact_constant=False, exact_linear=True,
        )

        def recovery(samples, *, policy):
            return positive if len(samples) >= policy.min_side_samples else None

        with patch(
            "custom_components.ha_sauna.core.detector.curve_change", side_effect=local_features,
        ), patch(
            "custom_components.ha_sauna.core.detector.curve_trend", side_effect=recovery,
        ) as fitted:
            # Keep the real opening and its supported close candidate available
            # while isolating the lifetime of its separate recovery evidence.
            with patch.object(detector, "_curve_confirmation", return_value=False):
                for offset in (1, 2):
                    second = (2 * side + offset) * grid
                    self.assertEqual(original_sample(
                        detector, second, values=(trace(0)[0] + .1 * offset, trace(0)[1]),
                        measured=at(second),
                    ), [])
                key = ("door_close", Position.UPPER, Quantity.TEMPERATURE)
                candidate = detector._curve_candidates[key]
                last = detector.index * grid
                self.assertTrue(detector._temperature_recovery(Position.UPPER, candidate, at(last)))
                calls = fitted.call_count
                oldest = fitted.call_args.args[0][0][0]
                first_expiry = oldest + timedelta(seconds=detector.p["door_window_seconds"] + grid)
                self.assertLessEqual((first_expiry - at(last)).total_seconds(),
                                     detector.p["door_window_seconds"])
                self.assertEqual(detector.advance(first_expiry, enabled=True), [])
                self.assertFalse(detector._temperature_recovery(Position.UPPER, candidate, first_expiry))
                self.assertEqual(fitted.call_count, calls)
                expired = last + detector.p["door_window_seconds"] + grid
                self.assertLess(expired - last, detector.p["sensor_timeout_seconds"])
                self.assertLess(expired - last, detector.p["curve_window_seconds"])
                self.assertEqual(detector.advance(at(expired), enabled=True), [])
                self.assertFalse(detector._temperature_recovery(Position.UPPER, candidate, at(expired)))
                self.assertEqual(fitted.call_count, calls)
                self.assertEqual(detector._curve_candidates[key].candidate_id, candidate.candidate_id)
                for offset in range(1, side + 1):
                    second = expired + offset * grid
                    self.assertEqual(original_sample(
                        detector, second, values=(trace(0)[0] + .2 + .1 * offset, trace(0)[1]),
                        measured=at(second),
                    ), [])
            fresh = detector._curve_candidates[key]
            self.assertEqual(fresh.candidate_id, candidate.candidate_id)
            self.assertTrue(detector._temperature_recovery(Position.UPPER, fresh, at(second)))
            self.assertGreater(fitted.call_count, calls)
            self.assertTrue(detector._curve_confirmation(
                "door_close", detector.positions, (Quantity.TEMPERATURE,),
                floor=detector._curve_open_effective_at, recovery=True,
            ))

    def test_unconfirmed_earlier_infusion_support_cannot_backdate_a_later_fresh_candidate(self):
        selected = detection_parameters()
        old_onset = at(180)
        observed = []
        detector = Detector(selected, T0, (Position.UPPER,), observer=observed.append)

        def infusion_evidence(samples, **kwargs):
            # A single old local witness cannot confirm itself. The existing
            # trace's later +3 RH flank and subsequent fall form another pulse.
            if not samples or any(
                value != trace((stamp - T0).total_seconds())[1]
                for stamp, value in samples
            ):
                return None
            newest = samples[-1][0]
            if kwargs["direction"] == 1 and newest == at(200):
                target = old_onset
            elif kwargs["direction"] == 1 and newest in (at(245), at(246)):
                target = at(240)
            elif kwargs["direction"] == -1 and newest in (at(285), at(286)):
                target = at(280)
            else:
                return None
            result = local_features(samples, target=target, **kwargs)
            if result is None:
                return None
            rising = kwargs["direction"] == 1
            return replace(
                result, slope_before=0.0 if rising else 1.0,
                slope_after=1.0 if rising else -1.0,
                slope_change=1.0 if rising else -2.0,
                signed_strength=1.0 if rising else -2.0,
                after_trend=CurveTrendFeatures(
                    direction=1 if rising else -1, slope=1.0 if rising else -1.0,
                    noise_scale=1.0, level_score=1.0, trend_score=0.0, score_gain=1.0,
                    exact_constant=False, exact_linear=False,
                ),
            )

        actual = []
        admitted_again = 200 + selected.values["infusion_window_seconds"]
        with patch("custom_components.ha_sauna.core.detector.curve_change", side_effect=infusion_evidence):
            for second in range(291):
                for quantity, value in zip(QUANTITIES, trace(second), strict=True):
                    detector.accept(measurement(Position.UPPER, quantity, value, second))
                # A controller admission boundary replaces the old hold-attempt
                # boundary. Earlier candidate evidence cannot cross that owner.
                admitted = second <= 200 or second >= admitted_again
                actual.extend(detector.advance(
                    at(second), enabled=True,
                    allowed=lambda kind: kind != Kind.INFUSION or admitted,
                ))
        earlier = next(row for row in observed if row["at"] == at(200))
        local = earlier["curve_candidates"]["infusion"]["upper"]["humidity"]
        self.assertEqual(local["status"], "supported")
        self.assertEqual(local["onset_at"], old_onset)
        self.assertIsNone(local["fresh_newest_at"])
        self.assertNotIn(Kind.INFUSION, earlier["signals"])
        self.assertTrue(any(
            proposal["status"] == "available"
            for proposal in earlier["curve_proposals"].get("infusion", ())
        ))
        blocked = next(row for row in observed if row["at"] == at(201))
        self.assertTrue(any(
            proposal["status"] == "invalid"
            and proposal["reason"] == "admission_context"
            for proposal in blocked["curve_proposals"].get("infusion", ())
        ))
        infusions = [event for event in actual if event.kind == Kind.INFUSION]
        self.assertEqual(len(infusions), 1, msg=f"actual detections: {actual!r}")
        rising = next(row for row in observed if row["at"] == at(246))
        self.assertNotIn(Kind.INFUSION, rising["signals"])
        self.assertEqual(rising["infusion_episode"]["phase"], "pending")
        self.assertEqual(infusions[0].trace_at, at(286))
        self.assertEqual(infusions[0].effective_at, at(240))
        consumed = next(
            proposal for row in observed if row["at"] == infusions[0].trace_at
            for proposal in row["curve_proposals"]["infusion"]
            if proposal["status"] == "consumed"
        )
        self.assertNotIn(local["candidate_id"], consumed["candidate_ids"])
