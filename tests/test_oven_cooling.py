"""Focused, pure tests for the dynamic oven-cooling duration policy."""
from datetime import UTC, datetime, timedelta
from math import log
import unittest

from custom_components.ha_sauna.core.contracts import ContactorMark, ReadinessPause
from custom_components.ha_sauna.core.oven_cooling import calculate_oven_cooling

T0 = datetime(2030, 1, 1, tzinfo=UTC)


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def calculate(*, marks=(), pauses=(), now=20, start=0, **settings):
    return calculate_oven_cooling(
        contactor_history=marks,
        readiness_pauses=pauses,
        session_started_at=T0,
        window_started_at=at(start),
        at=at(now),
        **settings,
    )


class OvenCoolingPolicyTests(unittest.TestCase):
    def test_uses_analytic_exponentially_weighted_minutes(self):
        result = calculate(
            marks=(ContactorMark(at(0), True), ContactorMark(at(10), False)),
            now=10,
            oven_cooling_half_life_minutes=10,
        )
        # Integral_0^10 2**(-(10-t)/10) dt, expressed in minutes.
        expected = 10 / log(2) * (1 - 0.5)
        self.assertAlmostEqual(result.weighted_heat_minutes, expected)
        self.assertEqual(result.weighted_idle_minutes, 0)
        self.assertAlmostEqual(result.duration_seconds, (5 + expected / 2) * 60)
        self.assertEqual(result.quality, "complete")

    def test_recent_heat_has_more_effect_than_equal_old_heat(self):
        old = calculate(
            marks=(ContactorMark(at(0), True), ContactorMark(at(5), False)), now=20
        )
        recent = calculate(
            marks=(ContactorMark(at(15), True), ContactorMark(at(20), False)), now=20
        )
        self.assertGreater(recent.weighted_heat_minutes, old.weighted_heat_minutes)
        self.assertGreater(recent.duration_seconds, old.duration_seconds)

    def test_idle_is_only_confirmed_off_inside_corrected_readiness_pause(self):
        result = calculate(
            marks=(
                ContactorMark(at(0), False),
                ContactorMark(at(5), True),
                ContactorMark(at(10), False),
            ),
            pauses=(ReadinessPause(at(0), at(10)),),
            now=10,
            oven_cooling_half_life_minutes=10,
        )
        off = 10 / log(2) * (0.5**0.5 - 0.5)
        heat = 10 / log(2) * (1 - 0.5**0.5)
        self.assertAlmostEqual(result.weighted_heat_minutes, heat)
        self.assertAlmostEqual(result.weighted_idle_minutes, off)
        # Idle credit remains signed in the balance; it is not floored by itself.
        self.assertEqual(result.duration_seconds, 5 * 60)

    def test_unknown_contactor_never_credits_idle_and_marks_result_incomplete(self):
        result = calculate(
            marks=(ContactorMark(at(0), None),),
            pauses=(ReadinessPause(at(0), at(20)),),
        )
        self.assertEqual(result.weighted_heat_minutes, 0)
        self.assertEqual(result.weighted_idle_minutes, 0)
        self.assertEqual(result.duration_seconds, 5 * 60)
        self.assertEqual(result.quality, "incomplete")

    def test_old_idle_gets_less_credit_than_equal_recent_idle(self):
        early = calculate(
            marks=(ContactorMark(at(0), False),),
            pauses=(ReadinessPause(at(0), at(5)),),
            now=20,
        )
        late = calculate(
            marks=(ContactorMark(at(0), False),),
            pauses=(ReadinessPause(at(15), at(20)),),
            now=20,
        )
        expected_early = 15 / log(2) * (0.5 - 0.5 ** (20 / 15))
        expected_late = 15 / log(2) * (1 - 0.5 ** (5 / 15))
        self.assertAlmostEqual(early.weighted_idle_minutes, expected_early)
        self.assertAlmostEqual(late.weighted_idle_minutes, expected_late)
        self.assertLess(early.weighted_idle_minutes, late.weighted_idle_minutes)

    def test_overlapping_pauses_and_duplicate_marks_are_not_double_counted(self):
        result = calculate(
            marks=(
                ContactorMark(at(0), True),
                ContactorMark(at(10), False),
                ContactorMark(at(10), False),
            ),
            pauses=(ReadinessPause(at(10), at(18)), ReadinessPause(at(12), at(20))),
            now=20,
        )
        expected_idle = 15 / log(2) * (1 - 0.5 ** (10 / 15))
        self.assertAlmostEqual(result.weighted_idle_minutes, expected_idle)

    def test_window_clips_old_history_and_base_above_maximum_is_preserved(self):
        result = calculate(
            marks=(ContactorMark(at(0), True), ContactorMark(at(10), False)),
            start=5,
            now=10,
            after_run_minutes=20,
            oven_cooling_max_minutes=15,
        )
        self.assertEqual(result.window_started_at, at(5))
        self.assertGreater(result.weighted_heat_minutes, 0)
        self.assertEqual(result.maximum_minutes, 20)
        self.assertEqual(result.duration_seconds, 20 * 60)

    def test_projection_incompleteness_is_reported_without_changing_math(self):
        result = calculate(
            marks=(ContactorMark(at(0), False),),
            pauses=(ReadinessPause(at(0), at(20)),),
            readiness_complete=False,
        )
        self.assertGreater(result.weighted_idle_minutes, 0)
        self.assertEqual(result.quality, "incomplete")

    def test_invalid_window_and_policy_numbers_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "window"):
            calculate(start=10, now=5)
        with self.assertRaisesRegex(ValueError, "ratio"):
            calculate(oven_cooling_heat_idle_ratio=0)
