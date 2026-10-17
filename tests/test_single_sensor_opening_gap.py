"""A fixed existing opening prefix cannot expire because RH stops reporting."""

from dataclasses import replace
import unittest

import test_single_sensor_closing as closing_fixture

from custom_components.ha_sauna.core.models import Position, Quantity


class SingleSensorOpeningGapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        closing_fixture.SingleSensorClosingTests.setUpClass()

    def test_pending_opening_survives_rh_silence_and_continued_own_temperature(self):
        fixture = closing_fixture.SingleSensorClosingTests(methodName="runTest")
        # Existing small_joint_dip fixture binds after exactly K falling
        # originals. Reuse that fixed prefix; do not search for a detector event.
        bound_tick = fixture.fall_start + fixture.count - 1
        detector, events, _ = fixture.run_inputs(
            positions=(Position.UPPER,), stop=bound_tick + 1,
        )
        self.assertFalse(detector.open)
        self.assertEqual(fixture.doors(events), [])
        episode = detector._position_state(Position.UPPER)["door"]
        self.assertIsNotNone(episode)
        candidate = episode.get("original_open")
        self.assertIsNotNone(
            candidate, "The existing fixed K-fall prefix must bind before confirmation",
        )
        bound_at, windows = candidate["received_at"], candidate["windows"]
        humidity = detector.latest[Position.UPPER, Quantity.HUMIDITY]
        humidity_originals = tuple(detector._curve_series[Position.UPPER, Quantity.HUMIDITY]["points"])
        support = detector._original_time_support_cache[Position.UPPER, Quantity.HUMIDITY][2]
        self.assertIsNotNone(support)
        context = detector._original_context()
        last_at = fixture.at(fixture.recovery_start - 1)
        self.assertFalse(
            support.pairing_fresh(last_at), "The unchanged falling section must exceed RH cadence",
        )
        self.assertLess(
            (last_at - humidity.measured_at).total_seconds(),
            fixture.parameters.values["sensor_timeout_seconds"],
        )
        for tick in range(bound_tick + 1, fixture.recovery_start):
            now = fixture.at(tick)
            original = detector.latest[Position.UPPER, Quantity.TEMPERATURE]
            value = fixture.pair(Position.UPPER, tick)[0]
            self.assertTrue(detector.accept(replace(
                original, value=value, raw_value=str(value), received_at=now, measured_at=now,
            )))
            events.extend(detector.advance(now, enabled=True))
            self.assertIs(detector._position_state(Position.UPPER)["door"], episode)
            self.assertIs(
                episode.get("original_open"), candidate,
                f"Opening evidence lost during unchanged RH originals at {now}",
            )
            self.assertEqual(candidate["received_at"], bound_at)
            self.assertEqual(candidate["windows"], windows)
            self.assertFalse(detector.open)
            self.assertEqual(fixture.doors(events), [])
        self.assertEqual(detector._original_context(), context)
        self.assertEqual(
            tuple(detector._curve_series[Position.UPPER, Quantity.HUMIDITY]["points"]),
            humidity_originals,
        )
        self.assertIsNotNone(detector._value(Position.UPPER, Quantity.HUMIDITY, last_at))
