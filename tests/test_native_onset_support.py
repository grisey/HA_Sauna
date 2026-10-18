"""Native RH input ownership; no duplicate physical session assessment.

Reuse the existing declared-kink fixture and its controlled geometry owner.
The assertion concerns the original inputs delivered to that owner, not a
fabricated door decision. No production threshold or sample minimum changes.
"""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import unittest
from unittest.mock import patch

import test_detector as detector_fixtures

from custom_components.ha_sauna.core.detector import Detector
from custom_components.ha_sauna.core.models import Position, Quantity


class NativeOnsetSupportTests(unittest.TestCase):
    def test_bound_native_window_reaches_geometry_owner_at_sparse_original_cadence(self):
        fixture = detector_fixtures.InfusionLifecycleTests(methodName="runTest")
        fixture.setUp()
        _, _, original_rows = fixture._kink_originals(rising=False, large=True)
        p = fixture.p
        # An input-delivery boundary, derived from the configured contract:
        # the original count in the rolling duration cannot reach both sides.
        # It is not a new detector parameter or a fitted climate value.
        slow_step = p["curve_window_seconds"] / (
            2 * p["curve_min_side_samples"] - 1
        ) + fixture.step
        original_onsets = Detector._original_opening_onset_witnesses
        for cadence in ("dense_control", "slow", "irregular"):
            with self.subTest(cadence=cadence):
                supplied = []
                spacing = 1 if cadence == "dense_control" else slow_step / fixture.step
                rows = [
                    (
                        second * spacing
                        + ((index % 2) * fixture.step if cadence == "irregular" else 0),
                        temperature,
                        humidity,
                    )
                    for index, (second, temperature, humidity) in enumerate(original_rows)
                ]
                self.assertTrue(all(a[0] < b[0] for a, b in zip(rows, rows[1:])))
                self.assertLess(
                    max(b[0] - a[0] for a, b in zip(rows, rows[1:])),
                    p["sensor_timeout_seconds"],
                )

                def observe(detector, episode, prefix, windows, **kwargs):
                    # The existing empty declared-form fixture deliberately
                    # supplies no geometry verdict. Keep the real native
                    # windows, binding intervals, sources and input ordering.
                    with patch(
                        "custom_components.ha_sauna.core.detector.curve_change",
                        return_value=None,
                    ) as geometry:
                        result = original_onsets(
                            detector, episode, prefix, windows, **kwargs,
                        )
                    for name, native in (kwargs.get("onset_intervals") or {}).items():
                        onset = native["interval"].previous[0]
                        originals = next(
                            humidity for position, _, humidity in prefix.points
                            if position.value == name
                        )
                        for call in geometry.call_args_list:
                            if call.kwargs.get("onset_at") == onset:
                                supplied.append((
                                    prefix.received_at, native["window"], onset,
                                    originals, call.args[0], call.kwargs["policy"],
                                ))
                    return result

                detector = Detector(fixture.parameters, detector_fixtures.T0, (Position.UPPER,))
                with (
                    fixture._patch_math([], forms=detector_fixtures.original_contrasts),
                    patch.object(Detector, "_original_opening_onset_witnesses", new=observe),
                ):
                    fixture._feed_kink_originals(detector, rows, [])
                self.assertTrue(supplied, "The real native binding must reach its geometry owner")
                outside_rolling_window = False
                for received_at, native, onset, originals, points, policy in supplied:
                    with self.subTest(received_at=received_at):
                        self.assertIs(policy, detector.curve_policy)
                        self.assertLessEqual(len(points), p["curve_max_points"])
                        self.assertTrue(all(point in originals for point in points))
                        self.assertTrue(all(stamp <= received_at for stamp, _ in points))
                        # Exact timestamps AND values of this already bound
                        # window must reach the unchanged mathematical owner.
                        self.assertTrue(all(
                            point in points for point in native.previous + native.current
                        ))
                        self.assertIn(onset, {stamp for stamp, _ in points})
                        rolling_start = received_at - timedelta(seconds=p["curve_window_seconds"])
                        old_selection = tuple(point for point in originals
                                              if rolling_start <= point[0] <= received_at)
                        if cadence == "dense_control":
                            self.assertEqual(points, old_selection)
                        elif native.start_at < rolling_start:
                            outside_rolling_window = True
                            self.assertLess(len(old_selection), 2 * policy.min_side_samples)
                            self.assertGreaterEqual(len(points), 2 * policy.min_side_samples)
                self.assertEqual(outside_rolling_window, cadence != "dense_control")
                self.assertEqual(detector.p, p)
                self.assertEqual(detector.positions, (Position.UPPER,))
                self.assertIsNotNone(detector.latest[Position.UPPER, Quantity.HUMIDITY])

    def test_later_own_form_replaces_only_a_rejected_native_onset(self):
        """Selection contract on existing inputs; declared fits are not evidence of fit quality."""
        fixture = detector_fixtures.InfusionLifecycleTests(methodName="runTest")
        fixture.setUp()
        _, _, rows = fixture._kink_originals(rising=False, large=True)
        original_onsets = Detector._original_opening_onset_witnesses
        prepared = []

        def observe(detector, episode, prefix, windows, **kwargs):
            result = original_onsets(detector, episode, prefix, windows, **kwargs)
            intervals = kwargs.get("onset_intervals") or {}
            native = intervals.get(Position.UPPER.value)
            if native is None or kwargs.get("transitions") is not None:
                return result
            fixed_at = native["interval"].previous[0]
            start = min(
                prefix.received_at - timedelta(seconds=fixture.p["curve_window_seconds"]),
                native["window"].start_at,
            )
            points = tuple(
                point for point in detector._original_segment_points(
                    prefix, Position.UPPER, Quantity.HUMIDITY,
                ) if start <= point[0] <= prefix.received_at
            )
            minimum = detector.curve_policy.min_side_samples

            def evaluable(at):
                before = sum(stamp <= at for stamp, _ in points)
                return min(before, len(points) - before) >= minimum

            later = next((
                stamp for stamp, _ in native["window"].current
                if stamp > fixed_at and evaluable(stamp)
            ), None)
            if later is not None and evaluable(fixed_at):
                # Save a real source/prefix/window binding, with no declared
                # geometry verdict yet. Each subcase starts from this state.
                prepared[:] = [deepcopy(
                    (detector, episode, prefix, windows, intervals, fixed_at, later),
                    {id(detector.p): detector.p},
                )]
            return result

        detector = Detector(fixture.parameters, detector_fixtures.T0, (Position.UPPER,))
        with (
            fixture._patch_math([], forms=detector_fixtures.original_contrasts),
            patch.object(Detector, "_original_opening_onset_witnesses", new=observe),
        ):
            fixture._feed_kink_originals(detector, rows, [])
        self.assertTrue(prepared, "The existing fixture must provide both evaluable onsets")
        base = prepared[0]
        cases = (
            ("rejected", "rejects", "supports", "later", "own_form_bound"),
            ("valid_fixed", "supports", "supports", "fixed", "own_form_bound"),
            ("missing_fixed", None, "supports", None, "no_evaluable_form"),
            ("unresolved_fixed", "unresolved", "supports", None, "form_not_supported"),
            ("missing_free", "rejects", None, None, "form_not_supported"),
            ("wrong_free_direction", "rejects", "supports", None, "form_not_supported"),
            ("recovery", "rejects", "supports", None, "humidity_recovery"),
            ("unknown_connection", "rejects", "supports", None, "humidity_connection_unresolved"),
            ("changed_source", "rejects", "supports", None, "source_or_epoch_changed"),
            ("changed_epoch", "rejects", "supports", None, "source_or_epoch_changed"),
            ("local_attribution", "rejects", "supports", None, "form_not_supported"),
        )
        for case, fixed_evidence, free_evidence, expected, reason in cases:
            with self.subTest(case=case):
                detector, episode, prefix, windows, intervals, fixed_at, later_at = deepcopy(
                    base, {id(base[0].p): base[0].p},
                )
                key = ("door_open", Position.UPPER, Quantity.HUMIDITY)
                self.assertNotIn(key, detector._curve_candidates)
                native = intervals[Position.UPPER.value]
                self.assertLess(fixed_at, later_at)
                self.assertLessEqual(native["window"].previous[-1][0], later_at)
                self.assertLessEqual(later_at, native["window"].newest_at)
                # The preparation deliberately cached absent fits. Only that
                # memo is reset; originals and their binding remain unchanged.
                detector._curve_fit_cache.clear()
                until = (prefix.received_at - detector_fixtures.T0).total_seconds()
                shapes = [fixture._shape(
                    "open", (at - detector_fixtures.T0).total_seconds(), until,
                ) for at in (fixed_at, later_at)]

                def declared_geometry(samples, **kwargs):
                    evidence = (fixed_evidence if kwargs.get("onset_at") == fixed_at
                                else free_evidence)
                    if evidence is None:
                        return None
                    features = fixture._math_curve(shapes, samples, **kwargs)
                    self.assertIsNotNone(features)
                    features = replace(features, evidence=evidence)
                    if evidence == "unresolved":
                        features = replace(features, after_trend=None)
                    if case == "wrong_free_direction" and features.onset_at == later_at:
                        features = replace(features, after_trend=replace(
                            features.after_trend, direction=1,
                        ))
                    return features

                if case == "changed_source":
                    temperature_source, _ = episode["sources"][Position.UPPER]
                    episode["sources"][Position.UPPER] = (
                        temperature_source, temperature_source,
                    )
                elif case == "changed_epoch":
                    detector._curve_series[Position.UPPER, Quantity.HUMIDITY]["epoch"] += 1
                options = {"onset_intervals": intervals, "diagnostics": []}
                if case == "local_attribution":
                    options["transitions"] = {Position.UPPER.value: (
                        native["window"].start_at, native["window"].newest_at,
                    )}
                # These two cases isolate consumption of the existing RH
                # counterform verdict, not its mathematical derivation.
                connected = detector._original_opening_humidity_connected

                def connection(*args):
                    if case in ("recovery", "unknown_connection"):
                        return False if case == "recovery" else None
                    return connected(*args)

                with (
                    patch("custom_components.ha_sauna.core.detector.curve_change",
                          side_effect=declared_geometry) as geometry,
                    patch.object(detector, "_original_opening_humidity_connected",
                                 side_effect=connection) as continuity,
                ):
                    witnesses = original_onsets(detector, episode, prefix, windows, **options)
                diagnostic, = options["diagnostics"]
                self.assertEqual(diagnostic["reason"], reason)
                self.assertEqual(detector.p, fixture.p)
                if expected is None:
                    self.assertEqual(witnesses, ())
                else:
                    (actual_key, witness), = witnesses
                    expected_at = later_at if expected == "later" else fixed_at
                    self.assertEqual(actual_key, key)
                    self.assertEqual(witness.onset_at, expected_at)
                    self.assertEqual(witness.features.onset_at, expected_at)
                    self.assertEqual(witness.features.newest_at, prefix.received_at)
                    self.assertTrue(all(point in detector._original_segment_points(
                        prefix, Position.UPPER, Quantity.HUMIDITY,
                    ) for point in witness.original_points))
                    self.assertTrue(all(
                        stamp <= prefix.received_at for stamp, _ in witness.original_points
                    ))
                if case in ("changed_source", "changed_epoch"):
                    geometry.assert_not_called()
                    continuity.assert_not_called()
                else:
                    self.assertEqual(geometry.call_count, 1 if case == "local_attribution" else 2)
                if case in ("rejected", "recovery", "unknown_connection"):
                    continuity.assert_called_once()
                    self.assertEqual(continuity.call_args.args[1].onset_at, later_at)
                    self.assertEqual(diagnostic["fit_method"], "supported_later_own_form")
                    self.assertEqual(diagnostic["rejected_native_form"]["onset_at"], fixed_at)
                elif expected == "fixed":
                    self.assertEqual(diagnostic["fit_method"], "fixed_native_onset")
                    self.assertNotIn("rejected_native_form", diagnostic)
