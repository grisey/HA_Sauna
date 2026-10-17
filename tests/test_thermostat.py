from datetime import timedelta
import unittest

from custom_components.ha_sauna.core.contracts import ControlInputs
from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.models import ThermostatState
from custom_components.ha_sauna.core.parameters import BY_KEY, Parameters
from custom_components.ha_sauna.core.thermostat import evaluate, temperature_limits
from test_foundation import T0, parameters


class ThermostatTests(unittest.TestCase):
    def setUp(self):
        self.parameters = parameters()
        self.target = self.parameters.values["target_temperature_c"]
        # These expectations use the stated arithmetic, not the policy helper.
        self.lower = self.target - self.parameters.values["readiness_hysteresis_c"]
        self.upper = self.target + self.parameters.values["readiness_offset_c"]
        self.cooldown = self.parameters.seconds("thermostat_cooldown_minutes")
        self.minimum_heat = self.parameters.seconds("minimum_heating_minutes")

    def decide(self, state=None, **kwargs):
        args = dict(now=T0, parameters=self.parameters, temperature=self.lower - 1,
                    enabled=True, gang=False, after_run=False)
        args.update(kwargs)
        return evaluate(state or ThermostatState(), **args)

    def test_pure_hysteresis_ignores_session_inputs_and_all_heating_timers(self):
        state = ThermostatState(demand=True, cooldown_until=T0 + timedelta(hours=1))
        state, decision = self.decide(
            state, pure_hysteresis=True, temperature=self.upper, heating_since=T0,
            heating_active=True,
            inputs=ControlInputs(gang_heat_demand=True, temporary_door_heat=True),
        )
        self.assertFalse(decision.heat)
        self.assertIsNone(state.cooldown_until)
        state, decision = self.decide(
            state, pure_hysteresis=True, temperature=self.lower,
            inputs=ControlInputs(cooling=True),
        )
        self.assertTrue(decision.heat)
        self.assertIsNone(state.cooldown_until)

    def test_pure_hysteresis_retains_interlocks(self):
        for args in ({"protection": ("fault",)}, {"inhibits": ("fault",)},
                     {"temperature": None}, {"temperature": float("nan")},
                     {"temperature": float("inf")}, {"enabled": False}):
            with self.subTest(args=args):
                _, decision = self.decide(
                    ThermostatState(demand=True), pure_hysteresis=True, **args
                )
                self.assertFalse(decision.heat)

    def test_hysteresis_and_cooldown_are_separate_from_operation(self):
        state, decision = self.decide()
        self.assertTrue(decision.heat)
        state, decision = self.decide(state, temperature=self.target)
        self.assertTrue(decision.heat)
        state, decision = self.decide(state, temperature=self.upper)
        self.assertFalse(decision.heat)
        self.assertEqual(state.cooldown_until, T0 + timedelta(seconds=self.cooldown))
        state, decision = self.decide(state, now=T0 + timedelta(seconds=self.cooldown - 1), temperature=self.lower - 1)
        self.assertFalse(decision.heat)
        _, decision = self.decide(state, now=T0 + timedelta(seconds=self.cooldown), temperature=self.lower - 1)
        self.assertTrue(decision.heat)

    def test_default_limits_switch_at_setpoint_offsets_and_hold_inside_band(self):
        configured = self.parameters
        target = configured.values["target_temperature_c"]
        lower = target - configured.values["readiness_hysteresis_c"]
        upper = target + configured.values["readiness_offset_c"]
        self.assertEqual(temperature_limits(target, configured), (lower, upper))
        controller = Controller(configured)
        self.assertEqual(controller.thermostat_restart_temperature, lower)
        self.assertEqual(controller.thermostat_target, upper)
        state, decision = self.decide(parameters=configured, temperature=lower + .01)
        self.assertFalse(decision.heat)
        self.assertEqual(decision.reason, "hysteresis_band")
        state, decision = self.decide(state, parameters=configured, temperature=lower)
        self.assertTrue(decision.heat)
        for temperature in (target, upper - .01):
            state, decision = self.decide(state, parameters=configured, temperature=temperature)
            self.assertTrue(decision.heat)
            self.assertEqual(decision.reason, "hysteresis_band")
        state, decision = self.decide(state, parameters=configured, temperature=upper)
        self.assertFalse(decision.heat)
        after_pause = T0 + timedelta(seconds=self.cooldown)
        for temperature in (upper - .01, target, lower + .01):
            state, decision = self.decide(state, parameters=configured, temperature=temperature, now=after_pause)
            self.assertFalse(decision.heat)
        _, decision = self.decide(state, parameters=configured, temperature=lower, now=after_pause)
        self.assertTrue(decision.heat)

    def test_upper_offset_does_not_move_the_lower_setpoint_threshold(self):
        lower, upper = temperature_limits(self.target, self.parameters)
        self.assertEqual((lower, upper), (self.lower, self.upper))
        _, decision = self.decide(temperature=self.target)
        self.assertFalse(decision.heat)
        _, decision = self.decide(temperature=self.lower)
        self.assertTrue(decision.heat)
        self.assertEqual(temperature_limits(None, self.parameters), (None, None))

    def test_explicit_zero_lower_distance_restarts_at_setpoint(self):
        configured = Parameters({**self.parameters.as_dict(), "readiness_hysteresis_c": 0})
        target = configured.values["target_temperature_c"]
        self.assertEqual(temperature_limits(target, configured)[0], target)
        _, decision = self.decide(parameters=configured, temperature=target + .01)
        self.assertFalse(decision.heat)
        _, decision = self.decide(parameters=configured, temperature=target)
        self.assertTrue(decision.heat)

    def test_live_gang_demand_starts_heat_even_above_the_upper_cutoff(self):
        _, decision = self.decide(gang=True, temperature=self.upper + 1)
        self.assertTrue(decision.heat)
        self.assertEqual(decision.reason, "gang_heat_demand")

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
        self.assertEqual(configured.values["target_temperature_c"], BY_KEY["target_temperature_c"].default)
        _, decision = self.decide(
            parameters=configured,
            temperature=(configured.values["target_temperature_c"]
                         - configured.values["readiness_hysteresis_c"] - 1),
        )
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
        state, decision = self.decide(state, now=T0 + timedelta(seconds=self.minimum_heat - 1),
            heating_since=T0, temperature=self.upper + 1)
        self.assertTrue(decision.heat)
        self.assertEqual(decision.reason, "minimum_heating")
        state, decision = self.decide(state, now=T0 + timedelta(seconds=self.minimum_heat),
            heating_since=T0, temperature=self.upper + 1)
        self.assertFalse(decision.heat)
        self.assertEqual(state.cooldown_until, T0 + timedelta(seconds=self.minimum_heat + self.cooldown))

    def test_minimum_heating_never_delays_oven_cooling_off_or_safety(self):
        for args in ({"after_run": True}, {"enabled": False},
                     {"temperature": None},
                     {"protection": ("missing_feedback",)}):
            with self.subTest(args=args):
                _, decision = self.decide(ThermostatState(demand=True),
                    now=T0 + timedelta(seconds=self.cooldown), heating_since=T0, **args)
                self.assertFalse(decision.heat)

    def test_live_gang_demand_is_a_positive_continuous_on_input(self):
        for temperature in (self.lower - 1, self.target, self.upper + 1):
            _, decision = self.decide(
                inputs=ControlInputs(gang_heat_demand=True), temperature=temperature
            )
            self.assertTrue(decision.heat)
            self.assertEqual(decision.reason, "gang_heat_demand")

    def test_gang_demand_bypasses_cooldown_until_the_live_level_clears(self):
        waiting = ThermostatState(cooldown_until=T0 + timedelta(seconds=self.cooldown))
        state, decision = self.decide(
            waiting, temperature=self.upper + 1, inputs=ControlInputs(gang_heat_demand=True)
        )
        self.assertTrue(decision.heat)
        self.assertEqual(decision.reason, "gang_heat_demand")
        _, decision = self.decide(state, temperature=self.upper + 1, inputs=ControlInputs())
        self.assertFalse(decision.heat)
        self.assertEqual(decision.reason, "temperature_reached")

    def test_temporary_door_level_bypasses_upper_cutoff_and_cooldown(self):
        waiting = ThermostatState(cooldown_until=T0 + timedelta(seconds=self.cooldown))
        state, decision = self.decide(
            waiting, temperature=self.upper + 1, inputs=ControlInputs(temporary_door_heat=True)
        )
        self.assertTrue(decision.heat)
        self.assertEqual(decision.reason, "temporary_door_heat")
        self.assertIsNone(state.cooldown_until)

    def test_priority_is_cooling_then_gang_then_temporary_door_heat(self):
        _, decision = self.decide(
            temperature=self.upper + 1,
            inputs=ControlInputs(
                gang_heat_demand=True, temporary_door_heat=True, cooling=True
            ),
        )
        self.assertFalse(decision.heat)
        self.assertEqual(decision.reason, "after_run")
        _, decision = self.decide(
            temperature=self.upper + 1,
            inputs=ControlInputs(gang_heat_demand=True, temporary_door_heat=True),
        )
        self.assertTrue(decision.heat)
        self.assertEqual(decision.reason, "gang_heat_demand")

    def test_invalid_critical_temperature_beats_every_positive_demand(self):
        _, decision = self.decide(
            temperature=None,
            inputs=ControlInputs(gang_heat_demand=True, temporary_door_heat=True),
        )
        self.assertFalse(decision.heat)
        self.assertEqual(decision.reason, "upper_temperature_unavailable")

    def test_door_pulse_preserves_feedback_minimum_and_superior_blocks(self):
        running = ThermostatState(demand=True)
        _, decision = self.decide(running, temperature=self.upper + 1, heating_since=T0,
            inputs=ControlInputs(temporary_door_heat=True))
        self.assertEqual(decision.reason, "temporary_door_heat")
        for kwargs in ({"enabled": False}, {"protection": ("fault",)},
                       {"inhibits": ("fault",)}, {"temperature": None},
                       {"inputs": ControlInputs(temporary_door_heat=True, cooling=True)}):
            args = {"inputs": ControlInputs(temporary_door_heat=True), **kwargs}
            _, decision = self.decide(**args)
            self.assertFalse(decision.heat)
