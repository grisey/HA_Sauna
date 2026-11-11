"""Kennzahlen nach dem Kennzahlenkatalog mit synthetischen Sitzungen."""

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from custom_components.ha_sauna import archive as archive_module
from custom_components.ha_sauna.archive import Archive
from custom_components.ha_sauna.core import key_figures as kf
from custom_components.ha_sauna.core.key_figure_display import (
    curve,
    figure,
    largest_remainder,
    present_session,
    present_statistics,
    rounded,
)
from custom_components.ha_sauna.core.moisture import absolute_humidity
from custom_components.ha_sauna.core.parameters import BY_KEY, ParameterError
from custom_components.ha_sauna.key_figure_parameters import door_sensor_lag

T0 = datetime(2026, 11, 2, 16, 0, tzinfo=UTC)
BERLIN = ZoneInfo("Europe/Berlin")
PARAMETERS = {"readiness_hysteresis_c": 1.0, "readiness_offset_c": 1.0, "nominal_power_kw": 4.5}


def series(points):
    points = list(points)
    return kf.Series(tuple(t for t, _ in points), tuple(float(v) for _, v in points))


def flat(value, end=7200, step=10):
    return series((t, value) for t in range(0, end + 1, step))


def session(**changes):
    values = {
        "started_at": T0,
        "duration": 7200.0,
        "upper_temperature": flat(80),
        "lower_temperature": flat(70),
        "upper_humidity": flat(10),
        "lower_humidity": flat(12),
        "gangs": (),
        "infusions": (),
        "doors": (),
        "phases": ((0.0, "aufheizen"), (1000.0, "bereit"), (7000.0, "aus")),
        "targets": ((0.0, 80.0),),
        "heating": ((0.0, 1500.0),),
        "energy_kwh": 2.0,
        "measured_kwh": 0.0,
        "oven_cooling": (),
        "parameters": PARAMETERS,
        "parameter_origin": "session",
        "volume_m3": None,
        "price_eur_per_kwh": None,
        "session_id": "s1",
        "door_sensor_lag": 20.0,
    }
    values.update(changes)
    return kf.SessionInput(**values)


class SeriesTests(unittest.TestCase):
    def test_value_at_a_time_is_the_last_value_at_or_before_it(self):
        reihe = series([(10, 1), (20, 2), (30, 3)])
        self.assertEqual((reihe.at(20), reihe.at(29.9), reihe.at(100)), (2, 2, 3))

    def test_time_before_the_first_value_uses_it_only_within_five_seconds(self):
        reihe = series([(10, 1), (20, 2)])
        self.assertEqual(reihe.at(5), 1)
        self.assertIsNone(reihe.at(4.9))

    def test_absolute_humidity_uses_the_temperature_of_the_same_position(self):
        result = session(upper_temperature=series([(0, 80)]), upper_humidity=series([(0, 20)]),
                         lower_temperature=series([(0, 60)]), lower_humidity=series([(0, 20)]))
        self.assertAlmostEqual(result.upper_absolute_humidity.at(0), absolute_humidity(80, 20))
        self.assertAlmostEqual(result.lower_absolute_humidity.at(0), absolute_humidity(60, 20))


