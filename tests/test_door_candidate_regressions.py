"""Focused counterexamples on existing inputs; real replay review remains separate."""

from datetime import timedelta
import hashlib
import json
import math
from pathlib import Path
import unittest
from unittest.mock import patch

import test_detector as detector_fixtures
import test_original_curve_evidence as original_fixtures
from test_detector import T0, trace
from test_original_curve_evidence import CLIMATE, ORIGIN

from custom_components.ha_sauna.core import detector as detector_core
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
                closes = [event for event in events if event.kind == Kind.DOOR_CLOSE]
                self.assertEqual(len(closes), 1)
                self.assertGreater(closes[0].trace_at, braking_at)
                self.assertLessEqual(closes[0].effective_at, closes[0].trace_at)
                self.assertFalse(detector.open)
                row = next(item for item in observed if item["at"] == closes[0].trace_at)
                form = row["original_forms"]
                self.assertTrue(form["discrete_temperature_braking"]["result"])
                witnesses = form["temperature_braking_witnesses"]
                self.assertEqual(set(witnesses), set(closes[0].channels))
                for witness in witnesses.values():
                    self.assertIsNotNone(witness["fresh_newest_at"])
                    self.assertIsNotNone(witness["fresh_observed_at"])
                    self.assertGreater(witness["fresh_newest_at"], witness["first_newest_at"])
                    self.assertLessEqual(witness["fresh_observed_at"], form["confirmation_received_at"])

    def test_smooth_open_cooling_is_not_a_discrete_closing_transition(self):
        fixture = self.fixture
        detector, events = self._open_door()
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
        exit_start = ORIGIN + timedelta(seconds=curves["occupied_infusion"]["duration_s"])
        exit_end = exit_start + timedelta(seconds=curves["exit"]["duration_s"])
        local_fall_calls = []
        pending_local_calls = []
        infusion_guard_calls = []
        access_sequence = 0
        native_local_fall_intervals = detector._original_local_fall_intervals
        native_infusion_guard = detector._original_opening_infusion_guard
        native_window_changes = detector_core.original_window_changes

        def window_diagnostic(window):
            return {
                key: getattr(window, key) for key in (
                    "start_at", "newest_at", "previous", "current", "scale",
                    "b", "v", "e_minus", "e", "mean_minus", "mean",
                )
            }

        def candidate_diagnostic(candidate):
            candidate = candidate or {}
            return {
                "received_at": candidate.get("received_at"),
                "intervals": {
                    group: {
                        name: {
                            "previous": native["interval"].previous,
                            "current": native["interval"].current,
                            "received_at": native["received_at"],
                            "window_start_at": native["window"].start_at,
                            "window_newest_at": native["window"].newest_at,
                        } for name, native in candidate.get(key, {}).items()
                    } for group, key in (
                        ("binding", "humidity_onset_intervals"),
                        ("transition", "humidity_fall_transition_intervals"),
                        ("local", "local_humidity_onset_intervals"),
                    )
                },
                "local_temperature_references": {
                    name: window_diagnostic(window)
                    for name, window in candidate.get("temperature_fall_witnesses", {}).items()
                },
                "onset_witnesses": tuple({
                    "position": key[1].value, "quantity": key[2].value,
                    "onset_at": witness.onset_at, "candidate_id": witness.candidate_id,
                } for key, witness in candidate.get("onset_witnesses", ())),
            }

        def finish_local_calls(phase, received_at, current_candidate=None):
            current_candidate = (current_candidate if current_candidate is not None else
                                 (detector.door_episode or {}).get("original_open"))
            for call, bound_candidate in pending_local_calls:
                # The initial local call precedes candidate creation. The next
                # native guard receives the actual newly created candidate.
                held = bound_candidate if bound_candidate is not None else current_candidate
                call["held_state_before_next_access"] = candidate_diagnostic(held)
                call["candidate_removed_or_replaced"] = held is not current_candidate
                call["next_access"] = {
                    "sequence": access_sequence, "phase": phase, "received_at": received_at,
                }
            pending_local_calls.clear()

        def observe_infusion_guard(candidate, prefix, positions):
            nonlocal access_sequence
            access_sequence += 1
            finish_local_calls("infusion_guard", prefix.received_at, candidate)
            result = native_infusion_guard(candidate, prefix, positions)
            infusion_guard_calls.append({
                "received_at": prefix.received_at,
                "sequence": access_sequence,
                "candidate_received_at": candidate["received_at"],
                "native_result": result,
                "candidate_after_guard": candidate_diagnostic(candidate),
            })
            return result

        def observe_local_fall_intervals(prefix, windows):
            nonlocal access_sequence
            access_sequence += 1
            # Read only calls made by this owner; a skipped secant calculation
            # stays absent rather than being evaluated by the diagnostic.
            finish_local_calls("local_fall_intervals", prefix.received_at)
            calculated = []
            candidate = (detector.door_episode or {}).get("original_open")
            before = candidate_diagnostic(candidate)

            def observe_window_changes(window, *, tolerance=0.0):
                result = native_window_changes(window, tolerance=tolerance)
                calculated.append((window, tolerance, result))
                return result

            with patch.object(detector_core, "original_window_changes", observe_window_changes):
                result = native_local_fall_intervals(prefix, windows)
            kinks = dict(prefix.humidity_kinks)
            call = {
                "sequence": access_sequence,
                "received_at": prefix.received_at, "original": prefix.original,
                "candidate_before": before,
                "positions": {
                    name: {
                        "window": window_diagnostic(pair.humidity),
                        "kink_valid": kinks.get(name) is not None,
                        "kink_owns_window": (kinks[name].window is pair.humidity
                                             if kinks.get(name) is not None else None),
                        "kink_window_support": ({
                            "start_at": kinks[name].window.start_at,
                            "newest_at": kinks[name].window.newest_at,
                        } if kinks.get(name) is not None else None),
                        "kink": ({
                            key: getattr(kinks[name], key) for key in (
                                "status", "factor", "history_count", "history_start",
                                "history_end", "history_scale", "catalog_scale",
                                "used_scale", "delta", "tolerance", "effective_change",
                            )
                        } if kinks.get(name) is not None else None),
                        "window_changes": tuple({
                            "tolerance": tolerance,
                            "result": (tuple({
                                key: getattr(change, key)
                                for key in ("previous", "current", "direction", "change")
                            } for change in changes) if changes is not None else None),
                        } for window, tolerance, changes in calculated if window is pair.humidity),
                        "returned_interval": ({
                            key: getattr(result[name]["interval"], key)
                            for key in ("previous", "current", "direction", "change")
                        } if name in result else None),
                    }
                    for name, pair in windows
                },
            }
            local_fall_calls.append(call)
            pending_local_calls.append((call, candidate))
            return result

        owner_patch = patch.object(
            detector, "_original_local_fall_intervals", observe_local_fall_intervals,
        )
        owner_patch.start()
        self.addCleanup(owner_patch.stop)
        guard_patch = patch.object(detector, "_original_opening_infusion_guard", observe_infusion_guard)
        guard_patch.start()
        self.addCleanup(guard_patch.stop)

        def observe(row):
            nonlocal access_sequence
            access_sequence += 1
            finish_local_calls("observer", row["at"])
            diagnostic = detector_fixtures.opening_diagnostic(detector, row)
            forms = diagnostic["original_forms"] or {}
            candidate = (detector.door_episode or {}).get("original_open") or {}
            diagnostic["original_forms"] = {
                key: forms.get(key) for key in (
                    "received_at", "new_original", "evaluable", "candidate_received_at",
                    "opening_candidate_reset_reason", "binding_humidity_fall_onset",
                    "binding_humidity_fall_kink", "binding_humidity_fall",
                    "native_humidity_onset_intervals", "binding_onsets", "binding_support",
                    "onset_witness_received_at", "complete_fresh_original_block",
                    "contiguous_candidate_support", "lower_level", "lowering_witness_received_at",
                    "resolved_joint_fall", "bound_humidity_onsets", "infusion_decay_guard",
                    "humidity_branch_counterform", "humidity_fall_ended", "humidity_recovery",
                    "temperature_fall_counterform", "temperature_recovery_counterform",
                    "temperature_fall_ended", "opening_confirmation", "confirmation_support",
                )
            }
            # Keep the native witness identity and support, not a repeated copy
            # of every original retained by each fitted beginning witness.
            diagnostic["onset_witnesses"] = tuple({
                "route": key[0], "position": key[1].value, "quantity": key[2].value,
                "onset_at": witness.onset_at, "candidate_id": witness.candidate_id,
                "first_at": witness.original_points[0][0],
                "newest_at": witness.features.newest_at,
                "original_count": len(witness.original_points),
            } for key, witness in diagnostic["onset_witnesses"])
            diagnostic["local_temperature_references"] = {
                name: window_diagnostic(window)
                for name, window in candidate.get("temperature_fall_witnesses", {}).items()
            }
            diagnostic["candidate_received_at"] = candidate.get("received_at")
            diagnostic["door_open_localization"] = row.get("door_open_localization")
            diagnostic["door_open_onset_attribution"] = row.get("door_open_onset_attribution")
            diagnostic["local_fall_calls"] = tuple(local_fall_calls)
            diagnostic["infusion_guard_calls"] = tuple(infusion_guard_calls)
            local_fall_calls.clear()
            infusion_guard_calls.clear()
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
                events.extend(detector.advance(at, enabled=True))
            if name == "occupied_infusion":
                self.assertNotIn(Kind.DOOR_OPEN, [event.kind for event in events])
                self.assertIn(Kind.INFUSION, [event.kind for event in events])
                elapsed += curve["duration_s"]
                exit_at = ORIGIN + timedelta(seconds=elapsed)
        # Follow candidates that actually overlap the existing exit, including
        # their earlier binding and own supporting windows before that join.
        trace_end = min((
            row["at"] for row in observed_openings
            if exit_start <= row["at"] <= exit_end and Kind.DOOR_OPEN in row["signals"]
        ), default=exit_end)
        exit_candidates = {
            row["candidate_received_at"] for row in observed_openings
            if exit_start <= row["at"] <= trace_end and row["candidate_received_at"] is not None
        }
        context_start = min((
            stamp
            for row in observed_openings if row["candidate_received_at"] in exit_candidates
            for support in (row["original_forms"].get("binding_support") or {}).values()
            for stamp in support.values()
        ), default=exit_start)
        relevant = [
            row for row in observed_openings
            if min(exit_start, context_start) <= row["at"] <= trace_end
        ]
        observed_openings = []
        previous = {}
        for row in relevant:
            # All RH owner calls remain explicit; repeated retained state is
            # emitted only when it changes, with null retaining reset evidence.
            state = {**row, **row["original_forms"]}
            for key in ("at", "signals", "original_forms", "local_fall_calls", "infusion_guard_calls"):
                state.pop(key, None)
            observed_openings.append({
                "at": row["at"], "signals": row["signals"],
                "changes": {key: value for key, value in state.items()
                            if key not in previous or value != previous[key]},
                "local_fall_calls": row["local_fall_calls"],
                "infusion_guard_calls": row["infusion_guard_calls"],
            })
            previous = state
        opens = [event for event in events if event.kind == Kind.DOOR_OPEN]
        self.assertEqual(len(opens), 1)
        self.assertGreaterEqual(opens[0].effective_at, exit_at, msg=json.dumps(
            observed_openings, default=str, sort_keys=True,
        ))
        self.assertLessEqual(opens[0].effective_at, opens[0].trace_at)

    def test_real_door_fall_after_infusion_may_remain_above_preinfusion_temperature(self):
        flow = detector_fixtures.InfusionLifecycleTests()
        flow.setUp()
        points, _, _, _ = flow._scenario(merged=False)
        peak = flow.begin + flow.width
        # Allow both the local turn and its two subsequent complete blocks
        # while the final temperature still stays above the pre-infusion value.
        fall = peak + 2 * flow.width
        gain = abs(flow.temperature - trace(85)[0])
        self.assertGreater(gain, 0)
        temperatures = [
            (0, flow.temperature), (flow.begin, flow.temperature),
            (peak, flow.temperature + 2 * gain),
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
        flow._feed(detector, peak, points, events, temperatures=temperatures)
        self.assertIn(Kind.INFUSION, [event.kind for event in events])
        self.assertIsNotNone(detector.infusion_episode)
        flow._feed(detector, fall, points, events, temperatures=temperatures)
        opens = [event for event in events if event.kind == Kind.DOOR_OPEN]
        self.assertEqual(len(opens), 1, msg=json.dumps(
            observed, default=str, sort_keys=True,
        ))
        observed = flow._value(temperatures, (opens[0].trace_at - T0).total_seconds())
        self.assertGreater(observed, flow.temperature)
        self.assertGreaterEqual(opens[0].effective_at, flow._at(peak))
        self.assertLessEqual(opens[0].effective_at, opens[0].trace_at)


if __name__ == "__main__":
    unittest.main()
