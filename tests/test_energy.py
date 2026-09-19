from dataclasses import replace
from datetime import timedelta
import unittest
from custom_components.ha_sauna.core.energy import advance
from custom_components.ha_sauna.core.models import Energy
from custom_components.ha_sauna.core.controller import Controller
from test_foundation import T0, parameters


class EnergyTests(unittest.TestCase):
    def test_measured_power_replaces_nominal_and_includes_standby(self):
        state = advance(Energy(accounted_at=T0), T0+timedelta(hours=1),
            power_w=2000, valid_until=T0+timedelta(hours=2), heating=True, nominal_kw=4.5)
        self.assertEqual(state.total_kwh, 2)
        self.assertEqual(state.source, "measured")
        state = advance(state, T0+timedelta(hours=2), power_w=50,
            valid_until=T0+timedelta(hours=2), heating=False, nominal_kw=4.5)
        self.assertEqual(state.total_kwh, 2.05)

    def test_stale_measurement_is_split_at_validity_limit_and_marked_mixed(self):
        state = advance(Energy(accounted_at=T0), T0+timedelta(hours=1),
            power_w=2000, valid_until=T0+timedelta(minutes=30), heating=True, nominal_kw=4.5)
        self.assertEqual(state.measured_kwh, 1)
        self.assertEqual(state.estimated_kwh, 2.25)
        self.assertEqual(state.source, "mixed")
        state = advance(state, T0+timedelta(hours=2), power_w=None,
            valid_until=None, heating=None, nominal_kw=4.5)
        self.assertEqual(state.total_kwh, 3.25)
        self.assertEqual(state.unknown_seconds, 3600)
        self.assertEqual(state.source, "incomplete")

    def test_budget_reset_does_not_erase_session_energy_and_new_session_resets(self):
        p = parameters()
        c = Controller(type(p)({**p.as_dict(), "heat_reset_minutes": 1,
                               "session_gap_minutes": 10, "nominal_power_kw": 4.5}))
        c.set_operation(True, T0)
        c.report_heating(True, T0)
        c.report_heating(False, T0+timedelta(seconds=60))
        c.advance(T0+timedelta(seconds=120))
        self.assertEqual(c.session.heating.elapsed_seconds, 0)
        self.assertAlmostEqual(c.session.energy.total_kwh, .075)
        c.report_heating(True, T0+timedelta(seconds=120))
        c.report_heating(False, T0+timedelta(seconds=180))
        self.assertAlmostEqual(c.session.energy.total_kwh, .15)
        c.set_operation(False, T0+timedelta(seconds=180))
        c.set_operation(True, T0+timedelta(minutes=14))
        self.assertEqual(c.session.energy.total_kwh, 0)
        self.assertAlmostEqual(c.completed_sessions[0].energy.total_kwh, .15)