class SessionFigureTests(unittest.TestCase):
    def test_session_without_gang(self):
        figures = kf.session_figures(session())
        self.assertEqual(figures["gang_count"], 0)
        self.assertEqual(figures["sauna_time_seconds"], 0)
        self.assertIsNone(figures["gang_duration_mean_seconds"])
        self.assertEqual((figures["rests_seconds"], figures["rest_mean_seconds"]), ([], None))
        self.assertIsNone(figures["heating_in_gangs"])
        self.assertEqual(figures["maximum_temperature_position"],
                         {"relation": "before", "gang": None})
        self.assertEqual((figures["warmup_end_at"], figures["warmup_end_kind"]), (1000.0, "ready"))

    def test_gangs_sauna_time_rest_and_infusions(self):
        gangs = (kf.GangInput(1800.0, 2700.0, (2000.0,)), kf.GangInput(4500.0, 5400.0, ()))
        figures = kf.session_figures(session(
            gangs=gangs, infusions=(kf.InfusionInput(2000.0), kf.InfusionInput(6000.0)),
        ))
        self.assertEqual(figures["gang_count"], 2)
        self.assertEqual(figures["sauna_time_seconds"], 1800.0)
        self.assertEqual(figures["gang_duration_mean_seconds"], 900.0)
        self.assertEqual(figures["rests_seconds"], [1800.0])
        self.assertEqual((figures["infusions_in_gangs"], figures["infusions_total"]), (1, 2))

    def test_gang_without_documented_end_leaves_sums_missing(self):
        gangs = (kf.GangInput(1800.0, 2700.0), kf.GangInput(4500.0, None))
        figures = kf.session_figures(session(gangs=gangs))
        self.assertEqual(figures["gang_count"], 2)
        self.assertIsNone(figures["gangs"][1]["duration_seconds"])
        self.assertIsNone(figures["sauna_time_seconds"])
        self.assertIsNone(figures["gang_duration_mean_seconds"])
        self.assertIsNone(figures["control_in_ready"])
        self.assertEqual(figures["rests_seconds"], [1800.0])

    def test_maximum_temperature_with_position_and_target(self):
        temperature = series([(0, 20), (1900, 85), (2000, 90), (2100, 80), (7200, 80)])
        gangs = (kf.GangInput(1800.0, 2700.0),)
        figures = kf.session_figures(session(upper_temperature=temperature, gangs=gangs,
                                             targets=((0.0, 85.0),)))
        self.assertEqual((figures["maximum_temperature_c"], figures["maximum_temperature_at"]),
                         (90.0, 2000.0))
        self.assertEqual(figures["maximum_temperature_position"], {"relation": "in", "gang": 1})
        self.assertEqual(figures["maximum_temperature_target_c"], 85.0)
        later = kf.session_figures(session(upper_temperature=temperature,
                                           gangs=(kf.GangInput(100.0, 500.0),)))
        self.assertEqual(later["maximum_temperature_position"], {"relation": "after", "gang": 1})

    def test_warmup_ends_at_ready_or_entry_door_whichever_is_earlier(self):
        gangs = (kf.GangInput(1800.0, 2700.0),)
        doors = (("door_open", 900.0), ("door_close", 1800.0))
        figures = kf.session_figures(session(gangs=gangs, doors=doors))
        self.assertEqual((figures["warmup_end_at"], figures["warmup_end_kind"]), (900.0, "gang"))
        self.assertEqual(figures["warmup_seconds"], 900.0)
        tie = kf.session_figures(session(gangs=gangs, doors=(("door_open", 1000.0),
                                                              ("door_close", 1800.0))))
        self.assertEqual(tie["warmup_end_kind"], "ready")

    def test_manual_session_without_ready_ends_warmup_at_the_first_entry(self):
        temperature = series([(t, 20 + t / 60) for t in range(0, 7201, 10)])
        figures = kf.session_figures(session(
            phases=((0.0, "manuell"), (7000.0, "aus")), targets=(),
            upper_temperature=temperature, gangs=(kf.GangInput(3000.0, 3600.0),),
            doors=(("door_open", 2900.0), ("door_close", 3000.0)),
            heating=((0.0, 1500.0), (2000.0, 2600.0), (3200.0, 3300.0)),
        ))
        self.assertTrue(figures["manual"])
        self.assertTrue(figures["energy_upper_bound"])
        self.assertIsNone(figures["ready_at"])
        self.assertEqual((figures["warmup_end_at"], figures["warmup_end_kind"]), (2900.0, "gang"))
        self.assertAlmostEqual(figures["energy_warmup_kwh"], 2100 / 3600 * 4.5)
        self.assertAlmostEqual(figures["energy_after_warmup_kwh"], 2.0 - 2100 / 3600 * 4.5)
        self.assertEqual(figures["heating_phases_after_warmup"], 1)
        # Die Eintrittsöffnung beendet das Aufheizen und zählt nicht mehr dazu.
        self.assertEqual(figures["door_openings_warmup"], 0)
        self.assertAlmostEqual(figures["rise_per_kwh"],
                               (temperature.at(2900) - 20) / figures["energy_warmup_kwh"])
        self.assertIsNone(figures["control_in_ready"])
        self.assertEqual(figures["targets_c"], [])
        self.assertIsNone(figures["gangs"][0]["target_c"])
        self.assertIsNone(figures["gangs"][0]["target_deviation"])

    def test_warmup_curve_from_heating_start_to_the_end_of_warmup(self):
        temperature = series([(0, 20), (300, 30), (600, 45), (1000, 60), (1500, 70)])
        figures = kf.session_figures(session(upper_temperature=temperature))
        self.assertEqual(figures["warmup_end_at"], 1000.0)
        self.assertEqual(figures["warmup_curve"],
                         [(0.0, 20.0), (300.0, 30.0), (600.0, 45.0), (1000.0, 60.0)])
        late = kf.session_figures(session(upper_temperature=temperature,
                                          heating=((150.0, 1500.0),)))
        # Erster Punkt ist T beim Heizbeginn (K-S11), Zeiten ab Heizbeginn.
        self.assertEqual(late["warmup_curve"][:2], [(0.0, 20.0), (150.0, 30.0)])
        self.assertEqual(late["warmup_curve"][-1], (850.0, 60.0))
        self.assertEqual(late["start_temperature_c"], late["warmup_curve"][0][1])
        self.assertEqual(late["warmup_end_temperature_c"], late["warmup_curve"][-1][1])
        self.assertIsNone(kf.session_figures(session(heating=()))["warmup_curve"])

    def test_warmup_stays_open_without_documented_first_gang_start(self):
        figures = kf.session_figures(session(gangs=(kf.GangInput(None, 2700.0),)))
        self.assertIsNone(figures["warmup_seconds"])
        self.assertIsNone(figures["energy_warmup_kwh"])

    def test_nominal_power_of_the_check_is_delivered(self):
        figures = kf.session_figures(session())
        self.assertEqual(figures["nominal_power_kw"], 4.5)
        self.assertEqual(present_session(figures)["nominal_power_kw"],
                         {"value": 4.5, "display": 4.5, "unit": "kW"})
        missing = kf.session_figures(session(parameters={}))
        self.assertIsNone(missing["nominal_power_kw"])

    def test_heating_energy_and_cost(self):
        figures = kf.session_figures(session(heating=((0.0, 1500.0), (3000.0, 3600.0)),
                                             price_eur_per_kwh=0.28))
        self.assertEqual(figures["heating_time_seconds"], 2100.0)
        self.assertAlmostEqual(figures["heating_share"], 2100 / 7200)
        self.assertAlmostEqual(figures["energy_check_kwh"], 2100 / 3600 * 4.5)
        self.assertAlmostEqual(figures["cost_eur"], 0.56)
        self.assertAlmostEqual(figures["energy_warmup_kwh"], 1000 / 3600 * 4.5)
        self.assertEqual(figures["heating_phases_after_warmup"], 1)
        self.assertIsNone(kf.session_figures(session(price_eur_per_kwh=None))["cost_eur"])

    def test_heating_share_in_gangs_and_oven_cooling_gang(self):
        gangs = (kf.GangInput(1800.0, 2700.0), kf.GangInput(4500.0, 5400.0))
        figures = kf.session_figures(session(
            gangs=gangs, heating=((0.0, 2250.0),), oven_cooling=((2700.0, 3300.0),
                                                                  (5400.0, 6000.0)),
        ))
        self.assertAlmostEqual(figures["heating_in_gangs"], 450 / 1800)
        self.assertEqual([c["gang"] for c in figures["oven_cooling"]], [1, 2])
        self.assertEqual(figures["oven_cooling"][0]["duration_seconds"], 600.0)

    def test_control_in_ready_is_time_weighted_without_gangs_cooling_and_doors(self):
        temperature = series([(1000, 78), (2000, 80), (3000, 82.5), (4000, 80)])
        figures = kf.session_figures(session(
            upper_temperature=temperature, phases=((0.0, "aufheizen"), (1000.0, "bereit"),
                                                   (5000.0, "aus")),
            gangs=(kf.GangInput(2500.0, 2800.0),), oven_cooling=((2800.0, 2900.0),),
            doors=(("door_open", 3500.0), ("door_close", 3600.0)),
        ))
        control = figures["control_in_ready"]
        self.assertEqual(control["window_seconds"], 4000 - 400 - 100)
        self.assertAlmostEqual(control["below"], 1000 / 3500)
        self.assertAlmostEqual(control["above"], 900 / 3500)
        self.assertAlmostEqual(control["within"], 1600 / 3500)
        self.assertEqual((control["lowest_c"], control["lowest_at"]), (-2.0, 1000))
        self.assertEqual((control["highest_c"], control["highest_at"]), (2.5, 3000))

    def test_ready_history_gives_ready_and_control_window_also_in_manual_operation(self):
        figures = kf.session_figures(session(
            phases=((0.0, "manuell"), (4000.0, "aus"), (4200.0, "manuell"), (7000.0, "aus")),
            readiness=((1000.0, True), (2500.0, False), (3000.0, True)),
            gangs=(kf.GangInput(2500.0, 2800.0),),
            doors=(("door_open", 2400.0), ("door_close", 2500.0)),
        ))
        self.assertTrue(figures["manual"])
        self.assertEqual(figures["ready_at"], 1000.0)
        self.assertEqual((figures["warmup_end_at"], figures["warmup_end_kind"]), (1000.0, "ready"))
        control = figures["control_in_ready"]
        # Bereit bei eingeschaltetem Betrieb, ohne Gang und offene Tür.
        self.assertEqual(control["window_seconds"], 1400 + 1000 + 2800)
        self.assertEqual((control["mean_c"], control["within"]), (0.0, 1.0))

    def test_ready_history_replaces_phase_ready_and_agrees_with_it_in_automatic(self):
        temperature = series([(1000, 78), (2000, 80), (3000, 82.5), (4000, 80)])
        keys = ("ready_at", "warmup_end_at", "warmup_end_kind", "warmup_seconds",
                "control_in_ready")
        phases = kf.session_figures(session(upper_temperature=temperature))
        history = kf.session_figures(session(upper_temperature=temperature,
                                             readiness=((1000.0, True),)))
        self.assertEqual({k: history[k] for k in keys}, {k: phases[k] for k in keys})
        never = kf.session_figures(session(readiness=()))
        self.assertIsNone(never["ready_at"])
        self.assertIsNone(never["control_in_ready"])
        # Ohne Phasen und ohne Verlauf (ältere Sitzungen) gibt es kein Bereit.
        without = kf.session_figures(session(phases=()))
        self.assertIsNone(without["ready_at"])
        self.assertIsNone(without["control_in_ready"])

    def test_targets_until_operation_off(self):
        figures = kf.session_figures(session(targets=((0.0, 85.0), (3000.0, 88.0),
                                                      (7000.0, 90.0))))
        self.assertEqual(figures["targets_c"], [85.0, 88.0])

    def test_outside_temperature_is_the_entry_at_the_start_with_its_measurement_time(self):
        figures = kf.session_figures(session())
        self.assertIsNone(figures["outside_temperature_c"])
        self.assertIsNone(figures["outside_temperature_measured_at"])
        # no age limit: measured 1.5 h before the start, current in HA at the start
        readings = (kf.OutsideInput(0.0, 6.5, -5400.0), kf.OutsideInput(1800.0, 6.1, 0.0))
        figures = kf.session_figures(session(outside=readings))
        self.assertEqual(figures["outside_temperature_c"], 6.5)
        self.assertEqual(figures["outside_temperature_measured_at"], -5400.0)
        # within the session: the last entry before the time
        self.assertEqual(kf.outside_at(session(outside=readings), 1799.0).value, 6.5)
        self.assertEqual(kf.outside_at(session(outside=readings), 1800.0).value, 6.1)
        # a source without measurement time, and one only after the start
        figures = kf.session_figures(session(outside=(kf.OutsideInput(0.0, 6.5, None),)))
        self.assertEqual((figures["outside_temperature_c"],
                          figures["outside_temperature_measured_at"]), (6.5, None))
        later = kf.session_figures(session(outside=(kf.OutsideInput(600.0, 6.5, 0.0),)))
        self.assertIsNone(later["outside_temperature_c"])

    def test_rejected_candidates_without_the_ones_taken_as_door(self):
        # K-E2: an episode taken over by presence counts as door operation, not as rejected
        self.assertIsNone(kf.session_figures(session())["rejected_candidates"])
        episodes = (kf.EpisodeInput(100.0, 140.0, False, (100.0, 120.0)),
                    kf.EpisodeInput(300.0, 340.0, True, (300.0, 305.0, 310.0)),
                    kf.EpisodeInput(500.0, 520.0, False, (500.0,)))
        figures = kf.session_figures(session(
            rejected_candidates=(100.0, 120.0, 200.0, 300.0, 305.0, 310.0, 500.0),
            rejected_episodes=episodes,
        ))
        self.assertEqual(figures["rejected_candidates"], {
            "candidates": 7, "rejected": 4, "rejected_in_episodes": 3, "rejected_single": 1,
            "rejected_episodes": 2, "taken_as_door": 3, "door_episodes": 1,
        })
        none = kf.session_figures(session(rejected_candidates=()))["rejected_candidates"]
        self.assertEqual((none["candidates"], none["rejected"]), (0, 0))

    def test_parameters_missing_leave_dependent_figures_missing(self):
        figures = kf.session_figures(session(parameters={}, parameter_origin=None))
        self.assertIsNone(figures["energy_warmup_kwh"])
        self.assertIsNone(figures["energy_check_kwh"])
        self.assertIsNone(figures["control_in_ready"])


