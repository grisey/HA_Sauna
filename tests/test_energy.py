from dataclasses import replace
from datetime import timedelta
import json
import unittest
from custom_components.ha_sauna.core.energy import advance, energy_for_interval
from custom_components.ha_sauna.core.models import Energy, EnergyInterval
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
        self.assertEqual(state.intervals, (
            EnergyInterval(T0, T0+timedelta(hours=1), "measured", 2),
            EnergyInterval(T0+timedelta(hours=1), T0+timedelta(hours=2),
                           "measured", .05),
        ))
        self.assertEqual((state.measured_seconds, state.estimated_seconds,
                          state.unknown_seconds), (7200, 0, 0))

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
        self.assertEqual(state.intervals, (
            EnergyInterval(T0, T0+timedelta(minutes=30), "measured", 2),
            EnergyInterval(T0+timedelta(minutes=30), T0+timedelta(hours=1),
                           "estimated", parameters().values["nominal_power_kw"]),
            EnergyInterval(T0+timedelta(hours=1), T0+timedelta(hours=2),
                           "unknown", None),
        ))

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
        self.assertEqual(c.session.energy.intervals, (
            EnergyInterval(T0, T0+interval, "estimated", p.values["nominal_power_kw"]),
            EnergyInterval(T0+interval, T0+interval*2, "estimated", 0),
        ))
        c.report_heating(True, T0 + interval * 2)
        c.report_heating(False, T0 + interval * 3)
        self.assertAlmostEqual(c.session.energy.total_kwh, expected_kwh * 2)
        c.set_operation(False, T0 + interval * 3)
        c.set_operation(True, T0 + interval * 3 + timedelta(seconds=gap))
        self.assertEqual(c.session.energy.total_kwh, 0)
        self.assertAlmostEqual(c.completed_sessions[0].energy.total_kwh, expected_kwh * 2)
        restarted_at = T0+interval*3+timedelta(seconds=gap)
        self.assertEqual(c.session.energy, Energy(accounted_at=restarted_at))
        completed = c.completed_sessions[0]
        self.assertEqual(completed.energy.intervals, (
            EnergyInterval(T0, T0+interval, "estimated", p.values["nominal_power_kw"]),
            EnergyInterval(T0+interval, T0+interval*2, "estimated", 0),
            EnergyInterval(T0+interval*2, T0+interval*3,
                           "estimated", p.values["nominal_power_kw"]),
            EnergyInterval(T0+interval*3, restarted_at, "estimated", 0),
        ))
        self.assertEqual(completed.energy.estimated_seconds, reset*3+gap)
        self.assertEqual(completed.energy.accounted_at, completed.ended_at)

    def test_controller_power_expiry_records_independent_fallback(self):
        p = parameters()
        nominal_kw = p.values["nominal_power_kw"]
        measured_kw = nominal_kw / 3
        ttl = p.values["sensor_timeout_seconds"]
        expiry = T0 + timedelta(seconds=ttl)
        ended_at = expiry + timedelta(seconds=ttl / 2)
        for fallback in (True, False, None):
            with self.subTest(fallback=fallback):
                c = Controller(p)
                c.begin_session("expiry", T0)
                c.report_fallback_heating(fallback, T0)
                c.report_heating(True, T0)
                c.report_power(measured_kw * 1000, expiry, T0)
                c.advance(ended_at)
                state = c.session.energy
                self.assertAlmostEqual(state.measured_kwh, measured_kw * ttl / 3600)
                self.assertAlmostEqual(
                    state.estimated_kwh,
                    nominal_kw * ttl / 2 / 3600 if fallback is True else 0,
                )
                self.assertEqual(
                    (state.measured_seconds, state.estimated_seconds,
                     state.unknown_seconds),
                    (ttl, ttl / 2 if fallback is not None else 0,
                     ttl / 2 if fallback is None else 0),
                )
                self.assertEqual(
                    state.source, "incomplete" if fallback is None else "mixed",
                )
                self.assertEqual(state.intervals, (
                    EnergyInterval(T0, expiry, "measured", measured_kw),
                    EnergyInterval(
                        expiry, ended_at,
                        "unknown" if fallback is None else "estimated",
                        None if fallback is None else nominal_kw if fallback else 0,
                    ),
                ))

    def test_zero_rates_keep_measured_off_and_unknown_sources(self):
        p = parameters()
        nominal_kw = p.values["nominal_power_kw"]
        seconds = p.values["sensor_timeout_seconds"] / 4
        span = timedelta(seconds=seconds)
        state = advance(
            Energy(accounted_at=T0), T0 + span, power_w=0,
            valid_until=T0 + span, heating=False, nominal_kw=nominal_kw,
        )
        state = advance(
            state, T0 + span * 2, power_w=None, valid_until=None,
            heating=False, nominal_kw=nominal_kw,
        )
        state = advance(
            state, T0 + span * 3, power_w=None, valid_until=None,
            heating=None, nominal_kw=nominal_kw,
        )
        self.assertEqual(state.intervals, (
            EnergyInterval(T0, T0 + span, "measured", 0),
            EnergyInterval(T0 + span, T0 + span * 2, "estimated", 0),
            EnergyInterval(T0 + span * 2, T0 + span * 3, "unknown", None),
        ))
        self.assertEqual(state.total_kwh, 0)
        self.assertEqual(state.source, "incomplete")
        self.assertEqual(
            (state.measured_seconds, state.estimated_seconds, state.unknown_seconds),
            (seconds, seconds, seconds),
        )
        for index, source in enumerate(("measured", "estimated", "incomplete")):
            with self.subTest(source=source):
                selected = energy_for_interval(
                    state, T0 + span * index, T0 + span * (index + 1),
                )
                self.assertIsNotNone(selected)
                self.assertEqual(selected.source, source)
                self.assertEqual(selected.total_kwh, 0)
                self.assertEqual(
                    (selected.measured_seconds, selected.estimated_seconds,
                     selected.unknown_seconds),
                    tuple(seconds if i == index else 0 for i in range(3)),
                )

    def test_equal_inputs_coalesce_across_irregular_advances(self):
        p = parameters()
        nominal_kw = p.values["nominal_power_kw"]
        measured_kw = nominal_kw / 2
        ttl = p.values["sensor_timeout_seconds"]
        ended_at = T0 + timedelta(seconds=ttl)
        state = Energy(accounted_at=T0)
        for fraction in (1 / 8, 3 / 8, 1):
            state = advance(
                state, T0 + timedelta(seconds=ttl * fraction),
                power_w=measured_kw * 1000, valid_until=ended_at,
                heating=True, nominal_kw=nominal_kw,
            )
        self.assertEqual(state.intervals, (
            EnergyInterval(T0, ended_at, "measured", measured_kw),
        ))
        self.assertAlmostEqual(state.total_kwh, measured_kw * ttl / 3600)
        self.assertEqual(state.measured_seconds, ttl)
        self.assertEqual(state.estimated_seconds, 0)
        selected = energy_for_interval(
            state, T0 + timedelta(seconds=ttl / 4),
            T0 + timedelta(seconds=ttl * 3 / 4),
        )
        self.assertIsNotNone(selected)
        self.assertAlmostEqual(selected.total_kwh, measured_kw * ttl / 2 / 3600)
        self.assertEqual(selected.measured_seconds, ttl / 2)

    def test_nonuniform_slice_and_adjacent_parts_use_original_rates(self):
        p = parameters()
        nominal_kw = p.values["nominal_power_kw"]
        seconds = p.values["sensor_timeout_seconds"] / 2
        span = timedelta(seconds=seconds)
        low_kw, high_kw = nominal_kw / 4, nominal_kw / 2
        ended_at = T0 + span * 3
        state = advance(
            Energy(accounted_at=T0), T0 + span, power_w=low_kw * 1000,
            valid_until=ended_at, heating=False, nominal_kw=nominal_kw,
        )
        state = advance(
            state, ended_at, power_w=high_kw * 1000,
            valid_until=ended_at, heating=False, nominal_kw=nominal_kw,
        )
        self.assertAlmostEqual(
            state.total_kwh, (low_kw * seconds + high_kw * seconds * 2) / 3600,
        )
        started_at = T0 + span / 4
        split_at, selected_end = T0 + span, T0 + span * 7 / 4
        selected = energy_for_interval(state, started_at, selected_end)
        left = energy_for_interval(state, started_at, split_at)
        right = energy_for_interval(state, split_at, selected_end)
        self.assertIsNotNone(selected)
        self.assertIsNotNone(left)
        self.assertIsNotNone(right)
        piece_seconds = seconds * 3 / 4
        expected = (low_kw + high_kw) * piece_seconds / 3600
        self.assertAlmostEqual(selected.measured_kwh, expected)
        self.assertAlmostEqual(left.measured_kwh, low_kw * piece_seconds / 3600)
        self.assertAlmostEqual(right.measured_kwh, high_kw * piece_seconds / 3600)
        self.assertEqual(
            (selected.measured_seconds, selected.estimated_seconds,
             selected.unknown_seconds), (piece_seconds * 2, 0, 0),
        )
        self.assertEqual(selected.accounted_at, selected_end)
        self.assertEqual(selected.intervals, (
            EnergyInterval(started_at, split_at, "measured", low_kw),
            EnergyInterval(split_at, selected_end, "measured", high_kw),
        ))
        self.assertAlmostEqual(left.total_kwh + right.total_kwh, expected)
        self.assertNotAlmostEqual(selected.total_kwh, state.total_kwh / 2)
        self.assertEqual(state.intervals, (
            EnergyInterval(T0, split_at, "measured", low_kw),
            EnergyInterval(split_at, ended_at, "measured", high_kw),
        ))

    def test_first_anchor_zero_span_and_reverse_time(self):
        p = parameters()
        nominal_kw = p.values["nominal_power_kw"]
        span = timedelta(seconds=p.values["sensor_timeout_seconds"] / 2)
        state = advance(
            Energy(), T0, power_w=nominal_kw * 1000,
            valid_until=T0 + span, heating=True, nominal_kw=nominal_kw,
        )
        self.assertEqual(state, Energy(accounted_at=T0))
        self.assertEqual(advance(
            state, T0, power_w=None, valid_until=None,
            heating=None, nominal_kw=nominal_kw,
        ), state)
        for evidence in (state, {}):
            with self.subTest(evidence=evidence):
                self.assertEqual(
                    energy_for_interval(evidence, T0, T0), Energy(accounted_at=T0),
                )
                with self.assertRaises(ValueError):
                    energy_for_interval(evidence, T0, T0 - span)
        with self.assertRaises(ValueError):
            advance(
                state, T0 - span, power_w=None, valid_until=None,
                heating=None, nominal_kw=nominal_kw,
            )

    def test_original_unknown_differs_from_missing_and_gapped_coverage(self):
        p = parameters()
        nominal_kw = p.values["nominal_power_kw"]
        seconds = p.values["sensor_timeout_seconds"] / 4
        span = timedelta(seconds=seconds)
        unknown = advance(
            Energy(accounted_at=T0), T0 + span, power_w=None, valid_until=None,
            heating=None, nominal_kw=nominal_kw,
        )
        selected = energy_for_interval(unknown, T0, T0 + span)
        self.assertIsNotNone(selected)
        self.assertEqual(selected.unknown_seconds, seconds)
        self.assertEqual(selected.total_kwh, 0)
        self.assertEqual(selected.source, "incomplete")
        for evidence in (
            Energy(accounted_at=T0 + span), replace(unknown, intervals=()),
            {"unknown_seconds": seconds, "accounted_at": (T0 + span).isoformat()},
        ):
            with self.subTest(evidence=evidence):
                self.assertIsNone(energy_for_interval(evidence, T0, T0 + span))
        for started_at, ended_at in (
            (T0 - span, T0 + span), (T0, T0 + span * 2),
        ):
            with self.subTest(started_at=started_at, ended_at=ended_at):
                self.assertIsNone(energy_for_interval(unknown, started_at, ended_at))
        # Missing middle evidence remains a gap, even with equal surrounding rates.
        prefix = advance(
            Energy(accounted_at=T0), T0 + span, power_w=nominal_kw * 1000,
            valid_until=T0 + span * 3, heating=True, nominal_kw=nominal_kw,
        )
        gapped = advance(
            replace(prefix, accounted_at=T0 + span * 2), T0 + span * 3,
            power_w=nominal_kw * 1000, valid_until=T0 + span * 3,
            heating=True, nominal_kw=nominal_kw,
        )
        self.assertEqual(gapped.intervals, (
            EnergyInterval(T0, T0 + span, "measured", nominal_kw),
            EnergyInterval(T0 + span * 2, T0 + span * 3, "measured", nominal_kw),
        ))
        self.assertIsNone(energy_for_interval(gapped, T0, T0 + span * 3))

    def test_iso_mapping_matches_owner_slice_and_honors_accounted_bound(self):
        p = parameters()
        nominal_kw = p.values["nominal_power_kw"]
        measured_kw = nominal_kw / 3
        seconds = p.values["sensor_timeout_seconds"] / 2
        span = timedelta(seconds=seconds)
        state = advance(
            Energy(accounted_at=T0), T0 + span * 2,
            power_w=measured_kw * 1000, valid_until=T0 + span,
            heating=True, nominal_kw=nominal_kw,
        )
        state = advance(
            state, T0 + span * 3, power_w=None, valid_until=None,
            heating=None, nominal_kw=nominal_kw,
        )
        # Explicit JSON mapping; archive.plain currently omits the new intervals.
        mapping = json.loads(json.dumps({
            "measured_kwh": state.measured_kwh,
            "estimated_kwh": state.estimated_kwh,
            "measured_seconds": state.measured_seconds,
            "estimated_seconds": state.estimated_seconds,
            "unknown_seconds": state.unknown_seconds,
            "accounted_at": state.accounted_at.isoformat(),
            "intervals": [
                {"started_at": item.started_at.isoformat(),
                 "ended_at": item.ended_at.isoformat(), "source": item.source,
                 "power_kw": item.power_kw}
                for item in state.intervals
            ],
        }))
        started_at, ended_at = T0 + span / 2, T0 + span * 5 / 2
        for evidence in (state, mapping):
            with self.subTest(evidence=type(evidence).__name__):
                selected = energy_for_interval(evidence, started_at, ended_at)
                self.assertIsNotNone(selected)
                self.assertAlmostEqual(
                    selected.measured_kwh, measured_kw * seconds / 2 / 3600,
                )
                self.assertAlmostEqual(
                    selected.estimated_kwh, nominal_kw * seconds / 3600,
                )
                self.assertEqual(
                    (selected.measured_seconds, selected.estimated_seconds,
                     selected.unknown_seconds), (seconds / 2, seconds, seconds / 2),
                )
                self.assertEqual(selected.source, "incomplete")
        self.assertEqual(
            energy_for_interval(mapping, started_at.isoformat(), ended_at.isoformat()),
            energy_for_interval(state, started_at, ended_at),
        )
        self.assertIsNone(energy_for_interval(
            {**mapping, "accounted_at": (T0 + span * 2).isoformat()},
            started_at, ended_at,
        ))
        self.assertIsNone(energy_for_interval(
            {key: value for key, value in mapping.items() if key != "accounted_at"},
            started_at, ended_at,
        ))

    def test_controller_reports_book_previous_inputs_and_same_time_final_value(self):
        p = parameters()
        nominal_kw = p.values["nominal_power_kw"]
        seconds = p.values["sensor_timeout_seconds"] / 4
        span = timedelta(seconds=seconds)
        low_kw, skipped_kw, final_kw = nominal_kw / 3, nominal_kw / 2, nominal_kw
        c = Controller(p)
        c.begin_session("input-order", T0)
        c.report_heating(True, T0)
        c.report_power(low_kw * 1000, T0 + span * 4, T0)
        c.report_power(skipped_kw * 1000, T0 + span * 5, T0 + span)
        booked = c.session.energy
        self.assertEqual(booked.intervals, (
            EnergyInterval(T0, T0 + span, "measured", low_kw),
        ))
        c.report_heating(False, T0 + span)
        c.report_power(final_kw * 1000, T0 + span * 5, T0 + span)
        self.assertEqual(c.session.energy, booked)
        c.advance(T0 + span * 2)
        c.report_power(None, None, T0 + span * 2)
        c.report_heating(True, T0 + span * 2)
        c.report_heating(False, T0 + span * 3)
        c.advance(T0 + span * 4)
        state = c.session.energy
        self.assertEqual(state.intervals, (
            EnergyInterval(T0, T0 + span, "measured", low_kw),
            EnergyInterval(T0 + span, T0 + span * 2, "measured", final_kw),
            EnergyInterval(T0 + span * 2, T0 + span * 3, "estimated", nominal_kw),
            EnergyInterval(T0 + span * 3, T0 + span * 4, "estimated", 0),
        ))
        self.assertAlmostEqual(state.measured_kwh, (low_kw + final_kw) * seconds / 3600)
        self.assertAlmostEqual(state.estimated_kwh, nominal_kw * seconds / 3600)
        self.assertEqual(
            (state.measured_seconds, state.estimated_seconds, state.unknown_seconds),
            (seconds * 2, seconds * 2, 0),
        )
