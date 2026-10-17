"""Native RH input ownership; no duplicate physical session assessment.

Reuse the existing declared-kink fixture and its controlled geometry owner.
The assertion concerns the original inputs delivered to that owner, not a
fabricated door decision. No production threshold or sample minimum changes.
"""

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
