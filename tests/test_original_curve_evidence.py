"""Fixed-original contracts and independent Pro values on bound replay prefixes.

The golden values below are evaluation criteria, not operational parameters or
physical event labels. They come from the supplied package's independent
``Kontrolle2_Exakte_Praefixwerte.csv`` (``exact_decimal`` column); source indices
come from ``Alle_Praefixmerkmale.csv.gz``. Only the four selected prefixes are
evaluated, without recalibrating or replaying the complete recordings.
"""

from datetime import datetime, timedelta
import gzip
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import call, patch

from test_detector import trace
from test_settings import fixture

from custom_components.ha_sauna.core import curve_change as curves
from custom_components.ha_sauna.core.curve_change import (
    OriginalPositionWindows,
    original_contrasts,
    original_water_pair,
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