class GangFigureTests(unittest.TestCase):
    def test_entry_and_exit_temperature_at_door_openings(self):
        temperature = series([(0, 80), (1700, 85), (1750, 79), (2550, 83), (2600, 78)])
        doors = (("door_open", 1700.0), ("door_close", 1800.0),
                 ("door_open", 2550.0), ("door_close", 2600.0))
        gang = kf.session_figures(session(upper_temperature=temperature, doors=doors,
                                          gangs=(kf.GangInput(1800.0, 2700.0),)))["gangs"][0]
        self.assertEqual((gang["entry_opening"], gang["entry_temperature_c"]), (1700.0, 85.0))
        self.assertEqual((gang["exit_opening"], gang["exit_temperature_c"]), (2550.0, 83.0))
        without = kf.session_figures(session(upper_temperature=temperature,
                                             gangs=(kf.GangInput(1800.0, 2700.0),)))["gangs"][0]
        self.assertEqual((without["entry_temperature_c"], without["exit_temperature_c"]),
                         (79.0, 78.0))

    def test_inner_door_openings_exclude_entry_and_exit(self):
        doors = (("door_open", 1700.0), ("door_close", 1800.0), ("door_open", 2000.0),
                 ("door_close", 2010.0), ("door_open", 2600.0), ("door_close", 2650.0))
        gang = kf.session_figures(session(doors=doors,
                                          gangs=(kf.GangInput(1800.0, 2700.0),)))["gangs"][0]
        self.assertEqual((gang["inner_door_openings"], gang["exit_opening"]), (1, 2600.0))

    def test_humidity_rise_until_the_detected_end_and_at_most_the_next_infusion(self):
        humidity = series([(1990, 10), (2000, 10), (2010, 30), (2020, 40), (2030, 20),
                           (2040, 60), (2100, 10)])
        infusions = (kf.InfusionInput(2000.0, 2020.0), kf.InfusionInput(2030.0, 2090.0))
        figures = kf.session_figures(session(
            upper_humidity=humidity, infusions=infusions, volume_m3=6.0,
            gangs=(kf.GangInput(1800.0, 2700.0, (2000.0, 2030.0)),),
        ))
        gang = figures["gangs"][0]
        rise = absolute_humidity(80, 40) - absolute_humidity(80, 10)
        second = absolute_humidity(80, 60) - absolute_humidity(80, 20)
        self.assertAlmostEqual(gang["humidity_rises_gm3"][0], rise)
        self.assertAlmostEqual(gang["humidity_rises_gm3"][1], second)
        self.assertAlmostEqual(gang["humidity_rise_mean_gm3"], (rise + second) / 2)
        self.assertAlmostEqual(gang["water_mean_l"], (rise + second) / 2 * 6 / 1000)
        capped = kf.infusion_rise_window(
            session(infusions=(kf.InfusionInput(2000.0, 2100.0), kf.InfusionInput(2030.0))),
            2000.0,
        )
        self.assertEqual(capped, (2000.0, 2030.0))

    def test_infusion_without_detected_end_has_no_rise(self):
        figures = kf.session_figures(session(
            infusions=(kf.InfusionInput(2000.0, None),), volume_m3=6.0,
            gangs=(kf.GangInput(1800.0, 2700.0, (2000.0,)),),
        ))
        gang = figures["gangs"][0]
        self.assertEqual(gang["humidity_rises_gm3"], [None])
        self.assertIsNone(gang["humidity_rise_mean_gm3"])
        self.assertIsNone(gang["water_mean_l"])

    def test_water_requires_the_cabin_volume(self):
        figures = kf.session_figures(session(
            infusions=(kf.InfusionInput(2000.0, 2020.0),),
            gangs=(kf.GangInput(1800.0, 2700.0, (2000.0,)),),
        ))
        self.assertIsNotNone(figures["gangs"][0]["humidity_rise_mean_gm3"])
        self.assertIsNone(figures["gangs"][0]["water_mean_l"])

    def test_highest_humidity_with_absolute_humidity_at_that_time(self):
        humidity = series([(1800, 10), (2000, 25), (2100, 15)])
        gang = kf.session_figures(session(upper_humidity=humidity,
                                          gangs=(kf.GangInput(1800.0, 2700.0),)))["gangs"][0]
        self.assertEqual((gang["highest_humidity_percent"], gang["highest_humidity_at"]),
                         (25.0, 2000))
        self.assertAlmostEqual(gang["absolute_humidity_at_highest_gm3"],
                               absolute_humidity(80, 25))

    def test_target_and_target_deviation_in_the_gang(self):
        temperature = series([(1800, 80), (2100, 86), (2400, 83)])
        gang = kf.session_figures(session(
            upper_temperature=temperature, targets=((0.0, 85.0),),
            gangs=(kf.GangInput(1800.0, 2700.0),),
        ))["gangs"][0]
        self.assertEqual(gang["target_c"], 85.0)
        deviation = gang["target_deviation"]
        self.assertAlmostEqual(deviation["mean_c"], (-5 * 300 + 1 * 300 - 2 * 300) / 900)
        self.assertEqual((deviation["lowest_c"], deviation["highest_c"]), (-5.0, 1.0))


