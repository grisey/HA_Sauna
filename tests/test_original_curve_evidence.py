"""Fixed-original contracts and independent Pro values on bound replay prefixes.

The golden values below are evaluation criteria, not operational parameters or
physical event labels. They come from the supplied package's independent
``Kontrolle2_Exakte_Praefixwerte.csv`` (``exact_decimal`` column); source indices
come from ``Alle_Praefixmerkmale.csv.gz``. Only the four selected prefixes are
evaluated, without recalibrating or replaying the complete recordings.
"""

from dataclasses import replace
from datetime import datetime, timedelta
import gzip
import hashlib
import json
from math import isfinite, sqrt
import os
from pathlib import Path
import unittest
from unittest.mock import call, patch

from test_detector import trace
from test_settings import fixture

from custom_components.ha_sauna.core import curve_change as curves
from custom_components.ha_sauna.core.curve_change import (
    OriginalPositionWindows,
    original_contrasts,
    original_flattening,
    original_water_pair,
    original_water_pair_support,
    original_water_window,
    original_window,
)
from custom_components.ha_sauna.core.moisture import absolute_humidity
from custom_components.ha_sauna.core.parameters import Parameters


ROOT = Path(__file__).resolve().parent
ORIGIN = datetime.fromisoformat(fixture("session")["origin"])
CLIMATE = (
    "upper_temperature", "upper_humidity", "lower_temperature", "lower_humidity",
)
CONTRAST_FIELDS = (
    "opening", "braking", "warming_min", "warming_max", "rise", "fall",
    "background_slope", "background_residual",
)

# Original package: HA_Sauna_Gesamtpaket/02_Neue_Belege. Source rows count
# climate originals only; each channel index starts at zero. The R04 rows are
# the first episode's background confirmation and the second episode's fall
# confirmation. These references assess arithmetic, not event recurrence.
PRO_PREFIXES = (
    (
        "R03", 1728, 572.419482, (409, 449, 404, 462),
        (
            1.4357942341989889, -2.8295073926688772,
            -5.0167248165022656, -3.0642048752985154,
            -1.5516706397805743, 1.4357942341989889,
            1.5516706397805743, 1.8728699129465108,
        ),
    ),
    (
        "R03", 2233, 728.015057, (532, 577, 535, 585),
        (
            -56.697605452715461, -0.88698752452087126,
            2.4489002367475152, 4.3666371816984055,
            26.765596772917744, -56.697605452715461,
            56.697605452715461, 111.38519164233529,
        ),
    ),
    (
        "R04", 32746, 10658.212391, (7705, 8719, 7588, 8730),
        (
            -1.4827854608096998, -0.47069060758893117,
            0.43464891766525869, 1.4827854608096998,
            -0.065237250280922154, -0.66528951310152006,
            0.66528951310152006, 3.5200851102399251,
        ),
    ),
    (
        "R04", 32850, 10689.811525, (7729, 8748, 7608, 8761),
        (
            0.036215092076947101, -0.95248010450212737,
            -1.1990538333236744, -0.036215092076947101,
            -31.156878298764678, 29.576334128510769,
            31.156878298764678, 40.780676865611699,
        ),
    ),
)


def channel_arguments(values, position, quantity):
    """Use the sole product catalogue for all operating and support limits."""
    return {
        "count": values["curve_original_samples"],
        "scale": values[f"curve_original_noise_{quantity}_{position}"],
        "max_gap_seconds": values[
            f"curve_original_gap_{quantity}_{position}_seconds"
        ],
    }


def original_prefix(recording, source_row):
    """Read a selected original prefix, checking its existing fixture binding."""
    case_path = ROOT / "fixtures/replay" / f"{recording}.json"
    case = json.loads(case_path.read_text(encoding="utf-8"))
    path = case_path.parent / case["samples_file"]
    if hashlib.sha256(path.read_bytes()).hexdigest() != case["samples_sha256"]:
        raise ValueError(f"Measurement checksum mismatch: {path}")
    originals = {role: [] for role in CLIMATE}
    seen = 0
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if row["role"] not in originals:
                continue
            at = ORIGIN + timedelta(seconds=row["t"])
            originals[row["role"]].append((at, row["value"]))
            seen += 1
            if seen == source_row:
                return {role: tuple(points) for role, points in originals.items()}, at
    raise ValueError(f"{recording}: missing climate source row {source_row}")


def position_windows(originals, at, values):
    return {
        position: OriginalPositionWindows(**{
            quantity: original_window(
                originals[f"{position}_{quantity}"], at=at,
                **channel_arguments(values, position, quantity),
            )
            for quantity in ("temperature", "humidity")
        })
        for position in ("upper", "lower")
    }


def linear_originals(values):
    """A known straight cooling line on catalogue-supported original times."""
    count = values["curve_original_samples"]
    gap = min(
        channel_arguments(values, position, quantity)["max_gap_seconds"]
        for position in ("upper", "lower")
        for quantity in ("temperature", "humidity")
    )
    step = timedelta(seconds=gap / 2)
    originals = {
        f"{position}_{quantity}": tuple(
            (ORIGIN + index * step, trace(0)[column] - index)
            for index in range(2 * count)
        )
        for position in ("upper", "lower")
        for column, quantity in enumerate(("temperature", "humidity"))
    }
    return originals, step


def humidity_step_originals(values, previous_step, current_step):
    """Piecewise RH lines on the existing linear fixture's original times."""
    originals, step = linear_originals(values)
    count = values["curve_original_samples"]
    for position in ("upper", "lower"):
        role = f"{position}_humidity"
        baseline = originals[role][0][1]
        originals[role] = tuple(
            (stamp, baseline + (
                index * previous_step if index < count
                else (count - 1) * previous_step + (index - count + 1) * current_step
            ))
            for index, (stamp, _) in enumerate(originals[role])
        )
    return originals, step


def water_arguments(values):
    """Use the existing upper-position water support and error scales."""
    return {
        "temperature_scale": values["curve_original_noise_temperature_upper"],
        "humidity_scale": values["curve_original_noise_humidity_upper"],
        "max_gap_seconds": values["curve_original_gap_temperature_upper_seconds"],
    }


def water_envelope_originals(values, envelopes, ticks=None):
    """Attach mathematical envelope criteria to real synchronous pair identities.

    These rational density intervals exercise only window geometry. The water
    formula and its actual bounds have their own independent pairing tests.
    """
    temperature, humidity = trace(0)
    step = timedelta(seconds=min(
        water_arguments(values)["max_gap_seconds"],
        values["curve_original_gap_humidity_upper_seconds"],
    ) / 4)
    ticks = range(len(envelopes)) if ticks is None else ticks
    pairs = []
    for tick, (lower, upper) in zip(ticks, envelopes, strict=True):
        stamp = ORIGIN + tick * step
        pair = original_water_pair(
            (stamp, humidity), ((stamp, temperature),), at=stamp,
            **water_arguments(values),
        )
        pairs.append(replace(pair, lower_density=lower, upper_density=upper))
    return tuple(pairs), step


