"""Verlaufsprojektion: Messungen spaltenweise, ohne Verlust der vom Verlauf gelesenen Felder."""

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from test_foundation import T0, bindings, parameters

from custom_components.ha_sauna.archive import Archive
from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.models import (
    Measurement,
    Position,
    Quantity,
    SampleIdentity,
)
from custom_components.ha_sauna.presentation import (
    history_measurement_columns,
    public_measurement,
)

HISTORY_KINDS = ("measurement", "source_snapshot", "phase", "session")


def expand(columns):
    """Die Datensätze, die das Panel aus den Spalten bildet (historyPageRecords)."""
    times = dict(map(tuple, columns["record_received_at"]))
    result = []
    for index, identity in enumerate(columns["id"]):
        role = dict(columns["roles"][columns["role"][index]])
        kind = role.pop("kind")
        received_at = columns["received_at"][index]
        result.append({
            "id": identity, "kind": kind, "received_at": times.get(identity, received_at),
            "payload": {**role, "value": columns["value"][index], "received_at": received_at},
            "display": {"absolute_humidity": {
                "value": columns["absolute_humidity"][index],
                "received_at": received_at,
                "valid_until": columns["absolute_humidity_valid_until"][index],
            }},
        })
    return result


def read_fields(record, *, public=False):
    """Die Felder einer Messung, die der Verlauf liest (Index, Werte, Steuerung)."""
    payload = record["payload"]
    keys = ("position", "quantity", "value", "received_at") + (
        ("source_matches_binding",) if public else ("source",)
    )
    return {
        "id": record["id"], "kind": record["kind"], "received_at": record["received_at"],
        "payload": {key: payload[key] for key in keys if key in payload},
        "display": record["display"],
    }


class HistoryColumnTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.archive = Archive(Path(self.directory.name) / "columns.sqlite", "entry")
        await self.archive.start()
        self.controller = Controller(parameters())
        self.controller.begin_session("columns", T0)
        self.configuration = {
            "parameters": parameters().as_dict(), "bindings": bindings().as_dict(),
        }
        self.sources = {
            quantity: self.configuration["bindings"][f"upper_{quantity.value}"]
            for quantity in Quantity
        }

    async def asyncTearDown(self):
        await self.archive.close()
        self.directory.cleanup()

    def measurement(self, second, quantity, value, *, sample=None, source=None):
        at = T0 + timedelta(seconds=second)
        return Measurement(
            Position.UPPER, quantity, value, "" if value is None else str(value),
            source or self.sources[quantity], at, at,
            SampleIdentity("sensors/test", "boot", sample, second * 10**9) if sample else None,
        )

    async def fill(self):
        self.archive.save_session(self.controller.session, T0, self.configuration)
        for second in range(1, 6):
            for quantity, value in ((Quantity.TEMPERATURE, 70.0 + second),
                                    (Quantity.HUMIDITY, 10.5 + second)):
                self.archive.append("measurement", T0 + timedelta(seconds=second),
                                    self.measurement(second, quantity, value, sample=second),
                                    "columns")
        # Anfangsmessung mit eigener Archivzeit, fremde Quelle, fehlender Wert.
        self.archive.append("source_snapshot", T0 + timedelta(seconds=7),
                            self.measurement(2, Quantity.TEMPERATURE, 72.0), "columns")
        self.archive.append("measurement", T0 + timedelta(seconds=8),
                            self.measurement(8, Quantity.TEMPERATURE, 80.0,
                                             source="sensor.fremd"), "columns")
        self.archive.append("measurement", T0 + timedelta(seconds=9),
                            self.measurement(9, Quantity.HUMIDITY, None), "columns")
        # Ältere Form ohne Quelle und Empfangszeit bleibt ein einzelner Datensatz.
        self.archive.append("measurement", T0 + timedelta(seconds=10),
                            {"position": "upper", "quantity": "temperature", "value": 73},
                            "columns")
        self.archive.append("phase", T0 + timedelta(seconds=11), {"phase": "bereit"}, "columns")
        await self.archive.flush()
        return self.archive.read("columns", humidity_display=True, kinds=HISTORY_KINDS,
                                 now=T0 + timedelta(seconds=20))

    async def test_columns_keep_every_read_field_order_and_unusual_records(self):
        page = await self.fill()
        records, columns = history_measurement_columns(page["records"])
        measured = expand(columns)
        self.assertEqual(columns["id"], sorted(columns["id"]))
        self.assertEqual(len(columns["roles"]), 4)
        self.assertEqual(len(measured), 13)
        # Zusammengeführt ergeben Spalten und übrige Datensätze die Archivreihenfolge.
        merged = sorted(records + measured, key=lambda record: record["id"])
        self.assertEqual([record["id"] for record in merged],
                         [record["id"] for record in page["records"]])
        originals = {record["id"]: record for record in page["records"]}
        for record in measured:
            self.assertEqual(record, read_fields(originals[record["id"]]))
        self.assertTrue(any(item["display"]["absolute_humidity"]["value"] is not None
                            for item in measured))
        self.assertEqual(columns["record_received_at"],
                         [[record["id"], record["received_at"]] for record in page["records"]
                          if record["kind"] == "source_snapshot"])
        legacy = [record for record in records if record["kind"] == "measurement"]
        self.assertEqual(legacy, [originals[legacy[0]["id"]]])
        self.assertNotIn("source", legacy[0]["payload"])
        self.assertEqual({record["kind"] for record in records},
                         {"measurement", "phase", "session"})
        # Rohfelder bleiben der Abfrage ohne Projektion vorbehalten.
        self.assertTrue(all("raw_value" in originals[identity]["payload"]
                            for identity in columns["id"]))

    async def test_public_columns_match_public_measurement_without_sources(self):
        page = await self.fill()
        _records, columns = history_measurement_columns(
            page["records"], configuration=self.configuration, public=True,
        )
        self.assertNotIn("sensor.", str(columns))
        self.assertEqual(
            sorted((role["position"], role["quantity"], role["source_matches_binding"])
                   for role in columns["roles"] if role["kind"] == "measurement"),
            [("upper", "humidity", True), ("upper", "temperature", False),
             ("upper", "temperature", True)],
        )
        originals = {record["id"]: record for record in page["records"]}
        for record in expand(columns):
            original = originals[record["id"]]
            shown = {**original, "payload": public_measurement(
                original["payload"], configuration=self.configuration)}
            self.assertEqual(record, read_fields(shown, public=True))

    async def test_history_pages_keep_their_cursor_with_columns(self):
        page = await self.fill()
        identities, after = [], 0
        while True:
            current = self.archive.read("columns", after=after, limit=4, humidity_display=True,
                                        kinds=HISTORY_KINDS, now=T0 + timedelta(seconds=20))
            records, columns = history_measurement_columns(current["records"])
            identities += sorted([record["id"] for record in records] + columns["id"])
            if current["next_after"] is None:
                break
            self.assertEqual(current["next_after"], max(identities))
            after = current["next_after"]
        self.assertEqual(identities, [record["id"] for record in page["records"]])


if __name__ == "__main__":
    unittest.main()
