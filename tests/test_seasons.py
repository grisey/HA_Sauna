"""Jahreszeiten nach astronomischem Beginn (Meeus, Kapitel 27) und Zeiträume der Statistik."""

import unittest
from datetime import UTC, datetime, timedelta

from custom_components.ha_sauna.core.seasons import (
    _delta_t_seconds,
    period_label,
    period_of,
    period_start,
    solar_term,
)


class SolarTermTests(unittest.TestCase):
    def test_meeus_example_june_solstice_1962(self):
        # Beispiel 27.a: JDE 2437837.39245 = 21.06.1962, 21:25:08 dynamische Zeit.
        dynamical = solar_term(1962, "summer") + timedelta(seconds=_delta_t_seconds(1962.5))
        expected = datetime(1962, 6, 21, 21, 25, 8, tzinfo=UTC)
        self.assertLess(abs((dynamical - expected).total_seconds()), 2)

    def test_published_equinoxes_and_solstices(self):
        # Veröffentlichte Zeitpunkte (UTC, auf die Minute) der Jahre 2024 bis 2026.
        published = {
            (2024, "spring"): (3, 20, 3, 6), (2024, "summer"): (6, 20, 20, 51),
            (2024, "autumn"): (9, 22, 12, 44), (2024, "winter"): (12, 21, 9, 21),
            (2025, "spring"): (3, 20, 9, 1), (2025, "summer"): (6, 21, 2, 42),
            (2025, "autumn"): (9, 22, 18, 19), (2025, "winter"): (12, 21, 15, 3),
            (2026, "spring"): (3, 20, 14, 46), (2026, "summer"): (6, 21, 8, 24),
            (2026, "autumn"): (9, 23, 0, 5), (2026, "winter"): (12, 21, 20, 50),
        }
        for (year, term), (month, day, hour, minute) in published.items():
            with self.subTest(year=year, term=term):
                expected = datetime(year, month, day, hour, minute, tzinfo=UTC)
                self.assertLess(abs((solar_term(year, term) - expected).total_seconds()), 120)

    def test_years_outside_the_table_are_rejected(self):
        with self.assertRaises(ValueError):
            solar_term(999, "spring")


class PeriodTests(unittest.TestCase):
    def test_season_parts_and_summer_change_at_the_astronomical_instant(self):
        autumn = solar_term(2026, "autumn")
        second = timedelta(seconds=1)
        self.assertEqual(period_of(autumn - second), {"summer": 2026})
        self.assertEqual(period_of(autumn), {"season": 2026, "part": "autumn"})
        winter = solar_term(2026, "winter")
        self.assertEqual(period_of(winter), {"season": 2026, "part": "winter"})
        self.assertEqual(period_of(datetime(2027, 1, 10, tzinfo=UTC)),
                         {"season": 2026, "part": "winter"})
        spring = solar_term(2027, "spring")
        self.assertEqual(period_of(spring - second), {"season": 2026, "part": "winter"})
        self.assertEqual(period_of(spring), {"season": 2026, "part": "spring"})
        self.assertEqual(period_of(solar_term(2027, "summer")), {"summer": 2027})

    def test_local_time_does_not_move_the_boundary(self):
        autumn = solar_term(2026, "autumn")
        berlin = autumn.astimezone(datetime(2026, 9, 1).astimezone().tzinfo)
        self.assertEqual(period_of(berlin), period_of(autumn))

    def test_labels_and_order(self):
        self.assertEqual(period_label({"season": 2026}), "2026/27")
        self.assertEqual(period_label({"season": 2099}), "2099/00")
        self.assertEqual(period_label({"summer": 2027}), "Sommer 2027")
        self.assertEqual(period_label({"season": 2026, "part": "spring"}), "Frühling")
        ordered = sorted([{"summer": 2027}, {"season": 2026}, {"summer": 2026}],
                         key=period_start)
        self.assertEqual(ordered, [{"summer": 2026}, {"season": 2026}, {"summer": 2027}])


if __name__ == "__main__":
    unittest.main()
