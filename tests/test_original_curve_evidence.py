"""Fixed-original contracts and independent Pro values on bound replay prefixes.

The golden values below are evaluation criteria, not operational parameters or
physical event labels. They come from the supplied package's independent
``Kontrolle2_Exakte_Praefixwerte.csv`` (``exact_decimal`` column); source indices
come from ``Alle_Praefixmerkmale.csv.gz``. Only the four selected prefixes are
evaluated, without recalibrating or replaying the complete recordings.
"""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta
import gzip
import hashlib
import json
from math import ceil, isfinite, sqrt
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
    original_decline_evidence,
    original_edge_change,
    original_flattening,
    original_followup,
    original_kink_tolerance,
    original_water_pair,
    original_water_pair_support,
    original_water_window,
    original_window,
)
from custom_components.ha_sauna.core.detector import Detector
from custom_components.ha_sauna.core.models import Measurement, Position, Quantity
from custom_components.ha_sauna.core.moisture import absolute_humidity
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.timeline import Kind


ROOT = Path(__file__).resolve().parent
ORIGIN = datetime.fromisoformat(fixture("session")["origin"])
CLIMATE = (
    "upper_temperature",
    "upper_humidity",
    "lower_temperature",
    "lower_humidity",
)
CONTRAST_FIELDS = (
    "opening",
    "braking",
    "warming_min",
    "warming_max",
    "rise",
    "fall",
    "background_slope",
    "background_residual",
)

# Original package: HA_Sauna_Gesamtpaket/02_Neue_Belege. Source rows count
# climate originals only; each channel index starts at zero. The R04 rows are
# the first episode's background confirmation and the second episode's fall
# confirmation. These references assess arithmetic, not event recurrence.
PRO_PREFIXES = (
    (
        "R03",
        1728,
        572.419482,
        (409, 449, 404, 462),
        (
            1.4357942341989889,
            -2.8295073926688772,
            -5.0167248165022656,
            -3.0642048752985154,
            -1.5516706397805743,
            1.4357942341989889,
            1.5516706397805743,
            1.8728699129465108,
        ),
    ),
    (
        "R03",
        2233,
        728.015057,
        (532, 577, 535, 585),
        (
            -56.697605452715461,
            -0.88698752452087126,
            2.4489002367475152,
            4.3666371816984055,
            26.765596772917744,
            -56.697605452715461,
            56.697605452715461,
            111.38519164233529,
        ),
    ),
    (
        "R04",
        32746,
        10658.212391,
        (7705, 8719, 7588, 8730),
        (
            -1.4827854608096998,
            -0.47069060758893117,
            0.43464891766525869,
            1.4827854608096998,
            -0.065237250280922154,
            -0.66528951310152006,
            0.66528951310152006,
            3.5200851102399251,
        ),
    ),
    (
        "R04",
        32850,
        10689.811525,
        (7729, 8748, 7608, 8761),
        (
            0.036215092076947101,
            -0.95248010450212737,
            -1.1990538333236744,
            -0.036215092076947101,
            -31.156878298764678,
            29.576334128510769,
            31.156878298764678,
            40.780676865611699,
        ),
    ),
)


def channel_arguments(values, position, quantity):
    """Use the sole product catalogue for all operating and support limits."""
    return {
        "count": values["curve_original_samples"],
        "scale": values[f"curve_original_noise_{quantity}_{position}"],
        "max_gap_seconds": values[f"curve_original_gap_{quantity}_{position}_seconds"],
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
        position: OriginalPositionWindows(
            **{
                quantity: original_window(
                    originals[f"{position}_{quantity}"],
                    at=at,
                    **channel_arguments(values, position, quantity),
                )
                for quantity in ("temperature", "humidity")
            }
        )
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
            (
                stamp,
                baseline
                + (
                    index * previous_step
                    if index < count
                    else (count - 1) * previous_step
                    + (index - count + 1) * current_step
                ),
            )
            for index, (stamp, _) in enumerate(originals[role])
        )
    return originals, step


def kink_history_originals(values, shape, *, history_windows=None):
    """Extend the existing line fixture backwards; shape uses original ticks."""
    originals, step = linear_originals(values)
    active = originals["upper_humidity"]
    count = values["curve_original_samples"]
    if history_windows is None:
        history_windows = values["curve_original_kink_history_windows"]
    size = 2 * count + history_windows - 1
    stamps = tuple(
        active[0][0] - (size - index) * step for index in range(size)
    ) + tuple(stamp for stamp, _ in active)
    return tuple(
        (stamp, trace(0)[1] + shape(index - size)) for index, stamp in enumerate(stamps)
    ), step


def kink_arguments(values):
    return {
        "factor": values["curve_original_kink_factor"],
        "min_history_windows": values["curve_original_kink_history_windows"],
        "max_gap_seconds": values["curve_original_gap_humidity_upper_seconds"],
    }


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
    step = timedelta(
        seconds=min(
            water_arguments(values)["max_gap_seconds"],
            values["curve_original_gap_humidity_upper_seconds"],
        )
        / 4
    )
    ticks = range(len(envelopes)) if ticks is None else ticks
    pairs = []
    for tick, (lower, upper) in zip(ticks, envelopes, strict=True):
        stamp = ORIGIN + tick * step
        pair = original_water_pair(
            (stamp, humidity),
            ((stamp, temperature),),
            at=stamp,
            **water_arguments(values),
        )
        pairs.append(replace(pair, lower_density=lower, upper_density=upper))
    return tuple(pairs), step


