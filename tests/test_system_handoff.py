"""Closed system results retain the existing archive and preview wire values."""

import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

from custom_components.ha_sauna.archive import Archive
from custom_components.ha_sauna.core.history import measurement_window
from custom_components.ha_sauna.core.model_handoff import HandoffContext, ModelHandoff
from custom_components.ha_sauna.core.system_handoff import (
    ArchiveRecord,
    ArchiveSessionList,
    ArchiveSessionNotFound,
    ArchiveSessionOutput,
    EnvironmentSnapshot,
    MeasurementWindow,
    archive_read_output,
    to_wire,
)
from test_cooling import at, controller
from test_foundation import T0, bindings, parameters
from tools.local_preview import Preview


class EnvironmentSnapshotTests(unittest.TestCase):
    def test_existing_preview_values_and_original_source_times_roundtrip(self):
        result = Preview().state()["environment"]
        snapshot = EnvironmentSnapshot.from_result(result)
        self.assertEqual(snapshot.to_wire(), result)
        self.assertEqual(snapshot.measurement_time, result["measurement_time"])
        self.assertNotEqual(snapshot.measurement_time, snapshot.updated_at)
        wind = next(value for value in snapshot.values if value.key == "wind_direction")
        self.assertEqual(wind.value, "W")
        result["values"][0]["value"] = 999
        result["station"]["name"] = "Changed later"
        self.assertEqual(snapshot.values[0].value, 16.4)
        self.assertEqual(snapshot.station.name, "Beispielstation")
        ModelHandoff(HandoffContext("entry", "environment", T0), None, snapshot)
        with self.assertRaises(FrozenInstanceError):
            snapshot.values[0].value = 42

    def test_unconfigured_result_keeps_all_nulls_and_the_empty_values_list(self):
        result = {
            "configured": False,
            "station": None,
            "condition": None,
            "values": [],
            "measurement_time": None,
            "forecast_time": None,
            "source": {"mode": None, "interpolated": None},
            "updated_at": None,
        }
        self.assertEqual(EnvironmentSnapshot.from_result(result).to_wire(), result)
        result.pop("source")
        with self.assertRaises(KeyError):
            EnvironmentSnapshot.from_result(result)

    def test_measurement_window_is_the_existing_result_without_a_second_clock(self):
        for end, now in ((None, at(100)), (at(20), at(100)), (at(20), at(2000))):
            with self.subTest(end=end, now=now):
                result = measurement_window(T0, end, now)
                with patch(
                    "custom_components.ha_sauna.core.history.measurement_window",
                    side_effect=AssertionError(
                        "The supplied result is already calculated"
                    ),
                ):
                    output = MeasurementWindow.from_result(result)
                    self.assertEqual(output.to_wire(), result)


class ArchiveOutputTests(unittest.IsolatedAsyncioTestCase):
    def test_record_optional_headers_preserve_presence_and_api_values(self):
        base = {
            "id": 1,
            "entry_id": "entry",
            "session_id": "s",
            "kind": "source_state",
            "received_at": T0.isoformat(),
            "payload": {
                "role": "heater_power",
                "display": {"absolute_humidity": 12.3},
            },
        }
        for headers in (
            {},
            {"display": {"power_w": 2400}},
            {"display": {"power_w": None}},
            {"display": None},
            {"projection": "session_state"},
            {"projection": None},
            {"display": None, "projection": "session_state"},
        ):
            with self.subTest(headers=headers):
                result = {**base, **headers}
                record = ArchiveRecord.from_result(result)
                self.assertEqual(record.to_wire(), result)
                self.assertEqual(set(record.optional_headers), set(headers))
        result = {**base, "display": {"power_w": 2400}}
        record = ArchiveRecord.from_result(result)
        result["display"]["power_w"] = 0
        self.assertEqual(record.to_wire()["display"], {"power_w": 2400})
        with self.assertRaises(TypeError):
            ArchiveRecord.from_result({**base, "unknown_header": None})

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.archive = Archive(Path(self.temp.name) / "handoff.sqlite", "entry")
        await self.archive.start()
        self.owner = controller()
        config = {
            "bindings": bindings().as_dict(),
            "parameters": parameters().as_dict(),
        }
        self.archive.save_session(self.owner.session, T0, config)
        # Open record payloads must not depend on a second record-kind catalogue.
        self.archive.append(
            "future_diagnostic",
            at(1),
            {
                "new": {"values": [1, None, "retained"]},
                "explicit_null": None,
            },
            "s",
        )
        self.archive.append("future_sequence", at(2), [1, {"detail": True}], "s")
        await self.archive.flush()

    async def asyncTearDown(self):
        await self.archive.close()
        self.temp.cleanup()

    async def test_real_read_page_roundtrips_without_queries_or_projection(self):
        result = self.archive.read("s", now=at(3))
        expected = self.archive.read("s", now=at(3))
        with (
            patch.object(
                self.archive, "read", side_effect=AssertionError("No new read")
            ),
            patch(
                "custom_components.ha_sauna.archive.project_archive",
                side_effect=AssertionError("No new projection"),
            ),
        ):
            output = ArchiveSessionOutput.from_result(result)
            self.assertEqual(output.to_wire(), expected)
        self.assertEqual(
            output.measurement_window.started_at,
            expected["measurement_window"]["started_at"],
        )
        result["session"]["configuration"]["bindings"].clear()
        diagnostic = next(
            item for item in result["records"] if item["kind"] == "future_diagnostic"
        )
        diagnostic["payload"]["new"]["values"].append("changed")
        self.assertEqual(output.to_wire(), expected)
        self.assertIsNone(output.next_after)
        ModelHandoff(HandoffContext("entry", "archive", T0), None, output)

    async def test_list_missing_session_and_empty_page_remain_distinct(self):
        listing = self.archive.read()
        output = archive_read_output(None, listing)
        self.assertIsInstance(output, ArchiveSessionList)
        self.assertEqual(output.to_wire(), listing)
        self.assertEqual(to_wire(output), listing)
        missing = archive_read_output("missing", self.archive.read("missing"))
        self.assertIsInstance(missing, ArchiveSessionNotFound)
        self.assertEqual(missing.session_id, "missing")
        self.assertIsNone(missing.to_wire())
        self.assertIsNone(to_wire(missing))
        empty = archive_read_output(
            "s", self.archive.read("s", after=2**63 - 1, now=T0)
        )
        self.assertIsInstance(empty, ArchiveSessionOutput)
        self.assertEqual(empty.records, ())
        self.assertEqual(empty.to_wire()["records"], [])
        self.assertEqual(ArchiveSessionList.from_result([]).to_wire(), [])

    async def test_pagination_keeps_the_supplied_cursor_and_complete_record_frame(self):
        result = self.archive.read("s", now=at(3), limit=1)
        output = ArchiveSessionOutput.from_result(result)
        self.assertEqual(output.next_after, result["next_after"])
        record = output.records[0]
        self.assertIsInstance(record, ArchiveRecord)
        self.assertEqual(to_wire(record), result["records"][0])
        self.assertEqual(record.entry_id, "entry")
        with self.assertRaises(TypeError):
            output.session["configuration"] = None
        incomplete = dict(result["records"][0])
        incomplete.pop("received_at")
        with self.assertRaises(TypeError):
            ArchiveRecord.from_result(incomplete)
