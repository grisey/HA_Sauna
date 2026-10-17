"""Lokale Kurvenevidenz aus bestehenden Original- und Zeitreihenfixtures."""

from dataclasses import replace
from datetime import datetime, timedelta
import gzip
import json
from math import log
from pathlib import Path
from time import perf_counter
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from test_detector import trace
from test_foundation import T0, parameters
from test_settings import fixture

from custom_components.ha_sauna.core import curve_change as curves
from custom_components.ha_sauna.core.curve_change import (
    CurvePolicy,
    curve_change,
    curve_level,
    curve_recovery,
    curve_trend,
)


ROOT = Path(__file__).resolve().parent


def curve_policy():
    values = parameters().values
    return CurvePolicy(
        min_side_samples=values["curve_min_side_samples"],
        max_points=values["curve_max_points"],
        max_candidates=values["curve_max_candidates"],
        robust_iterations=values["curve_robust_iterations"],
        huber_delta=values["curve_huber_delta"],
    )


def trace_points(stop):
    return [
        (T0 + timedelta(seconds=second), trace(second)[0]) for second in range(stop)
    ]


def level_trace_points(*, noisy=True):
    """Existing trace's post-decline plateau, optionally with balanced noise.

    The perturbation is the existing trace's small humidity step. Its alternating
    signs specify a test signal with no continuing decay, not a product limit.
    """
    noise = trace(151)[1] - trace(150)[1] if noisy else 0
    pattern = (-2, -1, 1, 2, 1, -1)
    return [
        (
            T0 + timedelta(seconds=second),
            trace(second)[1] + noise * pattern[index % len(pattern)],
        )
        for index, second in enumerate(range(85, 150))
    ]