class DoorTests(unittest.TestCase):
    DOORS = (("door_open", 500.0), ("door_close", 520.0),     # sonstige
             ("door_open", 1700.0), ("door_close", 1800.0),   # Eintritt
             ("door_open", 2000.0), ("door_close", 2010.0),   # sonstige im Gang
             ("door_open", 2600.0), ("door_close", 2650.0),   # Austritt
             ("door_open", 3000.0), ("door_close", 3600.0))   # Schlusslüften

    def test_door_classes(self):
        figures = kf.session_figures(session(doors=self.DOORS,
                                             gangs=(kf.GangInput(1800.0, 2700.0),)))
        self.assertEqual([d["door_class"] for d in figures["doors"]],
                         ["other", "entry", "other", "exit", "final_ventilation"])
        counts = {name: figures["door_classes"][name]["count"] for name in figures["door_classes"]}
        self.assertEqual(counts, {"entry": 1, "exit": 1, "final_ventilation": 1, "other": 2,
                                  "all": 5})
        # „Wieder erreicht bei x von y“: y sind die geprüften Vorgänge (ohne Schlusslüften).
        self.assertEqual([d["recovery_checked"] for d in figures["doors"]],
                         [True, True, True, True, False])
        self.assertEqual(figures["door_classes"]["all"]["recovery_checked_count"], 4)
        self.assertEqual(figures["door_classes"]["final_ventilation"]["recovery_checked_count"], 0)
        self.assertEqual(
            present_session(figures)["door_classes"]["all"]["recovery_checked_count"],
            {"value": 4, "display": 4, "unit": "count"})

    def test_gang_ended_by_hand_has_no_exit_door(self):
        # GE1-6: ein Gangende von Hand ergibt keinen Austritt-Türvorgang; die Austritts-
        # temperatur gilt wie ohne erkannte Tür am Gangende (K-G2), Öffnungen im Gang
        # zählen zu K-G6.
        temperature = series([(0, 80), (2600, 80), (2700, 70), (7200, 70)])
        figures = kf.session_figures(session(
            doors=self.DOORS, upper_temperature=temperature,
            gangs=(kf.GangInput(1800.0, 2700.0, manual_end=True),)))
        self.assertEqual([d["door_class"] for d in figures["doors"]],
                         ["other", "entry", "other", "other", "final_ventilation"])
        gang = figures["gangs"][0]
        self.assertIsNone(gang["exit_opening"])
        self.assertEqual(gang["exit_temperature_c"], 70.0)
        self.assertEqual(gang["inner_door_openings"], 2)
        self.assertEqual(gang["duration_seconds"], 900.0)

    def test_statistics_door_loss_over_entry_and_exit_with_the_largest_one(self):
        temperature = series([(0, 80), (1700, 80), (1750, 74), (1830, 80), (2000, 79),
                              (2600, 80), (2620, 70), (2700, 80), (3000, 80)])
        figures = kf.session_figures(session(doors=self.DOORS, upper_temperature=temperature,
                                             gangs=(kf.GangInput(1800.0, 2700.0),)))
        expected = [d for d in figures["doors"] if d["door_class"] in ("entry", "exit")]
        self.assertEqual([d["loss_c"] for d in expected], [6.0, 10.0])
        result = kf.statistics_figures([figures], BERLIN)
        door = result["door_entry_exit"]
        self.assertEqual(door["count"], len(expected))
        losses = [d["loss_c"] for d in expected]
        self.assertAlmostEqual(door["loss_mean_c"], sum(losses) / len(losses))
        largest = max(expected, key=lambda d: d["loss_c"])
        self.assertEqual(door["largest"], {
            "loss_c": largest["loss_c"], "door_class": largest["door_class"],
            "session_id": "s1", "started_at": figures["started_at"],
            "opened": largest["opened"], "open_seconds": largest["open_seconds"],
        })
        self.assertAlmostEqual(result["per_session"][0]["door_loss_entry_exit_mean_c"],
                               door["loss_mean_c"])
        # Dieselbe Rechnung steht je Sitzung auch im Endpunkt key-figures (Verlauf · Anlage).
        self.assertAlmostEqual(figures["door_loss_entry_exit_mean_c"], door["loss_mean_c"])
        self.assertEqual(present_session(figures)["door_loss_entry_exit_mean_c"]["display"],
                         8.0)
        shown = present_statistics(result)["door_entry_exit"]["largest"]
        self.assertEqual(shown["opened"]["display"],
                         (T0 + timedelta(seconds=largest["opened"])).isoformat())

    def test_entry_requires_the_closing_at_the_gang_start(self):
        figures = kf.session_figures(session(doors=self.DOORS,
                                             gangs=(kf.GangInput(1801.0, 2700.0),)))
        self.assertEqual(figures["doors"][1]["door_class"], "other")

    def test_unresolved_class_when_the_gang_end_is_not_documented(self):
        figures = kf.session_figures(session(doors=self.DOORS,
                                             gangs=(kf.GangInput(1800.0, None),)))
        self.assertEqual([d["door_class"] for d in figures["doors"]],
                         ["other", "entry", None, None, None])

    def test_loss_until_closing_plus_sensor_lag_and_recovery(self):
        temperature = series([(0, 80), (1700, 80), (1750, 76), (1810, 74), (1830, 73),
                              (1900, 79), (1950, 80.5), (7200, 80)])
        doors = (("door_open", 1700.0), ("door_close", 1800.0))
        door = kf.session_figures(session(upper_temperature=temperature, doors=doors,
                                          door_sensor_lag=20.0))["doors"][0]
        self.assertEqual(door["open_seconds"], 100.0)
        self.assertAlmostEqual(door["loss_c"], 6.0)
        self.assertEqual(door["recovered_seconds"], 150.0)
        longer = kf.session_figures(session(upper_temperature=temperature, doors=doors,
                                            door_sensor_lag=30.0))["doors"][0]
        self.assertAlmostEqual(longer["loss_c"], 7.0)
        missing = kf.session_figures(session(upper_temperature=temperature, doors=doors,
                                             door_sensor_lag=None))["doors"][0]
        self.assertIsNone(missing["loss_c"])

    def test_recovery_counts_only_before_the_next_opening(self):
        temperature = series([(0, 80), (1700, 80), (1750, 76), (1950, 80.5), (7200, 80)])
        doors = (("door_open", 1700.0), ("door_close", 1800.0),
                 ("door_open", 1900.0), ("door_close", 1910.0))
        figures = kf.session_figures(session(upper_temperature=temperature, doors=doors))
        self.assertIsNone(figures["doors"][0]["recovered_seconds"])

    def test_learned_sensor_lag_is_the_median_of_recovered_operations(self):
        def dip(opened, closed, low):
            return [(opened, 80), (low, 74), (closed + 200, 80)]
        temperature = series([(0, 80)] + dip(1000, 1050, 1060) + dip(2000, 2050, 2080)
                             + dip(3000, 3050, 3070) + [(4000, 80), (4010, 70), (7200, 70)])
        doors = (("door_open", 1000.0), ("door_close", 1050.0),
                 ("door_open", 2000.0), ("door_close", 2050.0),
                 ("door_open", 3000.0), ("door_close", 3050.0),
                 ("door_open", 4000.0), ("door_close", 4050.0))
        learned = kf.learn_door_sensor_lag([session(upper_temperature=temperature, doors=doors)])
        self.assertEqual(learned["operations"], 3)
        self.assertEqual(learned["value_seconds"], 20.0)
        with_infusion = kf.learn_door_sensor_lag([session(
            upper_temperature=temperature, doors=doors, infusions=(kf.InfusionInput(2100.0),),
        )])
        self.assertEqual(with_infusion["operations"], 2)
        self.assertIsNone(kf.learn_door_sensor_lag([session()]))

    def test_shipped_sensor_lag_has_its_learning_basis(self):
        stored = json.loads(Path(archive_module.__file__).with_name(
            "key_figure_parameters.json").read_text(encoding="utf-8"))["door_sensor_lag"]
        self.assertEqual(door_sensor_lag(), stored["value_seconds"])
        self.assertEqual(stored["operations"],
                         sum(item["operations"] for item in stored["sessions"]))
        self.assertEqual(stored["method"], "core.key_figures.learn_door_sensor_lag")


