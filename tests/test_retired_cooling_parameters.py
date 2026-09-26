"""Legacy cooling settings load without affecting active regulation."""
from datetime import UTC, datetime
import unittest

from custom_components.ha_sauna.core.models import ThermostatState
from custom_components.ha_sauna.core.parameters import (
    BY_KEY,
    EDITABLE_DEFINITIONS,
    LEGACY_COOLING_KEYS,
    Parameters,
)
from custom_components.ha_sauna.core.thermostat import evaluate


class RetiredCoolingParameterTests(unittest.TestCase):
    def test_saved_cooling_values_are_accepted_but_discarded(self):
        saved = dict.fromkeys(LEGACY_COOLING_KEYS, 0)
        saved.update(after_run_minutes=11, cooling_brightness_percent=9,
                     heat_reset_minutes=17)
        parameters = Parameters(saved)
        self.assertTrue(LEGACY_COOLING_KEYS.isdisjoint(parameters.as_dict()))
        self.assertTrue(LEGACY_COOLING_KEYS.isdisjoint(BY_KEY))
        self.assertTrue(LEGACY_COOLING_KEYS.isdisjoint(
            definition.key for definition in EDITABLE_DEFINITIONS))
        self.assertEqual(parameters.seconds("after_run_minutes"), 660)
        self.assertEqual(parameters.seconds("heat_reset_minutes"), 1020)
        self.assertEqual(parameters.values["cooling_brightness_percent"], 9)
        self.assertEqual(Parameters(parameters.as_dict()), parameters)

    def test_retired_values_do_not_gate_thermostat(self):
        baseline = Parameters({})
        obsolete = Parameters({key: -100 for key in LEGACY_COOLING_KEYS})
        self.assertEqual(obsolete, baseline)
        args = dict(now=datetime(2026, 1, 1, tzinfo=UTC), temperature=70,
                    enabled=True, gang=False, after_run=False)
        self.assertEqual(evaluate(ThermostatState(), parameters=obsolete, **args),
                         evaluate(ThermostatState(), parameters=baseline, **args))
        self.assertTrue(evaluate(ThermostatState(), parameters=baseline,
                                 **args)[1].heat)
        args["after_run"] = True
        self.assertFalse(evaluate(ThermostatState(), parameters=baseline,
                                  **args)[1].heat)
