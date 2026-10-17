"""Native R04 reference and isolated optional attribution-package contracts.

Package tests call the input store and proposal helpers directly. They do not
assert native event detection and do not create a fake door episode. Controlled
mathematical hypotheses exercise provenance/ambiguity, not physical scenarios.
"""

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

import test_r04_door_recovery as r04_recovery

from test_curve_candidates import at, supported_opening
from test_detector import detection_parameters, measurement, trace
from test_foundation import T0
from test_settings import fixture

from custom_components.ha_sauna.core.detector import Detector
from custom_components.ha_sauna.core.models import Position, Quantity
from custom_components.ha_sauna.core.timeline import Kind


QUANTITIES = (Quantity.TEMPERATURE, Quantity.HUMIDITY)


def local_features(
    samples,
    *,
    policy,
    onset_at=None,
    direction=None,
    onset_not_before=None,
    hinge_order=1,
    target=None,
):
    """Controlled linear-kink evidence; R04 below uses real mathematics."""
    if hinge_order != 1:
        return None
    onset_at = onset_at or target
    result = supported_opening(
        samples,
        policy=policy,
        onset_at=onset_at,
        direction=-1,
        onset_not_before=onset_not_before,
    )
    if (
        result is None
        or min(result.before_count, result.after_count) < policy.min_side_samples
    ):
        return None
    if direction == 1:
        return replace(
            result,
            slope_before=-2.0,
            slope_after=-1.0,
            slope_change=1.0,
            signed_strength=1.0,
        )
    return result


