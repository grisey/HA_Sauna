from datetime import timedelta
import unittest

from custom_components.ha_sauna.core.models import ThermostatState
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.thermostat import evaluate
from test_foundation import T0, parameters


class ThermostatTests(unittest.TestCase):
    def setUp(self):
        self.parameters = Parameters({**parameters().as_dict(),
            "target_temperature_c": 80, "safety_temperature_c": 110,
            "readiness_offset_c": 5, "readiness_hysteresis_c": 3,
            "thermostat_cooldown_minutes": 1})

    def decide(self, state=None, **kwargs):
        args = dict(now=T0, parameters=self.parameters, temperature=75,
                    enabled=True, gang=False, after_run=False)
        args.update(kwargs)
        return evaluate(state or ThermostatState(), **args)

    def test_hysteresis_and_cooldown_are_separate_from_operation(self):
        state, decision = self.decide()
        self.assertTrue(decision.heat)
        state, decision = self.decide(state, temperature=80)
        self.assertTrue(decision.heat)
        state, decision = self.decide(state, temperature=85)
        self.assertFalse(decision.heat)
        self.assertEqual(state.cooldown_until, T0 + timedelta(seconds=60))
        state, decision = self.decide(state, now=T0 + timedelta(seconds=59), temperature=70)
        self.assertFalse(decision.heat)
        _, decision = self.decide(state, now=T0 + timedelta(seconds=60), temperature=70)
        self.assertTrue(decision.heat)

    def test_provisional_gang_suppresses_regular_temperature_stops(self):
        _, decision = self.decide(gang=True, temperature=100)
        self.assertTrue(decision.heat)
        self.assertEqual(decision.reason, "gang")

    def test_safety_and_explicit_off_always_override_gang(self):
        for kwargs in ({"enabled": False},
                       {"protection": ("missing_feedback",)}, {"temperature": None}):
            with self.subTest(kwargs=kwargs):
                _, decision = self.decide(gang=True, **kwargs)
                self.assertFalse(decision.heat)

    def test_default_target_uses_normal_regulation_and_missing_measurement_still_blocks(self):
        values = self.parameters.as_dict()
        del values["target_temperature_c"]
        configured = Parameters(values)
        self.assertEqual(configured.values["target_temperature_c"], 80)
        _, decision = self.decide(parameters=configured)
        self.assertTrue(decision.heat)
        _, decision = self.decide(parameters=configured, temperature=None)
        self.assertFalse(decision.heat)
        self.assertEqual(decision.reason, "upper_temperature_unavailable")

    def test_oven_cooling_overrides_even_inconsistent_gang_state(self):
        for field in ("after_run",):
            for gang in (False, True):
                with self.subTest(field=field, gang=gang):
                    _, decision = self.decide(gang=gang, **{field: True})
                    self.assertFalse(decision.heat)
                    self.assertEqual(decision.reason, "after_run")

    def test_minimum_heating_defers_regular_stop_then_starts_cooldown(self):
        state = ThermostatState(demand=True)
        state, decision = self.decide(state, now=T0 + timedelta(seconds=599),
            heating_since=T0, temperature=90)
        self.assertTrue(decision.heat)
        self.assertEqual(decision.reason, "minimum_heating")
        state, decision = self.decide(state, now=T0 + timedelta(seconds=600),
            heating_since=T0, temperature=90)
        self.assertFalse(decision.heat)
        self.assertEqual(state.cooldown_until, T0 + timedelta(seconds=660))

    def test_minimum_heating_never_delays_oven_cooling_off_or_safety(self):
        for args in ({"after_run": True}, {"enabled": False},
                     {"temperature": None},
                     {"protection": ("missing_feedback",)}):
            with self.subTest(args=args):
                _, decision = self.decide(ThermostatState(demand=True),
                    now=T0 + timedelta(seconds=60), heating_since=T0, **args)
                self.assertFalse(decision.heat)
