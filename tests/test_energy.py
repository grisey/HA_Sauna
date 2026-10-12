from datetime import timedelta
import unittest
from custom_components.ha_sauna.core.energy import advance
from custom_components.ha_sauna.core.models import Energy
from custom_components.ha_sauna.core.controller import Controller
from test_foundation import T0, parameters


class EnergyTests(unittest.TestCase):
    def test_measured_power_replaces_nominal_and_includes_standby(self):
        state = advance(Energy(accounted_at=T0), T0+timedelta(hours=1),
            power_w=2000, valid_until=T0+timedelta(hours=2), heating=True, nominal_kw=parameters().values["nominal_power_kw"])
        self.assertEqual(state.total_kwh, 2)
        self.assertEqual(state.source, "measured")
        state = advance(state, T0+timedelta(hours=2), power_w=50,
            valid_until=T0+timedelta(hours=2), heating=False, nominal_kw=parameters().values["nominal_power_kw"])
        self.assertEqual(state.total_kwh, 2.05)

    def test_stale_measurement_is_split_at_validity_limit_and_marked_mixed(self):
        state = advance(Energy(accounted_at=T0), T0+timedelta(hours=1),
            power_w=2000, valid_until=T0+timedelta(minutes=30), heating=True, nominal_kw=parameters().values["nominal_power_kw"])
        self.assertEqual(state.measured_kwh, 1)
        self.assertEqual(state.estimated_kwh, parameters().values["nominal_power_kw"] / 2)
        self.assertEqual(state.source, "mixed")
        state = advance(state, T0+timedelta(hours=2), power_w=None,
            valid_until=None, heating=None, nominal_kw=parameters().values["nominal_power_kw"])
        self.assertEqual(state.total_kwh, 1 + parameters().values["nominal_power_kw"] / 2)
        self.assertEqual(state.unknown_seconds, 3600)
        self.assertEqual(state.source, "incomplete")

    def test_budget_reset_does_not_erase_session_energy_and_new_session_resets(self):
        p = parameters()
        c = Controller(p)
        reset = p.seconds("heat_reset_minutes")
        gap = p.seconds("session_gap_minutes")
        interval = timedelta(seconds=reset)
        expected_kwh = p.values["nominal_power_kw"] * reset / 3600
        c.set_operation(True, T0)
        c.report_heating(True, T0)
        c.report_heating(False, T0 + interval)
        c.advance(T0 + interval * 2)
        self.assertEqual(c.session.heating.elapsed_seconds, 0)
        self.assertAlmostEqual(c.session.energy.total_kwh, expected_kwh)
        c.report_heating(True, T0 + interval * 2)
        c.report_heating(False, T0 + interval * 3)
        self.assertAlmostEqual(c.session.energy.total_kwh, expected_kwh * 2)
        c.set_operation(False, T0 + interval * 3)
        c.set_operation(True, T0 + interval * 3 + timedelta(seconds=gap))
        self.assertEqual(c.session.energy.total_kwh, 0)
        self.assertAlmostEqual(c.completed_sessions[0].energy.total_kwh, expected_kwh * 2)