class OriginalCurveEvidenceTests(unittest.TestCase):
    def test_linear_cooling_has_analytic_windows_and_no_opening_contrast(self):
        values = Parameters({}).values
        count = values["curve_original_samples"]
        originals, step = linear_originals(values)
        at = originals["upper_temperature"][-1][0]
        windows = position_windows(originals, at, values)
        for position, pair in windows.items():
            for column, quantity in enumerate(("temperature", "humidity")):
                with self.subTest(position=position, quantity=quantity):
                    result = getattr(pair, quantity)
                    self.assertIsNotNone(result)
                    points = originals[f"{position}_{quantity}"]
                    self.assertEqual(result.previous, points[:count])
                    self.assertEqual(result.current, points[count:])
                    self.assertEqual(result.start_at, points[0][0])
                    self.assertEqual(result.newest_at, at)
                    self.assertEqual(result.scale, channel_arguments(
                        values, position, quantity,
                    )["scale"])
                    self.assertAlmostEqual(result.v, -1 / step.total_seconds())
                    self.assertAlmostEqual(result.b, -1 / step.total_seconds())
                    self.assertAlmostEqual(result.mean_minus,
                                           trace(0)[column] - (count - 1) / 2)
                    self.assertAlmostEqual(result.mean,
                                           trace(0)[column] - (3 * count - 1) / 2)
                    # Closed sums on an equally spaced K-point line:
                    # sum |i - mean(i)| = floor(K²/4),
                    # sum (i - mean(i))² = K(K²-1)/12.
                    expected_error = (
                        result.scale * 12 * (count * count // 4)
                        / (step.total_seconds() * count * (count * count - 1))
                    )
                    self.assertAlmostEqual(result.e, expected_error)
                    self.assertAlmostEqual(result.e_minus, expected_error)
                    self.assertAlmostEqual(result.r, 0)
                    self.assertAlmostEqual(result.r_minus, 0)
        contrast = original_contrasts(windows, expected_positions=("upper", "lower"))
        self.assertIsNotNone(contrast)
        self.assertAlmostEqual(contrast.braking, 0)
        self.assertAlmostEqual(contrast.opening, 0)
        self.assertLess(contrast.opening, values["curve_original_open_contrast"])
        # A continuing common fall still supports an already observed opening;
        # it need not manufacture another temperature kink.
        self.assertGreater(contrast.opening_support,
                           values["curve_original_open_contrast"])
        self.assertAlmostEqual(contrast.humidity_fall_onset, 0)

    def test_constant_rh_ramps_have_direction_without_an_onset(self):
        values = Parameters({}).values
        for direction, field, onset in (
            (1, "rise", "humidity_rise_onset"),
            (-1, "fall", "humidity_fall_onset"),
        ):
            with self.subTest(direction=direction):
                originals, _ = humidity_step_originals(values, direction, direction)
                at = originals["upper_humidity"][-1][0]
                result = original_contrasts(
                    position_windows(originals, at, values),
                    expected_positions=("upper", "lower"),
                )
                self.assertIsNotNone(result)
                self.assertGreater(getattr(result, field),
                                   values["curve_original_new_infusion_contrast"])
                self.assertAlmostEqual(result.humidity_rise_change, 0)
                self.assertAlmostEqual(getattr(result, onset), 0)

    def test_braked_rh_fall_has_positive_change_without_a_rise(self):
        values = Parameters({}).values
        originals, _ = humidity_step_originals(values, -1, -0.25)
        at = originals["upper_humidity"][-1][0]
        result = original_contrasts(
            position_windows(originals, at, values),
            expected_positions=("upper", "lower"),
        )
        self.assertIsNotNone(result)
        self.assertGreater(result.humidity_rise_change, 0)
        self.assertLess(result.rise, 0)
        self.assertLess(result.humidity_rise_onset, 0)
        self.assertLess(result.humidity_fall_onset, 0)

    def test_directed_rh_onset_requires_a_kink_at_every_required_position(self):
        values = Parameters({}).values
        significance = values["curve_original_significance"]
        for direction, field, onset in (
            (1, "rise", "humidity_rise_onset"),
            (-1, "fall", "humidity_fall_onset"),
        ):
            originals, _ = humidity_step_originals(values, 0, direction)
            at = originals["upper_humidity"][-1][0]
            windows = position_windows(originals, at, values)
            result = original_contrasts(windows, expected_positions=("upper", "lower"))
            self.assertIsNotNone(result)
            self.assertGreater(getattr(result, onset), significance)
            linear, _ = humidity_step_originals(values, direction, direction)
            linear_windows = position_windows(linear, at, values)
            for unchanged in ("upper", "lower"):
                with self.subTest(direction=direction, unchanged_position=unchanged):
                    mixed = {**windows, unchanged: replace(
                        windows[unchanged], humidity=linear_windows[unchanged].humidity,
                    )}
                    joint = original_contrasts(
                        mixed, expected_positions=("upper", "lower"),
                    )
                    self.assertIsNotNone(joint)
                    # Current directions are identical; either missing kink
                    # prevents joint onset without discarding that position.
                    self.assertAlmostEqual(getattr(joint, field),
                                           getattr(result, field))
                    self.assertAlmostEqual(getattr(joint, onset), 0)
                    self.assertEqual(joint.positions, tuple(mixed.items()))
                    for position in ("upper", "lower"):
                        selected = original_contrasts(
                            mixed, expected_positions=(position,),
                        )
                        self.assertIsNotNone(selected)
                        self.assertEqual(selected.positions,
                                         ((position, mixed[position]),))
                        if position == unchanged:
                            self.assertAlmostEqual(getattr(selected, onset), 0)
                        else:
                            self.assertGreater(getattr(selected, onset), significance)

    def test_directed_rh_onset_is_independent_of_temperature_shape(self):
        values = Parameters({}).values
        significance = values["curve_original_significance"]
        for direction, onset, opposite in (
            (1, "humidity_rise_onset", "humidity_fall_onset"),
            (-1, "humidity_fall_onset", "humidity_rise_onset"),
        ):
            originals, _ = humidity_step_originals(values, 0, direction)
            at = originals["upper_humidity"][-1][0]
            baseline = None
            for temperature_direction in (-1, 0, 1):
                with self.subTest(rh=direction, temperature=temperature_direction):
                    shaped = {
                        role: tuple(
                            (stamp, trace(0)[0] + index * temperature_direction)
                            for index, (stamp, _) in enumerate(points)
                        ) if role.endswith("_temperature") else points
                        for role, points in originals.items()
                    }
                    result = original_contrasts(
                        position_windows(shaped, at, values),
                        expected_positions=("upper", "lower"),
                    )
                    self.assertIsNotNone(result)
                    self.assertGreater(getattr(result, onset), significance)
                    self.assertLess(getattr(result, opposite), 0)
                    if baseline is None:
                        baseline = getattr(result, onset)
                    self.assertAlmostEqual(getattr(result, onset), baseline)
                    if direction == -1:
                        if temperature_direction == -1:
                            self.assertGreater(result.opening_support,
                                               values["curve_original_open_contrast"])
                        elif temperature_direction == 0:
                            self.assertAlmostEqual(result.opening_support, 0)
                        else:
                            self.assertLess(result.opening_support, 0)

    def test_local_rh_fluctuations_inside_consumed_scale_do_not_create_onset(self):
        values = Parameters({}).values
        count = values["curve_original_samples"]
        significance = values["curve_original_significance"]
        for index in range(count):
            for direction in (-1, 1):
                with self.subTest(current_index=index, direction=direction):
                    originals, _ = humidity_step_originals(values, 0, 0)
                    for position in ("upper", "lower"):
                        role = f"{position}_humidity"
                        amplitude = (values[f"curve_original_noise_humidity_{position}"]
                                     * min(0.5, significance / 2))
                        originals[role] = tuple(
                            (stamp, value + direction * amplitude
                             if offset == count + index else value)
                            for offset, (stamp, value) in enumerate(originals[role])
                        )
                    at = originals["upper_humidity"][-1][0]
                    windows = position_windows(originals, at, values)
                    result = original_contrasts(
                        windows, expected_positions=("upper", "lower"),
                    )
                    self.assertIsNotNone(result)
                    for position, pair in result.positions:
                        self.assertEqual(pair.humidity.scale, channel_arguments(
                            values, position, "humidity",
                        )["scale"])
                    self.assertLess(result.humidity_rise_onset, significance)
                    self.assertLess(result.humidity_fall_onset, significance)

    def test_rh_rise_change_preserves_value_units_and_time_origin(self):
        values = Parameters({}).values
        originals, _ = humidity_step_originals(values, 0, 1)
        at = originals["upper_humidity"][-1][0]
        baseline = original_contrasts(
            position_windows(originals, at, values),
            expected_positions=("upper", "lower"),
        )
        self.assertIsNotNone(baseline)
        self.assertGreater(baseline.humidity_rise_change, 0)
        variants = (
            (1, 0, timedelta(days=1)),
            (2, -10, timedelta()),
            (2, -10, timedelta(days=1)),
        )
        for factor, offset, shift in variants:
            with self.subTest(factor=factor, offset=offset, shift=shift):
                transformed = {
                    role: tuple(
                        (stamp + shift, factor * value + offset
                         if role.endswith("_humidity") else value)
                        for stamp, value in points
                    )
                    for role, points in originals.items()
                }
                scales = dict(values)
                for position in ("upper", "lower"):
                    scales[f"curve_original_noise_humidity_{position}"] *= factor
                result = original_contrasts(
                    position_windows(transformed, at + shift, scales),
                    expected_positions=("upper", "lower"),
                )
                self.assertIsNotNone(result)
                self.assertAlmostEqual(result.humidity_rise_change,
                                       baseline.humidity_rise_change)
                self.assertAlmostEqual(result.rise, baseline.rise)
                self.assertAlmostEqual(result.humidity_rise_onset,
                                       baseline.humidity_rise_onset)
                self.assertAlmostEqual(result.humidity_fall_onset,
                                       baseline.humidity_fall_onset)

    def test_rh_change_overflow_cannot_be_hidden_by_another_position(self):
        values = Parameters({}).values
        originals, _ = linear_originals(values)
        at = originals["upper_humidity"][-1][0]
        windows = position_windows(originals, at, values)
        self.assertIsNotNone(original_contrasts(
            windows, expected_positions=("upper", "lower"),
        ))
        # Robustness criterion: finite, positive bounds and a finite slope
        # change can still yield an overflowing normalized change. The other
        # position's zero change must not hide that unknown in the minimum.
        extreme = replace(
            windows["upper"].humidity,
            v=0, b=-1e308, e=1e-308, e_minus=1e-308,
        )
        self.assertTrue(all(isfinite(getattr(extreme, name))
                            for name in ("v", "b", "e", "e_minus")))
        windows["upper"] = replace(windows["upper"], humidity=extreme)
        self.assertIsNone(original_contrasts(
            windows, expected_positions=("upper", "lower"),
        ))

    def test_pro_r03_r04_prefixes_match_independent_rational_contrasts(self):
        values = Parameters({}).values
        count = values["curve_original_samples"]
        for recording, source_row, second, indices, expected in PRO_PREFIXES:
            with self.subTest(recording=recording, source_row=source_row):
                originals, at = original_prefix(recording, source_row)
                self.assertEqual(at, ORIGIN + timedelta(seconds=second))
                self.assertEqual(sum(map(len, originals.values())), source_row)
                windows = position_windows(originals, at, values)
                for role, index in zip(CLIMATE, indices, strict=True):
                    self.assertEqual(len(originals[role]) - 1, index, role)
                    position, quantity = role.split("_", 1)
                    window = getattr(windows[position], quantity)
                    self.assertIsNotNone(window, role)
                    self.assertEqual(window.previous,
                                     originals[role][-2 * count:-count], role)
                    self.assertEqual(window.current, originals[role][-count:], role)
                    self.assertEqual(window.newest_at, originals[role][-1][0], role)
                contrast = original_contrasts(
                    windows, expected_positions=("upper", "lower"),
                )
                self.assertIsNotNone(contrast)
                self.assertEqual(contrast.positions, tuple(windows.items()))
                self.assertIsNone(contrast.humidity_recovery)
                self.assertIsNone(contrast.closing)
                for field, oracle in zip(CONTRAST_FIELDS, expected, strict=True):
                    # Fixed arithmetic tolerance for all supplied rational
                    # values, independent of event thresholds and outcomes.
                    self.assertAlmostEqual(getattr(contrast, field), oracle,
                                           delta=1e-8, msg=field)

    def test_missing_positions_and_invalid_original_support_stay_unknown(self):
        values = Parameters({}).values
        count = values["curve_original_samples"]
        originals, _ = linear_originals(values)
        points = originals["upper_temperature"]
        at = points[-1][0]
        windows = position_windows(originals, at, values)
        self.assertIsNone(original_contrasts(
            {"upper": windows["upper"]}, expected_positions=("upper", "lower"),
        ))
        self.assertIsNone(original_contrasts(
            windows, expected_positions=("upper", "lower"),
            humidity_minima={"upper": windows["upper"].humidity.mean},
        ))
        arguments = channel_arguments(values, "upper", "temperature")
        unsupported_gap = (
            timedelta(seconds=arguments["max_gap_seconds"]) + timedelta.resolution
        )
        gap_points = points[:count] + tuple(
            (stamp + unsupported_gap, value) for stamp, value in points[count:]
        )
        variants = (
            ("insufficient originals", points[1:], at),
            ("duplicate boundary time", points[:count] + (
                (points[count - 1][0], points[count][1]),
            ) + points[count + 1:], at),
            ("reversed boundary time", points[:count] + (
                (points[count - 2][0], points[count][1]),
            ) + points[count + 1:], at),
            ("gap between windows", gap_points, gap_points[-1][0]),
            ("stale newest original", points, at + unsupported_gap),
        )
        for reason, samples, evaluated_at in variants:
            with self.subTest(reason=reason):
                self.assertIsNone(original_window(
                    samples, at=evaluated_at, **arguments,
                ))

    def test_water_pair_waits_for_right_original_and_consumes_moisture_formula(self):
        values = Parameters({}).values
        originals, _ = original_prefix("R03", PRO_PREFIXES[0][1])
        humidity = originals["upper_humidity"][0]
        temperatures = originals["upper_temperature"]
        before = next(
            point for point in reversed(temperatures) if point[0] < humidity[0]
        )
        after = next(point for point in temperatures if point[0] > humidity[0])
        arguments = {
            "temperature_scale": values["curve_original_noise_temperature_upper"],
            "humidity_scale": values["curve_original_noise_humidity_upper"],
            "max_gap_seconds": values["curve_original_gap_temperature_upper_seconds"],
        }
        self.assertIsNone(original_water_pair(
            humidity, temperatures, at=after[0] - timedelta.resolution, **arguments,
        ))
        self.assertIsNone(original_water_pair(
            humidity, (before,), at=after[0], **arguments,
        ))
        with patch.object(
            curves, "absolute_humidity", wraps=absolute_humidity,
        ) as density:
            result = original_water_pair(
                humidity, temperatures, at=after[0], **arguments,
            )
        self.assertIsNotNone(result)
        self.assertEqual(result.humidity_sample, humidity)
        self.assertEqual(result.temperature_before, before)
        self.assertEqual(result.temperature_after, after)
        self.assertEqual(result.available_at, after[0])
        fraction = (humidity[0] - before[0]) / (after[0] - before[0])
        expected_temperature = before[1] + fraction * (after[1] - before[1])
        self.assertAlmostEqual(result.temperature, expected_temperature)
        lower_arguments = (
            result.temperature - arguments["temperature_scale"],
            humidity[1] - arguments["humidity_scale"],
        )
        upper_arguments = (
            result.temperature + arguments["temperature_scale"],
            humidity[1] + arguments["humidity_scale"],
        )
        self.assertEqual(density.call_args_list,
                         [call(*lower_arguments), call(*upper_arguments)])
        self.assertEqual(result.lower_density, absolute_humidity(*lower_arguments))
        self.assertEqual(result.upper_density, absolute_humidity(*upper_arguments))

    def test_exact_water_timestamp_needs_no_later_temperature_or_clipped_envelope(self):
        values = Parameters({}).values
        # A synchronous case from the existing synthetic fixture. The exact
        # match has both T supports in its one original; a later point is absent.
        temperature, humidity = trace(0)
        arguments = {
            "temperature_scale": values["curve_original_noise_temperature_upper"],
            "humidity_scale": values["curve_original_noise_humidity_upper"],
            "max_gap_seconds": values["curve_original_gap_temperature_upper_seconds"],
        }
        temperature_sample = (ORIGIN, temperature)
        result = original_water_pair(
            (ORIGIN, humidity), (temperature_sample,), at=ORIGIN, **arguments,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.temperature_before, temperature_sample)
        self.assertEqual(result.temperature_after, temperature_sample)
        self.assertEqual(result.available_at, ORIGIN)
        self.assertEqual(result.temperature, temperature)
        self.assertIsNone(original_water_pair(
            (ORIGIN, arguments["humidity_scale"] / 2), (temperature_sample,),
            at=ORIGIN, **arguments,
        ))

    def test_water_pair_support_distinguishes_waiting_from_invalid_neighbors(self):
        values = Parameters({}).values
        arguments = water_arguments(values)
        temperature, humidity = trace(0)
        step = timedelta(seconds=arguments["max_gap_seconds"] / 4)
        before = (ORIGIN, temperature)
        humidity_sample = (ORIGIN + step, humidity)
        after = (ORIGIN + 2 * step, temperature)
        temperatures = (before, after)
        for evaluated_at in (humidity_sample[0], after[0] - timedelta.resolution):
            with self.subTest(evaluated_at=evaluated_at):
                support = original_water_pair_support(
                    humidity_sample, temperatures, at=evaluated_at, **arguments,
                )
                self.assertEqual(support.status, "pending")
                self.assertIsNone(support.pair)
                self.assertIsNone(original_water_pair(
                    humidity_sample, temperatures, at=evaluated_at, **arguments,
                ))
        ready = original_water_pair_support(
            humidity_sample, temperatures, at=after[0], **arguments,
        )
        self.assertEqual(ready.status, "ready")
        self.assertEqual(ready.pair, original_water_pair(
            humidity_sample, temperatures, at=after[0], **arguments,
        ))
        self.assertEqual(ready.pair.temperature_before, before)
        self.assertEqual(ready.pair.temperature_after, after)
        too_far = (
            ORIGIN + timedelta(seconds=arguments["max_gap_seconds"])
            + timedelta.resolution, temperature,
        )
        invalid_neighbors = (
            ("missing left", (after,)),
            ("gap at right", (before, too_far)),
            ("duplicate", (before, before, after)),
            ("nonfinite immediate right", (
                before, (after[0], float("nan")), (after[0] + step, temperature),
            )),
        )
        for reason, samples in invalid_neighbors:
            with self.subTest(reason=reason):
                support = original_water_pair_support(
                    humidity_sample, samples, at=too_far[0], **arguments,
                )
                self.assertEqual(support.status, "invalid")
                self.assertIsNone(support.pair)
                self.assertIsNone(original_water_pair(
                    humidity_sample, samples, at=too_far[0], **arguments,
                ))
        left_gap = original_water_pair_support(
            (too_far[0], humidity), (before,), at=too_far[0], **arguments,
        )
        self.assertEqual(left_gap.status, "invalid")

    def test_water_window_irregular_geometry_matches_rational_extreme_bounds(self):
        values = Parameters({}).values
        # On t=(0,1,3), the independent OLS weights are
        # (-2/7,-1/14,5/14). These fixed rational outcomes assess the
        # implementation without reimplementing its OLS calculation.
        envelopes = ((9, 11), (5, 9), (3.5, 4.5), (7.5, 8.5), (7, 9), (6, 10))
        ticks = (0, 1, 3, 4, 5, 7)
        pairs, step = water_envelope_originals(values, envelopes, ticks)
        unit = step.total_seconds()
        arguments = {
            "count": len(envelopes) // 2,
            "max_gap_seconds": values["curve_original_gap_humidity_upper_seconds"],
            "at": pairs[-1].available_at,
        }
        support = original_water_window(pairs, **arguments)
        self.assertEqual(support.status, "ready")
        result = support.features
        self.assertEqual(result.previous, pairs[:3])
        self.assertEqual(result.current, pairs[3:])
        self.assertEqual(result.start_at, pairs[0].humidity_sample[0])
        self.assertEqual(result.newest_at, pairs[-1].humidity_sample[0])
        self.assertEqual(result.available_at, pairs[-1].available_at)
        self.assertAlmostEqual(result.b * unit, -27 / 14)
        self.assertAlmostEqual(result.v * unit, 0)
        self.assertAlmostEqual(result.e_minus * unit, 17 / 28)
        self.assertAlmostEqual(result.e * unit, 13 / 14)
        # Independent endpoint choices attain both interval extremes. The
        # error is a worst-case envelope, with no root-sum-square assumption.
        for densities, old_slope, current_slope in (
            ((9, 5, 4.5, 7.5, 7, 10), -37 / 28, 13 / 14),
            ((11, 9, 3.5, 8.5, 9, 6), -71 / 28, -13 / 14),
        ):
            endpoints = tuple(
                replace(pair, lower_density=density, upper_density=density)
                for pair, density in zip(pairs, densities, strict=True)
            )
            extreme = original_water_window(endpoints, **arguments)
            self.assertEqual(extreme.status, "ready")
            self.assertAlmostEqual(extreme.features.b * unit, old_slope)
            self.assertAlmostEqual(extreme.features.v * unit, current_slope)
            self.assertEqual(extreme.features.e, 0)
            self.assertEqual(extreme.features.e_minus, 0)

    def test_water_window_requires_common_level_despite_zero_oscillation_slope(self):
        values = Parameters({}).values
        count = values["curve_original_samples"]
        significance = values["curve_original_significance"]
        # Symmetry about the time midpoint gives zero OLS slope, even when
        # the middle original's water level contradicts all other originals.
        jump = 1 + 2 * significance
        oscillation = tuple(
            (8 + jump, 10 + jump) if index in (count // 2, (count - 1) // 2)
            else (8, 10)
            for index in range(count)
        )
        pairs, _ = water_envelope_originals(
            values, ((8, 10),) * count + oscillation,
        )
        support = original_water_window(
            pairs, count=count, at=pairs[-1].available_at,
            max_gap_seconds=values["curve_original_gap_humidity_upper_seconds"],
        )
        self.assertEqual(support.status, "ready")
        result = support.features
        self.assertAlmostEqual(result.v, 0)
        self.assertFalse(result.constant_compatible(significance))
        self.assertGreater(*result.constant_bounds(significance))
        self.assertGreater(result.constant_lower, result.constant_upper)
        # A one-point intersection is sufficient; every observation can
        # contain the same level even when their envelope middles differ.
        touching = tuple(
            replace(pair, lower_density=6 if index % 2 else 8,
                    upper_density=8 if index % 2 else 10)
            for index, pair in enumerate(pairs)
        )
        constant = original_water_window(
            touching, count=count, at=touching[-1].available_at,
            max_gap_seconds=values["curve_original_gap_humidity_upper_seconds"],
        ).features
        self.assertEqual(constant.constant_bounds(1), (8, 8))
        self.assertEqual((constant.constant_lower, constant.constant_upper), (8, 8))
        self.assertTrue(constant.constant_compatible(1))
        self.assertTrue(constant.constant_compatible(2))
        self.assertFalse(constant.constant_compatible(0.5))
        for invalid in (0, -1, True, float("nan"), float("inf")):
            with self.subTest(significance=invalid):
                with self.assertRaises(ValueError):
                    constant.constant_bounds(invalid)

    def test_humidity_flattening_distinguishes_steady_t_fall_from_joint_braking(self):
        values = Parameters({}).values
        count = values["curve_original_samples"]
        significance = values["curve_original_significance"]
        originals, _ = humidity_step_originals(values, -1, 0)
        at = originals["upper_humidity"][-1][0]
        rh = position_windows(originals, at, values)["upper"].humidity
        fall = 8 * significance
        envelopes = tuple(
            (10 + (count - index) * fall - 1,
             10 + (count - index) * fall + 1)
            for index in range(count)
        ) + ((9, 11),) * count
        pairs, _ = water_envelope_originals(values, envelopes)
        water = original_water_window(
            pairs, count=count, at=pairs[-1].available_at,
            max_gap_seconds=values["curve_original_gap_humidity_upper_seconds"],
        ).features
        self.assertIsNotNone(rh)
        self.assertIsNotNone(water)
        for current_step, falling, not_braking in (
            (-1, True, True),
            (-1 + values["curve_original_noise_temperature_upper"] / 4, True, True),
            (0, False, False), (-0.25, True, False),
        ):
            thermal, _ = humidity_step_originals(values, -1, current_step)
            temperature = original_window(tuple(
                (stamp, trace(0)[0] + value - trace(0)[1])
                for stamp, value in thermal["upper_humidity"]
            ), at=at, **channel_arguments(values, "upper", "temperature"))
            self.assertIsNotNone(temperature)
            for quantity, humidity in (("RH", rh), ("AH", water)):
                with self.subTest(quantity=quantity, temperature_step=current_step):
                    result = original_flattening(
                        humidity, temperature, significance=significance,
                    )
                    self.assertIsNotNone(result)
                    self.assertIs(result.humidity_window, humidity)
                    self.assertIs(result.temperature_window, temperature)
                    self.assertEqual(result.significance, significance)
                    # Each reason remains observable, including T that is
                    # still falling but has already distinctly braked.
                    for name, operator, expected in (
                        ("previous_fall", ">", True),
                        ("current_flat", "<=", True),
                        ("braking", ">", True),
                        ("temperature_falling", ">", falling),
                        ("temperature_not_braking", "<=", not_braking),
                    ):
                        comparison = getattr(result, name)
                        self.assertEqual(comparison.operator, operator, name)
                        self.assertEqual(comparison.result, expected, name)
                        self.assertTrue(isfinite(comparison.value), name)
                        self.assertTrue(isfinite(comparison.threshold), name)
                        self.assertGreaterEqual(comparison.threshold, 0, name)
                    self.assertEqual(result.current_flat.value, 0)
                    self.assertEqual(result.initial, not_braking)
                    self.assertEqual(result.maintained, not_braking)

    def test_flattening_stays_maintained_after_previous_fall_rolls_out(self):
        values = Parameters({}).values
        count = values["curve_original_samples"]
        significance = values["curve_original_significance"]
        originals, step = humidity_step_originals(values, -1, 0)
        for role, points in tuple(originals.items()):
            tail_at, tail_value = points[-1]
            originals[role] += tuple(
                (tail_at + index * step,
                 tail_value - index if role.endswith("_temperature") else tail_value)
                for index in range(1, count + 1)
            )
        fall = 8 * significance
        envelopes = tuple(
            (10 + (count - index) * fall - 1,
             10 + (count - index) * fall + 1)
            for index in range(count)
        ) + ((9, 11),) * (2 * count)
        pairs, _ = water_envelope_originals(values, envelopes)
        first_windows = {}
        for prefix in (2 * count, 3 * count):
            selected = {role: points[:prefix] for role, points in originals.items()}
            at = selected["upper_temperature"][-1][0]
            windows = position_windows(selected, at, values)["upper"]
            water = original_water_window(
                pairs[:prefix], count=count, at=pairs[prefix - 1].available_at,
                max_gap_seconds=values["curve_original_gap_humidity_upper_seconds"],
            ).features
            for quantity, humidity in (("RH", windows.humidity), ("AH", water)):
                with self.subTest(quantity=quantity, prefix=prefix):
                    result = original_flattening(
                        humidity, windows.temperature, significance=significance,
                    )
                    self.assertIsNotNone(result)
                    self.assertTrue(result.maintained)
                    self.assertEqual(result.initial, prefix == 2 * count)
                    if prefix == 2 * count:
                        first_windows[quantity] = humidity
                    else:
                        # The old fall is absent from both retained windows;
                        # the previous window is now the original plateau.
                        self.assertEqual(humidity.previous,
                                         first_windows[quantity].current)
                        self.assertFalse(result.previous_fall.result)
                        self.assertFalse(result.braking.result)

    def test_flattening_accepts_exact_water_envelopes_and_rejects_unknown_inputs(self):
        values = Parameters({}).values
        count = values["curve_original_samples"]
        significance = values["curve_original_significance"]
        originals, _ = humidity_step_originals(values, -1, 0)
        at = originals["upper_temperature"][-1][0]
        windows = position_windows(originals, at, values)["upper"]
        pairs, _ = water_envelope_originals(values, tuple(
            (10 + count - index, 10 + count - index) for index in range(count)
        ) + ((10, 10),) * count)
        water = original_water_window(
            pairs, count=count, at=pairs[-1].available_at,
            max_gap_seconds=values["curve_original_gap_humidity_upper_seconds"],
        ).features
        self.assertIsNotNone(water)
        self.assertEqual((water.e, water.e_minus), (0, 0))
        exact = original_flattening(
            water, windows.temperature, significance=significance,
        )
        self.assertIsNotNone(exact)
        self.assertTrue(exact.initial)
        self.assertTrue(exact.maintained)
        for name in ("previous_fall", "current_flat", "braking"):
            self.assertEqual(getattr(exact, name).threshold, 0)
        invalid_humidity = (
            None, replace(windows.humidity, e=0),
            replace(windows.humidity, v=float("nan")),
            replace(water, e=-1), replace(water, b=float("inf")),
            replace(water, v=1e308, b=-1e308),
            replace(water, e=1e308, e_minus=1e308),
            replace(water, previous=()), replace(water, current=()),
            replace(water, current=(None,) + water.current[1:]),
            replace(water, current=(replace(
                water.current[0], upper_density=float("inf"),
            ),) + water.current[1:]),
        )
        for index, humidity in enumerate(invalid_humidity):
            with self.subTest(invalid_humidity=index):
                self.assertIsNone(original_flattening(
                    humidity, windows.temperature, significance=significance,
                ))
        for temperature in (None, replace(windows.temperature, e=0),
                            replace(windows.temperature, v=float("nan"))):
            with self.subTest(invalid_temperature=temperature):
                self.assertIsNone(original_flattening(
                    water, temperature, significance=significance,
                ))
        for invalid in (0, -1, True, float("nan"), float("inf")):
            with self.subTest(significance=invalid):
                self.assertIsNone(original_flattening(
                    water, windows.temperature, significance=invalid,
                ))

    def test_water_level_deviation_matches_weighted_rational_golden(self):
        values = Parameters({}).values
        pairs, _ = water_envelope_originals(
            values, ((8, 10),) * 3 + ((9, 11), (10, 14), (12, 20)),
        )
        result = original_water_window(
            pairs, count=3, at=pairs[-1].available_at,
            max_gap_seconds=values["curve_original_gap_humidity_upper_seconds"],
        ).features
        self.assertIsNotNone(result)
        # m=(10,12,16), a=(1,2,4): inverse-square weights give c=32/3.
        # The normalized residuals are (-2/3,2/3,4/3), hence D²=8/9.
        self.assertAlmostEqual(result.normalized_level_deviation(), sqrt(8 / 9))
        self.assertEqual(result.current, pairs[3:])
        # A normalized RMS and an all-point intersection are different norms.
        # These are mathematical criteria, not new operating thresholds.
        self.assertFalse(result.constant_compatible(1))
        self.assertLess(result.normalized_level_deviation(), 1)
        self.assertAlmostEqual(replace(
            result, current=tuple(reversed(result.current)),
        ).normalized_level_deviation(), sqrt(8 / 9))
        self.assertAlmostEqual(replace(
            result, previous=tuple(reversed(result.previous)),
        ).normalized_level_deviation(), sqrt(8 / 9))

    def test_water_level_deviation_counts_every_current_pair_and_equal_level(self):
        values = Parameters({}).values
        count = values["curve_original_samples"]
        pairs, _ = water_envelope_originals(values, ((8, 10),) * (2 * count))
        result = original_water_window(
            pairs, count=count, at=pairs[-1].available_at,
            max_gap_seconds=values["curve_original_gap_humidity_upper_seconds"],
        ).features
        self.assertIsNotNone(result)
        self.assertEqual(result.normalized_level_deviation(), 0)
        for index in range(count):
            with self.subTest(changed_current_index=index):
                current = tuple(
                    replace(pair, lower_density=8 + count, upper_density=10 + count)
                    if offset == index else pair
                    for offset, pair in enumerate(result.current)
                )
                # One displacement K among K equal-radius originals has
                # center displacement 1 and D²=K-1, wherever it occurs.
                self.assertAlmostEqual(replace(
                    result, current=current,
                ).normalized_level_deviation(), sqrt(count - 1))
        center = count + 1
        equal_level = tuple(
            replace(pair, lower_density=center - radius, upper_density=center + radius)
            for radius, pair in enumerate(result.current, start=1)
        )
        self.assertEqual(replace(
            result, current=equal_level,
        ).normalized_level_deviation(), 0)

    def test_water_level_deviation_distinguishes_alternating_k7_zero_slope_sequence(self):
        values = Parameters({}).values
        count = values["curve_original_samples"]
        self.assertEqual(count, 7)  # The declared cancellation criterion is K=7.
        significance = values["curve_original_significance"]
        amplitude = 2 * significance
        center = 10 + 2 * amplitude
        current = tuple(
            (center + sign * amplitude - 1, center + sign * amplitude + 1)
            for sign in (1, -1, 1, -1, 1, -1, 1)
        )
        pairs, _ = water_envelope_originals(
            values, ((center - 1, center + 1),) * count + current,
        )
        result = original_water_window(
            pairs, count=count, at=pairs[-1].available_at,
            max_gap_seconds=values["curve_original_gap_humidity_upper_seconds"],
        ).features
        self.assertIsNotNone(result)
        # Reflection symmetry cancels OLS slope. Four +A and three -A
        # observations still have D²=48*A²/49 around their free mean A/7.
        self.assertAlmostEqual(result.v, 0)
        self.assertLessEqual(abs(result.v), significance * result.e)
        self.assertAlmostEqual(result.normalized_level_deviation(),
                               amplitude * sqrt(48) / 7)
        self.assertGreater(result.normalized_level_deviation(), significance)

    def test_water_level_deviation_zero_radii_extremes_and_unknown_inputs(self):
        values = Parameters({}).values
        pairs, _ = water_envelope_originals(values, ((8, 10),) * 6)
        result = original_water_window(
            pairs, count=3, at=pairs[-1].available_at,
            max_gap_seconds=values["curve_original_gap_humidity_upper_seconds"],
        ).features
        self.assertIsNotNone(result)

        def score(envelopes):
            return replace(result, current=tuple(
                replace(pair, lower_density=lower, upper_density=upper)
                for pair, (lower, upper) in zip(result.current, envelopes, strict=True)
            )).normalized_level_deviation()

        self.assertEqual(score(((10, 10),) * 3), 0)
        # Two exact originals fix c=10; the third contributes (12-10)/2=1.
        self.assertAlmostEqual(score(((10, 10), (10, 10), (10, 14))), 1 / sqrt(3))
        self.assertIsNone(score(((10, 10), (11, 11), (10, 14))))
        # Finite endpoints may have overflowing sums or reciprocal squares.
        # Their midpoints/radii remain usable without an artificial floor.
        self.assertAlmostEqual(score((
            (7e307, 9e307), (8e307, 1e308), (9e307, 1.1e308),
        )), sqrt(2 / 3))
        self.assertAlmostEqual(score((
            (0, 2e-150), (1e-150, 3e-150), (2e-150, 4e-150),
        )), sqrt(2 / 3))
        self.assertAlmostEqual(score((
            (0, 2e-300), (0, 2e300), (0, 2e300),
        )), sqrt(2 / 3))
        self.assertIsNone(score(((10, 10), (0, 2e-310), (10, 14))))
        for bounds in ((float("nan"), 10), (8, float("inf")), (10, 8), (-1, 1)):
            with self.subTest(bounds=bounds):
                self.assertIsNone(score((bounds, (8, 10), (8, 10))))
        self.assertIsNone(replace(result, current=()).normalized_level_deviation())

    def test_r03_door2_prefixes_export_level_deviation_and_all_form_gates(self):
        """Extract the two declared prefixes; do not assume either qualifies.

        The reference is R03_trailing_pending_identity_review in the local
        vent-owner-review.json. Climate row numbers belong to the existing
        checksum-bound fixture; archive record IDs 3940/3945 are a different
        index. Source names below are the replay runner's functional BINDINGS,
        not a reconstruction of physical sensor IDs or source epochs.
        """
        values = Parameters({}).values
        count = values["curve_original_samples"]
        significance = values["curve_original_significance"]
        floor = ORIGIN + timedelta(seconds=899)
        references = (
            (2954, 3940, 949.789675, (711, 762, 716, 761), {
                "upper": (948.723650, 948.709000, 949.759458, (949.773302,)),
                "lower": (948.748448, 948.738673, 949.789675, ()),
            }),
            (2958, 3945, 950.838898, (712, 763, 717, 762), {
                "upper": (949.773302, 949.759458, 950.811703, (950.825598,)),
                "lower": (949.798450, 949.789675, 950.838898, ()),
            }),
        )
        case = json.loads((ROOT / "fixtures/replay/R03.json").read_text(
            encoding="utf-8",
        ))
        evidence = {
            "recording": "R03", "samples_sha256": case["samples_sha256"],
            "definition": "D=min_c sqrt(mean(((m_i-c)/a_i)^2))",
            "count": count, "significance": significance, "support_floor": floor,
            "source_binding": (
                "tests/sessions/runner.py:BINDINGS; functional fixture roles"
            ),
            "owner_scope": (
                "No controller replay, candidate admission or close-priority claim"
            ),
            "prefixes": [],
        }
        previous_newest = {}
        for source_row, record_id, second, indices, expected in references:
            originals, at = original_prefix("R03", source_row)
            self.assertEqual(at, ORIGIN + timedelta(seconds=second))
            self.assertEqual(sum(map(len, originals.values())), source_row)
            for role, index in zip(CLIMATE, indices, strict=True):
                self.assertEqual(len(originals[role]) - 1, index, role)
            windows = position_windows(originals, at, values)
            prefix = {"source_row": source_row, "archive_record_id": record_id,
                      "at": at, "positions": {}}
            for position, identities in expected.items():
                with self.subTest(source_row=source_row, position=position):
                    temperature_role, humidity_role = (
                        f"{position}_temperature", f"{position}_humidity",
                    )
                    temperatures, humidities = (
                        originals[temperature_role], originals[humidity_role],
                    )
                    temperature_arguments = channel_arguments(
                        values, position, "temperature",
                    )
                    humidity_arguments = channel_arguments(values, position, "humidity")
                    arguments = {
                        "temperature_scale": temperature_arguments["scale"],
                        "humidity_scale": humidity_arguments["scale"],
                        "max_gap_seconds": temperature_arguments["max_gap_seconds"],
                    }
                    pairs, pending = [], []
                    for point in humidities[-(2 * count + 1):]:
                        support = original_water_pair_support(
                            point, temperatures, at=at, **arguments,
                        )
                        self.assertIn(support.status, ("ready", "pending"))
                        if support.status == "pending":
                            pending.append(point[0])
                        else:
                            self.assertFalse(pending,
                                             "an inner pending original was skipped")
                            pairs.append(support.pair)
                    rh_gap = humidity_arguments["max_gap_seconds"]
                    support = original_water_window(
                        tuple(pairs), count=count, at=at, max_gap_seconds=rh_gap,
                    )
                    self.assertEqual(support.status, "ready")
                    water = support.features
                    self.assertEqual(water.previous, tuple(pairs[-2 * count:-count]))
                    self.assertEqual(water.current, tuple(pairs[-count:]))
                    self.assertEqual(len(water.previous + water.current), 2 * count)
                    (expected_rh, expected_left,
                     expected_right, expected_pending) = identities
                    last = water.current[-1]
                    self.assertEqual(tuple(
                        (stamp - ORIGIN).total_seconds() for stamp in (
                            last.humidity_sample[0], last.temperature_before[0],
                            last.temperature_after[0],
                        )
                    ), (expected_rh, expected_left, expected_right))
                    self.assertEqual(tuple(
                        (stamp - ORIGIN).total_seconds() for stamp in pending
                    ), expected_pending)
                    self.assertEqual(last.humidity_sample,
                                     humidities[-1 - len(pending)])
                    self.assertTrue(all(pair.temperature_before[0] >= floor
                                        for pair in water.previous + water.current))
                    temperature = windows[position].temperature
                    self.assertIsNotNone(temperature)
                    self.assertEqual(temperature.newest_at, temperatures[-1][0])
                    self.assertGreaterEqual(temperature.current[0][0], floor)
                    deviation = water.normalized_level_deviation()
                    self.assertIsNotNone(deviation)
                    self.assertTrue(isfinite(deviation))
                    lower, upper = water.constant_bounds(significance)
                    comparisons = {
                        "previous_water_fall": (
                            -water.b, ">", significance * water.e_minus,
                        ),
                        "current_water_flat": (
                            abs(water.v), "<=", significance * water.e,
                        ),
                        "water_fall_braking": (
                            water.v - water.b, ">",
                            significance * (water.e + water.e_minus),
                        ),
                        "temperature_still_falling": (
                            -temperature.v, ">", significance * temperature.e,
                        ),
                    }
                    gates = {
                        name: {
                            "value": value, "operator": operator,
                            "threshold": threshold,
                            "result": (value > threshold if operator == ">"
                                       else value <= threshold),
                        }
                        for name, (value, operator, threshold) in comparisons.items()
                    }
                    newest = (temperature.newest_at, water.newest_at)
                    fresh = (all(now > before for now, before in zip(
                        newest, previous_newest[position], strict=True,
                    )) if position in previous_newest else None)
                    if position in previous_newest:
                        self.assertTrue(fresh)
                    previous_newest[position] = newest
                    prefix["positions"][position] = {
                        "sources": {
                            "temperature": f"sensor.fixture_{temperature_role}",
                            "humidity": f"sensor.fixture_{humidity_role}",
                        },
                        "scales_and_temperature_gap": arguments, "humidity_gap_seconds": rh_gap,
                        "temperature_newest_at": temperature.newest_at,
                        "water_newest_at": water.newest_at,
                        "pending_rh_at": pending,
                        "later_temperature_and_paired_rh": fresh,
                        "v": water.v, "b": water.b, "e": water.e, "e_minus": water.e_minus,
                        "temperature_v": temperature.v,
                        "temperature_e": temperature.e,
                        "form_gates": gates,
                        "level_diagnostics": {
                            "D": deviation,
                            "water_level_deviation": {
                                "value": deviation, "operator": "<=",
                                "threshold": significance,
                                "result": deviation <= significance,
                            },
                            "all_k_constant_compatible": {
                                "value": lower, "operator": "<=", "threshold": upper,
                                "result": lower <= upper,
                            },
                        },
                        "pairs": [{
                            "rh": pair.humidity_sample,
                            "temperature_before": pair.temperature_before,
                            "temperature_after": pair.temperature_after,
                            "available_at": pair.available_at,
                            "middle": (pair.lower_density
                                       + (pair.upper_density - pair.lower_density) / 2),
                            "radius": (pair.upper_density - pair.lower_density) / 2,
                            "lower_density": pair.lower_density,
                            "upper_density": pair.upper_density,
                        } for pair in water.previous + water.current],
                    }
            evidence["prefixes"].append(prefix)
        serialized = json.dumps(evidence, default=lambda value: value.isoformat(),
                                allow_nan=False, sort_keys=True, indent=2) + "\n"
        if folder := os.environ.get("SAUNA_CORE_EVIDENCE"):
            path = Path(folder) / "r03-door2-level-deviation.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(serialized, encoding="utf-8")
        print(serialized)

    def test_water_window_keeps_exact_tail_and_never_skips_unknown_support(self):
        values = Parameters({}).values
        count = values["curve_original_samples"]
        pairs, step = water_envelope_originals(values, ((8, 10),) * (2 * count + 1))
        arguments = {
            "count": count,
            "max_gap_seconds": values["curve_original_gap_humidity_upper_seconds"],
            "at": pairs[-1].available_at,
        }
        support = original_water_window(pairs, **arguments)
        self.assertEqual(support.status, "ready")
        self.assertEqual(support.features.previous, pairs[1:count + 1])
        self.assertEqual(support.features.current, pairs[count + 1:])
        self.assertEqual(original_water_window((), **arguments).status, "insufficient")
        self.assertEqual(original_water_window(
            pairs[2:], **arguments,
        ).status, "insufficient")
        waiting = replace(
            pairs[-1],
            temperature_after=(pairs[-1].available_at + step, trace(0)[0]),
            available_at=pairs[-1].available_at + step,
        )
        pending = original_water_window(pairs[:-1] + (waiting,), **arguments)
        self.assertEqual(pending.status, "pending")
        self.assertIsNone(pending.features)
        ready = original_water_window(
            pairs[:-1] + (waiting,),
            **{**arguments, "at": waiting.available_at},
        )
        self.assertEqual(ready.status, "ready")
        self.assertEqual(ready.features.available_at, waiting.available_at)
        # The caller owns receipt freshness and source continuity. A complete
        # historical window does not expire while a later right T is pending.
        self.assertEqual(original_water_window(
            pairs, **{**arguments, "at": arguments["at"] + timedelta(
                seconds=2 * arguments["max_gap_seconds"],
            )},
        ).status, "ready")
        shift = timedelta(seconds=arguments["max_gap_seconds"]) + timedelta.resolution
        shifted = tuple(replace(
            pair,
            humidity_sample=(pair.humidity_sample[0] + shift, pair.humidity_sample[1]),
            temperature_before=(pair.temperature_before[0] + shift,
                                pair.temperature_before[1]),
            temperature_after=(pair.temperature_after[0] + shift,
                               pair.temperature_after[1]),
            available_at=pair.available_at + shift,
        ) for pair in pairs[count + 1:])
        variants = (
            ("unknown final pair", pairs[:-1] + (None,)),
            ("unknown interior pair", pairs[:count] + (None,) + pairs[count + 1:]),
            ("duplicate original", pairs[:-1] + (pairs[-2],)),
            ("reversed original", pairs[:-1] + (pairs[-3],)),
            ("gap between windows", pairs[:count + 1] + shifted),
            ("nonfinite bound", pairs[:-1] + (
                replace(pairs[-1], upper_density=float("inf")),
            )),
            ("inverted envelope", pairs[:-1] + (
                replace(pairs[-1], lower_density=11),
            )),
        )
        for reason, samples in variants:
            with self.subTest(reason=reason):
                result = original_water_window(samples, **arguments)
                self.assertEqual(result.status, "invalid")
                self.assertIsNone(result.features)
        invalid_arguments = (
            {"count": True}, {"count": 1}, {"count": float("nan")},
            {"max_gap_seconds": 0}, {"max_gap_seconds": float("inf")},
            {"at": None},
        )
        for invalid in invalid_arguments:
            with self.subTest(arguments=invalid):
                self.assertEqual(original_water_window(
                    pairs, **{**arguments, **invalid},
                ).status, "invalid")

    def test_single_position_preserves_algebra_without_empirical_transfer(self):
        values = Parameters({}).values
        originals, at = original_prefix("R03", PRO_PREFIXES[0][1])
        upper = position_windows(originals, at, values)["upper"]
        significance = values["curve_original_significance"]
        minimum = upper.humidity.mean - 2 * upper.humidity.scale * significance
        result = original_contrasts(
            {"upper": upper}, expected_positions=("upper",),
            humidity_minima={"upper": minimum},
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.positions, (("upper", upper),))
        self.assertEqual(result.warming_min, result.warming_max)
        self.assertEqual(result.fall, -result.rise)
        self.assertEqual(result.background_slope, abs(result.rise))
        self.assertAlmostEqual(result.humidity_recovery, significance)
        self.assertAlmostEqual(result.opening,
                               min(-result.warming_min, result.fall, -result.braking))
        # For one position W_min = W_max = W, so min(B, W) <= W and
        # max(min(B,U), min(B,W), W) simplifies to max(min(B,U), W).
        self.assertAlmostEqual(
            result.closing, max(min(result.braking, significance), result.warming_min),
        )


if __name__ == "__main__":
    unittest.main()
