"""History humidity uses original source evidence across archive pages."""

import copy
from dataclasses import replace
from datetime import timedelta
import unittest
from unittest.mock import patch

from aiohttp import ClientSession
from homeassistant.auth.const import GROUP_ID_USER

import harness
import test_archive as archive_fixture
import test_panel_detail_projection as panel_fixture
from test_detector import measurement
from test_foundation import T0

from custom_components.ha_sauna.core.models import Position, Quantity, Session
from custom_components.ha_sauna.core.moisture import current_absolute_humidity
from custom_components.ha_sauna.core.parameters import BY_KEY
from custom_components.ha_sauna.history_humidity import project_absolute_humidity


def _measurements(result):
    return [
        record
        for record in result["records"]
        if record["kind"] in {"measurement", "source_snapshot"}
    ]


def _display(record):
    return record["display"]["absolute_humidity"]


def _expected(temperature, humidity, at, timeout):
    return current_absolute_humidity(
        {
            f"{temperature.position.value}_temperature": temperature,
            f"{humidity.position.value}_humidity": humidity,
        },
        temperature.position,
        at,
        timeout,
    )


class ArchiveHumidityTests(unittest.IsolatedAsyncioTestCase):
    # Reuse the existing fixture without inheriting or rediscovering its tests.
    asyncSetUp = archive_fixture.ArchiveTests.asyncSetUp
    asyncTearDown = archive_fixture.ArchiveTests.asyncTearDown

    @property
    def timeout(self):
        return self.config["parameters"]["sensor_timeout_seconds"]

    @property
    def temperature(self):
        return self.config["parameters"]["standard_temperature_c"]

    @property
    def humidity(self):
        return harness.HA_INPUTS["seed_measurement"]

    @property
    def temperature_step(self):
        return BY_KEY["standard_temperature_c"].number_step

    def append(
        self,
        quantity,
        value,
        second,
        *,
        position=Position.UPPER,
        source=None,
        kind="measurement",
        outer_second=None,
    ):
        item = measurement(
            position,
            quantity,
            value,
            second,
            source=source
            or self.config["bindings"][f"{position.value}_{quantity.value}"],
        )
        at = (
            item.received_at
            if outer_second is None
            else (T0 + timedelta(seconds=outer_second))
        )
        self.archive.append(kind, at, item, "s")
        return item

    def read(self, **options):
        return self.archive.read(
            "s",
            kinds=("measurement", "source_snapshot"),
            now=T0,
            **options,
        )

    async def test_pages_keep_temperature_changes_nulls_and_originals(self):
        step = self.timeout / 10
        temperature = self.append(Quantity.TEMPERATURE, self.temperature, 0)
        humidity = self.append(Quantity.HUMIDITY, self.humidity, 0)
        changed = self.append(
            Quantity.TEMPERATURE,
            self.temperature + self.temperature_step,
            step,
        )
        self.append(Quantity.HUMIDITY, None, 2 * step)
        self.append(Quantity.TEMPERATURE, self.temperature, 3 * step)
        await self.archive.flush()
        original = copy.deepcopy(self.read())
        shown = self.read(humidity_display=True)
        values = [_display(record)["value"] for record in shown["records"]]
        self.assertIsNone(values[0])
        self.assertAlmostEqual(
            values[1],
            _expected(
                temperature,
                humidity,
                humidity.received_at,
                self.timeout,
            ),
        )
        self.assertAlmostEqual(
            values[2],
            _expected(
                changed,
                humidity,
                changed.received_at,
                self.timeout,
            ),
        )
        self.assertNotEqual(values[1], values[2])
        self.assertEqual(values[3:], [None, None])

        paged = []
        after = 0
        while True:
            page = self.read(after=after, limit=2, humidity_display=True)
            paged.extend(page["records"])
            if page["next_after"] is None:
                break
            self.assertGreater(page["next_after"], after)
            after = page["next_after"]
        self.assertEqual(paged, shown["records"])
        self.assertEqual(
            {key: value for key, value in shown.items() if key != "records"},
            {key: value for key, value in original.items() if key != "records"},
        )
        self.assertEqual(
            [
                {key: value for key, value in record.items() if key != "display"}
                for record in shown["records"]
            ],
            original["records"],
        )
        self.assertEqual(
            project_absolute_humidity(
                original["records"],
                bindings=self.config["bindings"],
                timeout_seconds=self.timeout,
            ),
            shown["records"],
        )
        self.assertEqual(self.read(), original)

    async def test_snapshot_clock_preserves_exact_expiry_and_then_unavailable(self):
        temperature = self.append(Quantity.TEMPERATURE, self.temperature, 0)
        humidity = self.append(Quantity.HUMIDITY, self.humidity, 0)
        self.append(
            Quantity.HUMIDITY,
            self.humidity,
            0,
            kind="source_snapshot",
            outer_second=self.timeout / 2,
        )
        boundary = self.append(
            Quantity.TEMPERATURE,
            self.temperature,
            self.timeout,
        )
        self.append(
            Quantity.TEMPERATURE,
            self.temperature,
            self.timeout + timedelta.resolution.total_seconds(),
        )
        await self.archive.flush()
        records = self.read(humidity_display=True)["records"]
        expires = (T0 + timedelta(seconds=self.timeout)).isoformat()
        self.assertNotEqual(records[2]["received_at"], T0.isoformat())
        self.assertEqual(
            _display(records[2]),
            {
                "value": _expected(temperature, humidity, T0, self.timeout),
                "received_at": T0.isoformat(),
                "valid_until": expires,
            },
        )
        self.assertEqual(
            _display(records[3]),
            {
                "value": _expected(
                    temperature, humidity, boundary.received_at, self.timeout
                ),
                "received_at": boundary.received_at.isoformat(),
                "valid_until": expires,
            },
        )
        self.assertIsNotNone(_display(records[3])["value"])
        self.assertIsNone(_display(records[4])["value"])
        self.assertIsNone(_display(records[4])["valid_until"])
        page = self.read(
            after=records[2]["id"],
            limit=1,
            humidity_display=True,
        )
        self.assertEqual(page["records"], [records[3]])

    async def test_old_snapshot_does_not_load_context_between_freshness_windows(self):
        temperature = self.append(Quantity.TEMPERATURE, self.temperature, 0)
        humidity = self.append(Quantity.HUMIDITY, self.humidity, 0)
        self.append(Quantity.TEMPERATURE, self.temperature, 2 * self.timeout)
        self.append(
            Quantity.HUMIDITY,
            self.humidity,
            0,
            kind="source_snapshot",
            outer_second=4 * self.timeout,
        )
        fresh_temperature = self.append(
            Quantity.TEMPERATURE,
            self.temperature + self.temperature_step,
            4 * self.timeout,
        )
        fresh_humidity = self.append(
            Quantity.HUMIDITY,
            self.humidity / 2,
            4 * self.timeout,
        )
        await self.archive.flush()
        original = self.read()["records"]
        with patch(
            "custom_components.ha_sauna.archive.project_absolute_humidity",
            wraps=project_absolute_humidity,
        ) as projection:
            page = self.read(after=original[2]["id"], humidity_display=True)
        projection.assert_called_once()
        context = projection.call_args.kwargs["context"]
        self.assertEqual(
            {record["id"] for record in context},
            {record["id"] for record in original[:2]},
        )
        records = page["records"]
        self.assertEqual(
            [record["id"] for record in records],
            [record["id"] for record in original[3:]],
        )
        self.assertAlmostEqual(
            _display(records[0])["value"],
            _expected(temperature, humidity, T0, self.timeout),
        )
        self.assertIsNone(_display(records[1])["value"])
        self.assertAlmostEqual(
            _display(records[2])["value"],
            _expected(
                fresh_temperature,
                fresh_humidity,
                fresh_humidity.received_at,
                self.timeout,
            ),
        )

    async def test_reordered_sources_obey_reception_time_and_original_id(self):
        step = self.timeout / 10
        self.append(Quantity.TEMPERATURE, self.temperature, 0)
        future = self.append(Quantity.HUMIDITY, self.humidity / 2, 3 * step)
        humidity = self.append(Quantity.HUMIDITY, self.humidity, 0)
        first = self.append(
            Quantity.TEMPERATURE,
            self.temperature + self.temperature_step,
            step,
        )
        later_id = self.append(Quantity.HUMIDITY, self.humidity / 3, step / 2)
        second = self.append(
            Quantity.TEMPERATURE,
            self.temperature + 2 * self.temperature_step,
            2 * step,
        )
        third = self.append(
            Quantity.TEMPERATURE,
            self.temperature + 3 * self.temperature_step,
            4 * step,
        )
        await self.archive.flush()
        records = self.read(humidity_display=True)["records"]
        for index, temperature, paired in (
            (3, first, humidity),
            (5, second, later_id),
            (6, third, future),
        ):
            with self.subTest(original_id=records[index]["id"]):
                self.assertAlmostEqual(
                    _display(records[index])["value"],
                    _expected(
                        temperature, paired, temperature.received_at, self.timeout
                    ),
                )
        page = self.read(
            after=records[2]["id"],
            limit=1,
            humidity_display=True,
        )
        self.assertEqual(page["records"], [records[3]])

    async def test_saved_binding_and_position_keep_source_pairs_separate(self):
        step = self.timeout / 10
        self.append(Quantity.TEMPERATURE, self.temperature, 0)
        lower_humidity = self.append(
            Quantity.HUMIDITY,
            self.humidity / 2,
            0,
            position=Position.LOWER,
        )
        self.append(
            Quantity.HUMIDITY,
            self.humidity,
            step,
            source=self.config["bindings"]["lower_humidity"],
        )
        self.append(Quantity.TEMPERATURE, self.temperature, 2 * step)
        upper_humidity = self.append(Quantity.HUMIDITY, self.humidity, 3 * step)
        lower_temperature = self.append(
            Quantity.TEMPERATURE,
            self.temperature,
            4 * step,
            position=Position.LOWER,
        )
        upper_temperature = self.append(
            Quantity.TEMPERATURE,
            self.temperature,
            5 * step,
        )
        await self.archive.flush()
        records = self.read(humidity_display=True)["records"]
        self.assertEqual(
            [_display(record)["value"] for record in records[:4]],
            [None, None, None, None],
        )
        self.assertAlmostEqual(
            _display(records[5])["value"],
            _expected(
                lower_temperature,
                lower_humidity,
                lower_temperature.received_at,
                self.timeout,
            ),
        )
        self.assertAlmostEqual(
            _display(records[6])["value"],
            _expected(
                upper_temperature,
                upper_humidity,
                upper_temperature.received_at,
                self.timeout,
            ),
        )
        self.assertNotEqual(
            _display(records[5])["value"], _display(records[6])["value"]
        )


class PanelHumidityApiTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = panel_fixture.PanelDetailProjectionTests.asyncSetUp
    asyncTearDown = panel_fixture.PanelDetailProjectionTests.asyncTearDown

    async def test_history_uses_saved_configuration_and_public_display_is_safe(self):
        runtime = self.runtime
        at = runtime._clock()
        runtime._clock = lambda: at
        current = copy.deepcopy(runtime.configuration.as_snapshot())
        saved = copy.deepcopy(current)
        definition = BY_KEY["sensor_timeout_seconds"]
        timeout = current["parameters"][definition.key] + definition.number_step
        if timeout > definition.maximum:
            timeout = current["parameters"][definition.key] - definition.number_step
        saved["parameters"][definition.key] = timeout
        position = Position.UPPER
        for quantity in Quantity:
            role = f"{position.value}_{quantity.value}"
            saved["bindings"][role] += "_historical"
        identity = "humidity-history"
        runtime.archive.save_session(Session.create(identity, at), at, saved)
        values = {
            Quantity.TEMPERATURE: saved["parameters"]["standard_temperature_c"],
            Quantity.HUMIDITY: harness.HA_INPUTS["seed_measurement"],
        }
        inputs = {}
        for quantity, value in values.items():
            inputs[quantity] = replace(
                measurement(
                    position,
                    quantity,
                    value,
                    0,
                    source=saved["bindings"][f"{position.value}_{quantity.value}"],
                ),
                received_at=at,
            )
            runtime.archive.append("measurement", at, inputs[quantity], identity)
        await runtime.archive.flush()
        original = copy.deepcopy(runtime.archive.read(identity, now=at))
        async with ClientSession(headers=self.headers) as client:
            async with client.get(
                self.base + "/archive",
                params={
                    "session_id": identity,
                    "projection": "history",
                },
            ) as response:
                self.assertEqual(response.status, 200, await response.text())
                shown = await response.json()
            async with client.get(
                self.base + "/archive",
                params={
                    "session_id": identity,
                },
            ) as response:
                self.assertEqual(response.status, 200, await response.text())
                raw = await response.json()
        display = _display(_measurements(shown)[-1])
        self.assertEqual(
            display,
            {
                "value": _expected(
                    inputs[Quantity.TEMPERATURE], inputs[Quantity.HUMIDITY], at, timeout
                ),
                "received_at": at.isoformat(),
                "valid_until": (at + timedelta(seconds=timeout)).isoformat(),
            },
        )
        self.assertIsNotNone(display["value"])
        self.assertEqual(raw["records"], original["records"])

        user = await self.hass.auth.async_create_user(
            "Öffentliche Feuchte",
            group_ids=[GROUP_ID_USER],
        )
        token = await self.hass.auth.async_create_refresh_token(
            user,
            client_id="http://localhost/",
        )
        headers = {
            "Authorization": "Bearer "
            + (self.hass.auth.async_create_access_token(token)),
        }
        async with ClientSession(headers=headers) as client:
            async with client.get(
                self.base + "/archive",
                params={
                    "session_id": identity,
                    "projection": "history",
                },
            ) as response:
                self.assertEqual(response.status, 200, await response.text())
                public = await response.json()
        self.assertNotIn("configuration", public["session"])
        for record in _measurements(public):
            self.assertNotIn("source", record["payload"])
            self.assertEqual(
                set(_display(record)), {"value", "received_at", "valid_until"}
            )
        self.assertEqual(_display(_measurements(public)[-1]), display)
        self.assertEqual(runtime.configuration.as_snapshot(), current)
        self.assertEqual(runtime.archive.read(identity, now=at), original)
