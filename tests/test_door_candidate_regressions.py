"""Focused counterexamples on existing inputs; real replay review remains separate."""

from datetime import timedelta
import hashlib
import json
import math
from pathlib import Path
import unittest

import test_detector as detector_fixtures
import test_original_curve_evidence as original_fixtures
from test_detector import T0, trace
from test_original_curve_evidence import CLIMATE, ORIGIN

from custom_components.ha_sauna.core.detector import Detector
from custom_components.ha_sauna.core.models import Measurement, Position, Quantity
from custom_components.ha_sauna.core.timeline import Kind


class DoorCandidateRegressionTests(unittest.TestCase):
    def setUp(self):
        # Composition reuses the declared input construction without inheriting
        # and rerunning the existing test methods.
        self.fixture = original_fixtures.OriginalCurveFollowupTests()
        self.fixture.setUp()

    def _open_door(self):
        fixture = self.fixture
        detector, events, _ = fixture._bind("opening")
        events += fixture._ramp(detector, 4 * fixture.count, fixture.fall_step)
        self.assertEqual([event.kind for event in events].count(Kind.DOOR_OPEN), 1)
        self.assertTrue(detector.open)
        return detector, events

    def test_sustained_braking_can_close_below_zero_temperature_slope(self):
        fixture = self.fixture
        for isolated_edge in (False, True):
            with self.subTest(isolated_edge=isolated_edge):
                detector, events = self._open_door()
                braking_at = fixture._at(detector.index)
                delta = tuple(
                    value / fixture.count if role.endswith("temperature") else 0
                    for role, value in zip(CLIMATE, fixture.fall_step, strict=True)
                )
                self.assertTrue(all(
                    value < 0 for role, value in zip(CLIMATE, delta, strict=True)
                    if role.endswith("temperature")
                ))
                events += fixture._ramp(detector, fixture.count, delta)
                if isolated_edge:
                    # The existing plateau regression uses the same 2.6-scale
                    # endpoint disturbance: one resolved secant is not a new
                    # sustained physical descent.
                    current = fixture._latest(detector)
                    noise = fixture.values["curve_original_noise_temperature_lower"]
                    changed = tuple(
                        value - 2.6 * noise if role == "lower_temperature" else value
                        for role, value in zip(CLIMATE, current, strict=True)
                    )
                    events += fixture._feed(detector, (changed,))
                events += fixture._ramp(detector, fixture.tail_ticks, delta)
                closes = [event for event in events if event.kind == Kind.DOOR_CLOSE]
                self.assertEqual(len(closes), 1)
                self.assertGreater(closes[0].trace_at, braking_at)
                self.assertLessEqual(closes[0].effective_at, closes[0].trace_at)
                self.assertFalse(detector.open)

    def test_smooth_open_cooling_is_not_a_discrete_closing_transition(self):
        fixture = self.fixture
        detector, events = self._open_door()
        initial = fixture._latest(detector)
        duration = fixture.tail_ticks
        # Analytic continuation with unchanged initial slope and smoothly
        # weakening cooling. The input contains no later slope discontinuity.
        states = (
            tuple(
                value + change * duration * (1 - math.exp(-tick / duration))
                if role.endswith("temperature") else value
                for role, value, change in zip(
                    CLIMATE, initial, fixture.fall_step, strict=True,
                )
            )
            for tick in range(1, duration + 1)
        )
        events += fixture._feed(detector, states)
        self.assertNotIn(Kind.DOOR_CLOSE, [event.kind for event in events])
        self.assertTrue(detector.open)

    def test_recorded_closed_door_infusions_keep_the_following_exit_available(self):
        fixture = self.fixture
        root = Path(__file__).resolve().parent
        manifest = json.loads((root / "scenario_manifest.json").read_text())
        source_path = root / manifest["source_curves"]
        payload = source_path.read_bytes()
        self.assertEqual(hashlib.sha256(payload).hexdigest(), manifest["source_curves_sha256"])
        curves = json.loads(payload)["curves"]
        detector, events = fixture._detector(), []
        elapsed = 0
        exit_at = None
        for name in ("occupied_infusion", "exit"):
            curve = curves[name]
            columns = tuple(curve["roles"].index(role) + 1 for role in CLIMATE)
            for row in curve["rows"]:
                if name == "exit" and row[0] == 0:
                    continue  # The join is the same already supplied original.
                at = ORIGIN + timedelta(seconds=elapsed + row[0])
                for role, column in zip(CLIMATE, columns, strict=True):
                    position, quantity = role.split("_", 1)
                    value = row[column]
                    self.assertTrue(detector.accept(Measurement(
                        Position(position), Quantity(quantity), value, str(value),
                        f"sensor.followup_{role}", at, at,
                    )))
                events.extend(detector.advance(at, enabled=True))
            if name == "occupied_infusion":
                self.assertNotIn(Kind.DOOR_OPEN, [event.kind for event in events])
                self.assertIn(Kind.INFUSION, [event.kind for event in events])
                elapsed += curve["duration_s"]
                exit_at = ORIGIN + timedelta(seconds=elapsed)
        opens = [event for event in events if event.kind == Kind.DOOR_OPEN]
        self.assertEqual(len(opens), 1)
        self.assertGreaterEqual(opens[0].effective_at, exit_at)
        self.assertLessEqual(opens[0].effective_at, opens[0].trace_at)

    def test_real_door_fall_after_infusion_may_remain_above_preinfusion_temperature(self):
        flow = detector_fixtures.InfusionLifecycleTests()
        flow.setUp()
        points, _, _, _ = flow._scenario(merged=False)
        peak = flow.begin + flow.width
        fall = peak + flow.width
        gain = abs(flow.temperature - trace(85)[0])
        self.assertGreater(gain, 0)
        temperatures = [
            (0, flow.temperature), (flow.begin, flow.temperature),
            (peak, flow.temperature + 2 * gain),
            (fall, flow.temperature + gain),
        ]
        detector, events = Detector(flow.parameters, T0), []
        flow._feed(detector, peak, points, events, temperatures=temperatures)
        self.assertIn(Kind.INFUSION, [event.kind for event in events])
        self.assertIsNotNone(detector.infusion_episode)
        flow._feed(detector, fall, points, events, temperatures=temperatures)
        opens = [event for event in events if event.kind == Kind.DOOR_OPEN]
        self.assertEqual(len(opens), 1)
        observed = flow._value(temperatures, (opens[0].trace_at - T0).total_seconds())
        self.assertGreater(observed, flow.temperature)
        self.assertGreaterEqual(opens[0].effective_at, flow._at(peak))
        self.assertLessEqual(opens[0].effective_at, opens[0].trace_at)


if __name__ == "__main__":
    unittest.main()
