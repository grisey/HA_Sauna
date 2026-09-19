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
    def test_door_with_delayed_humidity_drop_during_continuous_heating(self):
        detector = Detector(detection_parameters(), T0)
        events = []
        for i in range(80):
            detector.report_heating(True, T0 + timedelta(seconds=i))
            # Synthetische kurze Öffnung: beidseitig 1,2 °C/min, zunächst
            # sogar steigende Feuchte. Die bisherige UND-Regel reicht nicht.
            temperature = 50 if i < 20 else 50-.02*min(20,i-20)+max(0,i-40)*.03
            events += sample(detector, i, temperature, 30+i*.002)
        self.assertEqual([e.kind for e in events], [Kind.DOOR_OPEN, Kind.DOOR_CLOSE])
        self.assertGreaterEqual((events[0].detected_at-T0).total_seconds(), 25)

    def test_temperature_rule_does_not_treat_heater_off_as_door_opening(self):
        for heating, lower_falls, duration in ((False, True, 20), (None, True, 20), (True, False, 20), (True, True, 2)):
            with self.subTest(heating=heating, lower_falls=lower_falls, duration=duration):
                d = Detector(detection_parameters(), T0)
                events = []
                for i in range(70):
                    d.report_heating(heating if i >= 20 else True, T0+timedelta(seconds=i))
                    delta = -.03*(i-20) if 20 <= i < 20+duration else 0
                    for position in (Position.UPPER, Position.LOWER):
                        temp = 50+delta if position == Position.UPPER or lower_falls else 50
                        d.accept(measurement(position,Quantity.TEMPERATURE,temp,i))
                        d.accept(measurement(position,Quantity.HUMIDITY,30,i))
                    events += d.advance(T0+timedelta(seconds=i),enabled=True)
                self.assertNotIn(Kind.DOOR_OPEN,[e.kind for e in events])

    def test_additional_rule_requires_both_positions_and_restarts_proof_after_heater_change(self):
        for positions in ((Position.UPPER,), (Position.UPPER, Position.LOWER)):
            d=Detector(detection_parameters(),T0,positions)
            events=[]
            for i in range(80):
                # Wiederholtes Aus/Ein verhindert den durchgehenden Nachweis.
                d.report_heating(False if i%8==0 else True,T0+timedelta(seconds=i))
                events+=sample(d,i,50-.03*i,30,positions)
            self.assertNotIn(Kind.DOOR_OPEN,[e.kind for e in events])

    def test_hot_operation_keeps_reference_rule_and_warmup_rule_can_be_disabled(self):
        for base, limit in ((90,70),(50,0)):
            d=Detector(detection_parameters(door_heating_max_temperature_c=limit),T0)
            events=[]
            for i in range(80):
                d.report_heating(True,T0+timedelta(seconds=i))
                events+=sample(d,i,base-.03*i,30)
            self.assertNotIn(Kind.DOOR_OPEN,[e.kind for e in events])

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