class StatisticsTests(unittest.TestCase):
    def figures(self, session_id, start, **changes):
        return kf.session_figures(session(session_id=session_id, started_at=start, **changes))

    def test_totals_means_and_session_counts(self):
        first = self.figures("a", T0, gangs=(kf.GangInput(1800.0, 2700.0),
                                             kf.GangInput(4500.0, 5400.0)), price_eur_per_kwh=0.3)
        second = self.figures("b", T0 + timedelta(days=1), gangs=(kf.GangInput(1800.0, None),),
                              energy_kwh=None)
        result = kf.statistics_figures([first, second], BERLIN, 6.0)
        self.assertEqual(result["sessions"], {"value": 2, "sessions": 2})
        self.assertEqual(result["sauna_time_seconds"], {"value": 1800.0, "sessions": 1})
        self.assertEqual(result["gangs"]["value"], 3)
        self.assertEqual(result["gang_duration"]["gangs"], 2)
        self.assertEqual(result["energy_kwh"], {"value": 2.0, "sessions": 1})
        self.assertEqual(result["cost_eur"]["sessions"], 1)
        self.assertEqual(result["energy_per_sauna_hour_kwh"]["value"], 2.0 / 0.5)
        self.assertEqual(result["rest"]["rests"], 1)
        self.assertEqual(result["habits"]["most_frequent_gang_count"], [1, 2])
        self.assertEqual(result["habits"]["most_frequent_gang_count_sessions"], 1)
        rows = result["per_session"]
        self.assertEqual([row["duration_seconds"] for row in rows], [7200.0, 7200.0])
        self.assertEqual([row["gang_count"] for row in rows], [2, 1])
        self.assertEqual(rows[0]["gangs"][1], {"number": 2, "start": 4500.0, "end": 5400.0,
                                               "duration_seconds": 900.0, "infusion_count": 0})
        self.assertEqual(rows[1]["gangs"][0]["end"], None)
        self.assertEqual([row["infusions_in_gangs"] for row in rows], [0, 0])
        self.assertEqual(rows[0]["program_kind"], None)
        self.assertEqual(result["warmup"]["per_session"][0]["curve"], first["warmup_curve"])
        self.assertEqual(result["habits"]["sessions"][0]["weekday"], 1)
        self.assertEqual(result["habits"]["sessions"][0]["local_start"], "17:00:00")

    def test_test_sessions_count_in_sums_only(self):
        # G25 (Nutzer 10.11.2026): Testbetrieb zählt in Summen (K-T1 bis K-T4, K-T8),
        # nicht in Mitteln, Vergleichen und Rekorden.
        regular = self.figures("a", T0, gangs=(kf.GangInput(1800.0, 2700.0),))
        test = {**self.figures("t", T0 + timedelta(days=1),
                               gangs=(kf.GangInput(1800.0, 5400.0, (2000.0, 2100.0)),),
                               duration=9000.0, upper_temperature=flat(80, end=9000)),
                "test": True}
        both = kf.statistics_figures([regular, test], BERLIN)
        alone = kf.statistics_figures([regular], BERLIN)
        self.assertEqual(both["sessions"]["value"], 2)
        self.assertEqual(both["test_sessions"]["value"], 1)
        self.assertEqual(both["gangs"]["value"], 2)
        self.assertEqual(both["infusions"]["value"], 2)
        self.assertEqual(both["sauna_time_seconds"]["value"], 900.0 + 3600.0)
        self.assertEqual(both["energy_kwh"]["value"], 4.0)
        for key in ("gangs_per_session", "infusions_per_gang", "infusions_most",
                    "gang_duration", "gang_infusions", "rest", "habits", "records",
                    "warmup", "door_classes", "door_entry_exit", "per_session", "by_program"):
            with self.subTest(key=key):
                self.assertEqual(both[key], alone[key])
        self.assertEqual(alone["test_sessions"]["value"], 0)

    def test_comparisons_are_separated_by_program_kind(self):
        manual = self.figures("m", T0, phases=((0.0, "manuell"), (7000.0, "aus")),
                              gangs=(kf.GangInput(3000.0, 3600.0),))
        automatic = self.figures("p", T0, program={"kind": "program", "name": "P", "steps": [80]})
        result = kf.statistics_figures([manual, automatic], BERLIN)
        self.assertEqual(set(result["by_program"]), {"manual", "program"})
        self.assertEqual(result["by_program"]["manual"]["warmup"]["median_seconds"], 3000.0)
        self.assertEqual(result["by_program"]["program"]["warmup"]["median_seconds"], 1000.0)
        self.assertEqual(result["warmup"]["sessions"], 2)

    def test_earlier_warmup_names_kinds_and_operation(self):
        earlier = [self.figures("a", T0), self.figures("b", T0, phases=(
            (0.0, "manuell"), (7000.0, "aus")), gangs=(kf.GangInput(3000.0, 3600.0),))]
        self.assertEqual(kf.earlier_warmup(earlier), {
            "program_kind": None, "median_seconds": 1000.0, "kinds": ["ready"],
            "all_manual": False, "sessions": 1,
        })
        # Vergleiche nach Programmart getrennt: nur frühere Sitzungen derselben Art.
        self.assertEqual(kf.earlier_warmup(earlier, "manual"), {
            "program_kind": "manual", "median_seconds": 3000.0, "kinds": ["gang"],
            "all_manual": True, "sessions": 1,
        })
        self.assertEqual(kf.earlier_warmup(earlier, "program")["sessions"], 0)
        self.assertIsNone(kf.earlier_warmup(earlier, "program")["median_seconds"])

    def test_period_is_the_season_part_or_summer_of_the_start(self):
        autumn, summer = "2026-10-05T17:00:00+00:00", "2026-09-22T17:00:00+00:00"
        self.assertTrue(kf.in_period(autumn, {"season": 2026}))
        self.assertTrue(kf.in_period(autumn, {"season": 2026, "part": "autumn"}))
        self.assertFalse(kf.in_period(autumn, {"season": 2026, "part": "winter"}))
        self.assertTrue(kf.in_period(summer, {"summer": 2026}))
        self.assertFalse(kf.in_period(summer, {"season": 2026}))
        self.assertTrue(kf.in_period(summer, {}))

    def test_periods_list_seasons_with_parts_and_summers_in_order(self):
        started = ["2027-01-10T18:00:00+00:00", "2026-10-05T17:00:00+00:00",
                   "2026-09-20T17:00:00+00:00", "2027-07-01T18:00:00+00:00"]
        self.assertEqual(kf.periods(started), [
            {"summer": 2026, "label": "Sommer 2026"},
            {"season": 2026, "label": "2026/27", "parts": [
                {"part": "autumn", "label": "Herbst"}, {"part": "winter", "label": "Winter"}]},
            {"summer": 2027, "label": "Sommer 2027"},
        ])
        self.assertEqual(kf.periods([]), [])

    def test_lists_are_grouped_by_season_and_summer_or_by_season_part(self):
        sessions = [
            self.figures("s", datetime(2026, 8, 1, 17, tzinfo=UTC),
                         gangs=(kf.GangInput(1800.0, 2700.0),)),
            self.figures("a", datetime(2026, 10, 5, 17, tzinfo=UTC),
                         gangs=(kf.GangInput(1800.0, 2400.0),)),
            self.figures("w", datetime(2027, 1, 5, 17, tzinfo=UTC), energy_kwh=None),
        ]
        groups = kf.statistics_figures(sessions, BERLIN, grouping="season")["groups"]
        self.assertEqual([(g.get("summer"), g.get("season"), g["label"], g["sessions"])
                          for g in groups],
                         [(2026, None, "Sommer 2026", 1), (None, 2026, "2026/27", 2)])
        season = groups[1]
        self.assertEqual(season["sauna_time_seconds"], {"value": 600.0, "sessions": 2})
        self.assertEqual(season["energy_kwh"], {"value": 2.0, "sessions": 1})
        self.assertEqual((season["gangs"], season["infusions"]), (1, 0))
        self.assertEqual(season["heating_share"], 3000.0 / 14400.0)
        parts = kf.statistics_figures(sessions[1:], BERLIN, grouping="part")["groups"]
        self.assertEqual([(g["part"], g["label"]) for g in parts],
                         [("autumn", "Herbst"), ("winter", "Winter")])
        self.assertIsNone(kf.statistics_figures(sessions, BERLIN)["groups"])

    def test_habits_count_weekdays_and_give_the_middle_half_of_the_starts(self):
        starts = [datetime(2026, 11, 2, 16, tzinfo=UTC), datetime(2026, 11, 3, 17, tzinfo=UTC),
                  datetime(2026, 11, 9, 18, tzinfo=UTC), datetime(2026, 11, 9, 19, tzinfo=UTC),
                  datetime(2026, 11, 10, 20, tzinfo=UTC)]
        result = kf.statistics_figures(
            [self.figures(f"s{i}", start) for i, start in enumerate(starts)], BERLIN)
        habits = result["habits"]
        self.assertEqual(habits["weekday_sessions"], [3, 2, 0, 0, 0, 0, 0])
        # Ortszeit 17, 18, 19, 20, 21 Uhr: 1. Quartil 18 Uhr, 3. Quartil 20 Uhr.
        self.assertEqual(habits["start_quartiles_seconds"], [18 * 3600.0, 20 * 3600.0])
        single = kf.statistics_figures([self.figures("x", starts[0])], BERLIN)
        self.assertEqual(single["habits"]["start_quartiles_seconds"], [17 * 3600.0] * 2)
        self.assertIsNone(kf.statistics_figures([], BERLIN)["habits"]["start_quartiles_seconds"])

    def test_start_quartiles_put_the_day_boundary_into_the_widest_gap(self):
        # Beginn 21, 22, 23 Uhr, 0:30 und 1 Uhr (Ortszeit): die größte Lücke liegt
        # zwischen 1 und 21 Uhr, Beginnzeiten nach Mitternacht gehören zum Abend.
        hours = [21, 22, 23, 24.5, 25]
        starts = [datetime(2026, 11, 2, 20, tzinfo=UTC) + timedelta(hours=h - 21)
                  for h in hours]
        result = kf.statistics_figures(
            [self.figures(f"s{i}", start) for i, start in enumerate(starts)], BERLIN)
        self.assertEqual(result["habits"]["start_quartiles_seconds"],
                         [22 * 3600.0, 0.5 * 3600.0])
        shown = present_statistics(result)["habits"]["start_quartiles_seconds"]
        self.assertEqual([item["display"] for item in shown], [22 * 60, 30])
        # Liegt die größte Lücke um Mitternacht, gilt die Reihenfolge des Tages.
        self.assertEqual(kf.start_quartiles([17 * 3600.0, 18 * 3600.0, 20 * 3600.0]),
                         [17.5 * 3600.0, 19 * 3600.0])
        # Die Grenze folgt allein den Daten: Liegt die größte Lücke zwischen 8 und
        # 17 Uhr, beginnt der Tag um 17 Uhr; 0:30 und 8 Uhr zählen zu seinem Ende.
        self.assertEqual(kf.start_quartiles([8 * 3600.0, 17 * 3600.0, 18 * 3600.0,
                                             19 * 3600.0, 23 * 3600.0, 0.5 * 3600.0]),
                         [18.25 * 3600.0, 0.125 * 3600.0])
        self.assertIsNone(kf.start_quartiles([]))

    def test_longest_rest_names_its_session(self):
        short = self.figures("a", T0, gangs=(kf.GangInput(100.0, 200.0),
                                             kf.GangInput(500.0, 600.0)))
        long = self.figures("b", T0, gangs=(kf.GangInput(100.0, 200.0),
                                            kf.GangInput(2200.0, 2300.0)))
        rest = kf.statistics_figures([short, long], BERLIN)["rest"]
        self.assertEqual(rest["longest"], {"value": 2000.0, "session_id": "b"})


