"""Synthetische Zeitreihen prüfen Kausalität, Ausfall und Quellenwechsel."""
from datetime import timedelta
import json
from pathlib import Path
import unittest

from custom_components.ha_sauna.core.detector import Detector
from custom_components.ha_sauna.core.detection_parameters import candidate_values
from custom_components.ha_sauna.core.models import Measurement, Position, Quantity
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.timeline import Kind
from test_foundation import T0, parameters


def detection_parameters(**values):
    return Parameters({**parameters().as_dict(), "sensor_timeout_seconds": 10, **values})


def measurement(pos, quantity, value, second, source=None, measured=None):
    return Measurement(pos, quantity, value, str(value), source or f"sensor.{pos}_{quantity}",
        T0 + timedelta(seconds=second), measured)


def sample(detector, second, temperature, humidity, positions=(Position.UPPER, Position.LOWER)):
    for pos in positions:
        detector.accept(measurement(pos, Quantity.TEMPERATURE, temperature, second))
        detector.accept(measurement(pos, Quantity.HUMIDITY, humidity, second))
    return detector.advance(T0 + timedelta(seconds=second), enabled=True)


def trace(second):
    if second < 70:
        return 70, 40
    if second < 85:
        return 70 - .3 * (second - 70), 40 - .08 * (second - 70)
    if second < 150:
        return 65.5, 38.8
    if second < 260:
        return 65.5 + .025 * (second - 150), 38.8 + .02 * (second - 150) + (3 if second >= 240 else 0)
    if second < 280:
        return 68.25, 44
    return 68.25 - .05 * (second - 280), 44 - .06 * (second - 280)


class DetectorTests(unittest.TestCase):
    def test_diagnostic_observer_reports_actual_metrics_without_changing_signals(self):
        observed = []
        detector = Detector(detection_parameters(), T0, observer=observed.append)
        reference = Detector(detection_parameters(), T0)
        events = []
        for i in range(401):
            current = sample(detector, i, *trace(i))
            self.assertEqual(current, sample(reference, i, *trace(i)))
            events.extend(current)
        self.assertEqual(len(observed), 401)
        self.assertEqual(observed[0]["at"], T0)
        self.assertEqual(observed[200]["metrics"]["upper"]["strong_temperature_slope"],
                         observed[200]["metrics"]["lower"]["strong_temperature_slope"])
        self.assertEqual([k for row in observed for k in row["signals"]], [e.kind for e in events])
        self.assertIsNotNone(observed[100]["metrics"]["upper"]["door_temperature_slope"])

    def test_defaults_are_exactly_the_frozen_reference_and_overrides_are_consumed(self):
        expected = json.loads((Path(__file__).resolve().parents[1] / "candidate/parameter.json").read_text())
        self.assertEqual(candidate_values(detection_parameters().values), expected)
        self.assertEqual(Detector(detection_parameters(door_open_slope=-3), T0).p["door_open_slope"], -3)

    def test_synthetic_episode_person_infusion_and_ventilation(self):
        detector = Detector(detection_parameters(), T0)
        events = []
        for i in range(401):
            events += sample(detector, i, *trace(i))
        kinds = [e.kind for e in events]
        self.assertEqual(kinds.count(Kind.DOOR_OPEN), 2)
        self.assertEqual(kinds.count(Kind.DOOR_CLOSE), 1)
        self.assertEqual(kinds.count(Kind.VENTILATION), 2)
        self.assertIn(Kind.PERSON_STRONG, kinds)
        self.assertEqual(kinds.count(Kind.INFUSION), 1)
        self.assertLess(kinds.index(Kind.PERSON_STRONG), kinds.index(Kind.INFUSION))
        self.assertTrue(all(e.effective_at <= e.detected_at for e in events))
        self.assertEqual(detector.faults, ())

    def test_future_measurements_do_not_rewrite_the_past(self):
        detector = Detector(detection_parameters(), T0)
        for i in range(20):
            sample(detector, i, 70, 40)
        detector.accept(measurement(Position.UPPER, Quantity.TEMPERATURE, 10, 30))
        detector.advance(T0 + timedelta(seconds=25), enabled=True)
        self.assertEqual(detector.frames[Position.UPPER][-1]["T"], 70)
        detector.advance(T0 + timedelta(seconds=30), enabled=True)
        self.assertEqual(detector.frames[Position.UPPER][-1]["T"], 10)
        self.assertFalse(detector.accept(measurement(Position.UPPER, Quantity.TEMPERATURE, 90, 29)))

    def test_failure_single_sensor_operation_and_rejoin(self):
        detector = Detector(detection_parameters(), T0)
        events = []
        for i in range(401):
            positions = (Position.LOWER,) if 60 <= i <= 170 else (Position.UPPER, Position.LOWER)
            events += sample(detector, i, *trace(i), positions=positions)
            if i == 80:
                self.assertEqual(detector.active_positions, (Position.LOWER,))
                self.assertIn("upper_unavailable", detector.faults)
            if i == 180:
                self.assertEqual(detector.active_positions, (Position.UPPER, Position.LOWER))
                self.assertEqual(detector.faults, ())
        opening = next(e for e in events if e.kind == Kind.DOOR_OPEN)
        self.assertEqual(opening.channels, ("lower",))
        self.assertEqual(sum(e.kind == Kind.INFUSION for e in events), 1)

    def test_source_replacement_discards_old_history_and_hold_evidence(self):
        detector = Detector(detection_parameters(), T0, (Position.UPPER,))
        for i in range(50):
            sample(detector, i, 80, 40, (Position.UPPER,))
        detector.accept(measurement(Position.UPPER, Quantity.TEMPERATURE, 20, 50, source="sensor.replaced"))
        detector.accept(measurement(Position.UPPER, Quantity.HUMIDITY, 10, 50))
        self.assertEqual(detector.advance(T0 + timedelta(seconds=50), enabled=True), [])
        self.assertEqual(len(detector.frames[Position.UPPER]), 1)
        self.assertIsNone(detector.frames[Position.UPPER][-1]["Tm"])

    def test_missing_implausible_or_old_source_timestamp_is_not_healthy(self):
        for value, measured in ((None, None), (101, None), (40, T0 - timedelta(minutes=1))):
            with self.subTest(value=value, measured=measured):
                detector = Detector(detection_parameters(), T0)
                detector.accept(measurement(Position.UPPER, Quantity.TEMPERATURE, 70, 0))
                detector.accept(measurement(Position.UPPER, Quantity.HUMIDITY, value, 0, measured=measured))
                self.assertEqual(detector.advance(T0, enabled=True), [])
                self.assertEqual(detector.active_positions, ())

    def test_report_after_tick_at_equal_timestamp_is_used_next_tick_without_rewriting_history(self):
        detector = Detector(detection_parameters(), T0, (Position.UPPER,))
        sample(detector, 0, 70, 40, (Position.UPPER,))
        self.assertTrue(detector.accept(measurement(Position.UPPER, Quantity.TEMPERATURE, 71, 0)))
        self.assertEqual(detector.frames[Position.UPPER][-1]["T"], 70)
        detector.advance(T0 + timedelta(seconds=1), enabled=True)
        self.assertEqual(detector.frames[Position.UPPER][-1]["T"], 71)
        self.assertEqual(detector.frames[Position.UPPER][-2]["T"], 70)
