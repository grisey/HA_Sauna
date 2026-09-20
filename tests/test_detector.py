"""Synthetische Zeitreihen prüfen Kausalität, Ausfall und Quellenwechsel."""
from datetime import timedelta
import json
from pathlib import Path
import unittest

from custom_components.ha_sauna.core.detector import Detector
from custom_components.ha_sauna.core.detection_parameters import candidate_values
from custom_components.ha_sauna.core.models import Measurement, Position, Quantity
from custom_components.ha_sauna.core.moisture import absolute_humidity, saturation_vapor_pressure, WATER_VAPOR_GAS_CONSTANT
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
    def _ventilation_detector(self, *, positions=(Position.UPPER, Position.LOWER), **overrides):
        return Detector(detection_parameters(
            median_seconds=1, door_window_seconds=2, door_humidity_seconds=2,
            door_open_slope=-1, door_open_humidity_upper=.01, door_open_humidity_lower=.01,
            door_open_hold_seconds=1, door_heating_hold_seconds=1,
            door_heating_max_temperature_c=100, vent_baseline_seconds=5,
            vent_hold_seconds=30, **overrides), T0, positions)

    @staticmethod
    def _humidity_for_water(temperature, water):
        return water * WATER_VAPOR_GAS_CONSTANT * (temperature + 273.15) * 100 / (
            saturation_vapor_pressure(temperature) * 1000)

    def _open_for_ventilation(self, detector, *, positions=(Position.UPPER, Position.LOWER),
                              value_at=None, heating=False):
        """Eine reale Türflanke mit vorangehendem Referenzfenster erzeugen."""
        events = []
        value_at = value_at or (lambda second, position: (80 - max(0, second - 9) * 2,
                                                           40 - max(0, second - 9) * 5))
        for second in range(12):
            if heating:
                detector.report_heating(True, T0 + timedelta(seconds=second))
            temperature, humidity = value_at(second, Position.UPPER)
            for position in positions:
                temperature, humidity = value_at(second, position)
                detector.accept(measurement(position, Quantity.TEMPERATURE, temperature, second))
                detector.accept(measurement(position, Quantity.HUMIDITY, humidity, second))
            events += detector.advance(T0 + timedelta(seconds=second), enabled=True)
        self.assertIn(Kind.DOOR_OPEN, [event.kind for event in events])
        return events

    def test_two_sensor_water_loss_confirms_without_the_legacy_open_duration(self):
        detector = self._ventilation_detector()
        events = self._open_for_ventilation(detector)
        events += sample(detector, 12, 74, 20)
        ventilation = next(event for event in events if event.kind is Kind.VENTILATION)
        self.assertLess((ventilation.detected_at - detector.opened_at).total_seconds(), 30)
        metric = detector.diagnostic["metrics"]["upper"]
        self.assertGreaterEqual(metric["ventilation_temperature_loss"], 3)
        self.assertGreaterEqual(metric["ventilation_absolute_humidity_loss"], .30)

    def test_single_sensor_keeps_the_legacy_open_duration(self):
        detector = self._ventilation_detector(positions=(Position.UPPER,))
        events = self._open_for_ventilation(detector, positions=(Position.UPPER,))
        for second in range(12, 42):
            events += sample(detector, second, 74, 20, (Position.UPPER,))
        ventilation = [event for event in events if event.kind is Kind.VENTILATION]
        self.assertEqual(len(ventilation), 1)
        self.assertGreaterEqual((ventilation[0].detected_at - detector.opened_at).total_seconds(), 30)

    def test_runtime_positions_confirm_one_position_available_when_the_door_opens(self):
        """Die Runtime behält beide Rollen, obwohl unten vor der Öffnung fehlt."""
        detector = Detector(parameters(), T0)
        events = []
        for second in range(360):
            temperature = 80 if second < 120 else 80 - .2 * (second - 120)
            humidity = 20 if second < 120 else 20 - .08 * (second - 120)
            detector.accept(measurement(Position.UPPER, Quantity.TEMPERATURE, temperature, second))
            detector.accept(measurement(Position.UPPER, Quantity.HUMIDITY, humidity, second))
            events += detector.advance(T0 + timedelta(seconds=second), enabled=True)
        ventilation = [event for event in events if event.kind is Kind.VENTILATION]
        self.assertEqual([event.kind for event in events].count(Kind.DOOR_OPEN), 1)
        self.assertEqual(len(ventilation), 1)
        self.assertEqual(ventilation[0].channels, ("upper",))
        self.assertGreaterEqual((ventilation[0].detected_at - detector.opened_at).total_seconds(), 60)
        self.assertIn("lower_unavailable", detector.faults)

    def test_used_single_position_source_change_cannot_confirm_the_opening(self):
        detector = Detector(parameters(), T0)
        events = []
        for second in range(360):
            temperature = 80 if second < 120 else 80 - .2 * (second - 120)
            humidity = 20 if second < 120 else 20 - .08 * (second - 120)
            source = "sensor.upper" if second < 150 else "sensor.upper_replaced"
            detector.accept(measurement(Position.UPPER, Quantity.TEMPERATURE, temperature, second, source))
            detector.accept(measurement(Position.UPPER, Quantity.HUMIDITY, humidity, second, source))
            events += detector.advance(T0 + timedelta(seconds=second), enabled=True)
        self.assertIn(Kind.DOOR_OPEN, [event.kind for event in events])
        self.assertNotIn(Kind.VENTILATION, [event.kind for event in events])
        self.assertTrue(detector.ventilation_degraded)

    def test_cooling_with_preserved_absolute_water_does_not_confirm_ventilation(self):
        water = absolute_humidity(80, 40)
        def values(second, position):
            temperature = 80 - max(0, second - 9) * 2
            return temperature, self._humidity_for_water(temperature, water)
        detector = self._ventilation_detector()
        events = self._open_for_ventilation(detector, value_at=values, heating=True)
        for second in range(12, 50):
            temperature, humidity = values(second, Position.UPPER)
            events += sample(detector, second, temperature, humidity)
        self.assertNotIn(Kind.VENTILATION, [event.kind for event in events])

    def test_stratified_water_loss_at_only_one_position_does_not_confirm_ventilation(self):
        water = absolute_humidity(80, 40)
        def values(second, position):
            temperature = 80 - max(0, second - 9) * 2
            humidity = 20 if position is Position.UPPER else self._humidity_for_water(temperature, water * 1.2)
            return temperature, humidity
        detector = self._ventilation_detector()
        events = self._open_for_ventilation(detector, value_at=values, heating=True)
        for second in range(12, 50):
            for position in (Position.UPPER, Position.LOWER):
                temperature, humidity = values(second, position)
                detector.accept(measurement(position, Quantity.TEMPERATURE, temperature, second))
                detector.accept(measurement(position, Quantity.HUMIDITY, humidity, second))
            events += detector.advance(T0 + timedelta(seconds=second), enabled=True)
        self.assertNotIn(Kind.VENTILATION, [event.kind for event in events])

    def test_post_infusion_cooling_with_time_shifted_sensor_pairs_does_not_confirm(self):
        """Versetzte, aber frische Paare dürfen Luftumwälzung nicht als Verlust lesen."""
        water = absolute_humidity(80, 40)
        def values(second):
            temperature = 80 - max(0, second - 9) * 2
            return temperature, self._humidity_for_water(temperature, water)
        detector = self._ventilation_detector(sensor_timeout_seconds=10)
        events = []
        for second in range(50):
            detector.report_heating(True, T0 + timedelta(seconds=second))
            for position in (Position.UPPER, Position.LOWER):
                observed_second = second if position is Position.UPPER else max(0, second - 2)
                temperature, humidity = values(observed_second)
                measured = T0 + timedelta(seconds=observed_second)
                detector.accept(measurement(position, Quantity.TEMPERATURE, temperature, second, measured=measured))
                detector.accept(measurement(position, Quantity.HUMIDITY, humidity, second, measured=measured))
            events += detector.advance(T0 + timedelta(seconds=second), enabled=True)
        self.assertIn(Kind.DOOR_OPEN, [event.kind for event in events])
        self.assertNotIn(Kind.VENTILATION, [event.kind for event in events])

    def test_lost_position_during_open_door_cannot_fall_back_to_single_sensor_proof(self):
        water = absolute_humidity(80, 40)
        def values(second, position):
            temperature = 80 - max(0, second - 9) * 2
            return temperature, self._humidity_for_water(temperature, water)
        detector = self._ventilation_detector(sensor_timeout_seconds=5)
        events = self._open_for_ventilation(detector, value_at=values, heating=True)
        for second in range(12, 50):
            # Oben entsteht ein starker Verlust, unten fällt dagegen aus.
            detector.accept(measurement(Position.UPPER, Quantity.TEMPERATURE, 70, second))
            detector.accept(measurement(Position.UPPER, Quantity.HUMIDITY, 10, second))
            events += detector.advance(T0 + timedelta(seconds=second), enabled=True)
        self.assertNotIn(Kind.VENTILATION, [event.kind for event in events])
        self.assertTrue(detector.ventilation_degraded)

    def test_source_replacement_during_opening_invalidates_its_reference(self):
        detector = self._ventilation_detector()
        events = self._open_for_ventilation(detector)
        detector.accept(measurement(Position.UPPER, Quantity.TEMPERATURE, 74, 12, source="sensor.replaced"))
        detector.accept(measurement(Position.UPPER, Quantity.HUMIDITY, 20, 12))
        detector.accept(measurement(Position.LOWER, Quantity.TEMPERATURE, 74, 12))
        detector.accept(measurement(Position.LOWER, Quantity.HUMIDITY, 20, 12))
        events += detector.advance(T0 + timedelta(seconds=12), enabled=True)
        self.assertNotIn(Kind.VENTILATION, [event.kind for event in events])
        self.assertNotIn(Position.UPPER, detector.baseline)

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
        # Die erste alte Temperatur-only-Lüftung verliert weniger als 30 %
        # absoluten Wassergehalt und gilt deshalb nicht mehr als Nachweis.
        self.assertEqual(kinds.count(Kind.VENTILATION), 1)
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