class OriginalCurveEvidenceTests(unittest.TestCase):
    def test_kink_tolerance_preserves_units_offsets_and_direction(self):
        values = Parameters(
            {
                "curve_original_kink_factor": 0.5,
                "curve_original_kink_history_windows": 2,
            }
        ).values
        curvature = values["curve_original_noise_humidity_upper"]
        samples, _ = kink_history_originals(values, lambda index: curvature * index**2)
        window = original_window(
            samples, at=samples[-1][0], **channel_arguments(values, "upper", "humidity")
        )
        baseline = original_kink_tolerance(samples, window, **kink_arguments(values))
        self.assertIsNotNone(baseline)
        self.assertGreater(baseline.effective_change, 0)
        for time_factor, value_factor, offset, shift in (
            (1, 1, 100, timedelta(days=1)),
            (2, 3, -40, timedelta(days=2)),
            (2, -2, 80, timedelta(days=-1)),
        ):
            with self.subTest(time_factor=time_factor, value_factor=value_factor):
                transformed_values = Parameters(
                    {
                        **values,
                        "curve_original_noise_humidity_upper": (
                            abs(value_factor)
                            * values["curve_original_noise_humidity_upper"]
                        ),
                        "curve_original_gap_humidity_upper_seconds": (
                            time_factor
                            * values["curve_original_gap_humidity_upper_seconds"]
                        ),
                    }
                ).values
                transformed = tuple(
                    (
                        ORIGIN + time_factor * (stamp - ORIGIN) + shift,
                        value_factor * value + offset,
                    )
                    for stamp, value in samples
                )
                changed_window = original_window(
                    transformed,
                    at=transformed[-1][0],
                    **channel_arguments(transformed_values, "upper", "humidity"),
                )
                changed = original_kink_tolerance(
                    transformed,
                    changed_window,
                    **kink_arguments(transformed_values),
                )
                self.assertIsNotNone(changed)
                self.assertEqual(changed.history_count, baseline.history_count)
                self.assertEqual(changed.status, baseline.status)
                self.assertEqual(changed.factor, baseline.factor)
                for field in ("delta", "effective_change"):
                    self.assertAlmostEqual(
                        getattr(changed, field),
                        value_factor / time_factor * getattr(baseline, field),
                    )
                for field in (
                    "catalog_scale",
                    "history_scale",
                    "used_scale",
                    "tolerance",
                ):
                    self.assertAlmostEqual(
                        getattr(changed, field),
                        abs(value_factor) / time_factor * getattr(baseline, field),
                    )
                for field in ("level_change_median", "level_change_mad"):
                    self.assertAlmostEqual(
                        getattr(changed, field),
                        abs(value_factor) * getattr(baseline, field),
                    )
                self.assertEqual(
                    changed.history_start,
                    ORIGIN + time_factor * (baseline.history_start - ORIGIN) + shift,
                )
                self.assertEqual(
                    changed.history_end,
                    ORIGIN + time_factor * (baseline.history_end - ORIGIN) + shift,
                )

    def test_kink_factor_zero_monotonic_shrinkage_and_exact_boundary(self):
        count = Parameters({}).values["curve_original_samples"]
        # At one-second fixture spacing, this value error gives e=e_minus=1/2:
        # sum |i-mean(i)|=floor(K²/4), sum (i-mean(i))²=K(K²-1)/12.
        values = Parameters(
            {
                **{
                    f"curve_original_gap_{quantity}_{position}_seconds": 2
                    for quantity in ("temperature", "humidity")
                    for position in ("upper", "lower")
                },
                "curve_original_noise_humidity_upper": (
                    count * (count * count - 1) / (24 * (count * count // 4))
                ),
                "curve_original_kink_history_windows": 2,
            }
        ).values
        samples, step = kink_history_originals(
            values,
            lambda index: max(0, index - count + 1),
        )
        self.assertEqual(step.total_seconds(), 1)
        window = original_window(
            samples, at=samples[-1][0], **channel_arguments(values, "upper", "humidity")
        )
        previous_magnitude = None
        for factor, expected in ((0, 1), (0.25, 0.75), (0.5, 0.5), (1, 0), (2, 0)):
            with self.subTest(factor=factor):
                configured = Parameters(
                    {
                        **values,
                        "curve_original_kink_factor": factor,
                    }
                )
                result = original_kink_tolerance(
                    samples,
                    window,
                    **kink_arguments(configured.values),
                )
                self.assertIsNotNone(result)
                self.assertEqual(result.delta, 1)
                self.assertEqual(result.catalog_scale, 1)
                self.assertEqual(result.tolerance, factor)
                self.assertEqual(result.effective_change, expected)
                if previous_magnitude is not None:
                    self.assertLessEqual(
                        abs(result.effective_change), previous_magnitude
                    )
                previous_magnitude = abs(result.effective_change)

    def test_kink_history_distinguishes_straight_line_from_smooth_quadratic(self):
        values = Parameters({"curve_original_kink_history_windows": 2}).values
        count = values["curve_original_samples"]
        curvature = values["curve_original_noise_humidity_upper"]
        for name, shape, expected in (
            ("line", lambda index: -index, 0),
            ("smooth quadratic", lambda index: curvature * index**2, None),
        ):
            with self.subTest(shape=name):
                samples, step = kink_history_originals(values, shape)
                window = original_window(
                    samples,
                    at=samples[-1][0],
                    **channel_arguments(values, "upper", "humidity"),
                )
                result = original_kink_tolerance(
                    samples, window, **kink_arguments(values)
                )
                self.assertIsNotNone(result)
                self.assertEqual(result.status, "history")
                self.assertEqual(
                    result.history_count, values["curve_original_kink_history_windows"]
                )
                self.assertEqual(result.history_start, samples[0][0])
                self.assertEqual(result.history_end, samples[-2 * count - 1][0])
                if expected == 0:
                    self.assertEqual(result.history_scale, 0)
                    self.assertEqual(result.used_scale, result.catalog_scale)
                else:
                    # OLS of c*i² on equally spaced symmetric K supports has
                    # slope c*(first+last)/dt; neighboring centers differ by K.
                    analytic = 2 * curvature * count / step.total_seconds()
                    self.assertAlmostEqual(result.history_scale, analytic)
                    self.assertAlmostEqual(result.delta, analytic)
                    self.assertGreater(result.history_scale, result.catalog_scale)
                    self.assertAlmostEqual(result.used_scale, analytic)

    def test_kink_history_excludes_active_jump_support_and_future_values(self):
        values = Parameters({"curve_original_kink_history_windows": 2}).values
        samples, step = kink_history_originals(values, lambda index: -index)
        count = values["curve_original_samples"]
        window_arguments = channel_arguments(values, "upper", "humidity")
        window = original_window(samples, at=samples[-1][0], **window_arguments)
        baseline = original_kink_tolerance(samples, window, **kink_arguments(values))
        self.assertIsNotNone(baseline)
        jump = samples[: -2 * count] + tuple(
            (stamp, value + 1000 + 2 * index + 1000 * max(0, index - count + 1))
            for index, (stamp, value) in enumerate(samples[-2 * count :])
        )
        jumped_window = original_window(jump, at=jump[-1][0], **window_arguments)
        jumped = original_kink_tolerance(jump, jumped_window, **kink_arguments(values))
        self.assertIsNotNone(jumped)
        self.assertGreater(jumped.effective_change, baseline.effective_change)
        self.assertAlmostEqual(jumped.delta, 1000 / step.total_seconds())
        for field in (
            "history_count",
            "history_start",
            "history_end",
            "history_scale",
            "status",
            "catalog_scale",
            "used_scale",
            "tolerance",
        ):
            self.assertEqual(getattr(jumped, field), getattr(baseline, field))
        self.assertEqual(jumped.history_scale, 0)
        future = jump + (
            (jump[-1][0] + step, float("nan")),
            (jump[-1][0] + 2 * step, -1e12),
        )
        self.assertEqual(
            original_kink_tolerance(
                future,
                jumped_window,
                **kink_arguments(values),
            ),
            jumped,
        )

    def test_kink_history_gap_and_configured_minimum_preserve_zero_evidence(self):
        values = Parameters({"curve_original_kink_history_windows": 3}).values
        count = values["curve_original_samples"]
        curvature = values["curve_original_noise_humidity_upper"]
        samples, step = kink_history_originals(
            values,
            lambda index: curvature * index**2,
            history_windows=2,
        )
        window = original_window(
            samples, at=samples[-1][0], **channel_arguments(values, "upper", "humidity")
        )
        gap = timedelta(seconds=kink_arguments(values)["max_gap_seconds"]) + step
        old = tuple(
            (stamp - len(samples) * step - gap, value * 100)
            for stamp, value in samples[: -2 * count]
        )
        insufficient = original_kink_tolerance(
            old + samples, window, **kink_arguments(values)
        )
        self.assertIsNotNone(insufficient)
        self.assertEqual(insufficient.status, "insufficient")
        self.assertEqual(insufficient.history_count, 2)
        self.assertEqual(insufficient.history_start, samples[0][0])
        self.assertEqual(insufficient.history_end, samples[-2 * count - 1][0])
        self.assertGreater(insufficient.history_scale, insufficient.catalog_scale)
        self.assertEqual(insufficient.used_scale, insufficient.catalog_scale)
        lower_minimum = Parameters({**values, "curve_original_kink_history_windows": 2})
        sufficient = original_kink_tolerance(
            old + samples,
            window,
            **kink_arguments(lower_minimum.values),
        )
        self.assertEqual(sufficient.status, "history")
        self.assertEqual(sufficient.history_scale, insufficient.history_scale)
        self.assertEqual(sufficient.used_scale, sufficient.history_scale)
        detached = tuple((stamp - gap, value) for stamp, value in samples[: -2 * count])
        disconnected = original_kink_tolerance(
            detached + samples[-2 * count :],
            window,
            **kink_arguments(values),
        )
        self.assertIsNotNone(disconnected)
        self.assertEqual(disconnected.status, "insufficient")
        self.assertEqual(disconnected.history_count, 0)
        for field in ("history_start", "history_end", "history_scale"):
            self.assertIsNone(getattr(disconnected, field))
        self.assertEqual(disconnected.used_scale, disconnected.catalog_scale)
        straight, _ = kink_history_originals(values, lambda index: -index)
        straight_window = original_window(
            straight,
            at=straight[-1][0],
            **channel_arguments(values, "upper", "humidity"),
        )
        zero = original_kink_tolerance(
            straight, straight_window, **kink_arguments(values)
        )
        self.assertEqual(zero.status, "history")
        self.assertEqual(
            zero.history_count, values["curve_original_kink_history_windows"]
        )
        self.assertEqual(zero.history_scale, 0)

    def test_kink_invalid_inputs_remain_unknown_without_raw_mutation(self):
        values = Parameters({"curve_original_kink_history_windows": 2}).values
        samples, step = kink_history_originals(values, lambda index: -index)
        window = original_window(
            samples, at=samples[-1][0], **channel_arguments(values, "upper", "humidity")
        )
        snapshot = replace(window)
        arguments = kink_arguments(values)
        for invalid in (
            {"factor": -1},
            {"factor": True},
            {"factor": float("nan")},
            {"factor": float("inf")},
            {"factor": "1"},
            {"min_history_windows": 0},
            {"min_history_windows": True},
            {"min_history_windows": 1.5},
            {"max_gap_seconds": 0},
            {"max_gap_seconds": float("nan")},
            {"max_gap_seconds": float("inf")},
        ):
            with self.subTest(arguments=invalid):
                source = list(samples)
                self.assertIsNone(
                    original_kink_tolerance(
                        source,
                        window,
                        **{**arguments, **invalid},
                    )
                )
                self.assertEqual(source, list(samples))
                self.assertEqual(window, snapshot)
        for invalid in (
            ((None, samples[0][1]),) + samples[1:],
            (samples[1], samples[0]) + samples[2:],
            (samples[0],) + samples,
            *(
                (((samples[0][0], value),) + samples[1:])
                for value in (True, "40", None, float("nan"), float("inf"))
            ),
        ):
            with self.subTest(samples=repr(invalid[:2])):
                source = list(invalid)
                before = list(source)
                self.assertIsNone(original_kink_tolerance(source, window, **arguments))
                self.assertEqual(source, before)
                self.assertEqual(window, snapshot)
        for invalid_window in (
            replace(window, v=float("nan")),
            replace(window, e=0),
            replace(window, current=(window.previous[-1],) + window.current[1:]),
            replace(
                window,
                current=tuple(
                    (
                        stamp + timedelta(seconds=arguments["max_gap_seconds"]) + step,
                        value,
                    )
                    for stamp, value in window.current
                ),
            ),
            replace(
                window,
                current=window.current[:-1] + ((window.current[-1][0], float("inf")),),
            ),
        ):
            with self.subTest(window=repr(invalid_window)):
                self.assertIsNone(
                    original_kink_tolerance(samples, invalid_window, **arguments)
                )
                self.assertEqual(window, snapshot)

    def test_kink_results_freeze_shared_raw_windows_and_leave_contrasts_unchanged(self):
        values = Parameters({"curve_original_kink_factor": 0.5}).values
        originals, _ = humidity_step_originals(values, 0, 1)
        at = originals["upper_humidity"][-1][0]
        windows = position_windows(originals, at, values)
        before = original_contrasts(windows, expected_positions=("upper", "lower"))
        raw = windows["upper"].humidity
        raw_snapshot = replace(raw)
        source = list(originals["upper_humidity"])
        result = original_kink_tolerance(source, raw, **kink_arguments(values))
        disabled = Parameters({**values, "curve_original_kink_factor": 0})
        control = original_kink_tolerance(
            source, raw, **kink_arguments(disabled.values)
        )
        self.assertIsNotNone(result)
        self.assertIsNotNone(control)
        self.assertIs(result.window, raw)
        self.assertIs(control.window, raw)
        self.assertGreater(abs(control.effective_change), abs(result.effective_change))
        control_snapshot = replace(control)
        with self.assertRaises(FrozenInstanceError):
            result.effective_change = 0
        with self.assertRaises(FrozenInstanceError):
            result.window.v = 0
        source[-1] = (source[-1][0], source[-1][1] + 1000)
        self.assertEqual(control, control_snapshot)
        self.assertEqual(raw, raw_snapshot)
        self.assertEqual(
            original_contrasts(
                windows,
                expected_positions=("upper", "lower"),
            ),
            before,
        )

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
                    self.assertEqual(
                        result.scale,
                        channel_arguments(
                            values,
                            position,
                            quantity,
                        )["scale"],
                    )
                    self.assertAlmostEqual(result.v, -1 / step.total_seconds())
                    self.assertAlmostEqual(result.b, -1 / step.total_seconds())
                    self.assertAlmostEqual(
                        result.mean_minus, trace(0)[column] - (count - 1) / 2
                    )
                    self.assertAlmostEqual(
                        result.mean, trace(0)[column] - (3 * count - 1) / 2
                    )
                    # Closed sums on an equally spaced K-point line:
                    # sum |i - mean(i)| = floor(K²/4),
                    # sum (i - mean(i))² = K(K²-1)/12.
                    expected_error = (
                        result.scale
                        * 12
                        * (count * count // 4)
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
        self.assertGreater(
            contrast.opening_support, values["curve_original_open_contrast"]
        )
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
                self.assertGreater(
                    getattr(result, field),
                    values["curve_original_new_infusion_contrast"],
                )
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
                    mixed = {
                        **windows,
                        unchanged: replace(
                            windows[unchanged],
                            humidity=linear_windows[unchanged].humidity,
                        ),
                    }
                    joint = original_contrasts(
                        mixed,
                        expected_positions=("upper", "lower"),
                    )
                    self.assertIsNotNone(joint)
                    # Current directions are identical; either missing kink
                    # prevents joint onset without discarding that position.
                    self.assertAlmostEqual(
                        getattr(joint, field), getattr(result, field)
                    )
                    self.assertAlmostEqual(getattr(joint, onset), 0)
                    self.assertEqual(joint.positions, tuple(mixed.items()))
                    for position in ("upper", "lower"):
                        selected = original_contrasts(
                            mixed,
                            expected_positions=(position,),
                        )
                        self.assertIsNotNone(selected)
                        self.assertEqual(
                            selected.positions, ((position, mixed[position]),)
                        )
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
                        )
                        if role.endswith("_temperature")
                        else points
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
                            self.assertGreater(
                                result.opening_support,
                                values["curve_original_open_contrast"],
                            )
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
                        amplitude = values[
                            f"curve_original_noise_humidity_{position}"
                        ] * min(0.5, significance / 2)
                        originals[role] = tuple(
                            (
                                stamp,
                                value + direction * amplitude
                                if offset == count + index
                                else value,
                            )
                            for offset, (stamp, value) in enumerate(originals[role])
                        )
                    at = originals["upper_humidity"][-1][0]
                    windows = position_windows(originals, at, values)
                    result = original_contrasts(
                        windows,
                        expected_positions=("upper", "lower"),
                    )
                    self.assertIsNotNone(result)
                    for position, pair in result.positions:
                        self.assertEqual(
                            pair.humidity.scale,
                            channel_arguments(
                                values,
                                position,
                                "humidity",
                            )["scale"],
                        )
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
                        (
                            stamp + shift,
                            factor * value + offset
                            if role.endswith("_humidity")
                            else value,
                        )
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
                self.assertAlmostEqual(
                    result.humidity_rise_change, baseline.humidity_rise_change
                )
                self.assertAlmostEqual(result.rise, baseline.rise)
                self.assertAlmostEqual(
                    result.humidity_rise_onset, baseline.humidity_rise_onset
                )
                self.assertAlmostEqual(
                    result.humidity_fall_onset, baseline.humidity_fall_onset
                )

    def test_rh_change_overflow_cannot_be_hidden_by_another_position(self):
        values = Parameters({}).values
        originals, _ = linear_originals(values)
        at = originals["upper_humidity"][-1][0]
        windows = position_windows(originals, at, values)
        self.assertIsNotNone(
            original_contrasts(
                windows,
                expected_positions=("upper", "lower"),
            )
        )
        # Robustness criterion: finite, positive bounds and a finite slope
        # change can still yield an overflowing normalized change. The other
        # position's zero change must not hide that unknown in the minimum.
        extreme = replace(
            windows["upper"].humidity,
            v=0,
            b=-1e308,
            e=1e-308,
            e_minus=1e-308,
        )
        self.assertTrue(
            all(isfinite(getattr(extreme, name)) for name in ("v", "b", "e", "e_minus"))
        )
        windows["upper"] = replace(windows["upper"], humidity=extreme)
        self.assertIsNone(
            original_contrasts(
                windows,
                expected_positions=("upper", "lower"),
            )
        )

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
                    self.assertEqual(
                        window.previous, originals[role][-2 * count : -count], role
                    )
                    self.assertEqual(window.current, originals[role][-count:], role)
                    self.assertEqual(window.newest_at, originals[role][-1][0], role)
                contrast = original_contrasts(
                    windows,
                    expected_positions=("upper", "lower"),
                )
                self.assertIsNotNone(contrast)
                self.assertEqual(contrast.positions, tuple(windows.items()))
                self.assertIsNone(contrast.humidity_recovery)
                self.assertIsNone(contrast.closing)
                for field, oracle in zip(CONTRAST_FIELDS, expected, strict=True):
                    # Fixed arithmetic tolerance for all supplied rational
                    # values, independent of event thresholds and outcomes.
                    self.assertAlmostEqual(
                        getattr(contrast, field), oracle, delta=1e-8, msg=field
                    )

    def test_missing_positions_and_invalid_original_support_stay_unknown(self):
        values = Parameters({}).values
        count = values["curve_original_samples"]
        originals, _ = linear_originals(values)
        points = originals["upper_temperature"]
        at = points[-1][0]
        windows = position_windows(originals, at, values)
        self.assertIsNone(
            original_contrasts(
                {"upper": windows["upper"]},
                expected_positions=("upper", "lower"),
            )
        )
        self.assertIsNone(
            original_contrasts(
                windows,
                expected_positions=("upper", "lower"),
                humidity_minima={"upper": windows["upper"].humidity.mean},
            )
        )
        arguments = channel_arguments(values, "upper", "temperature")
        unsupported_gap = (
            timedelta(seconds=arguments["max_gap_seconds"]) + timedelta.resolution
        )
        gap_points = points[:count] + tuple(
            (stamp + unsupported_gap, value) for stamp, value in points[count:]
        )
        variants = (
            ("insufficient originals", points[1:], at),
            (
                "duplicate boundary time",
                points[:count]
                + ((points[count - 1][0], points[count][1]),)
                + points[count + 1 :],
                at,
            ),
            (
                "reversed boundary time",
                points[:count]
                + ((points[count - 2][0], points[count][1]),)
                + points[count + 1 :],
                at,
            ),
            ("gap between windows", gap_points, gap_points[-1][0]),
            ("stale newest original", points, at + unsupported_gap),
        )
        for reason, samples, evaluated_at in variants:
            with self.subTest(reason=reason):
                self.assertIsNone(
                    original_window(
                        samples,
                        at=evaluated_at,
                        **arguments,
                    )
                )

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
        self.assertIsNone(
            original_water_pair(
                humidity,
                temperatures,
                at=after[0] - timedelta.resolution,
                **arguments,
            )
        )
        self.assertIsNone(
            original_water_pair(
                humidity,
                (before,),
                at=after[0],
                **arguments,
            )
        )
        with patch.object(
            curves,
            "absolute_humidity",
            wraps=absolute_humidity,
        ) as density:
            result = original_water_pair(
                humidity,
                temperatures,
                at=after[0],
                **arguments,
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
        self.assertEqual(
            density.call_args_list, [call(*lower_arguments), call(*upper_arguments)]
        )
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
            (ORIGIN, humidity),
            (temperature_sample,),
            at=ORIGIN,
            **arguments,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.temperature_before, temperature_sample)
        self.assertEqual(result.temperature_after, temperature_sample)
        self.assertEqual(result.available_at, ORIGIN)
        self.assertEqual(result.temperature, temperature)
        self.assertIsNone(
            original_water_pair(
                (ORIGIN, arguments["humidity_scale"] / 2),
                (temperature_sample,),
                at=ORIGIN,
                **arguments,
            )
        )

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
                    humidity_sample,
                    temperatures,
                    at=evaluated_at,
                    **arguments,
                )
                self.assertEqual(support.status, "pending")
                self.assertIsNone(support.pair)
                self.assertIsNone(
                    original_water_pair(
                        humidity_sample,
                        temperatures,
                        at=evaluated_at,
                        **arguments,
                    )
                )
        ready = original_water_pair_support(
            humidity_sample,
            temperatures,
            at=after[0],
            **arguments,
        )
        self.assertEqual(ready.status, "ready")
        self.assertEqual(
            ready.pair,
            original_water_pair(
                humidity_sample,
                temperatures,
                at=after[0],
                **arguments,
            ),
        )
        self.assertEqual(ready.pair.temperature_before, before)
        self.assertEqual(ready.pair.temperature_after, after)
        too_far = (
            ORIGIN
            + timedelta(seconds=arguments["max_gap_seconds"])
            + timedelta.resolution,
            temperature,
        )
        invalid_neighbors = (
            ("missing left", (after,)),
            ("gap at right", (before, too_far)),
            ("duplicate", (before, before, after)),
            (
                "nonfinite immediate right",
                (
                    before,
                    (after[0], float("nan")),
                    (after[0] + step, temperature),
                ),
            ),
        )
        for reason, samples in invalid_neighbors:
            with self.subTest(reason=reason):
                support = original_water_pair_support(
                    humidity_sample,
                    samples,
                    at=too_far[0],
                    **arguments,
                )
                self.assertEqual(support.status, "invalid")
                self.assertIsNone(support.pair)
                self.assertIsNone(
                    original_water_pair(
                        humidity_sample,
                        samples,
                        at=too_far[0],
                        **arguments,
                    )
                )
        left_gap = original_water_pair_support(
            (too_far[0], humidity),
            (before,),
            at=too_far[0],
            **arguments,
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
            (8 + jump, 10 + jump)
            if index in (count // 2, (count - 1) // 2)
            else (8, 10)
            for index in range(count)
        )
        pairs, _ = water_envelope_originals(
            values,
            ((8, 10),) * count + oscillation,
        )
        support = original_water_window(
            pairs,
            count=count,
            at=pairs[-1].available_at,
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
            replace(
                pair,
                lower_density=6 if index % 2 else 8,
                upper_density=8 if index % 2 else 10,
            )
            for index, pair in enumerate(pairs)
        )
        constant = original_water_window(
            touching,
            count=count,
            at=touching[-1].available_at,
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
        envelopes = (
            tuple(
                (10 + (count - index) * fall - 1, 10 + (count - index) * fall + 1)
                for index in range(count)
            )
            + ((9, 11),) * count
        )
        pairs, _ = water_envelope_originals(values, envelopes)
        water = original_water_window(
            pairs,
            count=count,
            at=pairs[-1].available_at,
            max_gap_seconds=values["curve_original_gap_humidity_upper_seconds"],
        ).features
        self.assertIsNotNone(rh)
        self.assertIsNotNone(water)
        for current_step, falling, not_braking in (
            (-1, True, True),
            (-1 + values["curve_original_noise_temperature_upper"] / 4, True, True),
            (0, False, False),
            (-0.25, True, False),
        ):
            thermal, _ = humidity_step_originals(values, -1, current_step)
            temperature = original_window(
                tuple(
                    (stamp, trace(0)[0] + value - trace(0)[1])
                    for stamp, value in thermal["upper_humidity"]
                ),
                at=at,
                **channel_arguments(values, "upper", "temperature"),
            )
            self.assertIsNotNone(temperature)
            for quantity, humidity in (("RH", rh), ("AH", water)):
                with self.subTest(quantity=quantity, temperature_step=current_step):
                    result = original_flattening(
                        humidity,
                        temperature,
                        significance=significance,
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
                (
                    tail_at + index * step,
                    tail_value - index if role.endswith("_temperature") else tail_value,
                )
                for index in range(1, count + 1)
            )
        fall = 8 * significance
        envelopes = tuple(
            (10 + (count - index) * fall - 1, 10 + (count - index) * fall + 1)
            for index in range(count)
        ) + ((9, 11),) * (2 * count)
        pairs, _ = water_envelope_originals(values, envelopes)
        first_windows = {}
        for prefix in (2 * count, 3 * count):
            selected = {role: points[:prefix] for role, points in originals.items()}
            at = selected["upper_temperature"][-1][0]
            windows = position_windows(selected, at, values)["upper"]
            water = original_water_window(
                pairs[:prefix],
                count=count,
                at=pairs[prefix - 1].available_at,
                max_gap_seconds=values["curve_original_gap_humidity_upper_seconds"],
            ).features
            for quantity, humidity in (("RH", windows.humidity), ("AH", water)):
                with self.subTest(quantity=quantity, prefix=prefix):
                    result = original_flattening(
                        humidity,
                        windows.temperature,
                        significance=significance,
                    )
                    self.assertIsNotNone(result)
                    self.assertTrue(result.maintained)
                    self.assertEqual(result.initial, prefix == 2 * count)
                    if prefix == 2 * count:
                        first_windows[quantity] = humidity
                    else:
                        # The old fall is absent from both retained windows;
                        # the previous window is now the original plateau.
                        self.assertEqual(
                            humidity.previous, first_windows[quantity].current
                        )
                        self.assertFalse(result.previous_fall.result)
                        self.assertFalse(result.braking.result)

    def test_flattening_accepts_exact_water_envelopes_and_rejects_unknown_inputs(self):
        values = Parameters({}).values
        count = values["curve_original_samples"]
        significance = values["curve_original_significance"]
        originals, _ = humidity_step_originals(values, -1, 0)
        at = originals["upper_temperature"][-1][0]
        windows = position_windows(originals, at, values)["upper"]
        pairs, _ = water_envelope_originals(
            values,
            tuple((10 + count - index, 10 + count - index) for index in range(count))
            + ((10, 10),) * count,
        )
        water = original_water_window(
            pairs,
            count=count,
            at=pairs[-1].available_at,
            max_gap_seconds=values["curve_original_gap_humidity_upper_seconds"],
        ).features
        self.assertIsNotNone(water)
        self.assertEqual((water.e, water.e_minus), (0, 0))
        exact = original_flattening(
            water,
            windows.temperature,
            significance=significance,
        )
        self.assertIsNotNone(exact)
        self.assertTrue(exact.initial)
        self.assertTrue(exact.maintained)
        for name in ("previous_fall", "current_flat", "braking"):
            self.assertEqual(getattr(exact, name).threshold, 0)
        invalid_humidity = (
            None,
            replace(windows.humidity, e=0),
            replace(windows.humidity, v=float("nan")),
            replace(water, e=-1),
            replace(water, b=float("inf")),
            replace(water, v=1e308, b=-1e308),
            replace(water, e=1e308, e_minus=1e308),
            replace(water, previous=()),
            replace(water, current=()),
            replace(water, current=(None,) + water.current[1:]),
            replace(
                water,
                current=(
                    replace(
                        water.current[0],
                        upper_density=float("inf"),
                    ),
                )
                + water.current[1:],
            ),
        )
        for index, humidity in enumerate(invalid_humidity):
            with self.subTest(invalid_humidity=index):
                self.assertIsNone(
                    original_flattening(
                        humidity,
                        windows.temperature,
                        significance=significance,
                    )
                )
        for temperature in (
            None,
            replace(windows.temperature, e=0),
            replace(windows.temperature, v=float("nan")),
        ):
            with self.subTest(invalid_temperature=temperature):
                self.assertIsNone(
                    original_flattening(
                        water,
                        temperature,
                        significance=significance,
                    )
                )
        for invalid in (0, -1, True, float("nan"), float("inf")):
            with self.subTest(significance=invalid):
                self.assertIsNone(
                    original_flattening(
                        water,
                        windows.temperature,
                        significance=invalid,
                    )
                )

    def test_water_level_deviation_matches_weighted_rational_golden(self):
        values = Parameters({}).values
        pairs, _ = water_envelope_originals(
            values,
            ((8, 10),) * 3 + ((9, 11), (10, 14), (12, 20)),
        )
        result = original_water_window(
            pairs,
            count=3,
            at=pairs[-1].available_at,
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
        self.assertAlmostEqual(
            replace(
                result,
                current=tuple(reversed(result.current)),
            ).normalized_level_deviation(),
            sqrt(8 / 9),
        )
        self.assertAlmostEqual(
            replace(
                result,
                previous=tuple(reversed(result.previous)),
            ).normalized_level_deviation(),
            sqrt(8 / 9),
        )

    def test_water_level_deviation_counts_every_current_pair_and_equal_level(self):
        values = Parameters({}).values
        count = values["curve_original_samples"]
        pairs, _ = water_envelope_originals(values, ((8, 10),) * (2 * count))
        result = original_water_window(
            pairs,
            count=count,
            at=pairs[-1].available_at,
            max_gap_seconds=values["curve_original_gap_humidity_upper_seconds"],
        ).features
        self.assertIsNotNone(result)
        self.assertEqual(result.normalized_level_deviation(), 0)
        for index in range(count):
            with self.subTest(changed_current_index=index):
                current = tuple(
                    replace(pair, lower_density=8 + count, upper_density=10 + count)
                    if offset == index
                    else pair
                    for offset, pair in enumerate(result.current)
                )
                # One displacement K among K equal-radius originals has
                # center displacement 1 and D²=K-1, wherever it occurs.
                self.assertAlmostEqual(
                    replace(
                        result,
                        current=current,
                    ).normalized_level_deviation(),
                    sqrt(count - 1),
                )
        center = count + 1
        equal_level = tuple(
            replace(pair, lower_density=center - radius, upper_density=center + radius)
            for radius, pair in enumerate(result.current, start=1)
        )
        self.assertEqual(
            replace(
                result,
                current=equal_level,
            ).normalized_level_deviation(),
            0,
        )

    def test_water_level_deviation_distinguishes_alternating_k7_zero_slope_sequence(
        self,
    ):
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
            values,
            ((center - 1, center + 1),) * count + current,
        )
        result = original_water_window(
            pairs,
            count=count,
            at=pairs[-1].available_at,
            max_gap_seconds=values["curve_original_gap_humidity_upper_seconds"],
        ).features
        self.assertIsNotNone(result)
        # Reflection symmetry cancels OLS slope. Four +A and three -A
        # observations still have D²=48*A²/49 around their free mean A/7.
        self.assertAlmostEqual(result.v, 0)
        self.assertLessEqual(abs(result.v), significance * result.e)
        self.assertAlmostEqual(
            result.normalized_level_deviation(), amplitude * sqrt(48) / 7
        )
        self.assertGreater(result.normalized_level_deviation(), significance)

    def test_water_level_deviation_zero_radii_extremes_and_unknown_inputs(self):
        values = Parameters({}).values
        pairs, _ = water_envelope_originals(values, ((8, 10),) * 6)
        result = original_water_window(
            pairs,
            count=3,
            at=pairs[-1].available_at,
            max_gap_seconds=values["curve_original_gap_humidity_upper_seconds"],
        ).features
        self.assertIsNotNone(result)

        def score(envelopes):
            return replace(
                result,
                current=tuple(
                    replace(pair, lower_density=lower, upper_density=upper)
                    for pair, (lower, upper) in zip(
                        result.current, envelopes, strict=True
                    )
                ),
            ).normalized_level_deviation()

        self.assertEqual(score(((10, 10),) * 3), 0)
        # Two exact originals fix c=10; the third contributes (12-10)/2=1.
        self.assertAlmostEqual(score(((10, 10), (10, 10), (10, 14))), 1 / sqrt(3))
        self.assertIsNone(score(((10, 10), (11, 11), (10, 14))))
        # Finite endpoints may have overflowing sums or reciprocal squares.
        # Their midpoints/radii remain usable without an artificial floor.
        self.assertAlmostEqual(
            score(
                (
                    (7e307, 9e307),
                    (8e307, 1e308),
                    (9e307, 1.1e308),
                )
            ),
            sqrt(2 / 3),
        )
        self.assertAlmostEqual(
            score(
                (
                    (0, 2e-150),
                    (1e-150, 3e-150),
                    (2e-150, 4e-150),
                )
            ),
            sqrt(2 / 3),
        )
        self.assertAlmostEqual(
            score(
                (
                    (0, 2e-300),
                    (0, 2e300),
                    (0, 2e300),
                )
            ),
            sqrt(2 / 3),
        )
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
            (
                2954,
                3940,
                949.789675,
                (711, 762, 716, 761),
                {
                    "upper": (948.723650, 948.709000, 949.759458, (949.773302,)),
                    "lower": (948.748448, 948.738673, 949.789675, ()),
                },
            ),
            (
                2958,
                3945,
                950.838898,
                (712, 763, 717, 762),
                {
                    "upper": (949.773302, 949.759458, 950.811703, (950.825598,)),
                    "lower": (949.798450, 949.789675, 950.838898, ()),
                },
            ),
        )
        case = json.loads(
            (ROOT / "fixtures/replay/R03.json").read_text(
                encoding="utf-8",
            )
        )
        evidence = {
            "recording": "R03",
            "samples_sha256": case["samples_sha256"],
            "definition": "D=min_c sqrt(mean(((m_i-c)/a_i)^2))",
            "count": count,
            "significance": significance,
            "support_floor": floor,
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
            prefix = {
                "source_row": source_row,
                "archive_record_id": record_id,
                "at": at,
                "positions": {},
            }
            for position, identities in expected.items():
                with self.subTest(source_row=source_row, position=position):
                    temperature_role, humidity_role = (
                        f"{position}_temperature",
                        f"{position}_humidity",
                    )
                    temperatures, humidities = (
                        originals[temperature_role],
                        originals[humidity_role],
                    )
                    temperature_arguments = channel_arguments(
                        values,
                        position,
                        "temperature",
                    )
                    humidity_arguments = channel_arguments(values, position, "humidity")
                    arguments = {
                        "temperature_scale": temperature_arguments["scale"],
                        "humidity_scale": humidity_arguments["scale"],
                        "max_gap_seconds": temperature_arguments["max_gap_seconds"],
                    }
                    pairs, pending = [], []
                    for point in humidities[-(2 * count + 1) :]:
                        support = original_water_pair_support(
                            point,
                            temperatures,
                            at=at,
                            **arguments,
                        )
                        self.assertIn(support.status, ("ready", "pending"))
                        if support.status == "pending":
                            pending.append(point[0])
                        else:
                            self.assertFalse(
                                pending, "an inner pending original was skipped"
                            )
                            pairs.append(support.pair)
                    rh_gap = humidity_arguments["max_gap_seconds"]
                    support = original_water_window(
                        tuple(pairs),
                        count=count,
                        at=at,
                        max_gap_seconds=rh_gap,
                    )
                    self.assertEqual(support.status, "ready")
                    water = support.features
                    self.assertEqual(water.previous, tuple(pairs[-2 * count : -count]))
                    self.assertEqual(water.current, tuple(pairs[-count:]))
                    self.assertEqual(len(water.previous + water.current), 2 * count)
                    (expected_rh, expected_left, expected_right, expected_pending) = (
                        identities
                    )
                    last = water.current[-1]
                    self.assertEqual(
                        tuple(
                            (stamp - ORIGIN).total_seconds()
                            for stamp in (
                                last.humidity_sample[0],
                                last.temperature_before[0],
                                last.temperature_after[0],
                            )
                        ),
                        (expected_rh, expected_left, expected_right),
                    )
                    self.assertEqual(
                        tuple((stamp - ORIGIN).total_seconds() for stamp in pending),
                        expected_pending,
                    )
                    self.assertEqual(
                        last.humidity_sample, humidities[-1 - len(pending)]
                    )
                    self.assertTrue(
                        all(
                            pair.temperature_before[0] >= floor
                            for pair in water.previous + water.current
                        )
                    )
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
                            -water.b,
                            ">",
                            significance * water.e_minus,
                        ),
                        "current_water_flat": (
                            abs(water.v),
                            "<=",
                            significance * water.e,
                        ),
                        "water_fall_braking": (
                            water.v - water.b,
                            ">",
                            significance * (water.e + water.e_minus),
                        ),
                        "temperature_still_falling": (
                            -temperature.v,
                            ">",
                            significance * temperature.e,
                        ),
                    }
                    gates = {
                        name: {
                            "value": value,
                            "operator": operator,
                            "threshold": threshold,
                            "result": (
                                value > threshold
                                if operator == ">"
                                else value <= threshold
                            ),
                        }
                        for name, (value, operator, threshold) in comparisons.items()
                    }
                    newest = (temperature.newest_at, water.newest_at)
                    fresh = (
                        all(
                            now > before
                            for now, before in zip(
                                newest,
                                previous_newest[position],
                                strict=True,
                            )
                        )
                        if position in previous_newest
                        else None
                    )
                    if position in previous_newest:
                        self.assertTrue(fresh)
                    previous_newest[position] = newest
                    prefix["positions"][position] = {
                        "sources": {
                            "temperature": f"sensor.fixture_{temperature_role}",
                            "humidity": f"sensor.fixture_{humidity_role}",
                        },
                        "scales_and_temperature_gap": arguments,
                        "humidity_gap_seconds": rh_gap,
                        "temperature_newest_at": temperature.newest_at,
                        "water_newest_at": water.newest_at,
                        "pending_rh_at": pending,
                        "later_temperature_and_paired_rh": fresh,
                        "v": water.v,
                        "b": water.b,
                        "e": water.e,
                        "e_minus": water.e_minus,
                        "temperature_v": temperature.v,
                        "temperature_e": temperature.e,
                        "form_gates": gates,
                        "level_diagnostics": {
                            "D": deviation,
                            "water_level_deviation": {
                                "value": deviation,
                                "operator": "<=",
                                "threshold": significance,
                                "result": deviation <= significance,
                            },
                            "all_k_constant_compatible": {
                                "value": lower,
                                "operator": "<=",
                                "threshold": upper,
                                "result": lower <= upper,
                            },
                        },
                        "pairs": [
                            {
                                "rh": pair.humidity_sample,
                                "temperature_before": pair.temperature_before,
                                "temperature_after": pair.temperature_after,
                                "available_at": pair.available_at,
                                "middle": (
                                    pair.lower_density
                                    + (pair.upper_density - pair.lower_density) / 2
                                ),
                                "radius": (pair.upper_density - pair.lower_density) / 2,
                                "lower_density": pair.lower_density,
                                "upper_density": pair.upper_density,
                            }
                            for pair in water.previous + water.current
                        ],
                    }
            evidence["prefixes"].append(prefix)
        serialized = (
            json.dumps(
                evidence,
                default=lambda value: value.isoformat(),
                allow_nan=False,
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )
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
        self.assertEqual(support.features.previous, pairs[1 : count + 1])
        self.assertEqual(support.features.current, pairs[count + 1 :])
        self.assertEqual(original_water_window((), **arguments).status, "insufficient")
        self.assertEqual(
            original_water_window(
                pairs[2:],
                **arguments,
            ).status,
            "insufficient",
        )
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
        self.assertEqual(
            original_water_window(
                pairs,
                **{
                    **arguments,
                    "at": arguments["at"]
                    + timedelta(
                        seconds=2 * arguments["max_gap_seconds"],
                    ),
                },
            ).status,
            "ready",
        )
        shift = timedelta(seconds=arguments["max_gap_seconds"]) + timedelta.resolution
        shifted = tuple(
            replace(
                pair,
                humidity_sample=(
                    pair.humidity_sample[0] + shift,
                    pair.humidity_sample[1],
                ),
                temperature_before=(
                    pair.temperature_before[0] + shift,
                    pair.temperature_before[1],
                ),
                temperature_after=(
                    pair.temperature_after[0] + shift,
                    pair.temperature_after[1],
                ),
                available_at=pair.available_at + shift,
            )
            for pair in pairs[count + 1 :]
        )
        variants = (
            ("unknown final pair", pairs[:-1] + (None,)),
            ("unknown interior pair", pairs[:count] + (None,) + pairs[count + 1 :]),
            ("duplicate original", pairs[:-1] + (pairs[-2],)),
            ("reversed original", pairs[:-1] + (pairs[-3],)),
            ("gap between windows", pairs[: count + 1] + shifted),
            (
                "nonfinite bound",
                pairs[:-1] + (replace(pairs[-1], upper_density=float("inf")),),
            ),
            ("inverted envelope", pairs[:-1] + (replace(pairs[-1], lower_density=11),)),
        )
        for reason, samples in variants:
            with self.subTest(reason=reason):
                result = original_water_window(samples, **arguments)
                self.assertEqual(result.status, "invalid")
                self.assertIsNone(result.features)
        invalid_arguments = (
            {"count": True},
            {"count": 1},
            {"count": float("nan")},
            {"max_gap_seconds": 0},
            {"max_gap_seconds": float("inf")},
            {"at": None},
        )
        for invalid in invalid_arguments:
            with self.subTest(arguments=invalid):
                self.assertEqual(
                    original_water_window(
                        pairs,
                        **{**arguments, **invalid},
                    ).status,
                    "invalid",
                )

    def test_single_position_preserves_algebra_without_empirical_transfer(self):
        values = Parameters({}).values
        originals, at = original_prefix("R03", PRO_PREFIXES[0][1])
        upper = position_windows(originals, at, values)["upper"]
        significance = values["curve_original_significance"]
        minimum = upper.humidity.mean - 2 * upper.humidity.scale * significance
        result = original_contrasts(
            {"upper": upper},
            expected_positions=("upper",),
            humidity_minima={"upper": minimum},
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.positions, (("upper", upper),))
        self.assertEqual(result.warming_min, result.warming_max)
        self.assertEqual(result.fall, -result.rise)
        self.assertEqual(result.background_slope, abs(result.rise))
        self.assertAlmostEqual(result.humidity_recovery, significance)
        self.assertAlmostEqual(
            result.opening, min(-result.warming_min, result.fall, -result.braking)
        )
        # For one position W_min = W_max = W, so min(B, W) <= W and
        # max(min(B,U), min(B,W), W) simplifies to max(min(B,U), W).
        self.assertAlmostEqual(
            result.closing,
            max(min(result.braking, significance), result.warming_min),
        )


class OriginalFollowupArithmeticTests(unittest.TestCase):
    """Rational arithmetic criteria; these numbers are not sensor scenarios."""

    @staticmethod
    def _window(
        values, *, start=0, time_factor=1, value_factor=1, offset=0, shift=timedelta(0)
    ):
        points = tuple(
            (
                ORIGIN + timedelta(seconds=2 * time_factor * (start + index)) + shift,
                value_factor * value + offset,
            )
            for index, value in enumerate(values)
        )
        return original_window(
            points,
            count=3,
            scale=abs(value_factor),
            max_gap_seconds=2 * time_factor,
            at=points[-1][0],
        )

    def test_followup_counts_against_fixed_reference_without_accumulating_overlap(self):
        values = (12, 10, 8, 6, 6, 6) + (6,) * 6
        reference = self._window(values[:6])
        self.assertIsNotNone(reference)
        for length, previous, current in (
            (6, 0, 0),
            (7, 0, 1),
            (8, 0, 2),
            (9, 0, 3),
            (10, 1, 3),
            (11, 2, 3),
            (12, 3, 3),
        ):
            with self.subTest(prefix=length):
                window = self._window(values[:length])
                form = original_followup(window, reference)
                self.assertIsNotNone(form)
                self.assertIs(form.window, window)
                self.assertIs(form.reference, reference)
                self.assertEqual(
                    (form.fresh_previous, form.fresh_current), (previous, current)
                )
                # Fixed previous mean is 10; every current mean is 6.
                # Their retained level loss is (10-6)/(2*1)=2, even
                # when both moving sections have become the same plateau.
                self.assertEqual(form.reference_level_loss, 2)
                self.assertEqual(original_followup(window, reference), form)
                if length in (6, 9, 12):
                    # OLS over three originals at 0,2,4 has e=1/2.
                    # The fixed previous reference falls at b=-1.
                    self.assertEqual(form.current_direction, 0)
                    self.assertEqual(form.current_change, 1)
                    self.assertEqual(form.previous_direction, -2 if length == 6 else 0)
                    self.assertEqual(form.previous_change, 0 if length == 6 else 1)
                    # The reference's current slope is zero, unlike its
                    # previous slope -1; both denominators here equal 1.
                    self.assertEqual(form.current_change_from_reference_current, 0)
                    self.assertEqual(
                        form.previous_change_from_reference_current,
                        -1 if length == 6 else 0,
                    )
                    self.assertEqual(form.level_change, -2 if length == 6 else 0)
                    self.assertEqual(form.residual, 0)

    def test_new_original_fall_matches_rational_slope_without_full_window_wait(self):
        for time_factor, value_factor, offset in ((1, 1, 0), (3, 4, 100)):
            with self.subTest(time_factor=time_factor, value_factor=value_factor):
                reference = self._window(
                    (12, 10, 8, 6, 6, 6),
                    time_factor=time_factor,
                    value_factor=value_factor,
                    offset=offset,
                )
                # Two new values at 12 and 14 give slope -2, error 1,
                # mean 3 and level contrast (10 - 3) / 2 = 3.5.
                points = tuple(
                    (
                        ORIGIN + timedelta(seconds=t * time_factor),
                        v * value_factor + offset,
                    )
                    for t, v in ((12, 5), (14, 1))
                )
                form = original_decline_evidence(reference.current + points, reference)
                self.assertIsNotNone(form)
                self.assertEqual(form.points, points)
                self.assertEqual(form.direction, -2)
                self.assertEqual(form.level_loss, 3.5)
                self.assertAlmostEqual(form.slope, -2 * value_factor / time_factor)
                self.assertLess(len(form.points), len(reference.current))
                self.assertIsNone(original_decline_evidence(points[:1], reference))
                self.assertIsNone(original_decline_evidence(points[::-1], reference))
                self.assertIsNone(
                    original_decline_evidence(points + points[-1:], reference)
                )

    def test_both_humidity_forms_accept_resolved_slowing_without_plateau(self):
        values = Parameters({}).values
        temperature = self._window((30, 26, 22, 18, 14, 10))
        rh = self._window((30, 22, 14, 12, 10, 8))
        pairs, _ = water_envelope_originals(
            values,
            tuple((v - 1, v + 1) for v in (30, 22, 14, 12, 10, 8)),
        )
        water = original_water_window(
            pairs,
            count=3,
            at=pairs[-1].available_at,
            max_gap_seconds=values["curve_original_gap_humidity_upper_seconds"],
        ).features
        for humidity in (rh, water):
            with self.subTest(kind=type(humidity).__name__):
                form = original_flattening(humidity, temperature, significance=1)
                self.assertIsNotNone(form)
                self.assertFalse(form.current_flat.result)
                self.assertTrue(form.current_nonrising.result)
                self.assertTrue(form.abating)
                # The rolling previous window has reached the slower fall.
                # Retaining the original fall still proves its reduction.
                later = replace(humidity, b=humidity.v)
                self.assertFalse(
                    original_flattening(
                        later,
                        temperature,
                        significance=1,
                    ).abating
                )
                self.assertTrue(
                    original_flattening(
                        later,
                        temperature,
                        significance=1,
                        reference_humidity=humidity,
                    ).abating
                )
                accelerating = replace(
                    humidity, v=humidity.b - humidity.e - humidity.e_minus
                )
                self.assertFalse(
                    original_flattening(
                        accelerating,
                        temperature,
                        significance=1,
                    ).abating
                )
                rising = replace(humidity, v=2 * humidity.e)
                self.assertFalse(
                    original_flattening(
                        rising,
                        temperature,
                        significance=1,
                    ).abating
                )

    def test_level_background_uses_causal_mean_changes_and_keeps_value_units(self):
        for factor, offset in ((1, 0), (4, 100)):
            with self.subTest(factor=factor, offset=offset):
                samples = tuple(
                    (ORIGIN + timedelta(seconds=2 * i), offset + factor * i**2)
                    for i in range(15)
                )
                reference = self._window(
                    tuple(i**2 for i in range(15)), value_factor=factor, offset=offset
                )
                background = original_kink_tolerance(
                    samples,
                    reference,
                    factor=1,
                    min_history_windows=2,
                    max_gap_seconds=2,
                )
                # Four historical supports have mean changes 15, 21, 27, 33.
                # Their median is 24 and median absolute deviation is 6.
                self.assertEqual(background.history_count, 4)
                self.assertAlmostEqual(background.level_change_median, 24 * factor)
                self.assertAlmostEqual(background.level_change_mad, 6 * factor)
                points = tuple(
                    (ORIGIN + timedelta(seconds=t), offset) for t in (30, 32)
                )
                proof = original_decline_evidence(
                    points, reference, background=background
                )
                self.assertEqual(proof.direction, 0)
                self.assertAlmostEqual(proof.level_center, 24 * factor)
                # Independent normal MAD conversion; acceptance precision is
                # a numerical check, not an operational sensor parameter.
                self.assertAlmostEqual(proof.level_scale / factor, 8.895613311033612)
                self.assertAlmostEqual(
                    proof.level_loss, (302 / 3 - 24) / 8.895613311033612
                )
                self.assertIsNone(
                    original_decline_evidence(
                        points,
                        reference,
                        background=replace(background, level_change_mad=-1),
                    )
                )
                self.assertIsNone(
                    original_decline_evidence(
                        points,
                        replace(reference, mean_minus=reference.mean_minus + 1),
                        background=background,
                    )
                )

    def test_edge_change_matches_rational_secant_and_affine_symmetries(self):
        for time_factor, value_factor, offset, shift in (
            (1, 1, 0, timedelta(0)),
            (3, 4, 100, timedelta(days=2)),
            (2, -2, -30, timedelta(days=-1)),
        ):
            with self.subTest(time_factor=time_factor, value_factor=value_factor):
                arguments = dict(
                    time_factor=time_factor,
                    value_factor=value_factor,
                    offset=offset,
                    shift=shift,
                )
                reference = self._window((12, 10, 8, 6, 6, 6), **arguments)
                window = self._window((6, 6, 6, 6, 5, 1), start=6, **arguments)
                edge = original_edge_change(window, reference)
                self.assertIsNotNone(edge)
                self.assertIs(edge.window, window)
                self.assertIs(edge.reference, reference)
                # Last secant is (1-5)/2=-2 with error 2*1/2=1.
                # Reference v=0 and e=1/2, so change=-2/(3/2).
                sign = 1 if value_factor > 0 else -1
                self.assertAlmostEqual(edge.direction, -2 * sign)
                self.assertAlmostEqual(edge.change, -4 / 3 * sign)
                followup = original_followup(window, reference)
                self.assertIsNotNone(followup)
                # Reference previous mean 10 versus current mean 4:
                # L=(10-4)/(2*1)=3; reflection reverses this signed loss.
                self.assertAlmostEqual(followup.reference_level_loss, 3 * sign)
                # The new current OLS slope is (1-6)/4=-5/4 and its
                # previous section is constant; reference.v=0, e=e_minus=1/2.
                self.assertAlmostEqual(
                    followup.current_change_from_reference_current, -5 / 4 * sign
                )
                self.assertEqual(followup.previous_change_from_reference_current, 0)

    def test_edge_counterform_needs_resolved_fall_and_rejects_invalid_support(self):
        reference = self._window((12, 10, 8, 6, 10, 14))
        # reference.v=2 and e=1/2. Stopping that rise gives a negative
        # change, while its actual last direction remains flat or unresolved.
        for drop, direction, change, falling in (
            (0, 0, -4 / 3, False),
            (0.5, -1 / 4, -3 / 2, False),
            (4, -2, -8 / 3, True),
        ):
            with self.subTest(last_drop=drop):
                window = self._window((14, 14, 14, 14, 14, 14 - drop), start=6)
                edge = original_edge_change(window, reference)
                self.assertIsNotNone(edge)
                self.assertAlmostEqual(edge.direction, direction)
                self.assertAlmostEqual(edge.change, change)
                self.assertEqual(edge.direction < -1 and edge.change < -1, falling)
        duplicate = replace(
            window,
            current=window.current[:-1]
            + ((window.current[-2][0], window.current[-1][1]),),
        )
        smaller = original_window(
            (window.previous + window.current)[-4:],
            count=2,
            scale=1,
            max_gap_seconds=2,
            at=window.newest_at,
        )
        self.assertIsNotNone(smaller)
        invalid_pairs = (
            (None, reference),
            (window, None),
            (object(), reference),
            (window, object()),
            (replace(window, scale=2), reference),
            (replace(window, e=0), reference),
            (window, replace(reference, e=0)),
            (replace(window, current=window.current[:-1]), reference),
            (smaller, reference),
            (duplicate, reference),
            (reference, window),
        )
        for index, (current, binding) in enumerate(invalid_pairs):
            with self.subTest(invalid=index):
                self.assertIsNone(original_followup(current, binding))
                self.assertIsNone(original_edge_change(current, binding))
        self.assertIsNone(original_edge_change(reference, reference))
        # Only a denominator involving reference.e overflows here; the old
        # reference.e_minus comparisons would still have finite bounds.
        for field in ("e", "e_minus"):
            with self.subTest(overflowing_current_reference_bound=field):
                self.assertIsNone(
                    original_followup(
                        replace(window, **{field: 1e308}),
                        replace(reference, e=1e308),
                    )
                )


class OriginalCurveFollowupTests(unittest.TestCase):
    """Functional robustness criteria on declared, catalogue-timed originals.

    The bound scenario's ``exit`` endpoints supply door climate values and
    ``occupied_infusion`` supplies the moisture pulse at its initial T values.
    ``construction.stay`` supplies door, recovery and settled-tail durations.
    Linear joins isolate continuation and counterforms without claiming an
    exact physical event time. No detector decision or fitted form is replaced.
    """

    def setUp(self):
        manifest = json.loads(
            (ROOT / "scenario_manifest.json").read_text(
                encoding="utf-8",
            )
        )

        def declared_input(key, hash_key):
            path = ROOT / manifest[key]
            payload = path.read_bytes()
            self.assertEqual(hashlib.sha256(payload).hexdigest(), manifest[hash_key])
            return json.loads(payload)

        source = declared_input("source_curves", "source_curves_sha256")
        construction = declared_input("construction_file", "construction_sha256")
        self.parameters = Parameters({})
        self.values = self.parameters.values
        self.count = self.values["curve_original_samples"]
        self.step = self.values["grid_seconds"]
        self.baseline_ticks = (
            ceil(
                max(
                    self.values["curve_window_seconds"],
                    (
                        2 * self.count
                        + self.values["curve_original_kink_history_windows"]
                    )
                    * self.step,
                )
                / self.step
            )
            + 1
        )
        self.door_ticks = ceil(construction["stay"]["brief_door_seconds"] / self.step)
        self.close_ticks = ceil(construction["stay"]["brief_close_seconds"] / self.step)
        self.tail_ticks = ceil(
            construction["stay"]["closed_entry_tail_seconds"] / self.step
        )
        curve = source["curves"]["exit"]
        columns = tuple(curve["roles"].index(role) + 1 for role in CLIMATE)
        self.initial = tuple(curve["rows"][0][column] for column in columns)
        self.exchanged = tuple(curve["rows"][-1][column] for column in columns)
        infusion = source["curves"]["occupied_infusion"]
        columns = tuple(infusion["roles"].index(role) + 1 for role in CLIMATE)
        self.infusion_initial = tuple(infusion["rows"][0][column] for column in columns)
        self.infusion_peak = tuple(
            infusion["rows"][0 if role.endswith("temperature") else -1][column]
            for role, column in zip(CLIMATE, columns, strict=True)
        )
        self.fall_step = tuple(
            (after - before) / self.door_ticks
            for before, after in zip(self.initial, self.exchanged, strict=True)
        )
        self.recovery_step = tuple(
            (before - after) / self.close_ticks if role.endswith("temperature") else 0
            for role, before, after in zip(
                CLIMATE,
                self.initial,
                self.exchanged,
                strict=True,
            )
        )

    def _detector(self):
        detector = Detector(self.parameters, ORIGIN)
        detector.report_heating(False, ORIGIN)
        return detector

    def _at(self, tick):
        return ORIGIN + timedelta(seconds=tick * self.step)

    def _accept(self, detector, tick, state):
        at = self._at(tick)
        for role, value in zip(CLIMATE, state, strict=True):
            position, quantity = role.split("_", 1)
            self.assertTrue(
                detector.accept(
                    Measurement(
                        Position(position),
                        Quantity(quantity),
                        value,
                        str(value),
                        f"sensor.followup_{role}",
                        at,
                        at,
                    )
                )
            )

    @staticmethod
    def _latest(detector):
        return tuple(
            detector.latest[Position(position), Quantity(quantity)].value
            for position, quantity in (role.split("_", 1) for role in CLIMATE)
        )

    @staticmethod
    def _originals(detector):
        return {
            role: tuple(
                detector._curve_series[Position(position), Quantity(quantity)]["points"]
            )
            for role in CLIMATE
            for position, quantity in (role.split("_", 1),)
        }

    def _feed(self, detector, states, *, stop_when=None):
        events = []
        for state in states:
            tick = detector.index + 1
            self._accept(detector, tick, state)
            events.extend(detector.advance(self._at(tick), enabled=True))
            if stop_when is not None and stop_when():
                break
        return events

    def _ramp(self, detector, ticks, delta, *, stop_when=None):
        initial = self._latest(detector)
        return self._feed(
            detector,
            (
                tuple(
                    value + index * change
                    for value, change in zip(
                        initial,
                        delta,
                        strict=True,
                    )
                )
                for index in range(1, ticks + 1)
            ),
            stop_when=stop_when,
        )

    def _bind(self, route, *, fall_step=None):
        fall_step = self.fall_step if fall_step is None else fall_step
        detector = self._detector()
        events = self._feed(
            detector, (self.initial for _ in range(self.baseline_ticks))
        )

        def pending(key):
            return bool(detector.door_episode and detector.door_episode.get(key))

        events += self._ramp(
            detector,
            self.door_ticks,
            fall_step,
            stop_when=lambda: pending("original_open"),
        )
        self.assertTrue(pending("original_open"))
        self.assertNotIn(Kind.DOOR_OPEN, [item.kind for item in events])
        if route == "opening":
            return detector, events, detector.door_episode["original_open"]
        events += self._ramp(detector, 4 * self.count, fall_step)
        self.assertEqual([item.kind for item in events].count(Kind.DOOR_OPEN), 1)
        self.assertTrue(detector.open)
        events += self._ramp(
            detector,
            self.close_ticks,
            self.recovery_step,
            stop_when=lambda: pending("original_close"),
        )
        self.assertTrue(pending("original_close"))
        self.assertNotIn(Kind.DOOR_CLOSE, [item.kind for item in events])
        return detector, events, detector.door_episode["original_close"]

    def test_ticks_duplicates_and_overlapping_old_originals_do_not_confirm(self):
        for route, kind, key, delta in (
            ("opening", Kind.DOOR_OPEN, "original_open", self.fall_step),
            ("closing", Kind.DOOR_CLOSE, "original_close", self.recovery_step),
        ):
            with self.subTest(route=route):
                detector, events, candidate = self._bind(route)
                originals = self._originals(detector)
                bound_tick = detector.index
                for _ in range(2 * self.count):
                    events += detector.advance(self._at(bound_tick), enabled=True)
                for measurement in tuple(detector.latest.values()):
                    self.assertTrue(detector.accept(measurement))
                events += detector.advance(self._at(bound_tick + 1), enabled=True)
                self.assertEqual(self._originals(detector), originals)
                events += self._ramp(detector, self.count - 1, delta)
                current = self._originals(detector)
                for role in CLIMATE:
                    self.assertLessEqual(
                        current[role][-self.count][0], originals[role][-1][0]
                    )
                self.assertNotIn(kind, [item.kind for item in events])
                self.assertIs(detector.door_episode[key], candidate)

    def test_clear_continued_original_shapes_confirm_once_after_binding(self):
        for route, kind, delta, lower_plateau in (
            ("opening", Kind.DOOR_OPEN, self.fall_step, False),
            ("closing", Kind.DOOR_CLOSE, self.recovery_step, False),
            ("opening", Kind.DOOR_OPEN, (0,) * len(CLIMATE), True),
        ):
            with self.subTest(route=route, lower_plateau=lower_plateau):
                short_fall = (
                    tuple(
                        (after - before) / (self.count - 1)
                        for before, after in zip(
                            self.initial, self.exchanged, strict=True
                        )
                    )
                    if lower_plateau
                    else None
                )
                detector, events, candidate = self._bind(route, fall_step=short_fall)
                bound_at = self._at(detector.index)
                if lower_plateau:
                    # V3 requires a complete fresh common fall before a
                    # lower plateau can supply the later independent proof.
                    events += self._ramp(detector, self.count, self.fall_step)
                    self.assertNotIn(Kind.DOOR_OPEN, [item.kind for item in events])
                events += self._ramp(detector, 4 * self.count, delta)
                confirmations = [item for item in events if item.kind is kind]
                self.assertEqual(len(confirmations), 1)
                self.assertGreater(confirmations[0].trace_at, bound_at)
                if route == "opening":
                    if not lower_plateau:
                        self.assertIs(detector.door_episode["original_open"], candidate)
                    newest = {
                        name: (temperature, humidity)
                        for name, temperature, humidity in candidate["newest"]
                    }
                    for name, pair in candidate["confirmation_windows"]:
                        self.assertGreater(
                            pair.temperature.current[0][0], newest[name][0]
                        )
                        self.assertGreater(pair.humidity.current[0][0], newest[name][1])
                else:
                    self.assertFalse(detector.open)
                    self.assertIsNone(detector.door_episode)
                if lower_plateau:
                    if not any(item.kind is Kind.DOOR_CLOSE for item in events):
                        events += self._ramp(detector, self.tail_ticks, delta)
                    self.assertEqual(
                        [
                            item.kind
                            for item in events
                            if item.kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE)
                        ],
                        [Kind.DOOR_OPEN, Kind.DOOR_CLOSE],
                    )
                    self.assertFalse(detector.open)
                    self.assertIsNone(detector.door_episode)

    def test_small_joint_dip_returns_without_a_door_event(self):
        detector = self._detector()
        events = self._feed(
            detector, (self.initial for _ in range(self.baseline_ticks))
        )
        # Reuse one K-section of the existing fixture's slow loss and reverse
        # it immediately. The complete excursion returns to the old level;
        # it supplies neither a long continued fall nor a lower stable step.
        events += self._ramp(detector, self.count, self.fall_step)
        events += self._ramp(
            detector, self.count, tuple(-step for step in self.fall_step)
        )
        events += self._feed(detector, (self.initial for _ in range(self.tail_ticks)))
        self.assertEqual(
            [
                item.kind
                for item in events
                if item.kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE)
            ],
            [],
        )
        self.assertFalse(detector.open)
        self.assertNotIn(Kind.VENTILATION, [item.kind for item in events])

    def test_short_rh_pulse_confirms_once_after_its_peak(self):
        detector = self._detector()
        events = self._feed(
            detector, (self.infusion_initial for _ in range(self.baseline_ticks))
        )
        rise_ticks = self.count - 1
        rise_step = tuple(
            (peak - initial) / rise_ticks
            for initial, peak in zip(
                self.infusion_initial, self.infusion_peak, strict=True
            )
        )
        events += self._ramp(detector, rise_ticks, rise_step)
        peak_at = self._at(detector.index)
        self.assertNotIn(Kind.INFUSION, [item.kind for item in events])
        candidate = detector._original_infusion_candidate
        self.assertIsNotNone(candidate)
        bound = {name: humidity for name, _, humidity in candidate["newest"]}
        originals = self._originals(detector)
        for position in detector.positions:
            self.assertLess(
                sum(
                    stamp > bound[position.value]
                    for stamp, _ in originals[f"{position.value}_humidity"]
                ),
                self.count,
            )
        fall_step = tuple(
            (initial - peak) / self.count
            for initial, peak in zip(
                self.infusion_initial, self.infusion_peak, strict=True
            )
        )
        events += self._ramp(detector, self.count, fall_step)
        events += self._feed(
            detector, (self.infusion_initial for _ in range(self.tail_ticks))
        )
        infusions = [item for item in events if item.kind is Kind.INFUSION]
        self.assertEqual(len(infusions), 1)
        self.assertGreater(infusions[0].trace_at, peak_at)

    def test_renewed_fall_prevents_late_isolated_recovery_confirming_old_close(self):
        detector, events, previous = self._bind("closing")
        # A short approach is followed immediately by a contradictory original
        # fall; the first falling sample must not complete the old warm tail.
        events += self._ramp(detector, self.count - 1, self.recovery_step)
        self.assertNotIn(Kind.DOOR_CLOSE, [item.kind for item in events])
        events += self._ramp(detector, 3 * self.count, self.fall_step)
        self.assertTrue(detector.open)
        self.assertIsNot(detector.door_episode.get("original_close"), previous)
        self.assertNotIn(Kind.DOOR_CLOSE, [item.kind for item in events])
        # This new fragment has a fitting local brake but lacks a complete new
        # continuation. It cannot reactivate the much earlier close binding.
        events += self._ramp(detector, self.count - 1, self.recovery_step)
        self.assertTrue(detector.open)
        self.assertNotIn(Kind.DOOR_CLOSE, [item.kind for item in events])
        self.assertIsNot(detector.door_episode.get("original_close"), previous)

    def test_temperature_plateau_closes_but_flat_rh_with_continued_cooling_does_not(
        self,
    ):
        for tail in ("cooling", "early_rh_plateau", "plateau", "staggered_plateau"):
            with self.subTest(temperature_tail=tail):
                detector, events, _ = self._bind("opening")
                cooling = tail in ("cooling", "early_rh_plateau")
                cooling_delta = tuple(
                    change if role.endswith("temperature") else 0
                    for role, change in zip(CLIMATE, self.fall_step, strict=True)
                )
                if tail == "early_rh_plateau":
                    events += self._ramp(detector, self.count, self.fall_step)
                    self.assertFalse(detector.open)
                    self.assertNotIn(Kind.DOOR_OPEN, [item.kind for item in events])
                    self.assertNotIn(Kind.VENTILATION, [item.kind for item in events])
                    plateau_start = self._originals(detector)
                    # RH ends its fall before opening has its two new T
                    # sections. Public ventilation must still wait for OPEN.
                    events += self._ramp(detector, 2 * self.count - 1, cooling_delta)
                    originals = self._originals(detector)
                    for role in CLIMATE:
                        self.assertEqual(
                            sum(
                                stamp > plateau_start[role][-1][0]
                                for stamp, _ in originals[role]
                            ),
                            2 * self.count - 1,
                        )
                    self.assertFalse(detector.open)
                    self.assertNotIn(Kind.DOOR_OPEN, [item.kind for item in events])
                    self.assertNotIn(Kind.VENTILATION, [item.kind for item in events])
                    events += self._ramp(detector, self.count, cooling_delta)
                else:
                    events += self._ramp(detector, 4 * self.count, self.fall_step)
                self.assertTrue(detector.open)
                self.assertEqual(
                    [item.kind for item in events].count(Kind.DOOR_OPEN), 1
                )
                if tail == "staggered_plateau":
                    # Upper T is already flat in both OLS sections before
                    # lower T brakes. No prefix has a common local brake;
                    # their actual earlier falls belong to the same episode.
                    lower_fall = tuple(
                        change if role == "lower_temperature" else 0
                        for role, change in zip(CLIMATE, self.fall_step, strict=True)
                    )
                    events += self._ramp(detector, 3 * self.count, lower_fall)
                    self.assertTrue(detector.open)
                    self.assertNotIn(Kind.DOOR_CLOSE, [item.kind for item in events])
                delta = tuple(
                    change if cooling and role.endswith("temperature") else 0
                    for role, change in zip(CLIMATE, self.fall_step, strict=True)
                )
                if tail == "staggered_plateau":
                    plateau_state = self._latest(detector)
                    events += self._feed(
                        detector, (plateau_state for _ in range(self.count - 1))
                    )
                    # Mathematical endpoint perturbation: the last secant is
                    # resolved at -1.3 error bounds, but the whole K=7 OLS
                    # section remains flat. It is no new physical door loss.
                    noise = self.values["curve_original_noise_temperature_lower"]
                    perturbed = tuple(
                        value - 2.6 * noise if role == "lower_temperature" else value
                        for role, value in zip(CLIMATE, plateau_state, strict=True)
                    )
                    events += self._feed(detector, (perturbed,))
                    thermal = original_window(
                        self._originals(detector)["lower_temperature"],
                        at=self._at(detector.index),
                        **channel_arguments(self.values, "lower", "temperature"),
                    )
                    self.assertIsNotNone(thermal)
                    self.assertLessEqual(
                        abs(thermal.v),
                        self.values["curve_original_significance"] * thermal.e,
                    )
                    self.assertNotIn(Kind.DOOR_CLOSE, [item.kind for item in events])
                    events += self._feed(
                        detector, (plateau_state for _ in range(self.tail_ticks))
                    )
                else:
                    events += self._ramp(detector, self.tail_ticks, delta)
                doors = [
                    item.kind
                    for item in events
                    if item.kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE)
                ]
                if cooling:
                    self.assertEqual(doors, [Kind.DOOR_OPEN])
                    self.assertTrue(detector.open)
                    self.assertEqual(
                        [item.kind for item in events].count(Kind.VENTILATION), 1
                    )
                    self.assertNotIn(
                        Kind.VENTILATION_COMPLETE, [item.kind for item in events]
                    )
                    self.assertEqual(
                        [
                            item.kind
                            for item in events
                            if item.kind in (Kind.DOOR_OPEN, Kind.VENTILATION)
                        ],
                        [Kind.DOOR_OPEN, Kind.VENTILATION],
                    )
                else:
                    self.assertEqual(doors, [Kind.DOOR_OPEN, Kind.DOOR_CLOSE])
                    self.assertFalse(detector.open)
                    self.assertIsNone(detector.door_episode)
                if tail == "early_rh_plateau":
                    events += self._ramp(detector, self.close_ticks, self.recovery_step)
                    self.assertFalse(detector.open)
                    self.assertIsNone(detector.door_episode)
                    self.assertEqual(
                        [
                            item.kind
                            for item in events
                            if item.kind
                            in (Kind.DOOR_OPEN, Kind.VENTILATION, Kind.DOOR_CLOSE)
                        ],
                        [Kind.DOOR_OPEN, Kind.VENTILATION, Kind.DOOR_CLOSE],
                    )
                    # Isolate the next loss from the preceding warm recovery:
                    # both original sections retain its actual final level.
                    next_baseline = self._latest(detector)
                    events += self._feed(
                        detector, (next_baseline for _ in range(2 * self.count))
                    )
                    originals = self._originals(detector)
                    for role, value in zip(CLIMATE, next_baseline, strict=True):
                        self.assertEqual(
                            tuple(
                                point for _, point in originals[role][-2 * self.count :]
                            ),
                            (value,) * (2 * self.count),
                        )
                    self.assertFalse(detector.open)
                    self.assertIsNone(detector.door_episode)
                    # A formerly ventilated, now closed episode must not
                    # suppress the next pending RH stage before its OPEN.
                    next_cycle_traces = []
                    detector.observer = lambda row: next_cycle_traces.append(
                        {
                            "at": row["at"],
                            "signals": row["signals"],
                            "original_forms": row.get("original_forms"),
                            "original_ventilation": row.get("original_ventilation"),
                        }
                    )
                    events += self._ramp(
                        detector,
                        self.door_ticks,
                        self.fall_step,
                        stop_when=lambda: bool(
                            detector.door_episode
                            and detector.door_episode.get("original_open")
                        ),
                    )
                    self.assertTrue(
                        detector.door_episode
                        and detector.door_episode.get("original_open")
                    )
                    events += self._ramp(detector, self.count, self.fall_step)
                    events += self._ramp(detector, 2 * self.count - 1, cooling_delta)
                    self.assertFalse(detector.open)
                    self.assertEqual(
                        [item.kind for item in events].count(Kind.DOOR_OPEN), 1
                    )
                    self.assertEqual(
                        [item.kind for item in events].count(Kind.VENTILATION), 1
                    )
                    events += self._ramp(detector, 2 * self.count, cooling_delta)
                    self.assertTrue(
                        detector.open,
                        msg=json.dumps(
                            next_cycle_traces,
                            default=str,
                            ensure_ascii=False,
                        ),
                    )
                    self.assertEqual(
                        [
                            item.kind
                            for item in events
                            if item.kind
                            in (Kind.DOOR_OPEN, Kind.VENTILATION, Kind.DOOR_CLOSE)
                        ],
                        [
                            Kind.DOOR_OPEN,
                            Kind.VENTILATION,
                            Kind.DOOR_CLOSE,
                            Kind.DOOR_OPEN,
                            Kind.VENTILATION,
                        ],
                    )
                if tail == "staggered_plateau":
                    events += self._ramp(detector, 4 * self.count, self.fall_step)
                    events += self._ramp(detector, self.tail_ticks, (0,) * len(CLIMATE))
                    self.assertEqual(
                        [
                            item.kind
                            for item in events
                            if item.kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE)
                        ],
                        [
                            Kind.DOOR_OPEN,
                            Kind.DOOR_CLOSE,
                            Kind.DOOR_OPEN,
                            Kind.DOOR_CLOSE,
                        ],
                    )
                    self.assertFalse(detector.open)
                    self.assertIsNone(detector.door_episode)

    def test_same_stamp_temperature_edge_correction_waits_for_new_temperature_support(
        self,
    ):
        detector, events, _ = self._bind("opening")
        events += self._ramp(detector, 4 * self.count, self.fall_step)
        lower_fall = tuple(
            change if role == "lower_temperature" else 0
            for role, change in zip(CLIMATE, self.fall_step, strict=True)
        )
        events += self._ramp(detector, 3 * self.count, lower_fall)
        self.assertTrue(detector.open)
        plateau = self._latest(detector)
        epochs = detector._original_context()
        before = self._originals(detector)["upper_temperature"]
        original = detector.latest[Position.UPPER, Quantity.TEMPERATURE]
        corrected_at = original.measured_at
        fall = detector.door_episode["original_temperature_falls"][Position.UPPER]
        self.assertTrue(
            all(
                stamp != corrected_at
                for window in (fall["window"], fall.get("recovery_window"))
                if window is not None
                for stamp, _ in window.previous + window.current
            )
        )
        received = self._at(detector.index + 1)
        value = (
            original.value - 2.6 * self.values["curve_original_noise_temperature_upper"]
        )
        self.assertTrue(
            detector.accept(
                replace(
                    original,
                    value=value,
                    raw_value=str(value),
                    received_at=received,
                )
            )
        )
        events += detector.advance(received, enabled=True)
        after = self._originals(detector)["upper_temperature"]
        self.assertEqual(
            tuple(stamp for stamp, _ in after), tuple(stamp for stamp, _ in before)
        )
        self.assertEqual(after[:-1], before[:-1])
        self.assertEqual(after[-1], (corrected_at, value))
        thermal = original_window(
            after,
            at=received,
            **channel_arguments(self.values, "upper", "temperature"),
        )
        self.assertIsNotNone(thermal)
        self.assertLessEqual(
            abs(thermal.v), self.values["curve_original_significance"] * thermal.e
        )
        self.assertNotIn(Kind.DOOR_CLOSE, [item.kind for item in events])

        # Finish lower T's plateau inside the next raster. Upper T remains
        # within its original gap limit, with no new original or freshness.
        interval = timedelta(seconds=self.step / (2 * self.count + 2))
        lower = detector.latest[Position.LOWER, Quantity.TEMPERATURE]
        for index in range(1, 2 * self.count + 1):
            at = received + index * interval
            self.assertTrue(
                detector.accept(replace(lower, received_at=at, measured_at=at))
            )
        rh_at = received + (2 * self.count + 1) * interval
        for position in detector.positions:
            humidity = detector.latest[position, Quantity.HUMIDITY]
            self.assertTrue(
                detector.accept(
                    replace(
                        humidity,
                        received_at=rh_at,
                        measured_at=rh_at,
                    )
                )
            )
        events += detector.advance(
            received + timedelta(seconds=self.step), enabled=True
        )
        self.assertEqual(self._originals(detector)["upper_temperature"], after)
        self.assertEqual(detector._original_context(), epochs)
        self.assertTrue(detector.open)
        self.assertNotIn(Kind.DOOR_CLOSE, [item.kind for item in events])

        events += self._feed(detector, (plateau for _ in range(2 * self.count)))
        self.assertEqual(
            [
                item.kind
                for item in events
                if item.kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE)
            ],
            [Kind.DOOR_OPEN, Kind.DOOR_CLOSE],
        )
        self.assertFalse(detector.open)
        self.assertIsNone(detector.door_episode)

    def test_equal_originals_have_same_events_across_advance_grouping(self):
        states = [self.initial] * self.baseline_ticks
        states.extend(
            tuple(
                before + index * change
                for before, change in zip(
                    self.initial,
                    self.fall_step,
                    strict=True,
                )
            )
            for index in range(1, self.door_ticks + 1)
        )
        states.extend(
            tuple(
                before + index * change
                for before, change in zip(
                    self.exchanged,
                    self.recovery_step,
                    strict=True,
                )
            )
            for index in range(1, self.close_ticks + 1)
        )
        states.extend([states[-1]] * self.tail_ticks)
        observed = []
        for cadence, repeated in ((1, False), (1, True), (self.count, False)):
            with self.subTest(cadence=cadence, repeated=repeated):
                detector = self._detector()
                events = []
                for tick, state in enumerate(states):
                    self._accept(detector, tick, state)
                    if (tick + 1) % cadence == 0:
                        events += detector.advance(self._at(tick), enabled=True)
                        if repeated:
                            events += detector.advance(self._at(tick), enabled=True)
                events += detector.advance(self._at(len(states)), enabled=True)
                kinds = tuple(item.kind for item in events)
                doors = tuple(
                    kind
                    for kind in kinds
                    if kind
                    in (
                        Kind.DOOR_OPEN,
                        Kind.DOOR_CLOSE,
                    )
                )
                self.assertEqual(doors, (Kind.DOOR_OPEN, Kind.DOOR_CLOSE))
                self.assertFalse(detector.open)
                self.assertIsNone(detector.door_episode)
                observed.append(kinds)
        self.assertEqual(observed[0], observed[1])
        self.assertEqual(observed[0], observed[2])


