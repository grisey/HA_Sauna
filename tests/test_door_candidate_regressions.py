"""Focused counterexamples on existing inputs; real replay review remains separate."""

from datetime import timedelta
import hashlib
import json
import math
from pathlib import Path
import unittest

import test_detector as detector_fixtures
import test_original_curve_evidence as original_fixtures
from curve_slope_diagnostics import SlopeTransitionDiagnostics
from test_detector import T0, trace
from test_original_curve_evidence import CLIMATE, ORIGIN

from custom_components.ha_sauna.core.detector import Detector
from custom_components.ha_sauna.core.models import Measurement, Position, Quantity
from custom_components.ha_sauna.core.timeline import Kind


class DoorCandidateRegressionTests(unittest.TestCase):
    def setUp(self):
        # Composition reuses the declared input construction without inheriting
        # and rerunning the existing test methods.
        self.fixture = original_fixtures.OriginalCurveFollowupTests()
        self.fixture.setUp()

    def _open_door(self):
        fixture = self.fixture
        detector, events, _ = fixture._bind("opening")
        events += fixture._ramp(detector, 4 * fixture.count, fixture.fall_step)
        self.assertEqual([event.kind for event in events].count(Kind.DOOR_OPEN), 1)
        self.assertTrue(detector.open)
        return detector, events

    def test_sustained_braking_can_close_below_zero_temperature_slope(self):
        fixture = self.fixture
        for isolated_edge in (False, True):
            with self.subTest(isolated_edge=isolated_edge):
                detector, events = self._open_door()
                observed = []
                detector.observer = observed.append
                braking_at = fixture._at(detector.index)
                slope_diagnostic = SlopeTransitionDiagnostics(self, detector)
                slope_diagnostic.start_control(braking_at)
                delta = tuple(
                    value / fixture.count if role.endswith("temperature") else 0
                    for role, value in zip(CLIMATE, fixture.fall_step, strict=True)
                )
                self.assertTrue(all(
                    value < 0 for role, value in zip(CLIMATE, delta, strict=True)
                    if role.endswith("temperature")
                ))
                events += fixture._ramp(detector, fixture.count, delta)
                if isolated_edge:
                    # The existing plateau regression uses the same 2.6-scale
                    # endpoint disturbance: one resolved secant is not a new
                    # sustained physical descent.
                    current = fixture._latest(detector)
                    noise = fixture.values["curve_original_noise_temperature_lower"]
                    changed = tuple(
                        value - 2.6 * noise if role == "lower_temperature" else value
                        for role, value in zip(CLIMATE, current, strict=True)
                    )
                    events += fixture._feed(detector, (changed,))
                events += fixture._ramp(detector, fixture.tail_ticks, delta)
                case = f"linear_negative_braking:isolated_edge={isolated_edge}"
                slope_diagnostic.print_closures(case, events=events)
                slope_diagnostic.print_control(case)
                closes = [event for event in events if event.kind == Kind.DOOR_CLOSE]
                self.assertEqual(len(closes), 1)
                self.assertGreater(closes[0].trace_at, braking_at)
                self.assertLessEqual(closes[0].effective_at, closes[0].trace_at)
                self.assertFalse(detector.open)
                row = next(item for item in observed if item["at"] == closes[0].trace_at)
                form = row["original_forms"]
                proof = closes[0].door_close_evidence
                print("NEGATIVE_BRAKING_NATIVE_ATTRIBUTION=" + json.dumps({
                    "case": case,
                    "declared_braking_at": braking_at,
                    "effective_at": closes[0].effective_at,
                    "detected_at": closes[0].detected_at,
                    "trace_at": closes[0].trace_at,
                    "channels": closes[0].channels,
                    "native_evidence": {
                        "candidate_at": proof.candidate_at,
                        "confirmed_at": proof.confirmed_at,
                    } if proof is not None else None,
                    "position_selection": row.get("position_selection", {}).get("door_close"),
                    "original_forms": {key: form.get(key) for key in (
                        "candidate_received_at", "confirmation_received_at",
                        "candidate_originals", "confirmation_originals",
                        "new_original", "later_original_confirmation",
                        "temperature_close_terms", "temperature_level_followup",
                        "temperature_braking_witnesses", "followup",
                    )},
                    "consumed_close_proposals": [
                        item for item in row["curve_proposals"].get("door_close", ())
                        if item["status"] == "consumed"
                    ],
                }, default=str, sort_keys=True))
                self.assertGreaterEqual(closes[0].effective_at, braking_at)
                self.assertIsNotNone(proof)
                self.assertLess(proof.candidate_at, proof.confirmed_at)
                self.assertEqual(proof.confirmed_at, form["confirmation_received_at"])
                self.assertLessEqual(proof.confirmed_at, closes[0].trace_at)
                self.assertTrue(form["new_original"])
                self.assertTrue(form["later_original_confirmation"]["result"])
                consumed = [item for item in row["curve_proposals"].get("door_close", ())
                            if item["status"] == "consumed"]
                if form["discrete_temperature_braking"]["result"]:
                    witnesses = form["temperature_braking_witnesses"]
                    self.assertEqual(set(witnesses), set(closes[0].channels))
                    self.assertEqual(len(consumed), 1)
                    self.assertEqual(consumed[0]["attribution_basis"], "original_close_binding")
                    self.assertEqual(
                        set(consumed[0]["consumed_candidate_ids"]),
                        {witness["candidate_id"] for witness in witnesses.values()},
                    )
                    for witness in witnesses.values():
                        self.assertIsNotNone(witness["fresh_newest_at"])
                        self.assertIsNotNone(witness["fresh_observed_at"])
                        self.assertGreater(witness["fresh_newest_at"], witness["first_newest_at"])
                        self.assertLessEqual(witness["fresh_observed_at"], proof.confirmed_at)
                else:
                    # A slightly negative measured slope can be unresolved
                    # at the configured scale. Its two own settled blocks
                    # prove the close, not an unrelated historical onset.
                    self.assertTrue(form["temperature_close_terms"]["plateau_close"])
                    self.assertTrue(form["sustained_temperature_braking"]["result"])
                    self.assertEqual(closes[0].effective_at, closes[0].trace_at)
                    self.assertFalse(consumed)
                    self.assertFalse(any(
                        item["status"] in ("available", "bound")
                        for item in row["curve_proposals"].get("door_close", ())
                    ))
                    followups = form["temperature_level_followup"]
                    self.assertEqual(set(followups), set(closes[0].channels))
                    for roles in followups.values():
                        self.assertEqual(set(roles), {"temperature"})
                        support = roles["temperature"]
                        self.assertEqual(support["fresh_previous"], fixture.count)
                        self.assertEqual(support["fresh_current"], fixture.count)
                        self.assertLess(support["current_direction"], 0)
                        for key in ("previous_direction", "current_direction"):
                            self.assertLessEqual(
                                abs(support[key]), fixture.values["curve_original_significance"],
                            )
                        self.assertGreater(support["current_start_at"], support["reference_newest_at"])
                        self.assertLessEqual(support["newest_at"], proof.confirmed_at)

    def test_smooth_open_cooling_is_not_a_discrete_closing_transition(self):
        fixture = self.fixture
        detector, events = self._open_door()
        slope_diagnostic = SlopeTransitionDiagnostics(self, detector)
        slope_diagnostic.start_control(fixture._at(detector.index))
        initial = fixture._latest(detector)
        duration = fixture.tail_ticks
        # Analytic continuation with unchanged initial slope and smoothly
        # weakening cooling. The input contains no later slope discontinuity.
        states = (
            tuple(
                value + change * duration * (1 - math.exp(-tick / duration))
                if role.endswith("temperature") else value
                for role, value, change in zip(
                    CLIMATE, initial, fixture.fall_step, strict=True,
                )
            )
            for tick in range(1, duration + 1)
        )
        events += fixture._feed(detector, states)
        slope_diagnostic.print_closures("smooth_open_cooling", events=events)
        slope_diagnostic.print_control("smooth_open_cooling")
        self.assertNotIn(Kind.DOOR_CLOSE, [event.kind for event in events])
        self.assertTrue(detector.open)

    def test_recorded_closed_door_infusions_keep_the_following_exit_available(self):
        fixture = self.fixture
        root = Path(__file__).resolve().parent
        manifest = json.loads((root / "scenario_manifest.json").read_text())
        source_path = root / manifest["source_curves"]
        payload = source_path.read_bytes()
        self.assertEqual(hashlib.sha256(payload).hexdigest(), manifest["source_curves_sha256"])
        curves = json.loads(payload)["curves"]
        detector, events = fixture._detector(), []
        observed_openings = []
        supplied_originals = {role: {} for role in CLIMATE}

        def observe(row):
            if Kind.DOOR_OPEN in row.get("signals", ()):
                diagnostic = detector_fixtures.opening_diagnostic(detector, row)
                diagnostic["position_selection"] = row.get("position_selection", {})
                diagnostic["position_evidence"] = row.get("position_evidence", {})
                diagnostic["humidity_context"] = {
                    position.value: tuple(series[field] for field in ("source", "time_basis", "epoch"))
                    for (position, quantity), series in detector._curve_series.items()
                    if quantity == Quantity.HUMIDITY
                }
                observed_openings.append(diagnostic)

        detector.observer = observe
        elapsed = 0
        exit_at = None
        for name in ("occupied_infusion", "exit"):
            curve = curves[name]
            columns = tuple(curve["roles"].index(role) + 1 for role in CLIMATE)
            for row in curve["rows"]:
                if name == "exit" and row[0] == 0:
                    continue  # The join is the same already supplied original.
                at = ORIGIN + timedelta(seconds=elapsed + row[0])
                for role, column in zip(CLIMATE, columns, strict=True):
                    position, quantity = role.split("_", 1)
                    value = row[column]
                    self.assertTrue(detector.accept(Measurement(
                        Position(position), Quantity(quantity), value, str(value),
                        f"sensor.followup_{role}", at, at,
                    )))
                    supplied_originals[role][at] = value
                events.extend(detector.advance(at, enabled=True))
            if name == "occupied_infusion":
                self.assertNotIn(
                    Kind.DOOR_OPEN, [event.kind for event in events],
                    msg="CLOSED_DOOR_OPENING_EVIDENCE=" + json.dumps(
                        observed_openings, default=str, sort_keys=True,
                    ),
                )
                self.assertIn(Kind.INFUSION, [event.kind for event in events])
                elapsed += curve["duration_s"]
                exit_at = ORIGIN + timedelta(seconds=elapsed)
        opens = [event for event in events if event.kind == Kind.DOOR_OPEN]
        self.assertEqual(len(opens), 1)
        self.assertGreaterEqual(opens[0].effective_at, exit_at, msg=json.dumps(
            observed_openings, default=str, sort_keys=True,
        ))
        self.assertLessEqual(opens[0].effective_at, opens[0].trace_at)
        opening = opens[0]
        observed = next(item for item in observed_openings if item["at"] == opening.trace_at)
        forms, witnesses = observed["original_forms"], observed["onset_witnesses"]
        self.assertEqual({key[1].value for key, _ in witnesses}, set(opening.channels))
        consumed = next(proposal for proposal in observed["opening_proposals"]
                        if proposal.get("status") == "consumed"
                        and proposal["confirmed_at"] == opening.detected_at)
        self.assertEqual(
            (consumed["consumed_onset_at"], consumed["consumed_candidate_ids"]),
            (opening.effective_at, tuple(witness.candidate_id for _, witness in witnesses)),
        )
        self.assertEqual(opening.effective_at, min(witness.onset_at for _, witness in witnesses))
        bound = {name: humidity for name, _, humidity in forms["candidate_originals"]}
        confirmed = {name: humidity for name, _, humidity in forms["confirmation_originals"]}
        for key, witness in witnesses:
            name, role = key[1].value, f"{key[1].value}_humidity"
            source, time_basis, epoch = observed["humidity_context"][name]
            self.assertEqual(
                (key[0], key[2], source, time_basis, witness.context[1],
                 forms["bound_sources"][name][1],
                 witness.candidate_id.startswith(f"door_open:{name}:humidity:{source}:")),
                ("door_open", Quantity.HUMIDITY, f"sensor.followup_{role}",
                 "measured", (epoch,), source, True),
            )
            self.assertEqual(
                (witness.onset_at, witness.features.evidence,
                 witness.features.after_trend.direction, witness.features.slope_change < 0),
                (witness.features.onset_at, "supports", -1, True),
            )
            self.assertEqual(witness.original_points, tuple(
                (stamp, supplied_originals[role][stamp]) for stamp, _ in witness.original_points
            ))
            self.assertTrue(
                exit_at <= witness.onset_at < witness.features.newest_at
                == witness.original_points[-1][0] <= forms["onset_witness_received_at"]
                <= confirmed[name] <= forms["confirmation_received_at"] <= opening.trace_at
                and witness.first_observed_at <= forms["onset_witness_received_at"]
            )
            self.assertIn(confirmed[name], supplied_originals[role])
            self.assertGreater(confirmed[name], bound[name])

    def test_real_door_fall_after_infusion_may_remain_above_preinfusion_temperature(self):
        flow = detector_fixtures.InfusionLifecycleTests()
        flow.setUp()
        points, peak, door_at, fall = flow._infusion_then_independent_door()
        gain = abs(flow.temperature - trace(85)[0])
        self.assertGreater(gain, 0)
        temperatures = [
            (0, flow.temperature), (flow.begin, flow.temperature),
            (peak, flow.temperature + 2 * gain),
            (door_at, flow.temperature + 2 * gain),
            (fall, flow.temperature + gain),
        ]
        detector, events = Detector(flow.parameters, T0), []
        observed = []

        def observe(row):
            forms = row.get("original_forms", {})
            guard = forms.get("infusion_decay_guard") or {}
            candidate = (detector.door_episode or {}).get("original_open") or {}
            observed.append({
                "at": row["at"],
                "opening": {key: forms.get(key) for key in (
                    "received_at", "candidate_received_at", "opening_candidate_reset_reason",
                    "binding_humidity_fall_onset", "binding_humidity_fall_kink",
                    "complete_fresh_original_block", "contiguous_candidate_support",
                    "lower_level", "lowering_witness_received_at", "original_fall_evidence",
                    "resolved_joint_fall", "bound_humidity_onsets", "binding_onsets",
                    "humidity_branch_counterform", "humidity_fall_ended", "humidity_recovery",
                    "temperature_fall_counterform", "current_temperature_recovery",
                    "opening_confirmation",
                )},
                "bound_reference_levels": {
                    f"{name}:{quantity}": {
                        "start_at": window.start_at, "newest_at": window.newest_at,
                        "previous_mean": window.mean_minus, "current_mean": window.mean,
                        "previous_slope": window.b, "current_slope": window.v,
                    }
                    for name, pair in candidate.get("windows", ())
                    for quantity, window in (
                        ("temperature", pair.temperature), ("humidity", pair.humidity),
                    )
                },
                "local_temperature_references": {
                    name: {
                        "start_at": window.start_at, "newest_at": window.newest_at,
                        "previous": window.previous, "current": window.current,
                        "previous_mean": window.mean_minus, "current_mean": window.mean,
                        "previous_slope": window.b, "current_slope": window.v,
                    }
                    for name, window in candidate.get("temperature_fall_witnesses", {}).items()
                },
                "infusion_guard": {
                    "result": guard.get("result"), "reason": guard.get("reason"),
                    "local_fall": guard.get("new_local_temperature_fall"),
                    "native_references": guard.get("native_infusion_references"),
                },
            })

        detector.observer = observe
        flow._feed(detector, door_at, points, events, temperatures=temperatures)
        self.assertIn(Kind.INFUSION, [event.kind for event in events])
        self.assertIsNotNone(detector.infusion_episode)
        self.assertEqual(detector.infusion_episode.phase, "active")
        flow._feed(detector, fall, points, events, temperatures=temperatures)
        opens = [event for event in events if event.kind == Kind.DOOR_OPEN]
        self.assertEqual(len(opens), 1, msg=json.dumps(
            observed, default=str, sort_keys=True,
        ))
        observed = flow._value(temperatures, (opens[0].trace_at - T0).total_seconds())
        self.assertGreater(observed, flow.temperature)
        self.assertGreaterEqual(opens[0].effective_at, flow._at(door_at))
        self.assertLessEqual(opens[0].effective_at, opens[0].trace_at)


if __name__ == "__main__":
    unittest.main()
