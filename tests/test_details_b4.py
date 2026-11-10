"""B4a Details: Werte je Messposition (D5) und entfallene Achsskala-Einstellungen."""

import unittest
from datetime import UTC, datetime

from custom_components.ha_sauna.appearance import (
    default_appearance,
    validate_appearance,
)
from custom_components.ha_sauna.core.defaults import (
    RETIRED_PREFERENCE_KEYS,
    load_catalog,
    preference_values,
)
from custom_components.ha_sauna.core.display import position_values
from custom_components.ha_sauna.core.models import Measurement, Position, Quantity
from custom_components.ha_sauna.core.moisture import absolute_humidity

T0 = datetime(2026, 11, 10, 18, 0, tzinfo=UTC)


def measurement(position, quantity, value):
    return Measurement(Position(position), Quantity(quantity), value, str(value),
                       f"sensor.{position}_{quantity}", T0)


class PositionValueTests(unittest.TestCase):
    def test_values_validity_and_difference_upper_minus_lower(self):
        result = position_values(
            [measurement("upper", "temperature", 85.0), measurement("upper", "humidity", 12.0),
             measurement("lower", "temperature", 60.0), measurement("lower", "humidity", 20.0)],
            {"upper_temperature": {"state": "current"}, "upper_humidity": {"state": "stale"},
             "lower_temperature": {"state": "current"}, "lower_humidity": {"state": "current"}},
        )
        upper, lower = result["positions"]
        self.assertEqual((upper["position"], lower["position"]), ("upper", "lower"))
        self.assertEqual(upper["temperature"], 85.0)
        self.assertEqual(upper["humidity_state"], "stale")
        self.assertAlmostEqual(upper["absolute_humidity"], absolute_humidity(85.0, 12.0))
        self.assertAlmostEqual(result["difference"]["temperature"], 25.0)
        self.assertAlmostEqual(
            result["difference"]["absolute_humidity"],
            absolute_humidity(85.0, 12.0) - absolute_humidity(60.0, 20.0),
        )

    def test_missing_values_stay_missing_not_zero(self):
        result = position_values(
            [measurement("upper", "temperature", 85.0), measurement("lower", "temperature", None)],
        )
        upper, lower = result["positions"]
        self.assertIsNone(upper["absolute_humidity"])
        self.assertIsNone(lower["temperature"])
        self.assertIsNone(upper["temperature_state"])
        self.assertEqual(result["difference"], {"temperature": None, "absolute_humidity": None})

    def test_single_position_has_no_difference(self):
        result = position_values([measurement("upper", "temperature", 70.0)])
        self.assertEqual([p["position"] for p in result["positions"]], ["upper"])
        self.assertIsNone(result["difference"])
        self.assertEqual(position_values([]),
                         {"positions": [], "difference": None, "valid": False})


class RetiredPreferenceTests(unittest.TestCase):
    def test_retired_axis_preferences_left_the_catalog(self):
        preferences = load_catalog()["appearance"]["preferences"]
        self.assertEqual(len(RETIRED_PREFERENCE_KEYS), 5)
        self.assertFalse(RETIRED_PREFERENCE_KEYS & preferences.keys())
        self.assertFalse(RETIRED_PREFERENCE_KEYS & default_appearance()["preferences"].keys())

    def test_stored_appearance_with_retired_values_still_loads(self):
        stored = default_appearance()
        stored["preferences"] = {
            **stored["preferences"],
            "history_temperature_padding_c": 2,
            "history_temperature_scale_step_c": 10,
            "history_humidity_padding_percent": 2,
            "history_humidity_scale_step_percent": 20,
            "history_humidity_minimum_maximum_percent": 60,
        }
        loaded = validate_appearance(stored)
        self.assertFalse(RETIRED_PREFERENCE_KEYS & loaded["preferences"].keys())
        self.assertEqual(loaded["preferences"], preference_values({}))

    def test_other_unknown_preferences_are_still_rejected(self):
        with self.assertRaises(ValueError):
            preference_values({"history_unknown_padding": 1})


if __name__ == "__main__":
    unittest.main()
