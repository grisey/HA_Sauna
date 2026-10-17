"""Native originals and bounded derived pairing have distinct contracts.

Existing fixtures supply values and consumed parameters. Irregular times do
not imply source loss; pairing restrictions do not invalidate native windows.
No private recordings, desired event counts or skipped tests are used here.
"""

from collections.abc import Sequence
from copy import deepcopy
import json
from datetime import timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_detector import T0, measurement, trace
from test_original_curve_evidence import (
    ORIGIN,
    channel_arguments,
    kink_arguments,
    linear_originals,
    water_arguments,
)

from custom_components.ha_sauna.core.curve_change import (
    original_kink_tolerance,
    original_time_support,
    original_water_pair_support,
    original_water_window,
    original_window,
)
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.detector import Detector
from custom_components.ha_sauna.core.models import Position, Quantity


class OriginalTimeSupportTests(unittest.TestCase):
    def setUp(self):
        self.values = Parameters({}).values
        self.count = self.values["curve_original_samples"]
        self.originals, self.fixture_step = linear_originals(self.values)

    def slow_stream(self, position="upper", quantity="humidity", *, factor=1):
        arguments = channel_arguments(self.values, position, quantity)
        interval = timedelta(seconds=arguments["max_gap_seconds"]) + factor * self.fixture_step
        fixture = self.originals[f"{position}_{quantity}"]
        # Flat warm-up followed by the fixture's existing straight value line.
        values = (fixture[0][1],) * (2 * self.count) + tuple(value for _, value in fixture)
        return tuple((ORIGIN + index * interval, value) for index, value in enumerate(values)), interval

    def support(self, samples, *, position="upper", quantity="humidity", at=None, previous=None, diagnostic=None):
        arguments = channel_arguments(self.values, position, quantity)
        return original_time_support(
            samples,
            count=arguments["count"],
            max_gap_seconds=arguments["max_gap_seconds"],
            at=samples[-1][0] if at is None else at,
            previous=previous, diagnostic=diagnostic,
        )

    def window(self, samples, support, *, at=None, position="upper", quantity="humidity", diagnostic=None):
        return original_window(
            samples,
            **channel_arguments(self.values, position, quantity),
            at=samples[-1][0] if at is None else at,
            time_support=support, diagnostic=diagnostic,
        )

    def test_regular_slower_originals_keep_values_and_native_times_in_every_role(self):
        for position in ("upper", "lower"):
            for quantity in ("temperature", "humidity"):
                for factor in (1, 2):
                    with self.subTest(position=position, quantity=quantity, factor=factor):
                        samples, interval = self.slow_stream(position, quantity, factor=factor)
                        support = self.support(samples, position=position, quantity=quantity)
                        self.assertIsNotNone(support)
                        window = self.window(samples, support, position=position, quantity=quantity)
                        self.assertIsNotNone(window)
                        self.assertEqual(window.previous, samples[-2 * self.count : -self.count])
                        self.assertEqual(window.current, samples[-self.count :])
                        self.assertAlmostEqual(window.v, -1 / interval.total_seconds())
                        self.assertTrue(window.time_support.covers(window.start_at, window.newest_at))
                        self.assertIsNone(window.time_support.intervals)

    def test_native_windows_agree_with_and_without_sequence_support(self):
        samples = self.originals["upper_humidity"]
        legacy = self.window(samples, None)
        supported = self.window(samples, self.support(samples))
        self.assertIsNotNone(legacy)
        self.assertIsNotNone(supported)
        for field in ("previous", "current", "v", "b", "e", "e_minus", "r", "r_minus", "mean", "mean_minus"):
            self.assertEqual(getattr(legacy, field), getattr(supported, field))

    def test_native_originals_do_not_need_cadence_warmup(self):
        samples, _ = self.slow_stream()
        initial = samples[: 2 * self.count]
        support = self.support(initial)
        self.assertIsNotNone(support)
        self.assertTrue(support.covers(initial[0][0], initial[-1][0]))
        self.assertFalse(support.pairing_covers(initial[0][0], initial[-1][0]))
        diagnostic = {}
        window = self.window(initial, support, diagnostic=diagnostic)
        self.assertIsNotNone(window)
        self.assertEqual(diagnostic["reason"], "ready")
        self.assertEqual(diagnostic["required_count"], 2 * self.count)
        self.assertEqual(diagnostic["selected_count"], len(initial))
        self.assertEqual(window.previous + window.current, initial)
        self.assertFalse(window.time_support.pairing_covers(initial[0][0], initial[-1][0]))

    def test_report_outlier_preserves_native_window_without_widening_pairing(self):
        samples, interval = self.slow_stream()
        before = samples[-1][0]
        after = before + 2 * self.count * interval
        broken = samples + ((after, samples[-1][1]),)
        previous = self.support(samples)
        support = self.support(broken, previous=previous)
        rejected_pair = (before, after, (after - before).total_seconds(), previous.next_gap_seconds)
        self.assertIn(rejected_pair, support.breaks)
        self.assertTrue(support.covers(before, after))
        self.assertFalse(support.pairing_covers(before, after))
        window = self.window(broken, support)
        self.assertIsNotNone(window)
        self.assertEqual(window.previous + window.current, broken[-2 * self.count :])
        # Clipping a native proof must not expand the separate pairing policy.
        self.assertFalse(window.time_support.pairing_covers(before, after))
        later = tuple((after + index * (after - before), samples[-1][1])
                      for index in range(1, 4 * self.count))
        final = self.support(broken + later, previous=support)
        self.assertTrue(final.covers(before, later[-1][0]))
        self.assertFalse(final.pairing_covers(before, after))
        self.assertIn(rejected_pair, final.breaks)
        self.assertIsNotNone(self.window(broken + later, final))

    def test_waiting_preserves_native_evidence_and_future_cannot_rewrite_it(self):
        samples, interval = self.slow_stream()
        support = self.support(samples)
        boundary = samples[-1][0] + interval
        self.assertTrue(support.pairing_fresh(boundary))
        later = boundary + timedelta.resolution
        self.assertFalse(support.pairing_fresh(later))
        original = self.window(samples, support)
        self.assertEqual(self.window(samples, support, at=later), original)
        future = samples + ((boundary + self.count * interval, samples[-1][1]),)
        self.assertEqual(self.support(future, at=samples[-1][0]), support)
        self.assertEqual(self.support(samples, at=later), support)
        self.assertEqual(self.window(future, support, at=samples[-1][0]), original)

    def test_ring_buffer_keeps_native_sequence_and_separate_pair_restrictions(self):
        samples, interval = self.slow_stream()
        split = 2 * self.count
        shifted = tuple((stamp + 2 * self.count * interval, value) for stamp, value in samples[split:])
        samples = samples[:split] + shifted
        previous = None
        capacity = 2 * self.count + 1
        for end in range(1, len(samples) + 1):
            retained = samples[max(0, end - capacity) : end]
            updated = self.support(retained, previous=previous)
            full = self.support(samples[:end])
            self.assertIsNotNone(updated)
            self.assertEqual(updated.next_gap_seconds, full.next_gap_seconds)
            for left, right in zip(retained, retained[1:]):
                self.assertEqual(updated.covers(left[0], right[0]), full.covers(left[0], right[0]))
                self.assertEqual(updated.pairing_covers(left[0], right[0]), full.pairing_covers(left[0], right[0]))
            previous = updated
        # The old endpoints may be outside the bounded ring; compare the
        # retained proof with full history before asserting its pair restriction.
        full = self.support(samples)
        self.assertTrue(full.covers(samples[split - 1][0], samples[split][0]))
        self.assertFalse(full.pairing_covers(samples[split - 1][0], samples[split][0]))

    def test_append_does_not_scan_old_original_history(self):
        samples, interval = self.slow_stream()
        value = samples[-1][1]
        samples += tuple((samples[-1][0] + index * interval, value) for index in range(1, self.count * self.count))
        previous = self.support(samples)

        class CountedSeries(Sequence):
            def __init__(self, values):
                self.values = values
                self.reads = 0

            def __len__(self):
                return len(self.values)

            def __getitem__(self, index):
                self.reads += 1
                return self.values[index]

        series = CountedSeries(samples + ((samples[-1][0] + interval, value),))
        at = series.values[-1][0]
        diagnostic = {}
        updated = self.support(series, at=at, previous=previous, diagnostic=diagnostic)
        self.assertEqual(diagnostic["reason"], "ready")
        self.assertEqual(diagnostic["newest_at"], at)
        self.assertEqual(diagnostic["next_gap_seconds"], updated.next_gap_seconds)
        self.assertIsNotNone(updated)
        # One initial timestamp, binary search, overlap check, one new point.
        self.assertLessEqual(series.reads, len(series).bit_length() + 3)
        self.assertEqual(updated.newest_at, at)

    def test_invalid_order_or_unexplained_overlap_loss_is_unknown(self):
        samples, interval = self.slow_stream()
        for source in (
            (),
            samples + (samples[-1],),
            samples + (samples[-2],),
            samples[:-1] + ((samples[-1][0], float("nan")),),
        ):
            with self.subTest(source=source[-1:] if source else source):
                arguments = channel_arguments(self.values, "upper", "humidity")
                self.assertIsNone(original_time_support(
                    source, count=arguments["count"], max_gap_seconds=arguments["max_gap_seconds"], at=samples[-1][0]
                ))
        previous = self.support(samples)
        disconnected = ((samples[-1][0] + interval, samples[-1][1]),)
        diagnostic = {}
        self.assertIsNone(self.support(disconnected, previous=previous, diagnostic=diagnostic))
        self.assertEqual(diagnostic["reason"], "missing_previous_anchor")
        self.assertEqual(diagnostic["anchor_at"], previous.newest_at)
        bound = previous.restrict(samples[-2][0], samples[-1][0])
        self.assertIsNone(self.support(samples, previous=bound))

    def test_report_interval_does_not_erase_known_kink_history(self):
        samples, interval = self.slow_stream()
        start = samples[-1][0] + 2 * self.count * interval
        newer = tuple((start + index * interval, samples[-1][1]) for index in range(4 * self.count))
        originals = samples + newer
        support = self.support(originals)
        window = self.window(originals, support)
        self.assertIsNotNone(window)
        kink = original_kink_tolerance(originals, window, **kink_arguments(self.values), time_support=support)
        self.assertIsNotNone(kink)
        self.assertEqual(kink.history_start, originals[0][0])
        self.assertLess(kink.history_end, window.start_at)
        self.assertTrue(support.covers(samples[-1][0], newer[0][0]))
        self.assertFalse(support.pairing_covers(samples[-1][0], newer[0][0]))

    def test_water_waits_for_real_temperature_partner_and_rejects_broken_bracket(self):
        temperatures, interval = self.slow_stream(quantity="temperature")
        humidity_value = self.originals["upper_humidity"][0][1]
        left = temperatures[-1]
        humidity = (left[0] + interval / 2, humidity_value)
        support = self.support(temperatures, quantity="temperature")
        pending = original_water_pair_support(humidity, (left,), **water_arguments(self.values), at=humidity[0], time_support=support)
        self.assertEqual(pending.status, "pending")
        right = (left[0] + interval, left[1])
        ready_support = self.support(temperatures + (right,), quantity="temperature", previous=support)
        ready = original_water_pair_support(humidity, (left, right), **water_arguments(self.values), at=right[0], time_support=ready_support)
        self.assertEqual(ready.status, "ready")
        self.assertEqual(ready.pair.temperature_before, left)
        self.assertEqual(ready.pair.temperature_after, right)
        self.assertEqual(ready.pair.humidity_sample, humidity)
        far_right = (left[0] + 2 * self.count * interval, left[1])
        broken_support = self.support(temperatures + (far_right,), quantity="temperature", previous=support)
        invalid = original_water_pair_support(humidity, (left, far_right), **water_arguments(self.values), at=far_right[0], time_support=broken_support)
        self.assertEqual(invalid.status, "invalid")
        self.assertEqual(invalid.reason, "temperature_pairing_unresolved")
        self.assertTrue(broken_support.covers(left[0], far_right[0]))
        self.assertFalse(broken_support.pairing_covers(left[0], far_right[0]))
        self.assertIsNotNone(self.window(temperatures + (far_right,), broken_support, quantity="temperature"))

    def test_water_window_uses_native_humidity_support_without_receipt_timeout(self):
        humidity, _ = self.slow_stream()
        temperature_value = self.originals["upper_temperature"][0][1]
        temperatures = tuple((stamp, temperature_value) for stamp, _ in humidity)
        humidity_support = self.support(humidity)
        temperature_support = self.support(temperatures, quantity="temperature")
        selected = humidity[-2 * self.count :]
        pairs = tuple(original_water_pair_support(
            sample, ((sample[0], temperature_value),), **water_arguments(self.values), at=humidity[-1][0], time_support=temperature_support
        ).pair for sample in selected)
        self.assertNotIn(None, pairs)
        arguments = channel_arguments(self.values, "upper", "humidity")
        later = humidity[-1][0] + timedelta(seconds=2 * humidity_support.next_gap_seconds)
        result = original_water_window(
            pairs, count=self.count, max_gap_seconds=arguments["max_gap_seconds"], at=later, time_support=humidity_support
        )
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.features.newest_at, humidity[-1][0])
        self.assertFalse(humidity_support.pairing_fresh(later))


