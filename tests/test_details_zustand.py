"""B4c Details · Zustand: Messabstand, Gültigkeit, K-Z2 und Archiv und Start."""

import asyncio
import json
import sqlite3
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta

import test_key_figures as fixtures

from custom_components.ha_sauna.core.display import (
    first_ready_at,
    position_difference_means,
    position_values,
)
from custom_components.ha_sauna.core.models import Measurement, Position, Quantity

T0 = datetime(2026, 11, 2, 17, 0, tzinfo=UTC)


def at(seconds):
    return (T0 + timedelta(seconds=seconds)).isoformat()


def measurement(position, quantity, value):
    return Measurement(Position(position), Quantity(quantity), value, str(value),
                       f"sensor.{position}_{quantity}", T0)


class PositionValueTests(unittest.TestCase):
    def test_spacing_per_role_and_overall_validity(self):
        values = [measurement("upper", "temperature", 85.0), measurement("upper", "humidity", 12.0),
                  measurement("lower", "temperature", 70.0)]
        current = {role: {"state": "current"}
                   for role in ("upper_temperature", "upper_humidity", "lower_temperature")}
        result = position_values(values, current, {"upper_temperature": 1.0})
        upper, lower = result["positions"]
        self.assertEqual(upper["temperature_spacing_seconds"], 1.0)
        self.assertIsNone(upper["humidity_spacing_seconds"])
        self.assertIsNone(lower["temperature_spacing_seconds"])
        self.assertTrue(result["valid"])
        stale = {**current, "upper_humidity": {"state": "stale"}}
        self.assertFalse(position_values(values, stale)["valid"])
        self.assertFalse(position_values([])["valid"], "no measurement is not all valid")


class DifferenceMeanTests(unittest.TestCase):
    def test_time_weighted_mean_over_the_session_and_from_first_ready(self):
        points = [(at(0), "upper", 80.0), (at(0), "lower", 70.0),
                  (at(600), "upper", 90.0), (at(600), "lower", 75.0),
                  (at(900), "upper", None)]
        result = position_difference_means(points, started_at=at(0), ended_at=at(1200),
                                           ready_at=at(600))
        # Oben 80 für 600 s, 90 für 600 s; unten 70 und 75: Mittel 85 − 72,5.
        self.assertAlmostEqual(result["session_c"], 12.5)
        self.assertAlmostEqual(result["ready_c"], 15.0)
        self.assertEqual(result["ready_at"], at(600))

    def test_without_ready_or_lower_position(self):
        points = [(at(0), "upper", 80.0), (at(0), "lower", 70.0)]
        result = position_difference_means(points, started_at=at(0), ended_at=at(60))
        self.assertAlmostEqual(result["session_c"], 10.0)
        self.assertIsNone(result["ready_c"])
        self.assertIsNone(position_difference_means(
            [(at(0), "upper", 80.0)], started_at=at(0), ended_at=at(60)))

    def test_first_ready_mark(self):
        history = [{"at": at(10), "ready": False}, {"at": at(20), "ready": True},
                   {"at": at(30), "ready": True}]
        self.assertEqual(first_ready_at(history), at(20))
        self.assertIsNone(first_ready_at([]))
        self.assertIsNone(first_ready_at(None))


class ArchiveOverviewTests(unittest.TestCase):
    setUp = fixtures.ArchiveInputTests.setUp
    tearDown = fixtures.ArchiveInputTests.tearDown
    at = staticmethod(fixtures.ArchiveInputTests.at)
    write = fixtures.ArchiveInputTests.write
    payload = fixtures.ArchiveInputTests.payload
    event = fixtures.ArchiveInputTests.event

    def test_counts_size_and_temperature_points_of_the_session(self):
        self.write()
        overview = self.archive.detail_overview("s1")
        self.assertEqual(overview["sessions"], 1)
        # 721 Zeitpunkte × 4 Messungen, 5 Erkennungen, ein Aufgussende, ein abgeflachtes Abklingen (AF)
        self.assertEqual(overview["records"], 721 * 4 + 7)
        self.assertGreater(overview["size_bytes"], 0)
        self.assertIsNone(overview["start_seconds"], "the archive was not started")
        self.assertEqual(len(overview["points"]), 721 * 2)
        self.assertEqual(overview["points"][:2], [(self.at(0), "upper", 80.0),
                                                  (self.at(0), "lower", 70.0)])
        self.assertIsNone(self.archive.detail_overview()["points"])
        means = position_difference_means(overview["points"], started_at=self.at(0),
                                          ended_at=self.at(7200), ready_at=self.at(1000))
        self.assertAlmostEqual(means["session_c"], 10.0)
        self.assertAlmostEqual(means["ready_c"], 10.0)

    def test_points_are_extended_by_new_records_only(self):
        self.write()
        first = self.archive.detail_overview("s1")["points"]
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "INSERT INTO records(entry_id,session_id,kind,received_at,payload) "
                "VALUES('entry','s1','measurement',?,?)",
                (self.at(7210), json.dumps({"position": "upper", "quantity": "temperature",
                                            "value": 81.0})),
            )
        second = self.archive.detail_overview("s1")["points"]
        self.assertEqual(second, [*first, (self.at(7210), "upper", 81.0)])

    def test_start_duration_is_measured(self):
        async def run():
            await self.archive.start()
            try:
                return self.archive.start_seconds
            finally:
                await self.archive.close()

        seconds = asyncio.run(run())
        self.assertIsNotNone(seconds)
        self.assertGreaterEqual(seconds, 0)


if __name__ == "__main__":
    unittest.main()