if __name__ == "__main__":
    unittest.main()


class NativeInfusionOnsetTests(unittest.TestCase):
    def window(self, values, *, scale=0.1):
        points = tuple((ORIGIN + timedelta(seconds=second), value)
                       for second, value in values)
        return original_window(points, count=len(points) // 2, scale=scale,
                               max_gap_seconds=10, at=points[-1][0])

    def test_local_secants_keep_original_intervals_and_analytic_error_bounds(self):
        values = [(0, 0), (1, 0), (2, 0), (3, 10), (5, 14), (6, 20)]
        window = self.window(values)
        changes = curves.original_window_changes(window)
        self.assertEqual([(item.previous[0], item.current[0]) for item in changes],
                         [(ORIGIN + timedelta(seconds=a), ORIGIN + timedelta(seconds=b))
                          for a, b in ((2, 3), (3, 5), (5, 6))])
        for item, direction, change in zip(changes, (50, 20, 30), (100 / 3, 10, 20)):
            self.assertAlmostEqual(item.direction, direction)
            self.assertAlmostEqual(item.change, change)
        tolerant = curves.original_window_changes(window, tolerance=2)
        self.assertEqual([item.change for item in tolerant], [5, 1, 3])
        scaled = self.window([(time, 7 + 10 * value) for time, value in values], scale=1)
        for left, right in zip(tolerant, curves.original_window_changes(scaled, tolerance=20)):
            self.assertAlmostEqual(left.direction, right.direction)
            self.assertAlmostEqual(left.change, right.change)
        self.assertIsNone(curves.original_window_changes(window, tolerance=float("nan")))
        self.assertIsNone(curves.original_window_changes(window, tolerance=-1))
        flat = self.window([(second, 0) for second in range(-3, 3)])
        edge = original_edge_change(window, flat)
        self.assertAlmostEqual(changes[-1].direction, edge.direction)
        self.assertAlmostEqual(changes[-1].change, edge.change)

    def test_native_rise_inside_overlapping_k_block_is_assigned_by_its_interval(self):
        parameters = Parameters({})
        detector = Detector(parameters, ORIGIN)
        windows, kinks = [], []
        for name, offset in (("upper", 0), ("lower", .1)):
            values = [(second + offset, 20 + max(0, second - 10) * 3)
                      for second in range(14)]
            humidity = self.window(values)
            temperature = self.window([(second + offset, 80) for second in range(14)])
            windows.append((name, OriginalPositionWindows(temperature, humidity)))
            kinks.append((name, original_kink_tolerance(
                tuple((ORIGIN + timedelta(seconds=t), value) for t, value in values),
                humidity, factor=0, min_history_windows=1, max_gap_seconds=10)))
        intervals = detector._original_local_rise_intervals(
            tuple(windows), tuple(kinks), {position: ORIGIN for position in detector.positions})
        self.assertEqual(set(intervals), set(detector.positions))
        self.assertTrue(all(pair.humidity.current[0][0] < ORIGIN + timedelta(seconds=9)
                            for _, pair in windows))
        for end, expected in ((9, "new"), (10.5, "unknown"), (12, "continuation")):
            with self.subTest(end=end):
                episode = {"phase": "ended", "level_end_at": ORIGIN + timedelta(seconds=end)}
                self.assertEqual(detector._original_infusion_assignment(
                    episode, windows, (), intervals), expected)
        # An independently valid earlier onset remains continuation evidence.
        self.assertEqual(detector._original_infusion_assignment(
            {"phase": "ended", "level_end_at": ORIGIN + timedelta(seconds=9)},
            windows, (ORIGIN + timedelta(seconds=8),), intervals), "continuation")

    def test_later_curve_fit_cannot_replace_the_first_native_onset_interval(self):
        from test_detector import InfusionLifecycleTests, T0

        fixture = InfusionLifecycleTests()
        fixture.setUp()
        points, _, _, _ = fixture._scenario(merged=False)
        detector = Detector(fixture.parameters, T0)
        events = []
        peak = fixture.begin + fixture.width
        with patch.object(detector, "_original_rise_witness", return_value=None):
            for second in range(0, peak + fixture.step, fixture.step):
                fixture._send(detector, second, fixture._value(points, second), events)
                if detector._original_infusion_candidate is not None:
                    break
        component = detector._original_infusion_candidate
        self.assertIsNotNone(component)
        intervals = dict(component["onset_intervals"])
        self.assertEqual(set(intervals), set(detector.positions))
        beginnings = {position: interval.previous[0] for position, interval in intervals.items()}
        candidates = {
            position: replace(detector._curve_candidates["infusion", position, Quantity.HUMIDITY],
                              onset_at=beginning - timedelta(seconds=fixture.step))
            for position, beginning in beginnings.items()
        }
        with patch.object(detector, "_original_rise_witness",
                          side_effect=lambda position, *_: candidates[position]):
            fixture._feed(detector, peak + fixture.width, points, events)
        infusions = fixture._infusions(events)
        self.assertEqual(len(infusions), 1)
        self.assertEqual(infusions[0].effective_at, min(beginnings.values()))
        self.assertEqual(component["onset_intervals"], intervals)
        self.assertEqual(component["onset_witnesses"], {})
