"""Optional environment sources stay display-only in a real HA instance."""

import json
import unittest
from types import MappingProxyType
from unittest.mock import patch

from aiohttp import ClientSession
from homeassistant.auth.const import GROUP_ID_ADMIN, GROUP_ID_USER
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import entity_registry as er

from custom_components.ha_sauna.environment import environment_snapshot
from harness import create_sauna, retain_session, start_hass


class EnvironmentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass, self.temp = await start_hass()
        self.weather = "weather.synthetic_environment"
        self.temperature = "sensor.synthetic_environment_temperature"
        self.humidity = "sensor.synthetic_environment_humidity"
        self.measured = "sensor.synthetic_environment_measured"
        self.forecast = "sensor.synthetic_environment_forecast"
        self.weather_attrs = {
            "temperature": 17.5, "temperature_unit": "°C",
            "humidity": 64, "pressure": 1007, "pressure_unit": "hPa",
            "wind_speed": 8, "wind_speed_unit": "km/h", "wind_bearing": 220,
            "station_id": "TEST_STATION", "station_name": "Synthetic station",
            "private_debug_note": "synthetic-private-marker",
        }
        self.hass.states.async_set(self.weather, "cloudy", self.weather_attrs)

    async def asyncTearDown(self):
        await self.hass.async_block_till_done()
        await self.hass.async_stop(force=True)
        self.temp.cleanup()

    def snapshot(self, **bindings):
        return environment_snapshot(self.hass, bindings)

    @staticmethod
    def values(snapshot):
        return {item["key"]: item for item in snapshot["values"]}

    async def test_optional_sources_and_weather_attribute_fallback(self):
        empty = self.snapshot()
        self.assertFalse(empty["configured"])
        self.assertEqual(empty["values"], [])
        snapshot = self.snapshot(environment_weather=self.weather)
        self.assertTrue(snapshot["configured"])
        self.assertEqual(snapshot["condition"], "cloudy")
        values = self.values(snapshot)
        self.assertEqual(values["temperature"]["value"], 17.5)
        self.assertEqual(values["humidity"]["value"], 64)
        self.assertEqual(values["wind_direction"]["value"], 220)
        self.assertNotIn("absolute_humidity", values)
        self.assertNotIn("precipitation", values)
        self.assertEqual(snapshot["station"]["name"], "Synthetic station")
        self.assertIsNone(snapshot["measurement_time"])
        for attributes, key in (
            ({"temperature_unit": "%"}, "temperature"),
            ({"temperature": "not-a-number"}, "temperature"),
            ({"temperature": 10**400}, "temperature"),
            ({"wind_bearing": "not-a-direction"}, "wind_direction"),
        ):
            with self.subTest(attributes=attributes):
                self.hass.states.async_set(self.weather, "cloudy", {
                    **self.weather_attrs, **attributes,
                })
                value = self.values(self.snapshot(environment_weather=self.weather))[key]
                self.assertIsNone(value["value"])
                self.assertFalse(value["available"])
        self.hass.states.async_set(self.weather, "cloudy", {
            **self.weather_attrs, "wind_bearing": "W",
        })
        self.assertEqual(self.values(self.snapshot(environment_weather=self.weather))[
            "wind_direction"]["value"], "W")

    async def test_explicit_sensor_precedence_and_unavailable_never_becomes_zero(self):
        bindings = {
            "environment_weather": self.weather,
            "environment_temperature": self.temperature,
        }
        attrs = {"device_class": "temperature", "unit_of_measurement": "°C"}
        for raw, expected in (("0", 0), ("-3.25", -3.25),
                              ("unknown", None), ("unavailable", None)):
            with self.subTest(raw=raw):
                self.hass.states.async_set(self.temperature, raw, attrs)
                value = self.values(environment_snapshot(self.hass, bindings))["temperature"]
                self.assertEqual(value["value"], expected)
                self.assertEqual(value["available"], expected is not None)
        self.hass.states.async_remove(self.temperature)
        value = self.values(environment_snapshot(self.hass, bindings))["temperature"]
        self.assertIsNone(value["value"])
        self.assertFalse(value["available"])
        self.hass.states.async_set(self.temperature, "20", {
            "device_class": "temperature", "unit_of_measurement": "%",
        })
        self.assertIsNone(self.values(environment_snapshot(self.hass, bindings))[
            "temperature"]["value"])

    async def test_original_measurement_time_is_separate_from_ha_update(self):
        measured = "2025-02-03T08:20:00+00:00"
        forecast = "2025-02-03T11:00:00+00:00"
        self.hass.states.async_set(self.measured, measured, {"device_class": "timestamp"})
        self.hass.states.async_set(self.forecast, forecast, {"device_class": "timestamp"})
        snapshot = self.snapshot(
            environment_weather=self.weather,
            environment_measurement_time=self.measured,
            environment_forecast_time=self.forecast,
        )
        self.assertEqual(snapshot["measurement_time"], measured)
        self.assertEqual(snapshot["forecast_time"], forecast)
        self.assertEqual(self.values(snapshot)["temperature"]["last_updated"],
                         self.hass.states.get(self.weather).last_updated.isoformat())
        self.assertNotEqual(snapshot["measurement_time"], snapshot["updated_at"])
        self.hass.states.async_set(self.measured, "unknown", {"device_class": "timestamp"})
        self.assertIsNone(self.snapshot(environment_measurement_time=self.measured)[
            "measurement_time"])

    async def test_dwd_entry_metadata_preserves_mixed_and_interpolated_source(self):
        entry = ConfigEntry(
            domain="dwd_weather", title="Synthetic DWD station", version=1,
            minor_version=1, source="user", unique_id="synthetic-dwd",
            data={"data_type": "report_data", "interpolate": False},
            options={"data_type": "mixed_data", "interpolate": True},
            discovery_keys=MappingProxyType({}), subentries_data=(),
        )
        # Register real HA metadata without loading or contacting optional DWD.
        with patch.object(self.hass.config_entries, "async_setup", return_value=True):
            await self.hass.config_entries.async_add(entry)
        registry = er.async_get(self.hass)
        registered = registry.async_get_or_create(
            "weather", "dwd_weather", "synthetic-weather-source",
            config_entry=entry, suggested_object_id="synthetic_registered_weather",
        )
        self.hass.states.async_set(registered.entity_id, "cloudy", self.weather_attrs)
        snapshot = self.snapshot(environment_weather=registered.entity_id)
        self.assertEqual(snapshot["source"], {"mode": "mixed_data", "interpolated": True})
        self.assertNotIn(entry.entry_id, json.dumps(snapshot))
        self.assertNotIn(registered.entity_id, json.dumps(snapshot))
        # An additional unregistered source must not inherit DWD provenance.
        self.hass.states.async_set(self.temperature, "19", {
            "device_class": "temperature", "unit_of_measurement": "°C",
        })
        mixed_sources = self.snapshot(
            environment_weather=registered.entity_id,
            environment_temperature=self.temperature,
        )
        self.assertEqual(mixed_sources["source"], {"mode": None, "interpolated": None})

    async def test_normal_user_sees_environment_without_source_entity_ids(self):
        entry = await create_sauna(self.hass, binding_overrides={
            "environment_weather": self.weather,
        })
        # HA promotes the first regular user to owner, regardless of groups.
        await self.hass.auth.async_create_user(
            "Environment owner", group_ids=[GROUP_ID_ADMIN]
        )
        user = await self.hass.auth.async_create_user("Environment reader", group_ids=[GROUP_ID_USER])
        self.assertFalse(user.is_owner)
        self.assertFalse(user.is_admin)
        refresh = await self.hass.auth.async_create_refresh_token(user, client_id="http://localhost/")
        headers = {"Authorization": "Bearer " + self.hass.auth.async_create_access_token(refresh)}
        url = f"http://127.0.0.1:{self.hass.http.server_port}/api/ha_sauna/{entry.entry_id}/state"
        async with ClientSession(headers=headers) as client, client.get(url) as response:
            self.assertEqual(response.status, 200, await response.text())
            state = await response.json()
        self.assertFalse(state["permissions"]["admin"])
        self.assertEqual(self.values(state["environment"])["temperature"]["value"], 17.5)
        self.assertNotIn("bindings", state["configuration"])
        self.assertNotIn(self.weather, json.dumps(state))
        self.assertNotIn("private_debug_note", json.dumps(state))
        self.assertNotIn("synthetic-private-marker", json.dumps(state))

    async def test_weather_updates_do_not_enter_control_or_archive(self):
        entry = await create_sauna(self.hass, binding_overrides={
            "environment_weather": self.weather,
        }, parameter_overrides={"sensor_timeout_seconds": 60, "feedback_timeout_seconds": 60})
        runtime = entry.runtime_data
        await runtime.set_operation(True)
        retain_session(runtime)
        await self.hass.async_block_till_done()
        with patch.object(runtime, "device_input", wraps=runtime.device_input) as input_spy:
            self.hass.states.async_set(self.weather, "rainy", {
                **self.weather_attrs, "temperature": 16,
            })
            await self.hass.async_block_till_done()
            input_spy.assert_not_called()
        self.assertFalse(any(key.startswith("environment_") for key in runtime.device.bindings))
        self.assertFalse(any(key.startswith("environment_") for key in runtime.device.states))
        self.assertFalse(any(key.startswith("environment_") for key in runtime.device.measurements))
        await runtime.archive.flush()
        stored = runtime.archive.read(runtime.session.session_id)
        self.assertEqual(stored["session"]["configuration"]["bindings"][
            "environment_weather"], self.weather)
        source_records = [record for record in stored["records"]
                          if record["kind"] in {"measurement", "source_state"}]
        self.assertNotIn(self.weather, json.dumps(source_records))
        self.assertFalse(any(
            record["payload"].get("role", "").startswith("environment_")
            for record in source_records
        ))
        self.assertEqual(self.values(self.snapshot(environment_weather=self.weather))[
            "temperature"]["value"], 16)