class DisplayTests(unittest.TestCase):
    def test_commercial_rounding_at_the_boundary(self):
        self.assertEqual((rounded(2.05, 1), rounded(-2.05, 1), rounded(4.945, 2)),
                         (2.1, -2.1, 4.95))
        self.assertEqual(figure(0.125, "€")["display"], 0.13)

    def test_hours_and_minutes_round_to_the_nearest_minute(self):
        self.assertEqual(figure(3570.0, "h:mm")["display"], 60)
        self.assertEqual(figure(3569.0, "h:mm")["display"], 59)
        self.assertEqual(figure(5367.0, "min")["display"], 89.5)

    def test_shares_whole_percent_and_largest_remainder(self):
        self.assertEqual(figure(0.755, "share")["display"], 76)
        self.assertEqual(largest_remainder([1 / 3, 1 / 3, 1 / 3]), [34, 33, 33])
        self.assertEqual(largest_remainder([0.0922, 0.5271, 0.3807]), [9, 53, 38])
        self.assertIsNone(largest_remainder([0.5, None, 0.5]))

    def test_curve_keeps_points_where_the_shown_temperature_changes(self):
        points = [(0.0, 20.04), (10.0, 20.01), (20.0, 20.06), (31.0, 20.12), (40.0, 20.14),
                  (95.0, 20.149)]
        self.assertEqual(curve(points), [[0.0, 20.0], [0.3, 20.1], [1.6, 20.1]])
        self.assertEqual(figure(points, "curve"), {"display": curve(points), "unit": "curve"})
        self.assertEqual(figure(None, "curve"), {"display": None, "unit": "curve"})
        self.assertEqual(curve([(0.0, 50.0)]), [[0.0, 50.0]])

    def test_missing_values_stay_missing(self):
        self.assertEqual(figure(None, "°C"), {"value": None, "display": None, "unit": "°C"})
        self.assertEqual(figure(None, "time")["display"], None)

    def test_session_presentation(self):
        raw = kf.session_figures(session(
            gangs=(kf.GangInput(1800.0, 2700.0),),
            phases=((0.0, "aufheizen"), (1000.0, "bereit"), (5000.0, "aus")),
            upper_temperature=series([(0, 78), (3000, 80), (4000, 82.5)]),
        ))
        shown = present_session(raw, kf.earlier_warmup([]))
        self.assertEqual(shown["warmup_end_at"]["value"], (T0 + timedelta(seconds=1000)).isoformat())
        self.assertEqual(shown["gangs"][0]["duration_seconds"], {
            "value": 900.0, "display": 15.0, "unit": "min"})
        control = shown["control_in_ready"]
        self.assertEqual(sum(control[key]["display"] for key in ("below", "within", "above")), 100)
        self.assertEqual(shown["earlier_warmup"]["sessions"], 0)

    def test_statistics_presentation_keeps_session_counts(self):
        raw = kf.statistics_figures([kf.session_figures(session())], BERLIN)
        shown = present_statistics(raw)
        self.assertEqual(shown["energy_kwh"], {"value": 2.0, "display": 2.0, "unit": "kWh",
                                               "sessions": 1})
        row = shown["per_session"][0]
        self.assertEqual(row["duration_seconds"]["display"], 120)
        self.assertEqual(row["infusions_in_gangs"]["display"], 0)
        warmup = shown["warmup"]["per_session"][0]
        self.assertEqual(warmup["curve"]["unit"], "curve")
        self.assertEqual(warmup["curve"]["display"], [[0.0, 80.0], [16.7, 80.0]])
        self.assertEqual(shown["by_program"]["unknown"]["warmup"]["median_seconds"]["display"], 17)


