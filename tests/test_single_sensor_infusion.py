"""Sensor-role input variants of the existing native infusion fixture.

No mathematical result or detector state is mocked. These are provenance and
noninterference checks; physical event quality remains a replay assessment.
"""

from dataclasses import replace
import unittest

import test_detector as fixtures

from custom_components.ha_sauna.core.detector import Detector
from custom_components.ha_sauna.core.models import Position, Quantity
from custom_components.ha_sauna.core.timeline import Kind


class SingleSensorInfusionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.InfusionLifecycleTests(methodName="runTest")
        self.fixture.setUp()
        self.points, _, self.next_rise, _ = self.fixture._scenario(merged=False)

    def run_inputs(self, *, positions=(Position.UPPER, Position.LOWER),
                   upper="curve", lower="constant", upper_failure_after_end=False):
        fixture = self.fixture
        detector = Detector(fixture.parameters, fixtures.T0, positions=positions)
        events = []
        updates = []
        stop = self.next_rise + (fixture.width if upper_failure_after_end else 0)
        for second in range(0, stop, fixture.step):
            for position in positions:
                mode = upper if position is Position.UPPER else lower
                if (position is Position.UPPER and upper_failure_after_end
                        and any(update.end is not None for update in updates)):
                    mode = "unavailable"
                humidity = (fixture._value(self.points, second)
                            if mode == "curve" else fixture.baseline)
                if mode == "delayed_tail":
                    peak = fixture.begin + fixture.width
                    own_second = (second if second <= peak else
                                  peak + max(0, second - peak - fixture.width))
                    humidity = fixture._value(self.points, own_second)
                source = "sensor.infusion_" + position.value
                if mode == "source_change" and second >= fixture.begin:
                    source += "_replacement"
                for quantity, value in ((Quantity.TEMPERATURE, fixture.temperature),
                                        (Quantity.HUMIDITY, humidity)):
                    if mode == "unavailable":
                        value = None
                    report = fixtures.measurement(
                        position, quantity, value, second,
                        source=f"{source}_{quantity.value}", measured=fixture._at(second),
                    )
                    self.assertTrue(detector.accept(report))
                    if (mode == "correction" and quantity is Quantity.HUMIDITY
                            and second >= fixture.begin):
                        corrected = fixture._value(self.points, second)
                        self.assertTrue(detector.accept(replace(
                            report, value=corrected, raw_value=str(corrected),
                        )))
            events.extend(detector.advance(
                fixture._at(second), enabled=True, on_infusion_update=updates.append,
            ))
        return detector, events, updates

    @staticmethod
    def signature(events, updates):
        return (
            [(event.kind, event.effective_at, event.detected_at, event.trace_at,
              event.channels, event.door_close_evidence) for event in events],
            [(update.confirmed_detection.effective_at, update.end) for update in updates],
        )

    def assert_completed_first_infusion(self, detector, events, updates):
        infusions = [event for event in events if event.kind is Kind.INFUSION]
        self.assertEqual(len(infusions), 1)
        self.assertTrue(updates)
        self.assertTrue(all(update.confirmed_detection is infusions[0] for update in updates))
        self.assertIsNotNone(updates[-1].end)
        self.assertLess(updates[-1].end.confirmed_at, self.fixture._at(self.next_rise))
        episode = detector.infusion_episode
        self.assertIsNotNone(episode)
        self.assertEqual(episode.phase, "ended")
        self.assertIs(episode.confirmed_detection, infusions[0])

    def test_clear_upper_infusion_and_end_ignore_unused_lower_changes(self):
        control, events, updates = self.run_inputs(positions=(Position.UPPER,))
        self.assert_completed_first_infusion(control, events, updates)
        expected = self.signature(events, updates)
        for lower in ("constant", "unavailable", "source_change", "correction"):
            with self.subTest(lower=lower):
                detector, actual, changes = self.run_inputs(lower=lower)
                self.assert_completed_first_infusion(detector, actual, changes)
                self.assertEqual(self.signature(actual, changes), expected)
                self.assertEqual(detector.positions, (Position.UPPER, Position.LOWER))
                self.assertEqual(detector.infusion_episode.positions, (Position.UPPER,))

    def test_lower_peak_cannot_start_an_infusion_with_idle_available_upper(self):
        own, events, _ = self.run_inputs(positions=(Position.LOWER,), lower="curve")
        self.assertEqual(len([event for event in events if event.kind is Kind.INFUSION]), 1)
        self.assertEqual(own.infusion_episode.positions, (Position.LOWER,))
        detector, actual, _ = self.run_inputs(upper="constant", lower="curve")
        self.assertEqual([event for event in actual if event.kind is Kind.INFUSION], [])
        self.assertEqual(detector.active_positions, (Position.UPPER, Position.LOWER))

    def test_unavailable_upper_uses_complete_lower_pair_with_its_own_times(self):
        control, expected, expected_updates = self.run_inputs(positions=(Position.LOWER,), lower="curve")
        self.assert_completed_first_infusion(control, expected, expected_updates)
        detector, actual, actual_updates = self.run_inputs(upper="unavailable", lower="curve")
        self.assert_completed_first_infusion(detector, actual, actual_updates)
        self.assertEqual(self.signature(actual, actual_updates),
                         self.signature(expected, expected_updates))
        self.assertEqual(detector.infusion_episode.positions, (Position.LOWER,))

    def test_later_upper_failure_cannot_extend_its_already_confirmed_infusion_end(self):
        # The secondary uses the existing pulse with its tail shifted by one
        # fixture width. Once the primary has supplied a real end, its outage
        # must not let this unused tail rewrite that already booked decision.
        detector, events, updates = self.run_inputs(lower="delayed_tail", upper_failure_after_end=True)
        self.assert_completed_first_infusion(detector, events, updates)
        infusions = [event for event in events if event.kind is Kind.INFUSION]
        self.assertEqual(len(infusions), 1)
        self.assertEqual(infusions[0].channels, (Position.UPPER.value,))
        owned = [update for update in updates if update.confirmed_detection is infusions[0]]
        self.assertTrue(owned)
        self.assertIsNotNone(owned[0].end)
        self.assertTrue(all(update.end == owned[0].end for update in owned))
        _, _, lower_updates = self.run_inputs(
            positions=(Position.LOWER,), lower="delayed_tail", upper_failure_after_end=True,
        )
        self.assertTrue(lower_updates)
        self.assertIsNotNone(lower_updates[-1].end)
        self.assertGreater(lower_updates[-1].end.confirmed_at, owned[0].end.confirmed_at)


if __name__ == "__main__":
    unittest.main()
