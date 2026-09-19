import unittest
from custom_components.ha_sauna.core.power import heating, watts
from custom_components.ha_sauna.bindings import Bindings, BindingError, validate_metadata
from test_foundation import bindings, metadata


class PowerTests(unittest.TestCase):
    def test_watts_kilowatts_and_invalid_measurements(self):
        self.assertEqual(watts("6.5", "kW"), 6500)
        self.assertEqual(watts("6500", "W"), 6500)
        for value, unit in (("unknown", "W"), (-1, "W"), (True, "W"),
                            ("nan", "W"), ("inf", "kW"), (1, "kWh"), (10**400, "W")):
            with self.subTest(value=str(value), unit=unit):
                self.assertIsNone(watts(value, unit))

    def test_explicit_threshold_distinguishes_standby_and_heating(self):
        self.assertIsNone(heating(5000, None))
        self.assertIsNone(heating(None, 100))
        self.assertFalse(heating(100, 100))
        self.assertTrue(heating(101, 100))

    def test_binding_is_optional_and_accepts_only_power_units(self):
        self.assertNotIn("heater_power", bindings().values)
        b = Bindings({**bindings().as_dict(), "heater_power": "sensor.power"})
        attrs = metadata(b)
        for unit in ("W", "kW"):
            attrs["sensor.power"]["unit_of_measurement"] = unit
            validate_metadata(b, attrs)
        attrs["sensor.power"]["unit_of_measurement"] = "kWh"
        with self.assertRaises(BindingError):
            validate_metadata(b, attrs)