class ArchiveInputTests(unittest.TestCase):
    """Eingang einer archivierten Sitzung mit Zwischenspeicher je Sitzungsrevision."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "archive.sqlite"
        self.archive = Archive(self.path, "entry")
        self.archive._initialize(T0)

    def tearDown(self):
        self.directory.cleanup()

    @staticmethod
    def at(seconds):
        return (T0 + timedelta(seconds=seconds)).isoformat()

    def event(self, kind, seconds):
        return {"event_id": f"{kind}:{seconds}", "session_id": "s1", "kind": kind,
                "effective_at": self.at(seconds), "detected_at": self.at(seconds + 5)}

    def payload(self, configuration=True):
        close = self.event("door_close", 1800)
        infusion = self.event("infusion", 2000)
        gang = {
            "gang_id": "g1", "session_id": "s1", "started_at": self.at(1800),
            "detected_at": self.at(1810), "start_source_event_id": close["event_id"],
            "recognition_event_id": close["event_id"], "recognition_kind": "presence_entry",
            "start_basis": "door_close", "infusion_events": [infusion],
            "ended_at": self.at(2640), "end_event_id": "ventilation_confirmed:2640",
            "end_reason": "ventilation",
        }
        payload = {
            "timeline": {
                "session_id": "s1", "session_started_at": T0.isoformat(),
                "completed": [gang], "active": None, "retracted": [],
                "processed": [self.event("door_open", 1700), close, infusion,
                              self.event("door_open", 2600),
                              self.event("ventilation_confirmed", 2640),
                              self.event("door_close", 2650)],
            },
            "ended_at": self.at(7200),
            "base_phases": [{"at": self.at(0), "phase": "aufheizen"},
                            {"at": self.at(1000), "phase": "bereit"},
                            {"at": self.at(7000), "phase": "aus"}],
            "target_history": [{"at": self.at(0), "target_c": 80}],
            "heating": {"intervals": [{"started_at": self.at(0), "ended_at": self.at(1500)}]},
            "energy": {"estimated_kwh": 1.5, "measured_kwh": 0.5},
            "after_run_history": [{"started_at": self.at(2640), "ends_at": self.at(3300)}],
        }
        if configuration:
            payload["configuration"] = {"parameters": PARAMETERS, "control_mode": "automatic",
                                        "program_mode": "constant"}
        return payload

    def write(self, session_id="s1", payload=None, updated="u1", ended=True, started=None):
        payload = payload or self.payload()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "INSERT OR REPLACE INTO sessions(session_id,entry_id,started_at,updated_at,"
                "ended_at,payload) VALUES(?,?,?,?,?,?)",
                (session_id, "entry", started or T0.isoformat(), updated,
                 self.at(7200) if ended else None, json.dumps(payload)),
            )
            if session_id != "s1":
                return
            rows = [("measurement", self.at(t), {"position": p, "quantity": q, "value": v})
                    for t in range(0, 7201, 10)
                    for p, q, v in (("upper", "temperature", 80.0), ("lower", "temperature", 70.0),
                                    ("upper", "humidity", 30.0 if 2010 <= t < 2030 else 10.0),
                                    ("lower", "humidity", 12.0))]
            rows += [("detection", self.at(e[1] + 5), {"event": self.event(*e)}) for e in (
                ("door_open", 1700), ("door_close", 1800), ("infusion", 2000),
                ("door_open", 2600), ("door_close", 2650))]
            rows.append(("infusion_update", self.at(2040), {
                "source_event_id": "infusion:2000", "end": {"effective_at": self.at(2030)}}))
            db.executemany(
                "INSERT INTO records(entry_id,session_id,kind,received_at,payload) "
                "VALUES('entry','s1',?,?,?)",
                [(kind, at, json.dumps(data)) for kind, at, data in rows],
            )

    def test_retired_curve_values_in_archived_configuration_keep_key_figures(self):
        # E2a (K1): sessions archived with the former curve values stay readable.
        from test_retired_settings import RETIRED

        self.write()
        expected = self.archive.key_figures(volume_m3=6.0, price_eur_per_kwh=0.3)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DELETE FROM records")
        payload = self.payload()
        payload["configuration"]["parameters"] = {
            **PARAMETERS, **dict.fromkeys(RETIRED, 3.0),
        }
        self.write(payload=payload, updated="u2")
        figures = self.archive.key_figures(volume_m3=6.0, price_eur_per_kwh=0.3)
        self.assertEqual(figures[0]["figures"], expected[0]["figures"])
        self.assertEqual(figures[0]["figures"]["gang_count"], 1)

    def test_archived_session_inputs(self):
        self.write()
        entry = self.archive.key_figures(volume_m3=6.0, price_eur_per_kwh=0.3)[0]
        figures = entry["figures"]
        self.assertEqual(figures["gang_count"], 1)
        self.assertEqual(figures["sauna_time_seconds"], 840.0)
        self.assertEqual([d["door_class"] for d in figures["doors"]], ["entry", "exit"])
        self.assertEqual(figures["gangs"][0]["entry_opening"], 1700.0)
        self.assertAlmostEqual(figures["gangs"][0]["humidity_rise_mean_gm3"],
                               absolute_humidity(80, 30) - absolute_humidity(80, 10))
        self.assertEqual(figures["energy_kwh"], 2.0)
        self.assertAlmostEqual(figures["cost_eur"], 0.6)
        self.assertEqual(figures["oven_cooling"][0]["gang"], 1)
        self.assertEqual(figures["program"], {"kind": "constant"})
        self.assertEqual(figures["parameter_origin"], "session")
        # recorded before B2: no environment and no recognition records
        self.assertIsNone(figures["outside_temperature_c"])
        self.assertIsNone(figures["rejected_candidates"])

    def insert(self, rows):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executemany(
                "INSERT INTO records(entry_id,session_id,kind,received_at,payload) "
                "VALUES('entry','s1',?,?,?)",
                [(kind, self.at(at), json.dumps(data)) for kind, at, data in rows],
            )

    def environment(self, received, temperature, updated, measured=None, start=None):
        return ("environment", received, {
            "reason": "session_start" if (received < 1 if start is None else start)
            else "source_changed",
            "station": None, "condition": "cloudy",
            "measurement_time": self.at(measured) if measured is not None else None,
            "forecast_time": None, "source": {"mode": None, "interpolated": None},
            "updated_at": self.at(updated),
            "values": [
                {"key": "temperature", "state": str(temperature), "value": temperature,
                 "unit": "°C", "available": True, "last_updated": self.at(updated)},
                {"key": "humidity", "state": "unavailable", "value": None, "unit": "%",
                 "available": False, "last_updated": self.at(updated)},
            ],
        })

    def test_outside_temperature_and_rejected_candidates_from_the_new_records(self):
        self.write()
        self.insert([
            # the start entry is archived just after the session start
            self.environment(0.4, 5.5, -1800, measured=-5400),
            self.environment(1200, 4.0, 1200, measured=0),
            ("recognition_state", 40, {"at": self.at(31), "state": "closed", "probability": 0.99}),
            *(("recognition_rejected", at + 6, {
                "kind": "door_open", "effective_at": self.at(at), "decided_at": self.at(at + 5),
                "reason": "opening_form", "state": "open", "against": "recovery", "ratio": 200.0,
            }) for at in (1600, 2290, 2295, 3000)),
            ("recognition_episode", 1630, {
                "opened_at": self.at(1600), "closed_at": self.at(1604), "close_candidate_at": self.at(1604),
                "ended_at": self.at(1624), "decided_at": self.at(1625),
                "candidates": [self.at(1600)], "result": "rejected"}),
            ("recognition_episode", 2330, {
                "opened_at": self.at(2290), "closed_at": self.at(2300), "close_candidate_at": self.at(2300),
                "ended_at": self.at(2320), "decided_at": self.at(2321),
                "candidates": [self.at(2290), self.at(2295)], "result": "door"}),
        ])
        figures = self.archive.key_figures()[0]["figures"]
        self.assertEqual(figures["outside_temperature_c"], 5.5)
        self.assertEqual(figures["outside_temperature_measured_at"], -5400.0)
        self.assertEqual(figures["rejected_candidates"], {
            "candidates": 4, "rejected": 2, "rejected_in_episodes": 1, "rejected_single": 1,
            "rejected_episodes": 1, "taken_as_door": 2, "door_episodes": 1,
        })
        presented = self.archive.session_key_figures("s1")["figures"]
        self.assertEqual(presented["rejected_candidates"]["rejected"],
                         {"value": 2, "display": 2, "unit": "count"})
        self.assertEqual(presented["outside_temperature_c"]["display"], 5.5)
        self.assertEqual(presented["outside_temperature_measured_at"]["display"],
                         self.at(-5400))

    def test_without_an_entry_at_the_start_the_outside_temperature_is_missing(self):
        self.write()
        self.insert([self.environment(1200, 5.0, 1200, measured=0)])
        figures = self.archive.key_figures()[0]["figures"]
        self.assertIsNone(figures["outside_temperature_c"])
        self.assertIsNone(figures["outside_temperature_measured_at"])

    def test_ready_from_the_stored_ready_history_else_from_phases(self):
        self.write()
        self.assertEqual(self.archive.key_figures()[0]["figures"]["ready_at"], 1000.0)
        payload = self.payload()
        payload["ready_history"] = [{"at": self.at(900), "ready": True}]
        self.write(payload=payload, updated="u2")
        self.assertEqual(self.archive.key_figures()[0]["figures"]["ready_at"], 900.0)

    def test_cache_until_a_new_session_revision(self):
        self.write()
        with patch.object(archive_module, "session_figures",
                          wraps=archive_module.session_figures) as compute:
            self.archive.key_figures()
            self.archive.key_figures()
            self.assertEqual(compute.call_count, 1)
            self.write(updated="u2")
            self.archive.key_figures()
            self.assertEqual(compute.call_count, 2)
            self.archive.key_figures(volume_m3=6.0)
            self.assertEqual(compute.call_count, 3)

    def test_running_session_is_not_evaluated_and_deleted_session_leaves_the_cache(self):
        self.write(ended=False)
        self.assertEqual(self.archive.key_figures(), [])
        self.assertIsNone(self.archive.session_key_figures("s1"))
        self.write()
        self.archive.key_figures()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DELETE FROM sessions WHERE session_id='s1'")
        self.assertEqual(self.archive.key_figures(), [])
        self.assertEqual(self.archive._figure_cache, {})

    def test_missing_settings_use_the_next_stored_session_and_are_marked(self):
        self.write(payload=self.payload(configuration=False))
        later = self.payload()
        later["timeline"] = {**later["timeline"], "session_id": "s2"}
        self.write("s2", later, started=(T0 + timedelta(days=1)).isoformat())
        entries = {e["session_id"]: e for e in self.archive.key_figures()}
        self.assertEqual(entries["s1"]["parameter_session"], "s2")
        self.assertEqual(entries["s1"]["figures"]["parameter_origin"], "next_session")

    def second(self):
        later = self.payload()
        later["timeline"] = {**later["timeline"], "session_id": "s2"}
        self.write("s2", later, started=(T0 + timedelta(days=1)).isoformat())

    def test_prepared_figures_make_the_first_statistics_request_warm(self):
        # LZ2: Vorbereitung nach dem Start; derselbe Zwischenspeicher wie auf Anfrage.
        self.write()
        self.second()
        cold = Archive(self.path, "entry").key_figure_statistics(
            BERLIN, volume_m3=6.0, price_eur_per_kwh=0.3)
        self.archive.prepare_key_figures(volume_m3=6.0, price_eur_per_kwh=0.3)
        with patch.object(archive_module, "session_figures",
                          wraps=archive_module.session_figures) as compute:
            warm = self.archive.key_figure_statistics(
                BERLIN, volume_m3=6.0, price_eur_per_kwh=0.3)
            self.archive.session_key_figures("s1", volume_m3=6.0, price_eur_per_kwh=0.3)
            self.assertEqual(compute.call_count, 0)
            # Andere Einstellungen gelten nicht als vorbereitet.
            self.archive.key_figure_statistics(BERLIN, volume_m3=7.0, price_eur_per_kwh=0.3)
            self.assertEqual(compute.call_count, 2)
        self.assertEqual(warm, cold)

    def test_preparation_stops_when_the_archive_closes_or_pauses_for_a_backup(self):
        self.write()
        self.second()
        original = archive_module.session_figures
        for stop in ("close", "backup"):
            with self.subTest(stop=stop):
                self.archive._figure_cache.clear()
                self.archive.closed = False
                self.archive.resume.set()
                calls = []

                def compute(value, calls=calls, stop=stop):
                    calls.append(value)
                    if stop == "close":
                        self.archive.closed = True
                    else:
                        self.archive.resume.clear()
                    return original(value)

                with patch.object(archive_module, "session_figures", side_effect=compute):
                    self.archive.prepare_key_figures(volume_m3=6.0, price_eur_per_kwh=0.3)
                self.assertEqual(len(calls), 1)
                self.assertEqual(list(self.archive._figure_cache), ["s1"])
        # Eine Anfrage rechnet den Rest wie bisher.
        self.archive.resume.set()
        self.archive.closed = False
        with patch.object(archive_module, "session_figures",
                          wraps=archive_module.session_figures) as compute:
            self.archive.key_figures(volume_m3=6.0, price_eur_per_kwh=0.3)
            self.assertEqual(compute.call_count, 1)

    def test_request_during_preparation_waits_instead_of_computing_again(self):
        import threading

        self.write()
        started, release = threading.Event(), threading.Event()
        calls = []
        original = archive_module.session_figures

        def compute(value):
            calls.append(value)
            started.set()
            release.wait(5)
            return original(value)

        with patch.object(archive_module, "session_figures", side_effect=compute):
            preparing = threading.Thread(target=self.archive.prepare_key_figures)
            preparing.start()
            self.assertTrue(started.wait(5))
            answers = []
            request = threading.Thread(target=lambda: answers.append(
                self.archive.session_key_figures("s1")))
            request.start()
            request.join(0.2)
            self.assertTrue(request.is_alive(), "die Anfrage wartet auf die Vorbereitung")
            release.set()
            preparing.join(5)
            request.join(5)
        self.assertEqual(len(calls), 1)
        self.assertEqual(answers[0]["session_id"], "s1")

    def test_session_and_statistics_results(self):
        self.write()
        result = self.archive.session_key_figures("s1", volume_m3=6.0)
        self.assertEqual(result["figures"]["gang_count"]["display"], 1)
        statistics = self.archive.key_figure_statistics(BERLIN, season=2026, part="autumn")
        self.assertEqual(statistics["session_ids"], ["s1"])
        self.assertEqual(statistics["period"], {"season": 2026, "part": "autumn"})
        self.assertIsNone(statistics["figures"]["groups"])
        periods = [{"season": 2026, "label": "2026/27",
                    "parts": [{"part": "autumn", "label": "Herbst"}]}]
        self.assertEqual(statistics["periods"], periods)
        whole = self.archive.key_figure_statistics(BERLIN)
        self.assertEqual([g["label"] for g in whole["figures"]["groups"]], ["2026/27"])
        season = self.archive.key_figure_statistics(BERLIN, season=2026)
        self.assertEqual([g["label"] for g in season["figures"]["groups"]], ["Herbst"])
        gang = statistics["figures"]["per_session"][0]["gangs"][0]
        self.assertEqual(gang["start"]["unit"], "time")
        self.assertEqual(statistics["figures"]["sessions"]["value"], 1)
        other = self.archive.key_figure_statistics(BERLIN, summer=2026)
        self.assertEqual(other["session_ids"], [])
        # Die angebotenen Zeiträume gelten unabhängig vom gewählten.
        self.assertEqual(other["periods"], periods)


class TestSessionLabelTests(unittest.TestCase):
    """G25: Kennzeichnung im Archiv; automatisch bei einem Kalibrierprotokoll (Nutzer 10.11.)."""

    setUp = ArchiveInputTests.setUp
    tearDown = ArchiveInputTests.tearDown
    at = staticmethod(ArchiveInputTests.at)
    event = ArchiveInputTests.event
    payload = ArchiveInputTests.payload
    write = ArchiveInputTests.write

    def test_label_by_hand_and_by_calibration(self):
        import asyncio

        self.write()
        self.assertEqual(self.archive.session_key_figures("s1")["test"], False)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO records(entry_id,session_id,kind,received_at,payload) "
                       "VALUES('entry','s1','calibration',?,?)", (self.at(100), "{}"))
        result = self.archive.session_key_figures("s1")
        self.assertEqual((result["test"], result["test_source"]), (True, "calibration"))
        statistics = self.archive.key_figure_statistics(BERLIN)
        self.assertEqual(statistics["test_session_ids"], ["s1"])
        self.assertEqual(statistics["figures"]["test_sessions"]["value"], 1)

        self.write("s2", started=(T0 + timedelta(days=1)).isoformat(), ended=False)
        revision = self.archive.revision

        async def exercise():
            await self.archive.start()
            try:
                self.assertFalse(await self.archive.label_session("s1", False, T0, "user"))
                with self.assertRaises(KeyError):
                    await self.archive.label_session("missing", True, T0)
                with self.assertRaisesRegex(ValueError, "Abschluss"):
                    await self.archive.label_session("s2", True, T0)
            finally:
                await self.archive.close()

        asyncio.run(exercise())
        self.assertGreater(self.archive.revision, revision)
        result = self.archive.session_key_figures("s1")
        self.assertEqual((result["test"], result["test_source"]), (False, "user"))
        self.assertEqual(self.archive.key_figure_statistics(BERLIN)["test_session_ids"], [])


class ProgramTests(unittest.TestCase):
    def test_program_kind_from_session_state_and_settings(self):
        programs = [{"id": "p", "name": "Höhe", "start_c": 85, "end_c": 95,
                     "distribution_gangs": 5}]
        self.assertEqual(kf.session_program({}, {"control_mode": "manual"}, ()),
                         {"kind": "manual"})
        self.assertEqual(kf.session_program({}, {}, ((0.0, "manuell"),)), {"kind": "manual"})
        self.assertEqual(kf.session_program({"temperature_program_mode": "constant"},
                                            {"program_mode": "progressive"}, ()),
                         {"kind": "constant"})
        self.assertEqual(kf.session_program({}, {
            "program_mode": "progressive", "selected_program_id": "p",
            "temperature_programs": programs}, ()), {
            "kind": "program", "id": "p", "name": "Höhe", "steps": [85, 88, 90, 93, 95]})
        self.assertIsNone(kf.session_program({}, {}, ()))


class CabinVolumeSettingTests(unittest.TestCase):
    def test_cabin_volume_is_optional_and_positive(self):
        definition = BY_KEY["cabin_volume_m3"]
        self.assertEqual((definition.default, definition.optional, definition.unit),
                         (None, True, "m³"))
        self.assertIsNone(definition.validate(None))
        self.assertEqual(definition.validate(6), 6.0)
        with self.assertRaises(ParameterError):
            definition.validate(0)


if __name__ == "__main__":
    unittest.main()
