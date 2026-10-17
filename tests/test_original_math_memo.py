"""Identical pure requests reuse work; corrected original values never do."""

from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_original_curve_evidence as original_fixtures
from test_original_curve_evidence import CLIMATE

from custom_components.ha_sauna.core import detector as detector_module
from custom_components.ha_sauna.core.models import Position, Quantity


class OriginalMathMemoTests(unittest.TestCase):
    def setUp(self):
        self.fixture = original_fixtures.OriginalCurveFollowupTests()
        self.fixture.setUp()

    def _baseline(self):
        fixture = self.fixture
        detector = fixture._detector()
        fixture._feed(
            detector, (fixture.initial for _ in range(fixture.baseline_ticks))
        )
        return detector

    def _flush_pending(self, detector, at):
        # advance() uses this same consumer before evaluating and draining
        # original prefixes. Inspect that real input boundary, not accept().
        detector._consume(at)
        self.assertFalse(detector.pending)
        self.assertTrue(detector._original_prefixes)
        prefix = detector._original_prefixes[-1]
        self.assertEqual(prefix.received_at, at)
        return prefix

    def test_temperature_memo_preserves_window_identity_and_same_stamp_correction(self):
        fixture = self.fixture
        detector = self._baseline()
        fixture._accept(detector, detector.index + 1, fixture.initial)
        prefix = self._flush_pending(detector, fixture._at(detector.index + 1))
        window = dict(prefix.temperatures)["upper"]
        compute = detector_module.original_kink_tolerance
        with patch.object(detector_module, "original_kink_tolerance", wraps=compute) as calls:
            initial = detector._original_temperature_backgrounds(prefix, (("upper", window),))
            again = detector._original_temperature_backgrounds(prefix, (("upper", window),))
            self.assertEqual(calls.call_count, 1)
            self.assertEqual(again, initial)
            self.assertIs(initial[0][1].window, window)

            equal_window = replace(window)
            self.assertEqual(equal_window, window)
            self.assertIsNot(equal_window, window)
            rebound = detector._original_temperature_backgrounds(
                prefix, (("upper", equal_window),)
            )
            self.assertEqual(calls.call_count, 2)
            self.assertEqual(rebound, initial)
            self.assertIs(rebound[0][1].window, equal_window)

            original = detector.latest[Position.UPPER, Quantity.TEMPERATURE]
            value = original.value - 2.6 * fixture.values["curve_original_noise_temperature_upper"]
            self.assertTrue(detector.accept(replace(
                original, value=value, raw_value=str(value),
                received_at=fixture._at(detector.index + 2),
            )))
            corrected_prefix = self._flush_pending(
                detector, fixture._at(detector.index + 2),
            )
            corrected_window = dict(corrected_prefix.temperatures)["upper"]
            self.assertEqual(corrected_window.newest_at, window.newest_at)
            self.assertNotEqual(corrected_window.current, window.current)
            calls_before = calls.call_count
            corrected = detector._original_temperature_backgrounds(
                corrected_prefix, (("upper", corrected_window),)
            )
            self.assertEqual(calls.call_count, calls_before + 1)
            self.assertIs(corrected[0][1].window, corrected_window)
            self.assertNotEqual(corrected, initial)
            points = next(
                temperature for position, temperature, _ in corrected_prefix.points
                if position == Position.UPPER
            )
            expected = compute(
                points, corrected_window,
                factor=fixture.values["curve_original_significance"],
                min_history_windows=fixture.values["curve_original_kink_history_windows"],
                max_gap_seconds=fixture.values["curve_original_gap_temperature_upper_seconds"],
                time_support=detector._original_prefix_time_support(
                    corrected_prefix, Position.UPPER, Quantity.TEMPERATURE,
                ),
            )
            self.assertEqual(corrected[0][1], expected)

    def test_humidity_memo_recomputes_a_same_stamp_recovery_counterform(self):
        fixture = self.fixture
        detector = self._baseline()
        onset = fixture._at(detector.index)
        fixture._ramp(detector, 4 * fixture.count, fixture.fall_step)
        state = tuple(
            value + change
            for value, change in zip(fixture._latest(detector), fixture.fall_step, strict=True)
        )
        fixture._accept(detector, detector.index + 1, state)
        prefix = self._flush_pending(detector, fixture._at(detector.index + 1))
        witness = SimpleNamespace(onset_at=onset)

        def request(prefix):
            samples = next(
                humidity for position, _, humidity in prefix.points
                if position == Position.UPPER
            )
            window = dict(prefix.windows)["upper"].humidity
            support = detector._original_prefix_time_support(
                prefix, Position.UPPER, Quantity.HUMIDITY,
            )
            return samples, witness, window, "upper", support

        before = request(prefix)
        compute = detector_module.original_window
        with patch.object(detector_module, "original_window", wraps=compute) as calls:
            self.assertTrue(detector._original_opening_humidity_connected(*before))
            computations = calls.call_count
            self.assertGreater(computations, 0)
            self.assertTrue(detector._original_opening_humidity_connected(*before))
            self.assertEqual(calls.call_count, computations)

        original = detector.latest[Position.UPPER, Quantity.HUMIDITY]
        fall = fixture.fall_step[CLIMATE.index("upper_humidity")]
        value = original.value - 4 * fixture.count * fall
        self.assertTrue(detector.accept(replace(
            original, value=value, raw_value=str(value),
            received_at=fixture._at(detector.index + 2),
        )))
        corrected_prefix = self._flush_pending(
            detector, fixture._at(detector.index + 2),
        )
        after = request(corrected_prefix)
        self.assertEqual(tuple(stamp for stamp, _ in before[0]),
                         tuple(stamp for stamp, _ in after[0]))
        self.assertNotEqual(before[0], after[0])
        self.assertGreater(after[2].v, fixture.values["curve_original_significance"] * after[2].e)
        with patch.object(detector_module, "original_window", wraps=compute) as calls:
            self.assertFalse(detector._original_opening_humidity_connected(*after))
            computations = calls.call_count
            self.assertGreater(computations, 0)
            self.assertFalse(detector._original_opening_humidity_connected(*after))
            self.assertEqual(calls.call_count, computations)
        self.assertLessEqual(len(detector._original_humidity_connection_memo),
                             len(detector.positions))


if __name__ == "__main__":
    unittest.main()