class OriginalTimeAdapterTests(unittest.TestCase):
    def _advance_with_original_prefix(self, detector, received_at):
        """Observe genuine consumed inputs immediately before their evaluation.

        accept() queues measurements. advance() calls _consume() at the first
        due grid point, which creates the original prefixes; _sample() then
        consumes them. The observer forwards that same call without changing
        arguments, return values, prefix data or detector state.
        """
        prefixes = []
        evaluate = detector._sample

        def observe(*args, **kwargs):
            prefixes.extend(detector._original_prefixes)
            return evaluate(*args, **kwargs)

        step = timedelta(seconds=detector.grid_seconds)
        ticks, remainder = divmod(received_at - detector.origin, step)
        flush_at = detector.origin + (ticks + bool(remainder)) * step
        with patch.object(detector, "_sample", side_effect=observe):
            detector.advance(flush_at, enabled=True)
        self.assertFalse(detector.pending, "advance must consume the accepted batch")
        self.assertTrue(prefixes, "the consumed originals must produce a real prefix")
        self.assertEqual(prefixes[-1].received_at, received_at)
        return prefixes[-1]

    def _evicted_reference(self):
        parameters = Parameters({})
        values = parameters.values
        count = values["curve_original_samples"]
        _, step = linear_originals(values)
        interval = timedelta(seconds=max(
            values[f"curve_original_gap_{quantity.value}_upper_seconds"]
            for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY)
        )) + step
        detector = Detector(parameters, T0, (Position.UPPER,))
        reference = None
        anchor = None
        for index in range(4 * count + values["curve_max_points"]):
            at = T0 + index * interval
            for quantity, value in zip(
                (Quantity.TEMPERATURE, Quantity.HUMIDITY), trace(0), strict=True
            ):
                self.assertTrue(detector.accept(measurement(
                    Position.UPPER, quantity, value, (at - T0).total_seconds()
                )))
            prefix = self._advance_with_original_prefix(detector, at)
            if index == 4 * count - 1:
                reference = prefix
            if anchor is not None:
                self.assertTrue(detector._original_contiguous(prefix, anchor))
            if reference is not None:
                anchor = tuple(
                    ("upper", quantity, at)
                    for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY)
                )
        return detector, reference, prefix

    def test_historical_temperature_background_keeps_its_bound_time_proof(self):
        detector, old, current = self._evicted_reference()
        reference = dict(old.temperatures)["upper"]
        current_support = detector._original_prefix_time_support(
            current, Position.UPPER, Quantity.TEMPERATURE
        )
        self.assertFalse(current_support.covers(reference.start_at, reference.newest_at))
        self.assertTrue(reference.time_support.covers(reference.start_at, reference.newest_at))
        backgrounds = dict(detector._original_temperature_backgrounds(
            current, (("upper", reference),)
        ))
        self.assertIsNotNone(backgrounds["upper"])
        self.assertIs(backgrounds["upper"].window, reference)
        self.assertIsNone(backgrounds["upper"].history_start)

    def test_retained_humidity_witness_does_not_require_its_first_point_in_ring(self):
        detector, old, current = self._evicted_reference()
        reference = dict(old.windows)["upper"].humidity
        current_window = dict(current.windows)["upper"].humidity
        _, _, current_humidity = current.points[0]
        witness = SimpleNamespace(
            onset_at=reference.start_at,
            original_points=reference.previous + reference.current,
        )
        support = detector._original_prefix_time_support(
            current, Position.UPPER, Quantity.HUMIDITY
        )
        self.assertLess(witness.original_points[0][0], current_humidity[0][0])
        self.assertTrue(detector._original_opening_humidity_connected(
            current_humidity, witness, current_window, "upper", support
        ))

    def test_live_prefix_consumers_share_sequence_and_real_source_reset(self):
        parameters = Parameters({})
        values = parameters.values
        count = values["curve_original_samples"]
        _, fixture_step = linear_originals(values)
        interval = timedelta(seconds=max(
            values[f"curve_original_gap_{quantity.value}_{position.value}_seconds"]
            for position in (Position.UPPER, Position.LOWER)
            for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY)
        )) + fixture_step
        observed = []
        detector = Detector(parameters, T0, observer=observed.append)
        for index in range(4 * count):
            at = T0 + index * interval
            seconds = (at - T0).total_seconds()
            for position in detector.positions:
                for quantity, value in zip(
                    (Quantity.TEMPERATURE, Quantity.HUMIDITY), trace(0), strict=True
                ):
                    self.assertTrue(detector.accept(
                        measurement(position, quantity, value, seconds)
                    ))
            prefix = self._advance_with_original_prefix(detector, at)
        self.assertEqual(len(prefix.windows), len(detector.positions))
        records = {(position, quantity): record
                   for position, quantity, record in prefix.window_diagnostics}
        self.assertEqual(set(records), {
            (position.value, quantity.value)
            for position in detector.positions
            for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY)
        })
        published = observed[-1]["original_window_diagnostics"]
        selection = observed[-1]["position_selection"]["door_open"]
        self.assertTrue(all(item["status"] == "idle" for item in selection["results"].values()))
        for (position, quantity), record in records.items():
            with self.subTest(position=position, quantity=quantity):
                self.assertTrue(record["window_ready"])
                self.assertEqual(record["window"]["reason"], "ready")
                series = detector._curve_series[Position(position), Quantity(quantity)]
                self.assertEqual(record["source"], series["source"])
                self.assertEqual(record["epoch"], series["epoch"])
                self.assertEqual(record["time_basis"], series["time_basis"])
                self.assertEqual(published[position][quantity], record)
        saved_records = deepcopy(records)
        json.dumps(published, default=str, allow_nan=False)
        self.assertTrue(detector._original_humidity_available(detector.positions, at))
        references = tuple(
            (name, Quantity.HUMIDITY, pair.humidity.start_at)
            for name, pair in prefix.windows
        )
        self.assertTrue(detector._original_contiguous(prefix, references))
        thermal_backgrounds = detector._original_temperature_backgrounds(
            prefix, prefix.temperatures
        )
        self.assertTrue(all(value is not None for _, value in thermal_backgrounds))
        self.assertLess(2 * interval.total_seconds(), values["sensor_timeout_seconds"])
        self.assertTrue(detector._original_humidity_available(
            detector.positions, at + 2 * interval
        ))
        # A new real source is a new epoch; its one report cannot borrow the
        # previously learned cadence or the old source's original windows.
        self.assertTrue(detector.accept(measurement(
            Position.UPPER, Quantity.HUMIDITY, trace(0)[1],
            (at + interval - T0).total_seconds(), source="sensor.changed_humidity",
        )))
        changed = self._advance_with_original_prefix(detector, at + interval)
        self.assertFalse(detector._original_contiguous(changed, references))
        self.assertFalse(detector._original_humidity_available(
            detector.positions, at + interval
        ))
        changed_records = {(position, quantity): record
                           for position, quantity, record in changed.window_diagnostics}
        role = Position.UPPER.value, Quantity.HUMIDITY.value
        reset = changed_records[role]
        self.assertEqual(reset["source"], "sensor.changed_humidity")
        self.assertGreater(reset["epoch"], records[role]["epoch"])
        self.assertFalse(reset["window_ready"])
        self.assertEqual(reset["window"]["reason"], "insufficient_originals")
        self.assertEqual(reset["window"]["selected_count"], reset["available_count"])
        self.assertLess(reset["window"]["selected_count"], reset["window"]["required_count"])
        self.assertEqual(records, saved_records, "later source changes cannot rewrite the captured prefix")
        # Only lower RH reports again. Upper T stays a historical native
        # window; absence of a new upper original neither refutes nor confirms it.
        late_at = at + 2 * interval
        self.assertTrue(detector.accept(measurement(
            Position.LOWER, Quantity.HUMIDITY, trace(0)[1],
            (late_at - T0).total_seconds(),
        )))
        late = self._advance_with_original_prefix(detector, late_at)
        late_records = {(position, quantity): record
                        for position, quantity, record in late.window_diagnostics}
        thermal = late_records[Position.UPPER.value, Quantity.TEMPERATURE.value]
        self.assertTrue(thermal["window_ready"])
        self.assertTrue(thermal["window"]["cache_hit"])
        self.assertEqual(thermal["window"]["reason"], "ready")
        self.assertEqual(thermal["window"]["newest_at"], records[Position.UPPER.value, Quantity.TEMPERATURE.value]["window"]["newest_at"])
        self.assertEqual(records, saved_records)


if __name__ == "__main__":
    unittest.main()