class CurveCandidateLifecycleTests(unittest.TestCase):
    def test_r04_short_close_confirms_the_physical_recovery_before_the_old_trigger(
        self,
    ):
        started = perf_counter()
        zone = ZoneInfo("Europe/Berlin")
        reference = datetime(2000, 1, 1, 16, 2, 40, tzinfo=zone)
        opening_reference = datetime(2000, 1, 1, 16, 2, 15, tzinfo=zone)
        confirmation = datetime(2000, 1, 1, 16, 3, 19, tzinfo=zone)
        parameters = detection_parameters()
        values = parameters.values
        warmup = max(
            values["curve_window_seconds"],
            values["vent_baseline_seconds"]
            + max(values["door_window_seconds"], values["door_humidity_seconds"]),
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
                inputs.append(
                    replace(
                        measurement(
                            Position(position),
                            Quantity(quantity),
                            row["value"],
                            0,
                            source=f"sensor.fixture_{row['role']}",
                        ),
                        received_at=received,
                    )
                )
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
        self.assertEqual(
            len(openings), 1, msg=f"R04 opening evidence missing: {events!r}"
        )
        self.assertEqual(len(closes), 1)
        close = closes[0]
        self.assertLess(close.detected_at, confirmation)
        self.assertEqual(close.trace_at, close.detected_at)
        self.assertLess(openings[0].trace_at, close.trace_at)
        self.assertLessEqual(abs((close.effective_at - reference).total_seconds()), 10)
        consumed = [
            proposal
            for row in observed
            if row["at"] == close.trace_at
            for proposal in row["curve_proposals"].get("door_close", ())
            if proposal["status"] == "consumed"
        ]
        self.assertEqual(len(consumed), 1)
        package = consumed[0]
        self.assertIsNotNone(close.door_close_evidence)
        recovery = r04_recovery.R04DoorRecoveryTests()
        row = next(item for item in observed if item["at"] == close.trace_at)
        self.assertEqual(close.channels, (Position.UPPER.value,))
        recovery._assert_native_earlier_attribution(
            row, close, inputs, values, package,
        )
        for witness in package["witnesses"]:
            self.assertLessEqual(
                abs((witness["onset_at"] - reference).total_seconds()),
                10,
            )
        print(
            f"R04 lifecycle slice: samples={cursor} ticks={len(observed)} wall_seconds={perf_counter() - started:.6f}"
        )


class OptionalCurvePackageTests(unittest.TestCase):
    def _detector(self):
        return Detector(detection_parameters(), T0, (Position.UPPER,))

    @staticmethod
    def _context(detector):
        return (detector._curve_door_context, T0, T0)

    def _remember(self, detector, route="door_open", quantities=QUANTITIES):
        detector._remember_curve_proposal(
            route, detector.positions, quantities, floor=T0,
            context=self._context(detector),
            observed_at=at(detector.index * detector.grid_seconds),
        )
        return (route, detector.positions, quantities)

    def _feed_until(self, detector, last, *, route="door_open", quantities=QUANTITIES):
        # Isolated helper contract: normal input storage, no _sample/event path.
        while (detector.index + 1) * detector.grid_seconds <= last:
            second = (detector.index + 1) * detector.grid_seconds
            now = at(second)
            for quantity, value in zip(QUANTITIES, trace(second), strict=True):
                self.assertTrue(detector.accept(measurement(
                    Position.UPPER, quantity, value, second, measured=now,
                )))
            detector._consume(now)
            detector.index += 1
            for quantity in quantities:
                detector._curve_candidate(
                    route, Position.UPPER, quantity, now, now, T0,
                    self._context(detector),
                )
            self._remember(detector, route, quantities)

    def test_window_expiry_cannot_confirm_a_historical_package(self):
        detector = self._detector()
        last = (2 * detector.curve_policy.min_side_samples - 1) * detector.grid_seconds
        with patch(
            "custom_components.ha_sauna.core.detector.curve_change",
            side_effect=local_features,
        ) as fitted:
            self._feed_until(detector, last)
            key = self._remember(detector)
            package = dict(detector._curve_proposal_status[key])
            witness = detector._curve_proposals[key].candidates[0][1]
            calls = fitted.call_count
            later = last + detector.p["curve_window_seconds"] + detector.grid_seconds
            self.assertLess(later - last, detector.p["sensor_timeout_seconds"])
            for quantity in QUANTITIES:
                detector._curve_candidate(
                    "door_open", Position.UPPER, quantity, at(later), at(later),
                    T0, self._context(detector),
                )
            self.assertEqual(fitted.call_count, calls)
            self.assertLess(
                witness.onset_at, at(later - detector.p["curve_window_seconds"])
            )
            self.assertNotIn(key, detector._curve_proposals)
            expired = detector._curve_proposal_status[key]
            self.assertEqual(expired["proposal_id"], package["proposal_id"])
            self.assertEqual(expired["status"], "invalid")
            self.assertEqual(expired["reason"], "candidate_expired")

    def test_an_accepted_correction_rechecks_and_can_reject_saved_original_support(
        self,
    ):
        detector = self._detector()
        last = (2 * detector.curve_policy.min_side_samples - 1) * detector.grid_seconds
        with patch(
            "custom_components.ha_sauna.core.detector.curve_change",
            side_effect=local_features,
        ):
            self._feed_until(detector, last)
        key = self._remember(detector)
        witness = detector._curve_proposals[key].candidates[0][1]
        originals = witness.original_points
        corrected = trace(71)[0]
        with patch(
            "custom_components.ha_sauna.core.detector.curve_change", return_value=None
        ) as checked:
            detector.accept(
                measurement(
                    Position.UPPER,
                    Quantity.TEMPERATURE,
                    corrected,
                    last + detector.grid_seconds,
                    measured=at(last),
                )
            )
            detector._consume(at(last + detector.grid_seconds))
        fixed = [
            call
            for call in checked.call_args_list
            if call.kwargs["onset_at"] == witness.onset_at
        ]
        # The saved witness and still-current candidate may both be checked.
        # Every such comparison must preserve the same corrected originals.
        self.assertGreaterEqual(len(fixed), 1)
        for call in fixed:
            self.assertEqual(
                tuple(call.args[0]),
                tuple(
                    (stamp, corrected if stamp == at(last) else value)
                    for stamp, value in originals
                ),
            )
            self.assertEqual(call.kwargs["policy"], detector.curve_policy)
        self.assertNotIn(key, detector._curve_proposals)
        self.assertEqual(
            detector._curve_proposal_status[key]["reason"], "original_corrected"
        )

    def test_compatible_packages_do_not_drift_and_complete_competition_stays_ambiguous(
        self,
    ):
        detector = self._detector()
        grid = detector.grid_seconds
        side = detector.curve_policy.min_side_samples
        window = max(
            detector.p["door_window_seconds"], detector.p["door_humidity_seconds"]
        )
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
                    candidate.original_points,
                    policy=detector.curve_policy,
                    onset_at=onset,
                    direction=-1,
                    onset_not_before=T0,
                )
                self.assertIsNotNone(features)
                replacement = replace(
                    candidate,
                    candidate_id=f"{candidate.candidate_id}:{onset.isoformat()}",
                    onset_at=onset,
                    first_observed_at=now,
                    features=features,
                    first_newest_at=features.newest_at,
                    fresh_newest_at=None,
                    fresh_observed_at=None,
                )
                detector._curve_candidates[candidate_key] = replacement
                detector._curve_status[candidate_key] = {
                    **detector._curve_status[candidate_key],
                    "candidate_id": replacement.candidate_id,
                    "onset_at": onset,
                    "first_observed_at": now,
                    "before_count": features.before_count,
                    "after_count": features.after_count,
                    "first_newest_at": features.newest_at,
                    "fresh_newest_at": None,
                    "fresh_observed_at": None,
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
        fallback = at(detector.index * grid)
        self.assertEqual(
            detector._curve_beginning(
                "door_open",
                detector.positions,
                QUANTITIES,
                fallback,
                floor=T0,
                kind=Kind.DOOR_OPEN,
                now=fallback,
                decision_at=fallback,
            ),
            fallback,
        )

    def test_counterevidence_retires_the_old_package_before_a_new_candidate_replaces_it(
        self,
    ):
        detector = self._detector()
        grid = detector.grid_seconds
        side = detector.curve_policy.min_side_samples
        window = max(
            detector.p["door_window_seconds"], detector.p["door_humidity_seconds"]
        )
        shift = (ceil(window / (2 * grid)) + 1) * grid
        original = (side - 1) * grid
        target = [at(original)]

        def evidence(samples, **kwargs):
            result = local_features(samples, target=target[0], **kwargs)
            if result is not None and kwargs["onset_at"] not in (None, target[0]):
                return replace(result, evidence="rejects")
            return result

        with patch(
            "custom_components.ha_sauna.core.detector.curve_change",
            side_effect=evidence,
        ):
            self._feed_until(detector, (2 * side - 1) * grid)
            key = self._remember(detector)
            first = dict(detector._curve_proposal_status[key])
            target[0] = at(original + shift)
            self._feed_until(detector, original + shift + side * grid)
        replacement = detector._curve_proposal_status[key]
        self.assertEqual(replacement["status"], "available")
        self.assertNotEqual(replacement["proposal_id"], first["proposal_id"])
        self.assertEqual(replacement["onset_at"], target[0])
        self.assertTrue(
            set(first["candidate_ids"]).isdisjoint(replacement["candidate_ids"])
        )

    def test_simultaneous_roles_and_owner_boundary_use_actual_package_contracts(self):
        detector = self._detector()
        last = (2 * detector.curve_policy.min_side_samples - 1) * detector.grid_seconds
        with patch(
            "custom_components.ha_sauna.core.detector.curve_change",
            side_effect=local_features,
        ):
            self._feed_until(detector, last, quantities=(Quantity.TEMPERATURE,))
            opening_key = self._remember(detector)
            self.assertNotIn(opening_key, detector._curve_proposals)
            now = at(last)
            detector._curve_candidate(
                "door_open", Position.UPPER, Quantity.HUMIDITY,
                now, now, T0, self._context(detector),
            )
            self._remember(detector)
            opening = detector._curve_proposals[opening_key]
            self.assertEqual({key[2] for key, _ in opening.candidates}, set(QUANTITIES))
            # The actual optional close consumer requires only its own T role.
            detector._curve_candidate(
                "door_close", Position.UPPER, Quantity.TEMPERATURE,
                now, now, T0, self._context(detector),
            )
            key = self._remember(detector, "door_close", (Quantity.TEMPERATURE,))
        package = detector._curve_proposal_status[key]
        self.assertEqual(package["status"], "available")
        self.assertEqual(package["first_observed_at"], now)
        self.assertEqual(len(detector._curve_proposals[key].candidates), 1)
        self.assertEqual(detector._curve_beginning(
            "door_close", detector.positions, (Quantity.TEMPERATURE,), now,
            floor=package["onset_at"] + timedelta(seconds=detector.grid_seconds),
            kind=Kind.DOOR_CLOSE, now=now, decision_at=now,
            boundary_reason="ventilation_after_onset",
        ), now)
        self.assertEqual(detector._curve_proposal_status[key]["reason"], "ventilation_after_onset")
        self._remember(detector, "door_close", (Quantity.TEMPERATURE,))
        detector._clear_curve_route("door_close", reason="owner_context")
        self.assertNotIn(key, detector._curve_proposals)
        self.assertEqual(detector._curve_proposal_status[key]["reason"], "owner_context")
