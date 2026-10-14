"""Reale Originalfolgen prüfen Türerholung bei belegtem Heizkontext.

Der Heizkontext stammt aus dem ausgeführten R04. Die Ausschnitte prüfen den
Detektor mit diesem Kontext, nicht die Folgen einer geänderten Heizsteuerung.
"""

from dataclasses import asdict, replace
from datetime import datetime, timedelta
import gzip
import json
from pathlib import Path
from time import perf_counter
import unittest
from unittest.mock import patch

from test_detector import detection_parameters, measurement
from test_settings import fixture

from custom_components.ha_sauna.core import curve_change as curves
from custom_components.ha_sauna.core import detector as detector_module
from custom_components.ha_sauna.core.curve_change import curve_change
from custom_components.ha_sauna.core.detector import Detector
from custom_components.ha_sauna.core.models import Position, Quantity
from custom_components.ha_sauna.core.timeline import Kind


class R04DoorRecoveryTests(unittest.TestCase):
    def _assert_smooth_close_originals(
        self, row, event, inputs, opening_witnesses, values, floor, consumed,
    ):
        """Validate current full-form evidence without making its valley an onset."""
        selected = row["door_close_confirmation"]
        self.assertEqual(selected["confirmation_method"], "smooth_recovery")
        self.assertEqual(selected["confirmed_at"], event.trace_at)
        self.assertEqual(selected["effective_at"], event.effective_at)
        confirmations = {item["position"]: item for item in selected["confirmation"]}
        self.assertEqual(set(confirmations), set(event.channels))
        freshness = []
        for position in event.channels:
            evidence = row["curve_candidates"]["door_close"][position]["temperature"]["smooth_recovery"]
            confirmed = confirmations[position]
            self.assertEqual(confirmed["quantity"], "temperature")
            self.assertEqual({key: confirmed[key] for key in evidence}, evidence)
            self.assertNotIn("onset_at", confirmed)
            witness = opening_witnesses[position, "temperature"]
            self.assertTrue(evidence["supported"], evidence)
            self.assertTrue(evidence["within_window"], evidence)
            for key in ("source", "time_basis", "source_epochs"):
                self.assertEqual(evidence[key], witness[key], evidence)
            self.assertGreater(evidence["fresh_newest_at"], evidence["first_newest_at"])
            self.assertLessEqual(evidence["fresh_observed_at"], event.trace_at)
            freshness.append(evidence["fresh_observed_at"])
            self.assertGreaterEqual(
                evidence["decline_start_at"], max(
                    floor, event.trace_at - timedelta(seconds=values["curve_window_seconds"]),
                ),
            )
            self.assertLessEqual(evidence["decline_start_at"], evidence["decline_newest_at"])
            self.assertLess(evidence["decline_newest_at"], evidence["valley_at"])
            self.assertLess(evidence["valley_at"], evidence["recovery_start_at"])
            self.assertGreaterEqual(
                evidence["recovery_start_at"], max(
                    floor, event.trace_at - timedelta(seconds=values["door_window_seconds"]),
                ),
            )
            self.assertLessEqual(evidence["recovery_start_at"], evidence["recovery_newest_at"])
            self.assertLessEqual(evidence["recovery_newest_at"], event.trace_at)
            self.assertEqual(evidence["newest_at"], evidence["recovery_newest_at"])
            for stamp in (
                evidence["start_at"], evidence["newest_at"],
                evidence["decline_start_at"], evidence["decline_newest_at"],
                evidence["recovery_start_at"], evidence["recovery_newest_at"],
                evidence["first_newest_at"], evidence["fresh_newest_at"],
            ):
                self.assertTrue(any(
                    item.position.value == position and item.quantity == Quantity.TEMPERATURE
                    and item.source == evidence["source"] and item.received_at == stamp
                    for item in inputs
                ), evidence)
        self.assertIn(event.trace_at, freshness)
        if selected["onset_basis"] == "now_fallback":
            self.assertEqual(event.effective_at, event.trace_at)
            self.assertEqual(consumed, [])
            self.assertIsNone(selected["anchor_proposal_id"])
            self.assertEqual(selected["anchor_confirmation"], ())
            return
        self.assertEqual(selected["onset_basis"], "bound_temperature_anchor")
        self.assertEqual(len(consumed), 1)
        package = consumed[0]
        self.assertEqual(package["confirmation_method"], "smooth_recovery")
        self.assertEqual(package["onset_basis"], selected["onset_basis"])
        self.assertEqual(package["confirmation"], selected["confirmation"])
        self.assertEqual(package["proposal_id"], selected["anchor_proposal_id"])
        self.assertEqual(package["anchor_confirmation"], selected["anchor_confirmation"])
        self.assertIn("unused_confirmation", package)
        self.assertEqual(package["consumed_onset_at"], event.effective_at)
        self.assertEqual(
            event.effective_at, min(witness["onset_at"] for witness in package["witnesses"]),
        )
        self.assertGreater(event.trace_at, event.effective_at)
        self.assertIsNotNone(package["evidence_bound_at"])
        self.assertGreaterEqual(package["evidence_bound_at"], package["first_observed_at"])
        self.assertLessEqual(package["evidence_bound_at"], event.trace_at)
        anchors = {item["candidate_id"]: item for item in selected["anchor_confirmation"]}
        self.assertEqual(set(anchors), set(package["candidate_ids"]))
        for witness in package["witnesses"]:
            anchor = anchors[witness["candidate_id"]]
            opening_witness = opening_witnesses[witness["position"], witness["quantity"]]
            for key in ("source", "time_basis", "source_epochs"):
                self.assertEqual(witness[key], opening_witness[key])
            self.assertTrue(anchor["supported"], anchor)
            self.assertGreater(anchor["fresh_newest_at"], witness["first_newest_at"])
            self.assertLessEqual(anchor["fresh_observed_at"], package["evidence_bound_at"])
            self.assertLessEqual(anchor["newest_at"], package["evidence_bound_at"])
            self.assertGreaterEqual(witness["onset_at"], floor)
            current = confirmations[witness["position"]]
            self.assertGreaterEqual(witness["onset_at"], current["decline_start_at"])
            self.assertLessEqual(witness["onset_at"], current["decline_newest_at"])
            for stamp in (witness["onset_at"], anchor["fresh_newest_at"]):
                self.assertTrue(any(
                    item.position.value == witness["position"]
                    and item.quantity.value == witness["quantity"]
                    and item.source == witness["source"] and item.received_at == stamp
                    for item in inputs
                ), anchor)

    def test_first_two_door_sequences_recover_from_original_temperature(self):
        started = perf_counter()
        origin = datetime.fromisoformat(fixture("session")["origin"])

        def at(hour, minute, second=0):
            return origin.replace(hour=hour, minute=minute, second=second)

        begin, end = at(12, 57), at(13, 9)
        parameters = detection_parameters()
        observed = []
        detector = Detector(parameters, begin, observer=observed.append)
        # R04 actuator evidence: continuously ON 12:52:18.386868–13:24:17.414544.
        # Starting at the slice boundary gives no original before that boundary.
        detector.report_heating(True, begin)
        inputs = []
        path = Path(__file__).parent / "fixtures/replay/R04.samples.jsonl.gz"
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                received = origin + timedelta(seconds=row["t"])
                if received > end:
                    break
                if received < begin or row["role"] == "presence":
                    continue
                position, quantity = row["role"].split("_", 1)
                inputs.append(replace(
                    measurement(
                        Position(position), Quantity(quantity), row["value"], 0,
                        source=f"sensor.fixture_{row['role']}",
                    ),
                    received_at=received,
                ))

        events = []
        cursor = 0
        now = begin
        while now <= end:
            while cursor < len(inputs) and inputs[cursor].received_at <= now:
                self.assertTrue(detector.accept(inputs[cursor]))
                cursor += 1
            events.extend(detector.advance(now, enabled=True))
            now += timedelta(seconds=detector.grid_seconds)

        door_events = [
            event for event in events
            if event.kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE)
        ]
        # These are assessment intervals from the loss/recovery in the original
        # traces. They are not the old detector's second-exact trigger times.
        intervals = (
            (Kind.DOOR_OPEN, at(12, 58, 20), at(12, 58, 50), at(12, 59)),
            (Kind.DOOR_CLOSE, at(12, 58, 45), at(12, 59, 45), at(13, 0)),
            (Kind.DOOR_OPEN, at(13, 5, 30), at(13, 6, 10), at(13, 7)),
            (Kind.DOOR_CLOSE, at(13, 7, 35), at(13, 8, 25), end),
        )
        diagnostics = {
            "events": [
                (event.kind.value, event.effective_at, event.trace_at)
                for event in events
            ],
            "close_evidence": [
                {key: row[key] for key in (
                    "at", "signals", "conditions", "curve_candidates", "curve_proposals",
                )}
                for row in observed
                if row["at"] in (at(12, 59, 45), at(12, 59, 55), at(13, 8, 20), at(13, 8, 30))
            ],
        }
        print(
            f"R04 two-door slice: samples={cursor} ticks={len(observed)} "
            f"wall_seconds={perf_counter() - started:.6f} events={diagnostics['events']!r}",
        )
        self.assertEqual(
            [event.kind for event in door_events],
            [interval[0] for interval in intervals],
            msg=repr(diagnostics),
        )
        opening_witnesses = {}
        opening_floor = None
        for event, (_, earliest, latest, confirmation_limit) in zip(door_events, intervals):
            with self.subTest(kind=event.kind, onset=event.effective_at):
                with self.subTest(assessment="physical_event_onset"):
                    self.assertGreaterEqual(event.effective_at, earliest)
                    self.assertLessEqual(event.effective_at, latest)
                self.assertLessEqual(event.trace_at, confirmation_limit)
                self.assertEqual(event.detected_at, event.trace_at)

                row = next(item for item in observed if item["at"] == event.trace_at)
                route = "door_close" if event.kind == Kind.DOOR_CLOSE else "door_open"
                routes = (route,) if route == "door_close" else (route, "door_open_heating")
                consumed = [
                    proposal for name in routes
                    for proposal in row["curve_proposals"].get(name, ())
                    if proposal["status"] == "consumed"
                    and proposal["confirmed_at"] == event.trace_at
                ]
                if row.get("door_close_confirmation", {}).get("confirmation_method") == "smooth_recovery":
                    floor = max([opening_floor, *(
                        item.effective_at for item in events
                        if item.kind == Kind.VENTILATION
                        and opening_floor <= item.trace_at <= event.trace_at
                    )])
                    self._assert_smooth_close_originals(
                        row, event, inputs, opening_witnesses, parameters.values, floor, consumed,
                    )
                    continue
                self.assertGreater(event.trace_at, event.effective_at)
                self.assertEqual(len(consumed), 1)
                package = consumed[0]
                if event.kind == Kind.DOOR_OPEN:
                    opening_floor = event.effective_at
                    opening_witnesses = {
                        (witness["position"], witness["quantity"]): witness
                        for witness in package["witnesses"]
                    }
                self.assertEqual(package["consumed_onset_at"], event.effective_at)
                self.assertLess(package["first_observed_at"], event.trace_at)
                bound_at = package.get("evidence_bound_at")
                if event.kind == Kind.DOOR_CLOSE and package["quantities"] == ("temperature",):
                    self.assertIsNotNone(bound_at)
                    self.assertGreaterEqual(bound_at, package["first_observed_at"])
                    self.assertLessEqual(bound_at, event.trace_at)
                confirmations = {
                    item["candidate_id"]: item for item in package["confirmation"]
                }
                for witness in package["witnesses"]:
                    fresh = confirmations[witness["candidate_id"]]
                    self.assertTrue(fresh["supported"])
                    self.assertGreater(fresh["fresh_newest_at"], witness["first_newest_at"])
                    self.assertLessEqual(fresh["newest_at"], event.trace_at)
                    stamps = [
                        (stamp, witness["quantity"], witness["source"])
                        for stamp in (witness["onset_at"], fresh["fresh_newest_at"])
                    ]
                    if bound_at is not None:
                        recovery_quantity = fresh["recovery_quantity"]
                        self.assertIn(recovery_quantity, ("temperature", "humidity"))
                        recovery_witness = opening_witnesses[
                            witness["position"], recovery_quantity
                        ]
                        self.assertEqual(fresh["recovery_source"], recovery_witness["source"])
                        self.assertEqual(
                            fresh["recovery_time_basis"], recovery_witness["time_basis"],
                        )
                        self.assertEqual(
                            fresh["recovery_source_epochs"], recovery_witness["source_epochs"],
                        )
                        self.assertLessEqual(fresh["fresh_observed_at"], bound_at)
                        self.assertLessEqual(fresh["recovery_newest_at"], event.trace_at)
                        self.assertGreaterEqual(
                            fresh["recovery_start_at"],
                            event.trace_at - timedelta(seconds=parameters.values[
                                "door_window_seconds" if recovery_quantity == "temperature"
                                else "door_humidity_seconds"
                            ]),
                        )
                        stamps.extend(
                            (stamp, recovery_quantity, fresh["recovery_source"])
                            for stamp in (fresh["recovery_start_at"], fresh["recovery_newest_at"])
                        )
                    for stamp, quantity, source in stamps:
                        self.assertTrue(any(
                            item.position.value == witness["position"]
                            and item.quantity.value == quantity
                            and item.source == source
                            and item.received_at == stamp
                            for item in inputs
                        ))
                if bound_at is not None:
                    self.assertTrue(any(
                        item["recovery_observed_at"] == event.trace_at
                        or (item["recovery_quantity"] == "humidity"
                            and item["current_temperature_fresh_observed_at"] == event.trace_at)
                        for item in confirmations.values()
                    ))

    def test_later_door_closures_recover_from_original_temperature_and_humidity(self):
        origin = datetime.fromisoformat(fixture("session")["origin"])
        parameters = detection_parameters()
        values = parameters.values
        # Supply only the existing curve and ventilation baselines before each
        # opening reference, without carrying earlier detector conclusions in.
        warmup = timedelta(seconds=max(
            values["curve_window_seconds"],
            values["vent_baseline_seconds"] + max(
                values["door_window_seconds"], values["door_humidity_seconds"],
            ),
        ))

        def at(hour, minute, second=0, microsecond=0):
            return origin.replace(
                hour=hour, minute=minute, second=second, microsecond=microsecond,
            )

        # The first closure interval was independently accepted from the real
        # curves. The second is a broad assessment interval of their observed
        # fall-to-plateau recovery, not a ten-second physical contact timestamp:
        # at 15:26:00/20/40 upper T is 86.14/85.29/84.99 and lower T is
        # 78.40/77.07/76.78; RH settles before recovering. Both use raw originals.
        # The first input continues to 14:52 because its upper T rises only
        # around 14:51:20-30. This adds observation time, not a new contact time.
        cases = (
            (
                "after_ventilation", at(14, 48, 40), at(14, 50, 5), at(14, 50, 20),
                at(14, 52),
                at(14, 50, 13, 541953), (at(14, 50, 24), at(14, 50, 45)),
            ),
            (
                "short_under_heating", at(15, 25, 54), at(15, 26), at(15, 27),
                at(15, 28), at(15, 25, 21, 10284), (at(15, 26, 50), at(15, 27)),
            ),
        )
        math_contexts = {
            "after_ventilation": (
                {
                    "reference": "door remains open before accepted 14:50:05-20 closure",
                    "ticks": (at(14, 49, 18), at(14, 49, 45)),
                    "floor": at(14, 48, 41, 42470),
                    "floor_source": "archived confirmed opening",
                    "floor_context_end": at(14, 49, 46),
                    "opening_confirmed_at": at(14, 48, 51),
                    "opening_route": "door_open", "hinge_orders": (2,),
                },
                {
                    "reference": "accepted 14:50:05-20 closure; mathematical shape only",
                    "ticks": (at(14, 50, 24), at(14, 50, 45)),
                    "floor": at(14, 49, 46),
                    "floor_source": "archived ventilation confirmation",
                    "opening_confirmed_at": at(14, 48, 51),
                    "opening_route": "door_open", "hinge_orders": (2,),
                },
            ),
            "short_under_heating": (
                {
                    "reference": "15:26 recovery; final opening floor known only retrospectively",
                    "ticks": tuple(at(15, 26, second) for second in (25, 30, 35, 40, 45)),
                    "floor": at(15, 25, 54, 659854),
                    "floor_source": "final opening onset known retrospectively",
                    "opening_confirmed_at": at(15, 26, 47),
                    "opening_route": "door_open_heating", "hinge_orders": (1, 2),
                    "native_onsets": {
                        Position.UPPER: at(15, 26, 21, 977371),
                        Position.LOWER: at(15, 26, 24, 104382),
                    },
                },
                {
                    "reference": "15:26-27 recovery after archived opening confirmation",
                    "ticks": (at(15, 26, 50), at(15, 26, 55), at(15, 27), at(15, 27, 5)),
                    "floor": at(15, 25, 54, 659854),
                    "floor_source": "archived opening onset, confirmed before these queries",
                    "opening_confirmed_at": at(15, 26, 47),
                    "opening_route": "door_open_heating", "hinge_orders": (2,),
                },
            ),
        }

        def retrospective_math(snapshots, policy, *, hinge_order, context, native_onsets):
            """One explicit family; shape support cannot grant event authority."""
            floor = context["floor"]
            archived_confirmation = context["opening_confirmed_at"]
            queries, overlap = [], []

            def assess(tick, role, points, mode, onset=None):
                result = curve_change(
                    points, policy=policy, onset_at=onset, direction=1,
                    onset_not_before=floor, hinge_order=hinge_order,
                )
                knot = onset if onset is not None else (
                    result.onset_at if result is not None else None
                )
                before = sum(stamp <= knot for stamp, _ in points) if knot else None
                after = len(points) - before if before is not None else None
                record = {
                    "tick": tick, "role": f"{role[0].value}_{role[1].value}",
                    "query": mode, "hinge_order": hinge_order,
                    "floor": floor, "fixed_onset": onset,
                    "first_original": points[0][0] if points else None,
                    "newest_original": points[-1][0] if points else None,
                    "input_count": len(points), "before_count": before,
                    "after_count": after,
                    "fixed_eligible": (
                        onset >= floor and onset in {stamp for stamp, _ in points}
                        and min(before, after) >= policy.min_side_samples
                    ) if onset is not None else None,
                    "support_boundary": (
                        min(before, after) == policy.min_side_samples
                        if knot is not None else None
                    ),
                    "evidence": result.evidence if result is not None else None,
                    **({key: getattr(result, key) for key in (
                        "onset_at", "smooth_model", "smooth_score", "change_score",
                        "score_gain", "slope_before", "slope_after", "slope_change",
                        "curvature_change",
                    )} if result is not None else {}),
                    "after_direction": (
                        result.after_trend.direction
                        if result is not None and result.after_trend is not None else None
                    ),
                }
                return record, result

            for tick, roles in snapshots.items():
                supported, reconfirmed = {"free": [], "fixed": []}, {"free": [], "fixed": []}
                for role, snapshot in roles.items():
                    modes = [("free", None)]
                    if role[0] in native_onsets and role[1] == Quantity.TEMPERATURE:
                        modes.append(("fixed", native_onsets[role[0]]))
                    for mode, onset in modes:
                        record, result = assess(tick, role, snapshot["points"], mode, onset)
                        if result is not None and result.evidence == "supports":
                            supported[mode].append(role)
                            fresh = snapshot.get("fresh")
                            if fresh is not None:
                                fresh_tick, fresh_points = fresh
                                checked, confirmation = assess(
                                    fresh_tick, role, fresh_points,
                                    "frozen_first_fresh", result.onset_at,
                                )
                                checked["initial_newest_original"] = snapshot["points"][-1][0]
                                checked["relative_to_archived_open_confirmation"] = (
                                    "before" if fresh_tick < archived_confirmation
                                    else "same_tick" if fresh_tick == archived_confirmation
                                    else "after"
                                )
                                checked["crosses_known_floor_context_end"] = (
                                    fresh_tick >= context["floor_context_end"]
                                    if context.get("floor_context_end") is not None else False
                                )
                                record["first_fresh_recheck"] = checked
                                if confirmation is not None and confirmation.evidence == "supports":
                                    reconfirmed[mode].append(role)
                        queries.append(record)
                overlap.append({
                    "tick": tick,
                    "temperature_support": {
                        mode: [role[0].value for role in supported_roles
                               if role[1] == Quantity.TEMPERATURE]
                        for mode, supported_roles in supported.items()
                    },
                    "temperature_frozen_fresh_support": {
                        mode: [role[0].value for role in supported_roles
                               if role[1] == Quantity.TEMPERATURE]
                        for mode, supported_roles in reconfirmed.items()
                    },
                    "four_role_free_shape_overlap": len(supported["free"]) == len(roles),
                })
            return {
                "scope": "one explicit family; no model winner, detector event or contact time",
                "hinge_order": hinge_order,
                "reference": context["reference"],
                "onset_not_before": floor, "floor_source": context["floor_source"],
                "floor_context_end": context.get("floor_context_end"),
                "archived_opening_route": context["opening_route"],
                "archived_opening_confirmed_at": archived_confirmation,
                "queries": queries, "shape_overlap": overlap,
            }

        path = Path(__file__).parent / "fixtures/replay/R04.samples.jsonl.gz"
        for case in cases:
            name, opening_reference, earliest, latest, end, heating_at, checkpoints = case
            with self.subTest(case=name):
                started = perf_counter()
                begin = opening_reference - warmup
                observed = []
                detector = Detector(parameters, begin, observer=observed.append)
                # Completed R04 replay 38609533414 actuator history: OFF until
                # 14:50:13.541953, then ON until 15:12:15.892999; OFF until
                # 15:25:21.010284, then ON until 15:39:33.522890. Preserve each
                # switch's original timestamp.
                heating_reports = ((begin, False), (heating_at, True))
                inputs = []
                with gzip.open(path, "rt", encoding="utf-8") as stream:
                    for line in stream:
                        row = json.loads(line)
                        received = origin + timedelta(seconds=row["t"])
                        if received > end:
                            break
                        if received < begin or row["role"] == "presence":
                            continue
                        position, quantity = row["role"].split("_", 1)
                        inputs.append(replace(
                            measurement(
                                Position(position), Quantity(quantity), row["value"], 0,
                                source=f"sensor.fixture_{row['role']}",
                            ),
                            received_at=received,
                        ))

                events = []
                recovery_window = (
                    (at(14, 51), at(14, 52)) if name == "after_ventilation"
                    else (at(15, 26, 55), at(15, 27, 15))
                )
                recovery_calls, owner_rows = [], []
                active_recovery = None
                original_positions = {
                    (item.received_at, item.value): item.position.value
                    for item in inputs if item.quantity == Quantity.TEMPERATURE
                }
                real_recovery = detector_module.curve_recovery
                real_assessment = curves._recovery_assessment
                real_models = curves._smooth_models
                real_valley = curves._smooth_valley
                real_trend = curves.curve_trend

                def endpoints(points):
                    return {
                        "count": len(points),
                        "first": points[0][0] if points else None,
                        "newest": points[-1][0] if points else None,
                    }

                def observe_recovery(points, *, policy, current_not_before=None):
                    nonlocal active_recovery
                    positions = sorted({
                        original_positions[point] for point in points
                        if point in original_positions
                    })
                    call = {
                        "now": now, "positions": positions,
                        "current_not_before": current_not_before,
                        "originals": [stamp for stamp, _ in points],
                        "input": endpoints(points), "stages": [],
                    }
                    recovery_calls.append(call)
                    active_recovery = call
                    try:
                        result = real_recovery(
                            points, policy=policy, current_not_before=current_not_before,
                        )
                        call["return"] = asdict(result) if result is not None else None
                        return result
                    finally:
                        active_recovery = None

                def observe_assessment(points, selected, noise, cutoff, policy):
                    if active_recovery is None:
                        return real_assessment(points, selected, noise, cutoff, policy)
                    stage = {
                        "stage": "assessment", "input": endpoints(points),
                        "selected": endpoints(selected), "noise": noise,
                        "selected_originals": [stamp for stamp, _ in selected],
                        "current_not_before": cutoff,
                        "omitted_originals": sorted(
                            set(active_recovery["originals"]) - {stamp for stamp, _ in points}
                        ),
                    }
                    active_recovery["stages"].append(stage)
                    result = real_assessment(points, selected, noise, cutoff, policy)
                    stage["return"] = (
                        {"features": asdict(result[0]), "residuals": result[1]}
                        if result is not None else None
                    )
                    return result

                def observe_models(times, numbers, noise, policy):
                    result = real_models(times, numbers, noise, policy)
                    if active_recovery is not None:
                        active_recovery["stages"].append({
                            "stage": "smooth_models", "count": len(times), "noise": noise,
                            "return": [
                                {"score": score, "model": model,
                                 "coefficients": coefficients}
                                for score, model, _, coefficients in result[1]
                            ] if result is not None else None,
                        })
                    return result

                def observe_valley(coefficients, value_resolution, count):
                    result = real_valley(coefficients, value_resolution, count)
                    if active_recovery is not None:
                        active_recovery["stages"].append({
                            "stage": "smooth_valley", "coefficients": coefficients,
                            "value_resolution": value_resolution, "count": count,
                            "return": result,
                        })
                    return result

                def observe_trend(points, *, policy):
                    result = real_trend(points, policy=policy)
                    if active_recovery is not None:
                        active_recovery["stages"].append({
                            "stage": "curve_trend", "input": endpoints(points),
                            "return": asdict(result) if result is not None else None,
                        })
                    return result

                retrospective_ticks = {
                    tick for context in math_contexts[name] for tick in context["ticks"]
                }
                retrospective_inputs = {}
                cursor = heating_cursor = 0
                now = begin
                while now <= end:
                    while cursor < len(inputs) and inputs[cursor].received_at <= now:
                        self.assertTrue(detector.accept(inputs[cursor]))
                        cursor += 1
                    while (
                        heating_cursor < len(heating_reports)
                        and heating_reports[heating_cursor][0] <= now
                    ):
                        reported_at, heating = heating_reports[heating_cursor]
                        detector.report_heating(heating, reported_at)
                        heating_cursor += 1
                    if recovery_window[0] <= now <= recovery_window[1]:
                        # Forward each real call once. Missing later stages
                        # remain unknown, rather than being fitted a second time.
                        call_start = len(recovery_calls)
                        with (
                            patch.object(detector_module, "curve_recovery", observe_recovery),
                            patch.object(curves, "_recovery_assessment", observe_assessment),
                            patch.object(curves, "_smooth_models", observe_models),
                            patch.object(curves, "_smooth_valley", observe_valley),
                            patch.object(curves, "curve_trend", observe_trend),
                        ):
                            events.extend(detector.advance(now, enabled=True))
                        positions = {}
                        for position in detector.positions:
                            key = (position, Quantity.TEMPERATURE)
                            series = detector._curve_series[key]
                            cache = detector._curve_recovery_evidence.get(key, {}).get(
                                "smooth_form", {},
                            )
                            features = cache.get("features")
                            positions[position.value] = {
                                **{key: series[key] for key in (
                                    "source", "time_basis", "epoch", "version",
                                    "last_original_version", "last_original_received_at",
                                    "started_at",
                                )},
                                "cache": {key: cache.get(key) for key in (
                                    "evaluation", "original_supported", "first_newest_at",
                                    "fresh_newest_at", "fresh_observed_at",
                                )},
                                "cached_return": {
                                    key: getattr(features, key) for key in (
                                        "evidence", "model", "start_at", "newest_at", "valley_at",
                                        "decline_start_at", "decline_newest_at",
                                        "recovery_start_at", "recovery_newest_at",
                                    )
                                } if features is not None else None,
                                "current_status": detector._curve_status.get(
                                    ("door_close", *key), {},
                                ).get("smooth_recovery"),
                            }
                        owner_rows.append({
                            "now": now, "open": detector.open,
                            "door_context": detector._curve_door_context,
                            "episode_started_at": (
                                detector.door_episode["started_at"]
                                if detector.door_episode is not None else None
                            ),
                            "window_start": now - timedelta(
                                seconds=values["curve_window_seconds"],
                            ),
                            "current_window_start": now - timedelta(
                                seconds=values["door_window_seconds"],
                            ),
                            "recovery_call_indices": list(range(call_start, len(recovery_calls))),
                            "positions": positions,
                        })
                    else:
                        events.extend(detector.advance(now, enabled=True))
                    # Snapshot the normal unfiltered close inputs only. Fits
                    # run separately after the feed, and only on a failed case.
                    if now in retrospective_ticks:
                        retrospective_inputs[now] = {
                            (position, quantity): {"points": detector._curve_points(
                                position, quantity, now,
                            )}
                            for position in detector.positions
                            for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY)
                        }
                    for initial_tick, roles in retrospective_inputs.items():
                        if initial_tick >= now:
                            continue
                        for (position, quantity), snapshot in roles.items():
                            if "fresh" in snapshot or not snapshot["points"]:
                                continue
                            points = detector._curve_points(position, quantity, now)
                            if points and points[-1][0] > snapshot["points"][-1][0]:
                                snapshot["fresh"] = (now, points)
                    now += timedelta(seconds=detector.grid_seconds)

                diagnostic_times = {*checkpoints, latest, end}
                diagnostics = {
                    "case": name,
                    "window": (begin, end),
                    "heating_reports": heating_reports,
                    "closure_interval": (earliest, latest),
                    "events": [
                        (event.kind.value, event.effective_at,
                         event.detected_at, event.trace_at)
                        for event in events
                    ],
                    "evidence": [
                        {
                            "at": row["at"],
                            "signals": row["signals"],
                            "conditions": row["conditions"],
                            "door_close_confirmation": row.get("door_close_confirmation"),
                            "candidates": {
                                route: {
                                    position: {
                                        quantity: {key: candidate.get(key) for key in (
                                            "status", "onset_at", "first_observed_at",
                                            "fresh_observed_at", "slope_before",
                                            "slope_after", "score_gain", "after_trend",
                                            "recovery_trend", "smooth_recovery",
                                        )}
                                        for quantity, candidate in quantities.items()
                                    }
                                    for position, quantities in positions.items()
                                }
                                for route, positions in row["curve_candidates"].items()
                                if route in ("door_open", "door_open_heating", "door_close")
                            },
                            "proposals": {
                                route: [
                                    {key: proposal.get(key) for key in (
                                        "status", "onset_at", "onsets", "quantities",
                                        "first_observed_at", "evidence_bound_at",
                                        "confirmed_at", "consumed_onset_at", "reason",
                                        "confirmation",
                                    )}
                                    for proposal in proposals
                                ]
                                for route, proposals in row["curve_proposals"].items()
                                if route in ("door_open", "door_open_heating", "door_close")
                            },
                        }
                        for row in observed
                        if row["at"] in diagnostic_times or row["signals"]
                    ],
                }
                diagnostic = repr(diagnostics)
                print(
                    f"R04 {name}: samples={cursor} ticks={len(observed)} "
                    f"wall_seconds={perf_counter() - started:.6f} "
                    f"events={diagnostics['events']!r}",
                )
                print("R04 physical onset versus observed close decision: " + json.dumps({
                    "case": name, "physical_interval": (earliest, latest),
                    "input_through": end,
                    "close_decisions": [
                        {"effective_at": event.effective_at, "detected_at": event.detected_at,
                         "trace_at": event.trace_at,
                         "confirmation": next(
                             item.get("door_close_confirmation") for item in observed
                             if item["at"] == event.trace_at
                         ),
                         "effective_in_physical_interval": earliest <= event.effective_at <= latest}
                        for event in events if event.kind == Kind.DOOR_CLOSE
                    ],
                }, default=str, sort_keys=True))
                if name == "after_ventilation":
                    negative_context = math_contexts[name][0]
                    with self.subTest(assessment="still_open_after_earlier_temperature_binding"):
                        self.assertFalse(any(
                            event.kind == Kind.DOOR_CLOSE
                            and negative_context["ticks"][0] <= event.trace_at
                            <= negative_context["floor_context_end"]
                            for event in events
                        ), msg=diagnostic)
                doors = [event for event in events
                         if event.kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE)]
                if ([event.kind for event in doors] != [Kind.DOOR_OPEN, Kind.DOOR_CLOSE]
                        or any(not earliest <= event.effective_at <= latest
                               for event in doors if event.kind == Kind.DOOR_CLOSE)):
                    print("R04 executed smooth recovery diagnosis: " + json.dumps({
                        "case": name, "window": recovery_window,
                        "scope": "actual calls only; an unexecuted stage remains unknown",
                        "owner_rows": owner_rows, "recovery_calls": recovery_calls,
                    }, default=str, sort_keys=True), flush=True)
                if retrospective_inputs and [event.kind for event in doors] != [
                    Kind.DOOR_OPEN, Kind.DOOR_CLOSE,
                ]:
                    for context in math_contexts[name]:
                        snapshots = {
                            tick: retrospective_inputs[tick] for tick in context["ticks"]
                            if tick in retrospective_inputs
                        }
                        for hinge_order in context["hinge_orders"]:
                            print("R04 retrospective stateless diagnosis: " + json.dumps(
                                retrospective_math(
                                    snapshots, detector.curve_policy, hinge_order=hinge_order,
                                    context=context,
                                    native_onsets=(context.get("native_onsets", {})
                                                   if hinge_order == 1 else {}),
                                ),
                                default=str, sort_keys=True,
                            ))
                if name == "after_ventilation" and [event.kind for event in doors] != [
                    Kind.DOOR_OPEN, Kind.DOOR_CLOSE,
                ]:
                    columns = {
                        "T": ("status", "onset_at"),
                        "RH": ("direction", "original_supported", "within_window"),
                        "bound": (
                            "proposal_status", "recovery_quantity",
                            "current_temperature_supported",
                            "current_temperature_fresh_observed_at",
                            "fresh_observed_at", "recovery_observed_at",
                        ),
                    }
                    gate_intervals = (
                        ("still_open_before_physical_close", at(14, 49, 18), at(14, 49, 46)),
                        ("after_accepted_physical_close", checkpoints[0], checkpoints[-1]),
                    )
                    rows = {label: [] for label, _, _ in gate_intervals}
                    for row in observed:
                        interval = next((
                            label for label, start, stop in gate_intervals
                            if start <= row["at"] <= stop
                        ), None)
                        if interval is None:
                            continue
                        confirmations_by_position = {}
                        for proposal in row["curve_proposals"].get("door_close", ()):
                            if (proposal.get("evidence_bound_at") is None
                                    or proposal["quantities"] != ("temperature",)):
                                continue
                            positions = {
                                witness["candidate_id"]: witness["position"]
                                for witness in proposal["witnesses"]
                            }
                            for confirmation in proposal.get("confirmation", ()):
                                position = confirmation.get("position") or positions.get(
                                    confirmation.get("candidate_id"),
                                )
                                confirmations_by_position[position] = (
                                    proposal["status"],
                                    *(confirmation.get(key) for key in columns["bound"][1:]),
                                )
                        rows[interval].append({
                            "at": row["at"],
                            **{
                                position: {
                                    "T": tuple(quantities.get("temperature", {}).get(key)
                                               for key in columns["T"]),
                                    "RH": tuple(quantities.get("humidity", {}).get(
                                        "recovery_trend", {},
                                    ).get(key) for key in columns["RH"]),
                                    "bound": confirmations_by_position.get(position),
                                }
                                for position, quantities in row["curve_candidates"].get(
                                    "door_close", {},
                                ).items()
                            },
                        })
                    for label, start, stop in gate_intervals:
                        print("R04 observed supplement timing diagnosis: " + json.dumps(
                            {"case": name, "interval": label, "window": (start, stop),
                             "accepted_closure_interval": (earliest, latest),
                             "columns": columns, "rows": rows[label]},
                            default=str, sort_keys=True,
                        ))
                self.assertEqual(
                    [event.kind for event in doors],
                    [Kind.DOOR_OPEN, Kind.DOOR_CLOSE], msg=diagnostic,
                )
                opening, close = doors
                opening_row = next(
                    item for item in observed if item["at"] == opening.trace_at
                )
                opening_packages = [
                    proposal for route in ("door_open", "door_open_heating")
                    for proposal in opening_row["curve_proposals"].get(route, ())
                    if proposal["status"] == "consumed"
                ]
                self.assertEqual(len(opening_packages), 1, msg=diagnostic)
                opening_witnesses = {
                    (witness["position"], witness["quantity"]): witness
                    for witness in opening_packages[0]["witnesses"]
                }
                self.assertLess(opening.effective_at, close.effective_at, msg=diagnostic)
                self.assertLess(opening.trace_at, close.trace_at, msg=diagnostic)
                with self.subTest(assessment="physical_closure_onset"):
                    self.assertGreaterEqual(close.effective_at, earliest, msg=diagnostic)
                    self.assertLessEqual(close.effective_at, latest, msg=diagnostic)
                self.assertLessEqual(close.trace_at, end, msg=diagnostic)
                row = next(item for item in observed if item["at"] == close.trace_at)
                smooth_selected = (
                    row.get("door_close_confirmation", {}).get("confirmation_method")
                    == "smooth_recovery"
                )
                for event in doors:
                    if event.kind == Kind.DOOR_CLOSE and smooth_selected:
                        continue
                    self.assertGreater(event.trace_at, event.effective_at, msg=diagnostic)
                    self.assertEqual(event.detected_at, event.trace_at, msg=diagnostic)
                    self.assertIn(
                        event.effective_at, {item.received_at for item in inputs},
                        msg=diagnostic,
                    )
                consumed = [
                    proposal for proposal in row["curve_proposals"].get("door_close", ())
                    if proposal["status"] == "consumed"
                ]
                if smooth_selected:
                    floor = max([opening.effective_at, *(
                        event.effective_at for event in events
                        if event.kind == Kind.VENTILATION
                        and opening.trace_at <= event.trace_at <= close.trace_at
                    )])
                    self._assert_smooth_close_originals(
                        row, close, inputs, opening_witnesses, values, floor, consumed,
                    )
                    continue
                self.assertEqual(len(consumed), 1, msg=diagnostic)
                package = consumed[0]
                self.assertEqual(
                    package["consumed_onset_at"], close.effective_at, msg=diagnostic,
                )
                self.assertLess(package["first_observed_at"], close.trace_at, msg=diagnostic)
                self.assertEqual(package["confirmed_at"], close.trace_at, msg=diagnostic)
                confirmations = {
                    item["candidate_id"]: item for item in package["confirmation"]
                }
                for witness in package["witnesses"]:
                    fresh = confirmations[witness["candidate_id"]]
                    self.assertTrue(fresh["supported"], msg=diagnostic)
                    self.assertGreater(
                        fresh["fresh_newest_at"], witness["first_newest_at"],
                        msg=diagnostic,
                    )
                    self.assertLessEqual(fresh["newest_at"], close.trace_at, msg=diagnostic)
                    for stamp in (witness["onset_at"], fresh["fresh_newest_at"]):
                        self.assertTrue(any(
                            item.position.value == witness["position"]
                            and item.quantity.value == witness["quantity"]
                            and item.source == witness["source"]
                            and item.received_at == stamp
                            for item in inputs
                        ), msg=diagnostic)
                    if package.get("evidence_bound_at") is None:
                        continue
                    recovery_quantity = fresh["recovery_quantity"]
                    self.assertIn(recovery_quantity, ("temperature", "humidity"))
                    recovery_witness = opening_witnesses[
                        witness["position"], recovery_quantity
                    ]
                    for key in ("source", "time_basis", "source_epochs"):
                        self.assertEqual(
                            fresh[f"recovery_{key}"], recovery_witness[key],
                            msg=diagnostic,
                        )
                    window = timedelta(seconds=values[
                        "door_window_seconds" if recovery_quantity == "temperature"
                        else "door_humidity_seconds"
                    ])
                    self.assertGreaterEqual(
                        fresh["recovery_start_at"],
                        max(witness["onset_at"], close.trace_at - window),
                        msg=diagnostic,
                    )
                    self.assertLessEqual(
                        fresh["recovery_start_at"], fresh["recovery_newest_at"],
                        msg=diagnostic,
                    )
                    self.assertLessEqual(fresh["recovery_newest_at"], close.trace_at, msg=diagnostic)
                    self.assertLessEqual(fresh["recovery_observed_at"], close.trace_at, msg=diagnostic)
                    recovery_stamps = [fresh["recovery_start_at"], fresh["recovery_newest_at"]]
                    for stamp in recovery_stamps:
                        self.assertTrue(any(
                            item.position.value == witness["position"]
                            and item.quantity.value == recovery_quantity
                            and item.source == fresh["recovery_source"]
                            and item.received_at == stamp
                            for item in inputs
                        ), msg=diagnostic)
                    if recovery_quantity == "humidity":
                        self.assertTrue(fresh["current_temperature_supported"], msg=diagnostic)
                        newest = fresh["current_temperature_newest_at"]
                        self.assertGreaterEqual(
                            newest, close.trace_at - timedelta(seconds=values["door_window_seconds"]),
                            msg=diagnostic,
                        )
                        self.assertLessEqual(newest, close.trace_at, msg=diagnostic)
                        self.assertTrue(any(
                            item.position.value == witness["position"]
                            and item.quantity == Quantity.TEMPERATURE
                            and item.source == witness["source"]
                            and item.received_at == newest
                            for item in inputs
                        ), msg=diagnostic)
                if package.get("evidence_bound_at") is not None:
                    self.assertTrue(any(
                        item["recovery_observed_at"] == close.trace_at
                        or (item["recovery_quantity"] == "humidity"
                            and item["current_temperature_fresh_observed_at"] == close.trace_at)
                        for item in confirmations.values()
                    ), msg=diagnostic)
