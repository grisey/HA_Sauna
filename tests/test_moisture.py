from datetime import UTC, datetime, timedelta
import unittest

from custom_components.ha_sauna.core.models import Measurement, Position, Quantity
from custom_components.ha_sauna.core.moisture import (
    absolute_humidity,
    current_absolute_humidity,
    saturation_vapor_pressure,
)


NOW = datetime(2026, 9, 20, 12, tzinfo=UTC)


def measurement(position, quantity, value, received_at=NOW):
    return Measurement(position, quantity, value, str(value), f"sensor.{position.value}_{quantity.value}", received_at)


class MoistureTests(unittest.TestCase):
    def test_ashrae_formula_matches_plausible_room_and_sauna_values(self):
        self.assertAlmostEqual(saturation_vapor_pressure(20), 2338.80, places=2)
        self.assertAlmostEqual(absolute_humidity(20, 50), 8.64, places=2)
        self.assertAlmostEqual(absolute_humidity(80, 10), 29.09, places=2)

    def test_formula_rejects_invalid_inputs(self):
        self.assertEqual(absolute_humidity(20, 0), 0)
        self.assertGreater(absolute_humidity(20, 100), 0)
        with self.assertRaises(ValueError):
            absolute_humidity(20, 100.1)
        with self.assertRaises(ValueError):
            absolute_humidity(20, -0.1)
        with self.assertRaises(ValueError):
            saturation_vapor_pressure(201)

    def test_current_value_requires_the_complete_matching_source_pair(self):
        measurements = {
            "upper_temperature": measurement(Position.UPPER, Quantity.TEMPERATURE, 20),
            "upper_humidity": measurement(Position.UPPER, Quantity.HUMIDITY, 50),
            "lower_temperature": measurement(Position.LOWER, Quantity.TEMPERATURE, 30),
        }
        self.assertAlmostEqual(current_absolute_humidity(measurements, Position.UPPER, NOW, 180), 8.64, places=2)
        self.assertIsNone(current_absolute_humidity(measurements, Position.LOWER, NOW, 180))
        self.assertIsNone(current_absolute_humidity(
            {**measurements, "upper_humidity": measurement(Position.LOWER, Quantity.HUMIDITY, 50)},
            Position.UPPER, NOW, 180,
        ))

    def test_current_value_becomes_unavailable_for_invalid_stale_or_future_sources(self):
        measurements = {
            "upper_temperature": measurement(Position.UPPER, Quantity.TEMPERATURE, 20),
            "upper_humidity": measurement(Position.UPPER, Quantity.HUMIDITY, 50),
        }
        self.assertIsNone(current_absolute_humidity(
            {**measurements, "upper_humidity": measurement(Position.UPPER, Quantity.HUMIDITY, None)},
            Position.UPPER, NOW, 180,
        ))
        self.assertIsNone(current_absolute_humidity(
            {**measurements, "upper_temperature": measurement(Position.UPPER, Quantity.TEMPERATURE, 20, NOW - timedelta(seconds=181))},
            Position.UPPER, NOW, 180,
        ))
        self.assertIsNotNone(current_absolute_humidity(
            {**measurements, "upper_temperature": measurement(Position.UPPER, Quantity.TEMPERATURE, 20, NOW - timedelta(seconds=180))},
            Position.UPPER, NOW, 180,
        ))
        self.assertIsNone(current_absolute_humidity(
            {**measurements, "upper_humidity": measurement(Position.UPPER, Quantity.HUMIDITY, 50, NOW + timedelta(seconds=1))},
            Position.UPPER, NOW, 180,
        ))


if __name__ == "__main__":
    unittest.main()