def recovery_trace_points(degree=2):
    """A smooth U with a known geometric minimum and existing fixture noise."""
    source = level_trace_points()
    valley = source[len(source) // 2][0]
    tail = (source[-1][0] - valley).total_seconds()
    falling_step = trace(70)[0] - trace(71)[0]
    slower_step = trace(280)[0] - trace(281)[0]
    return [
        (
            stamp,
            trace(0)[0]
            + falling_step
            * tail
            / 2
            * (
                ((stamp - valley).total_seconds() / tail) ** 2
                + (
                    slower_step
                    / falling_step
                    * ((stamp - valley).total_seconds() / tail) ** 3
                    if degree == 3
                    else 0
                )
            )
            + value
            - trace(100)[1],
        )
        for stamp, value in source
    ]


class CurveLevelTests(unittest.TestCase):
    def test_new_level_after_decline_does_not_require_the_old_baseline(self):
        points = level_trace_points()
        before_decline = trace(69)[1]
        new_level = trace(100)[1]
        noise = trace(151)[1] - trace(150)[1]
        self.assertNotEqual(new_level, before_decline)
        # Translation is an independent oracle: changing the plateau's absolute
        # level cannot introduce or remove a trend in the supplied observations.
        for offset in (0, before_decline - new_level):
            with self.subTest(offset=offset):
                shifted = [(at, value + offset) for at, value in points]
                result = curve_level(shifted, policy=curve_policy())
                self.assertIsNotNone(result)
                self.assertEqual(result.evidence, "supports", result)
                self.assertFalse(result.exact_constant)
                self.assertAlmostEqual(result.level, new_level + offset, delta=noise)
                self.assertGreater(result.noise_scale, 0)
                self.assertGreater(result.score_gain, 0)
                self.assertEqual(result.start_at, shifted[0][0])
                self.assertEqual(result.newest_at, shifted[-1][0])

    def test_continuing_weak_and_curved_declines_are_not_a_level(self):
        points = level_trace_points()
        small_step = trace(151)[1] - trace(150)[1]
        span = (points[-1][0] - points[0][0]).total_seconds()
        for degree in (1, 2):
            with self.subTest(degree=degree):
                # Test oracle: both tails still decline throughout the window.
                # The small per-second change comes from the existing trace.
                declining = [
                    (
                        at,
                        value
                        + small_step
                        * span
                        * (1 - (at - points[0][0]).total_seconds() / span) ** degree,
                    )
                    for at, value in points
                ]
                result = curve_level(declining, policy=curve_policy())
                self.assertTrue(result is None or result.evidence == "rejects", result)

    def test_r04_another_peak_inside_the_tail_does_not_complete_decay(self):
        # Original R04 observations span the renewed peak at 15:57:56. These
        # bounds select a test counterexample; they are no product timing rule.
        zone = ZoneInfo("Europe/Berlin")
        start = datetime(2000, 1, 1, 15, 57, 45, tzinfo=zone)
        end = datetime(2000, 1, 1, 15, 58, 10, tzinfo=zone)
        origin = datetime.fromisoformat(fixture("session")["origin"])
        samples = {"upper_humidity": [], "lower_humidity": []}
        with gzip.open(
            ROOT / "fixtures/replay/R04.samples.jsonl.gz",
            "rt",
            encoding="utf-8",
        ) as stream:
            for line in stream:
                row = json.loads(line)
                at = origin + timedelta(seconds=row["t"])
                if at > end:
                    break
                if start <= at and row["role"] in samples:
                    samples[row["role"]].append((at, row["value"]))
        for role, points in samples.items():
            with self.subTest(role=role):
                self.assertGreaterEqual(
                    len(points),
                    2 * curve_policy().min_side_samples,
                )
                result = curve_level(points, policy=curve_policy())
                self.assertTrue(result is None or result.evidence == "rejects", result)

    def test_exact_constant_originals_supply_level_evidence_with_zero_mad(self):
        points = level_trace_points(noisy=False)
        result = curve_level(points, policy=curve_policy())
        self.assertIsNotNone(result)
        self.assertEqual(result.evidence, "supports")
        self.assertTrue(result.exact_constant)
        self.assertEqual(result.noise_scale, 0)
        self.assertEqual(result.level, points[0][1])
        self.assertEqual(result.count, len(points))
        self.assertIsNone(result.level_score)
        self.assertIsNone(result.trend_score)
        self.assertIsNone(result.score_gain)
        self.assertIsNone(result.trend_model)

    def test_irregular_original_times_remain_usable_but_invalid_originals_do_not(self):
        points = [
            (at + index % 3 * timedelta.resolution, value)
            for index, (at, value) in enumerate(level_trace_points())
        ]
        result = curve_level(points, policy=curve_policy())
        self.assertIsNotNone(result)
        self.assertEqual(result.evidence, "supports", result)
        self.assertEqual(result.newest_at, points[-1][0])
        variants = {}
        for name, replacement in (
            ("duplicate original time", (points[2][0], points[3][1])),
            ("reversed original time", (points[1][0], points[3][1])),
            ("missing time", (None, points[3][1])),
            ("nonfinite value", (points[3][0], float("nan"))),
            ("infinite value", (points[3][0], float("inf"))),
            ("boolean value", (points[3][0], True)),
        ):
            variants[name] = points[:3] + [replacement] + points[4:]
        for name, originals in variants.items():
            with self.subTest(name=name):
                self.assertIsNone(curve_level(originals, policy=curve_policy()))

    def test_downsampling_cannot_turn_a_hidden_excursion_into_exact_constancy(self):
        policy = curve_policy()
        count = 2 * policy.max_points + 1
        points = [
            (T0 + timedelta(seconds=index), trace(100)[1]) for index in range(count)
        ]
        # One interior original differs, even if bounded time sampling would
        # omit it. Neither a level nor exact equality may be invented from that.
        points[1] = (points[1][0], trace(69)[1])
        result = curve_level(points, policy=policy)
        self.assertTrue(result is None or result.evidence == "rejects", result)


class CurveTrendTests(unittest.TestCase):
    def test_old_turns_do_not_invent_falling_trend_after_a_plateau_onset(self):
        # The independent input has turns at 30/50/70 and is exactly constant
        # after 70. The old global fit reported a falling slope at 71 despite
        # every actual right-hand observation being 80.
        points = []
        for second in range(23, 84):
            fall = max(0, min(second - 30, 70 - second, 20))
            points.append((T0 + timedelta(seconds=second), 80 - 0.04 * fall))
        result = curve_change(
            points,
            policy=curve_policy(),
            onset_at=T0 + timedelta(seconds=71),
            direction=-1,
            onset_not_before=T0 + timedelta(seconds=57),
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.evidence, "supports")
        self.assertIsNotNone(result.after_trend)
        self.assertEqual(result.after_trend.direction, 0)
        self.assertTrue(result.after_trend.exact_constant)
        self.assertAlmostEqual(
            result.slope_after - result.slope_before,
            result.slope_change,
        )

    def test_actual_falling_flat_and_rising_flanks_have_their_own_direction(self):
        variants = ((-1, range(70, 85)), (0, range(85, 100)), (1, range(150, 165)))
        for direction, seconds in variants:
            with self.subTest(direction=direction):
                # Irregular original times stay on the existing trace's own
                # linear pieces; no measurement is interpolated by the helper.
                points = [
                    (
                        T0 + timedelta(seconds=second + (index % 3) / 4),
                        trace(second + (index % 3) / 4)[0],
                    )
                    for index, second in enumerate(seconds)
                ]
                result = curve_trend(points, policy=curve_policy())
                self.assertIsNotNone(result)
                self.assertEqual(result.direction, direction)
                self.assertEqual(result.exact_constant, direction == 0)
                self.assertEqual(result.exact_linear, direction != 0)
                self.assertEqual(result.noise_scale, 0)
                self.assertIsNone(result.level_score)
                self.assertIsNone(result.trend_score)
                if direction:
                    self.assertGreater(result.slope * direction, 0)

    def test_noisy_direction_competes_with_a_level_on_the_same_original_segment(self):
        points = level_trace_points()
        rate = trace(151)[1] - trace(150)[1]
        for direction in (-1, 0, 1):
            with self.subTest(direction=direction):
                originals = [
                    (at, value + direction * rate * (at - points[0][0]).total_seconds())
                    for at, value in points
                ]
                result = curve_trend(originals, policy=curve_policy())
                self.assertIsNotNone(result)
                self.assertEqual(result.direction, direction)
                self.assertGreater(result.noise_scale, 0)
                self.assertFalse(result.exact_constant)
                self.assertFalse(result.exact_linear)
                if direction:
                    self.assertGreater(result.slope * direction, 0)
                    self.assertGreater(result.score_gain, 0)
                else:
                    self.assertLess(result.score_gain, 0)

    def test_an_isolated_excursion_cannot_supply_direction_or_resolve_a_zero_mad(self):
        original = level_trace_points(noisy=False)
        for index in (0, len(original) // 2, len(original) - 1):
            with self.subTest(index=index):
                points = list(original)
                points[index] = (points[index][0], trace(69)[1])
                result = curve_trend(points, policy=curve_policy())
                self.assertTrue(result is None or result.direction == 0, result)
                if index == len(original) // 2:
                    # The symmetric isolated excursion leaves an unresolved
                    # median residual scale; it is no noiseless linear flank.
                    self.assertIsNone(result)

    def test_one_late_report_cannot_own_direction_after_a_dense_level_cluster(self):
        policy = curve_policy()
        late_at = T0 + timedelta(
            seconds=parameters().values["infusion_window_seconds"],
        )
        for noisy in (False, True):
            with self.subTest(noisy_cluster=noisy):
                # Distinct but tightly clustered originals precede one changed
                # report. Its long time lever cannot substitute for a second
                # observation of the new direction, even with cluster noise.
                cluster = level_trace_points(noisy=noisy)[:-1]
                points = [
                    (T0 + index * timedelta.resolution, value)
                    for index, (_, value) in enumerate(cluster)
                ]
                points.append((late_at, trace(69)[1]))
                self.assertLessEqual(len(points), policy.max_points)
                result = curve_trend(points, policy=policy)
                self.assertTrue(result is None or result.direction == 0, result)

    def test_hidden_originals_cannot_become_exact_collinearity_after_downsampling(self):
        policy = curve_policy()
        count = 2 * policy.max_points + 1
        points = [
            (T0 + timedelta(seconds=index), trace(70 + 14 * index / (count - 1))[0])
            for index in range(count)
        ]
        points[1] = (points[1][0], trace(69)[0])
        result = curve_trend(points, policy=policy)
        self.assertTrue(result is None or not result.exact_linear, result)
        for value in (float("nan"), float("inf"), True):
            with self.subTest(invalid_value=value):
                invalid = list(points)
                invalid[1] = (invalid[1][0], value)
                self.assertIsNone(curve_trend(invalid, policy=policy))
        duplicate = points[:1] + [(points[0][0], points[1][1])] + points[2:]
        self.assertIsNone(curve_trend(duplicate, policy=policy))


class CurveRecoveryTests(unittest.TestCase):
    def test_smooth_recovery_requires_both_original_flanks_without_an_onset_claim(self):
        policy = curve_policy()
        window = timedelta(seconds=parameters().values["door_window_seconds"])
        for degree in (2, 3):
            with self.subTest(degree=degree):
                points = recovery_trace_points(degree)
                geometric_valley = points[len(points) // 2][0]
                previous = None
                # Only the second input contains the final new original. The
                # stateless feature reports that frontier; its caller confirms
                # freshness and owns any event time separately.
                for originals in (points[:-1], points):
                    cutoff = originals[-1][0] - window
                    result = curve_recovery(
                        originals,
                        policy=policy,
                        current_not_before=cutoff,
                    )
                    self.assertIsNotNone(result)
                    self.assertEqual(result.evidence, "supports", result)
                    self.assertFalse(hasattr(result, "onset_at"))
                    self.assertEqual(result.start_at, originals[0][0])
                    self.assertEqual(result.newest_at, originals[-1][0])
                    self.assertEqual(result.count, len(originals))
                    self.assertLessEqual(
                        abs((result.valley_at - geometric_valley).total_seconds()),
                        parameters().values["grid_seconds"],
                    )
                    self.assertEqual(result.before_trend.direction, -1)
                    self.assertEqual(result.after_trend.direction, 1)
                    self.assertGreaterEqual(
                        result.before_count, policy.min_side_samples
                    )
                    self.assertGreaterEqual(result.after_count, policy.min_side_samples)
                    self.assertLess(result.decline_newest_at, result.valley_at)
                    self.assertLess(result.valley_at, result.recovery_start_at)
                    self.assertEqual(result.recovery_start_at, cutoff)
                    self.assertLess(result.decline_start_at, cutoff)
                    self.assertEqual(result.recovery_newest_at, originals[-1][0])
                    for stamp in (
                        result.decline_start_at,
                        result.decline_newest_at,
                        result.recovery_start_at,
                        result.recovery_newest_at,
                    ):
                        self.assertIn(stamp, {stamp for stamp, _ in originals})
                    if previous is not None:
                        self.assertGreater(result.newest_at, previous.newest_at)
                        self.assertGreater(
                            result.recovery_newest_at, previous.recovery_newest_at
                        )
                    previous = result

    def test_falling_level_heating_and_one_excursion_do_not_supply_full_recovery(self):
        source = level_trace_points()
        start, stop = source[0][0], source[-1][0]
        span = (stop - start).total_seconds()
        falling_step = trace(70)[0] - trace(71)[0]
        variants = {
            "continuing fall": [
                (
                    stamp,
                    trace(0)[0]
                    - falling_step * (stamp - start).total_seconds()
                    + value
                    - trace(100)[1],
                )
                for stamp, value in source
            ],
            "fall slows to an endpoint": [
                (
                    stamp,
                    trace(0)[0]
                    + falling_step * (stop - stamp).total_seconds() ** 2 / (2 * span)
                    + value
                    - trace(100)[1],
                )
                for stamp, value in source
            ],
            "level": [
                (stamp, trace(0)[0] + value - trace(100)[1]) for stamp, value in source
            ],
            "continuing heat": [
                (
                    stamp,
                    trace(0)[0]
                    + falling_step * (stamp - start).total_seconds()
                    + value
                    - trace(100)[1],
                )
                for stamp, value in source
            ],
        }
        isolated = list(variants["level"])
        middle = len(isolated) // 2
        isolated[middle] = (isolated[middle][0], trace(85)[0])
        variants["one isolated valley original"] = isolated
        for name, points in variants.items():
            with self.subTest(shape=name):
                result = curve_recovery(
                    points,
                    policy=curve_policy(),
                    current_not_before=stop
                    - timedelta(
                        seconds=parameters().values["door_window_seconds"],
                    ),
                )
                self.assertTrue(result is None or result.evidence == "rejects", result)

    def test_current_cutoff_cannot_replace_missing_decline_or_current_originals(self):
        policy = curve_policy()
        points = recovery_trace_points()
        window = timedelta(seconds=parameters().values["door_window_seconds"])
        cutoff = points[-1][0] - window
        supported = curve_recovery(points, policy=policy, current_not_before=cutoff)
        self.assertIsNotNone(supported)
        self.assertEqual(supported.evidence, "supports", supported)
        self.assertEqual(supported.recovery_start_at, cutoff)
        self.assertEqual(
            supported,
            curve_recovery(points, policy=policy, current_not_before=cutoff),
        )
        rising_only = points[len(points) // 2 :]
        cases = (
            (rising_only, cutoff),
            (points, points[-(policy.min_side_samples - 1)][0]),
            (points, points[-1][0] + window),
        )
        for originals, current_not_before in cases:
            with self.subTest(
                start=originals[0][0], current_not_before=current_not_before
            ):
                result = curve_recovery(
                    originals,
                    policy=policy,
                    current_not_before=current_not_before,
                )
                self.assertTrue(result is None or result.evidence == "rejects", result)


class CurveChangeTests(unittest.TestCase):
    def test_r04_short_closure_is_visible_while_humidity_still_falls(self):
        started = perf_counter()
        # Independent accepted reference: closure near 16:02:40 local time.
        reference = datetime(2000, 1, 1, 16, 2, 40, tzinfo=ZoneInfo("Europe/Berlin"))
        # Existing outputs in R04.json from GitHub run 38535278217 establish
        # the owner floor and when it was known, then the latest confirmation.
        # These are observed test references, not additional product deadlines.
        opened_at = datetime(2000, 1, 1, 16, 2, 20, tzinfo=reference.tzinfo)
        opening_confirmed_at = datetime(2000, 1, 1, 16, 2, 42, tzinfo=reference.tzinfo)
        closed_at = datetime(2000, 1, 1, 16, 3, 19, tzinfo=reference.tzinfo)
        values = parameters().values
        policy = curve_policy()
        lookback = timedelta(seconds=values["curve_window_seconds"])
        grid = timedelta(seconds=values["grid_seconds"])
        window_start = opening_confirmed_at - lookback
        origin = datetime.fromisoformat(fixture("session")["origin"])
        samples = {"upper_humidity": [], "lower_humidity": []}
        with gzip.open(
            ROOT / "fixtures/replay/R04.samples.jsonl.gz", "rt", encoding="utf-8"
        ) as stream:
            for line in stream:
                row = json.loads(line)
                at = origin + timedelta(seconds=row["t"])
                if at >= closed_at:
                    break
                if at >= window_start and row["role"] in samples:
                    samples[row["role"]].append((at, row["value"]))

        evaluations = {}
        curve_seconds = 0.0
        models = curves._models
        for role, originals in samples.items():
            with self.subTest(role=role):
                evaluations[role] = 0
                cursor = 0
                observed_at = opening_confirmed_at
                first_observed_at = None
                result = None
                points = []
                full_window_fits = []
                while observed_at < closed_at:
                    previous = cursor
                    while (
                        cursor < len(originals) and originals[cursor][0] <= observed_at
                    ):
                        cursor += 1
                    if cursor == previous:
                        observed_at += grid
                        continue
                    points = [
                        point
                        for point in originals[:cursor]
                        if observed_at - lookback <= point[0] <= observed_at
                    ]
                    full_window_fits = []

                    def observed_models(times, values, noise, knots, policy):
                        fitted = models(times, values, noise, knots, policy)
                        if not full_window_fits:
                            full_window_fits.append((tuple(knots), fitted))
                        return fitted

                    with patch.object(curves, "_models", side_effect=observed_models):
                        evaluations[role] += 1
                        evaluation_started = perf_counter()
                        result = curve_change(
                            points,
                            policy=policy,
                            direction=1,
                            onset_not_before=opened_at,
                        )
                        curve_seconds += perf_counter() - evaluation_started
                    if result is not None and result.evidence == "supports":
                        first_observed_at = observed_at
                        break
                    observed_at += grid

                knot_diagnostics = []
                for knots, fitted in full_window_fits:
                    if fitted is None:
                        continue
                    smooth_score, _, _, changes = fitted
                    span = (points[-1][0] - points[0][0]).total_seconds()
                    original_at = {
                        (stamp - points[0][0]).total_seconds() / span: stamp
                        for stamp, _ in points
                    }
                    knot_times = dict(knots)
                    search_cost = 2 * log(len(knots))
                    knot_diagnostics.extend(
                        {
                            "original_at": original_at[knot_times[index]].isoformat(),
                            "hinge": coefficients[3],
                            "slope_change_per_second": coefficients[3] / span,
                            "score_gain_after_search": smooth_score
                            - score
                            - search_cost,
                        }
                        for score, index, coefficients in changes
                    )
                diagnostic = (
                    f"{role}: {result!r}; first_observed_at={first_observed_at!r}; "
                    f"evaluations={evaluations[role]}; full_window_knots={knot_diagnostics!r}"
                )
                self.assertIsNotNone(result, msg=diagnostic)
                self.assertEqual(result.evidence, "supports", msg=diagnostic)
                self.assertIsNotNone(first_observed_at, msg=diagnostic)
                self.assertLess(first_observed_at, closed_at, msg=diagnostic)
                self.assertLessEqual(
                    abs((result.onset_at - reference).total_seconds()),
                    10,
                    msg=diagnostic,
                )
                self.assertLess(result.slope_before, result.slope_after, msg=diagnostic)
                self.assertLess(result.slope_after, 0, msg=diagnostic)
                self.assertIn(result.onset_at, {at for at, _ in points}, msg=diagnostic)
                self.assertEqual(result.newest_at, points[-1][0], msg=diagnostic)
                self.assertTrue(
                    all(at <= first_observed_at for at, _ in points), msg=diagnostic
                )
        print(
            f"R04 candidate scan: evaluations={sum(evaluations.values())} "
            f"per_role={evaluations!r} curve_change_seconds={curve_seconds:.6f} "
            f"wall_seconds={perf_counter() - started:.6f}"
        )

    def test_direction_and_inclusive_owner_floor_keep_the_full_original_support(self):
        policy = replace(curve_policy(), max_candidates=curve_policy().max_points)
        points = trace_points(80)
        onset = T0 + timedelta(seconds=70)
        with patch.object(curves, "_models", wraps=curves._models) as fits:
            result = curve_change(
                points,
                policy=policy,
                onset_at=onset,
                direction=-1,
                onset_not_before=onset,
            )
        self.assertIsNotNone(result)
        self.assertEqual(result.evidence, "supports")
        self.assertEqual(result.onset_at, onset)
        self.assertLess(result.slope_change, 0)
        self.assertEqual(len(fits.call_args_list[0].args[0]), len(points))
        self.assertEqual(result.before_count + result.after_count, len(points))
        opposite = curve_change(
            points,
            policy=policy,
            onset_at=onset,
            direction=1,
            onset_not_before=onset,
        )
        self.assertIsNotNone(opposite)
        self.assertEqual(opposite.evidence, "rejects")
        self.assertIsNone(
            curve_change(
                points,
                policy=policy,
                onset_at=onset,
                direction=-1,
                onset_not_before=onset + timedelta.resolution,
            )
        )

    def test_positive_change_can_leave_both_slopes_negative(self):
        policy = replace(curve_policy(), max_candidates=curve_policy().max_points)
        base = trace(0)[0]
        falling_step = trace(70)[0] - trace(71)[0]
        points = [
            (at, base - 2 * falling_step * (at - T0).total_seconds() - (value - base))
            for at, value in trace_points(80)
        ]
        result = curve_change(points, policy=policy)
        self.assertIsNotNone(result)
        self.assertEqual(result.evidence, "supports")
        self.assertGreater(result.slope_change, 0)
        self.assertLess(result.slope_before, result.slope_after)
        self.assertLess(result.slope_after, 0)

    def test_fixed_onset_cannot_invent_braking_during_continuous_cooling(self):
        # The existing detector input starts cooling at 70 and never slows.
        # This is its full 60-second original window at the false close tick;
        # neither the frozen hypothesis nor its owner floor can move for a fit.
        points = [
            (T0 + timedelta(seconds=second), 80 - 0.04 * max(0, second - 70))
            for second in range(43, 104)
        ]
        onset = T0 + timedelta(seconds=90)
        result = curve_change(
            points,
            policy=curve_policy(),
            onset_at=onset,
            direction=1,
            onset_not_before=T0 + timedelta(seconds=68),
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.evidence, "rejects", result)
        self.assertEqual(result.onset_at, onset)
        self.assertEqual(result.newest_at, points[-1][0])

        # The plateau fixture supplies balanced measurement noise, independent
        # of the fitted models. From 70 onward the underlying rate stays -.04
        # on both sides of the frozen onset, so there is no physical slowdown.
        noisy_points = [
            (at, value + noisy_level - trace(100)[1])
            for (at, value), (_, noisy_level) in zip(
                points,
                level_trace_points()[: len(points)],
                strict=True,
            )
        ]
        noisy_result = curve_change(
            noisy_points,
            policy=curve_policy(),
            onset_at=onset,
            direction=1,
            onset_not_before=T0 + timedelta(seconds=68),
        )
        self.assertTrue(
            noisy_result is None or noisy_result.evidence == "rejects",
            noisy_result,
        )

    def test_owner_floor_preserves_real_braking_after_earlier_heating(self):
        policy = curve_policy()
        opening = 70
        braking = 90
        falling_step = trace(70)[0] - trace(71)[0]
        slower_step = trace(280)[0] - trace(281)[0]
        # A smooth heating history reaches its maximum at the known opening.
        # The quadratic background and the real positive hinge are both in the
        # existing model family; their input rates come from the trace fixture.
        # Before the owner floor, the old heating has the opposite local sense.
        points = [
            (
                T0 + timedelta(seconds=second),
                trace(0)[0]
                - falling_step * (second - opening) ** 2 / (braking - opening)
                + (2 * falling_step - slower_step) * max(0, second - braking),
            )
            for second in range(43, braking + 2 * policy.min_side_samples)
        ]
        onset = T0 + timedelta(seconds=braking)
        with patch.object(curves, "_models", wraps=curves._models) as fits:
            result = curve_change(
                points,
                policy=policy,
                onset_at=onset,
                direction=1,
                onset_not_before=T0 + timedelta(seconds=opening),
            )
        self.assertIsNotNone(result)
        self.assertEqual(result.evidence, "supports", result)
        self.assertEqual(result.onset_at, onset)
        self.assertGreater(result.slope_change, 0)
        self.assertLess(result.slope_before, result.slope_after)
        self.assertLess(result.slope_after, 0)
        self.assertIsNotNone(result.after_trend)
        self.assertEqual(result.after_trend.direction, -1)
        self.assertEqual(len(fits.call_args_list[0].args[0]), len(points))
        self.assertEqual(result.before_count + result.after_count, len(points))

    def test_smooth_trends_and_an_isolated_outlier_do_not_support_a_change(self):
        policy = curve_policy()
        source = trace_points(80)
        base = source[0][1]
        amplitude = trace(70)[0] - trace(85)[0]
        span = (source[-1][0] - source[0][0]).total_seconds()
        variants = {
            f"smooth degree {degree}": [
                (at, base - amplitude * ((at - T0).total_seconds() / span) ** degree)
                for at, _ in source
            ]
            for degree in (1, 2, 3)
        }
        outlier = [(at, base) for at, _ in source]
        outlier[len(outlier) // 2] = (outlier[len(outlier) // 2][0], trace(85)[0])
        variants["one isolated original"] = outlier
        for name, points in variants.items():
            with self.subTest(name=name):
                result = curve_change(points, policy=policy)
                self.assertTrue(result is None or result.evidence == "rejects", result)

    def test_frozen_onset_cannot_move_or_become_an_interpolated_point(self):
        policy = replace(curve_policy(), max_candidates=curve_policy().max_points)
        initial = curve_change(trace_points(80), policy=policy)
        self.assertIsNotNone(initial)
        self.assertEqual(initial.evidence, "supports")
        self.assertEqual(initial.onset_at, T0 + timedelta(seconds=70))
        later = curve_change(trace_points(85), policy=policy, onset_at=initial.onset_at)
        self.assertIsNotNone(later)
        self.assertEqual(later.evidence, "supports")
        self.assertEqual(later.onset_at, initial.onset_at)
        self.assertIsNone(
            curve_change(
                trace_points(85),
                policy=policy,
                onset_at=initial.onset_at + timedelta(microseconds=1),
            )
        )

    def test_fit_budget_retains_only_original_points_and_pins_the_frozen_onset(self):
        policy = curve_policy()
        count = 2 * policy.max_points + 1
        points = [
            (
                T0 + timedelta(seconds=79 * index / (count - 1)),
                trace(79 * index / (count - 1))[0],
            )
            for index in range(count)
        ]
        onset_at = points[len(points) // 2][0]
        selections = []
        bounded = curves._bounded_samples

        def observed_selection(samples, limit, onset):
            selected = bounded(samples, limit, onset)
            selections.append(selected)
            return selected

        with (
            patch.object(curves, "_bounded_samples", side_effect=observed_selection),
            patch.object(
                curves,
                "_models",
                wraps=curves._models,
            ) as fits,
        ):
            curve_change(points, policy=policy, onset_at=onset_at)

        self.assertEqual(len(selections), 1)
        selected = selections[0]
        self.assertLessEqual(len(selected), policy.max_points)
        self.assertTrue(set(selected).issubset(points))
        self.assertEqual((selected[0], selected[-1]), (points[0], points[-1]))
        self.assertIn(onset_at, {at for at, _ in selected})
        self.assertGreaterEqual(fits.call_count, 1)
        self.assertLessEqual(fits.call_count, 2)
        for call in fits.call_args_list:
            times, values, _, knots, _ = call.args
            self.assertLessEqual(len(times), policy.max_points)
            self.assertEqual(len(times), len(values))
            self.assertLessEqual(len(knots), policy.max_candidates)

    def test_bound_transition_localizes_original_knot_without_trimming_context(self):
        points = trace_points(80)
        policy = curve_policy()
        onset = T0 + timedelta(seconds=70)
        bounds = (T0 + timedelta(seconds=69), T0 + timedelta(seconds=71))
        with patch.object(curves, "_models", wraps=curves._models) as fits:
            result = curve_change(points, policy=policy, direction=-1, onset_bounds=bounds)
        self.assertIsNotNone(result)
        self.assertEqual(result.evidence, "supports")
        self.assertEqual(result.onset_at, onset)
        self.assertEqual(result.newest_at, points[-1][0])
        self.assertEqual(result.before_count + result.after_count, len(points))
        full_times, _, _, knots, _ = fits.call_args_list[0].args
        self.assertEqual(len(full_times), len(points))
        self.assertEqual({points[index][0] for index, _ in knots}, {
            stamp for stamp, _ in points if bounds[0] <= stamp <= bounds[1]
        })
        self.assertLessEqual(len(knots), policy.max_candidates)
        # Independent changes of value units and clock origin preserve the
        # known fixture change, without prescribing the fit coefficients.
        moved = timedelta(days=1)
        transformed = curve_change(
            [(stamp + moved, 10 * value + 100) for stamp, value in points],
            policy=policy, direction=-1,
            onset_bounds=tuple(stamp + moved for stamp in bounds),
        )
        self.assertIsNotNone(transformed)
        self.assertEqual(transformed.evidence, "supports")
        self.assertEqual(transformed.onset_at, onset + moved)

    def test_onset_bounds_are_inclusive_and_do_not_reassign_fixed_onset(self):
        points = trace_points(80)
        policy = curve_policy()
        onset = T0 + timedelta(seconds=70)
        result = curve_change(points, policy=policy, onset_bounds=(onset, onset))
        self.assertIsNotNone(result)
        self.assertEqual(result.onset_at, onset)
        for bounds in (
            (onset + timedelta(seconds=1), onset + timedelta(seconds=2)),
            (onset + timedelta(seconds=1), onset),
            (onset,), (None, onset), ("earlier", "later"),
        ):
            with self.subTest(bounds=bounds):
                self.assertIsNone(curve_change(
                    points, policy=policy, onset_at=onset, onset_bounds=bounds,
                ))

    def test_integrated_hinge_has_continuous_slope_and_physical_curvature_units(self):
        policy = curve_policy()
        source = level_trace_points()
        original_onset = source[len(source) // 2][0]
        tail = (source[-1][0] - original_onset).total_seconds()
        falling_step = trace(70)[0] - trace(71)[0]
        slower_step = trace(280)[0] - trace(281)[0]
        curvature = falling_step / tail
        for direction in (-1, 1):
            for time_scale in (1, 2):
                with self.subTest(direction=direction, time_scale=time_scale):
                    # Analytic input: y' is -falling_step on both sides of k;
                    # y'' jumps by direction*curvature. The existing balanced
                    # fixture perturbation supplies noise without defining k.
                    points = [
                        (
                            source[0][0] + time_scale * (stamp - source[0][0]),
                            trace(0)[0]
                            - falling_step * (stamp - source[0][0]).total_seconds()
                            + direction
                            * curvature
                            * max(
                                0,
                                (stamp - original_onset).total_seconds(),
                            )
                            ** 2
                            / 2
                            + value
                            - trace(100)[1],
                        )
                        for stamp, value in source
                    ]
                    onset = points[len(points) // 2][0]
                    with patch.object(curves, "_models", wraps=curves._models) as fits:
                        result = curve_change(
                            points[:-1],
                            policy=policy,
                            hinge_order=2,
                            onset_at=onset,
                            direction=direction,
                            onset_not_before=points[0][0],
                        )
                    self.assertIsNotNone(result)
                    self.assertEqual(result.evidence, "supports", result)
                    self.assertEqual(result.hinge_order, 2)
                    self.assertEqual(result.onset_at, onset)
                    self.assertEqual(result.slope_before, result.slope_after)
                    self.assertEqual(result.slope_change, 0)
                    self.assertAlmostEqual(
                        result.slope_before,
                        -falling_step / time_scale,
                        delta=slower_step / time_scale,
                    )
                    self.assertAlmostEqual(
                        result.curvature_change,
                        direction * curvature / time_scale**2,
                        delta=slower_step / (tail * time_scale**2),
                    )
                    self.assertGreater(result.signed_strength * direction, 0)
                    self.assertLess(onset, min(points, key=lambda point: point[1])[0])
                    self.assertGreaterEqual(fits.call_count, 2)
                    self.assertTrue(
                        all(
                            call.kwargs.get("hinge_order") == 2
                            for call in fits.call_args_list
                        )
                    )
                    normalized_onset = (onset - points[0][0]) / (
                        points[-2][0] - points[0][0]
                    )
                    self.assertEqual(
                        [knot for _, knot in fits.call_args_list[-1].args[3]],
                        [normalized_onset],
                    )
                    fresh = curve_change(
                        points,
                        policy=policy,
                        hinge_order=2,
                        onset_at=result.onset_at,
                        direction=direction,
                        onset_not_before=points[0][0],
                    )
                    self.assertIsNotNone(fresh)
                    self.assertEqual(fresh.evidence, "supports", fresh)
                    self.assertEqual(fresh.hinge_order, 2)
                    self.assertEqual(fresh.onset_at, result.onset_at)
                    self.assertGreater(fresh.newest_at, result.newest_at)
                    self.assertEqual(fresh.slope_change, 0)

    def test_integrated_hinge_does_not_turn_smooth_cooling_or_a_trough_into_an_onset(
        self,
    ):
        source = level_trace_points()
        onset = source[len(source) // 2][0]
        span = (source[-1][0] - source[0][0]).total_seconds()
        falling_step = trace(70)[0] - trace(71)[0]
        amplitude = trace(70)[0] - trace(85)[0]
        variants = {}
        for degree in (1, 2, 3):
            # The analytic derivative stays negative throughout. Quadratic and
            # cubic slowing is globally smooth, with no new curvature jump.
            self.assertLess(degree * amplitude / span, falling_step)
            variants[f"smooth falling degree {degree}"] = [
                (
                    stamp,
                    trace(0)[0]
                    - falling_step * (stamp - source[0][0]).total_seconds()
                    + amplitude
                    * ((stamp - source[0][0]).total_seconds() / span) ** degree
                    + value
                    - trace(100)[1],
                )
                for stamp, value in source
            ]
        for degree in (2, 3):
            # A pre-existing smooth minimum is not the beginning of a new
            # curvature regime and cannot supply an earlier contact time.
            variants[f"smooth trough degree {degree}"] = [
                (
                    stamp,
                    trace(0)[0]
                    + amplitude
                    * (
                        ((stamp - onset).total_seconds() / span) ** 2
                        + (
                            ((stamp - onset).total_seconds() / span) ** 3
                            if degree == 3
                            else 0
                        )
                    )
                    + value
                    - trace(100)[1],
                )
                for stamp, value in source
            ]
        isolated = [
            (stamp, trace(0)[0] + value - trace(100)[1]) for stamp, value in source
        ]
        isolated[len(isolated) // 2] = (onset, trace(85)[0])
        variants["one isolated original"] = isolated
        for name, points in variants.items():
            with self.subTest(shape=name):
                result = curve_change(
                    points,
                    policy=curve_policy(),
                    hinge_order=2,
                    onset_at=onset,
                    direction=1,
                    onset_not_before=points[0][0],
                )
                self.assertTrue(result is None or result.evidence == "rejects", result)

    def test_hinge_order_is_explicit_and_invalid_orders_cannot_change_the_default(self):
        points = trace_points(80)
        policy = curve_policy()
        default = curve_change(points, policy=policy)
        self.assertIsNotNone(default)
        self.assertEqual(default.evidence, "supports")
        self.assertEqual(default.hinge_order, 1)
        self.assertEqual(default.curvature_change, 0)
        self.assertEqual(default, curve_change(points, policy=policy, hinge_order=1))
        for invalid in (True, False, 0, 3, 1.0, "2", None):
            with self.subTest(hinge_order=invalid):
                self.assertIsNone(
                    curve_change(points, policy=policy, hinge_order=invalid)
                )
        self.assertIsNone(
            curve_change(
                points,
                policy=policy,
                hinge_order=2,
                onset_at=points[len(points) // 2][0] + timedelta.resolution,
            )
        )
