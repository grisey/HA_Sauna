"""Read-only detail projections preserve archive evidence and historical models."""

import copy
import unittest

from aiohttp import ClientSession
from homeassistant.auth.const import GROUP_ID_USER

from harness import create_sauna, credentials, retain_session, start_hass
from custom_components.ha_sauna.core.models import Session


class PanelDetailProjectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass, self.temp = await start_hass()
        self.entry = await create_sauna(self.hass)
        self.runtime = self.entry.runtime_data
        self.base = (
            f"http://127.0.0.1:{self.hass.http.server_port}/api/ha_sauna/"
            + self.entry.entry_id
        )
        self.headers = {"Authorization": "Bearer " + await credentials(self.hass)}

    async def asyncTearDown(self):
        await self.hass.async_block_till_done()
        await self.hass.async_stop(force=True)
        self.temp.cleanup()

    async def test_detail_kinds_power_and_snapshot_projection_preserve_originals(self):
        runtime = self.runtime
        await runtime.set_operation(True)
        retain_session(runtime)
        identity = runtime.session.session_id
        at = runtime._clock()
        payloads = {
            "illuminance": {
                "state": "250",
                "attributes": {"unit_of_measurement": "lx"},
            },
            "heating_observation": {
                "source": "power",
                "heating": True,
                "power_w": 6000,
            },
            "notice": {"reason": "heating_without_temperature_rise"},
            "manual_light": {"value": 20},
            "manual_heater": {"value": False},
            "heater_override_rejected": {"reason": "Ofenkühlung läuft"},
            "manual_phase_end": {"purpose": "after_run", "ended_at": at},
            "manual_session_light_end": {
                "purpose": "session_gap",
                "light_ended_at": at,
            },
            "interruption": {"reason": "unload"},
        }
        for kind, payload in payloads.items():
            runtime.archive.append(kind, at, payload, identity)
        power_cases = [
            ("6", "kW", "power", 6000),
            ("2", "MW", "power", None),
            ("-1", "W", "power", None),
            ("2", None, "power", None),
            ("2", "W", "temperature", None),
        ]
        for value, unit, device_class, _expected in power_cases:
            runtime.archive.append(
                "source_state",
                at,
                {
                    "role": "heater_power",
                    "source": "sensor.test_power",
                    "state": value,
                    "attributes": {
                        "unit_of_measurement": unit,
                        "device_class": device_class,
                    },
                },
                identity,
            )
        await runtime.archive.flush()
        originals = runtime.archive.read(identity, limit=5000, now=at)
        unchanged = copy.deepcopy(originals["records"])
        async with ClientSession(headers=self.headers) as client:
            async with client.get(
                self.base + "/archive",
                params={"session_id": identity, "projection": "history"},
            ) as response:
                self.assertEqual(response.status, 200, await response.text())
                shown = await response.json()
            kinds = {item["kind"] for item in shown["records"]}
            self.assertTrue(set(payloads) <= kinds)
            powers = [
                item
                for item in shown["records"]
                if item["kind"] == "source_state"
                and item["payload"].get("source") == "sensor.test_power"
            ]
            self.assertEqual(
                [item["display"]["power_w"] for item in powers],
                [item[3] for item in power_cases],
            )
            snapshots = [item for item in shown["records"] if item["kind"] == "session"]
            self.assertTrue(snapshots)
            raw_by_id = {item["id"]: item for item in originals["records"]}
            for item in snapshots:
                self.assertEqual(item["projection"], "session_state")
                self.assertNotIn("timeline", item["payload"])
                self.assertEqual(
                    item["payload"]["configuration"],
                    raw_by_id[item["id"]]["payload"]["configuration"],
                )
            after = powers[1]["id"]
            async with client.get(
                self.base + "/archive",
                params={
                    "session_id": identity,
                    "projection": "history",
                    "after": after,
                },
            ) as response:
                continued = await response.json()
                self.assertTrue(
                    all(item["id"] > after for item in continued["records"])
                )
            async with client.get(
                self.base + "/archive", params={"session_id": identity}
            ) as response:
                raw = await response.json()
                self.assertTrue(
                    all(
                        "timeline" in item["payload"]
                        for item in raw["records"]
                        if item["kind"] == "session"
                    )
                )
                self.assertTrue(
                    all(
                        "projection" not in item and "display" not in item
                        for item in raw["records"]
                    )
                )
        original_ids = {item["id"] for item in unchanged}
        self.assertEqual(
            [
                item
                for item in runtime.archive.read(identity, limit=5000, now=at)[
                    "records"
                ]
                if item["id"] in original_ids
            ],
            unchanged,
        )

        user = await self.hass.auth.async_create_user(
            "Öffentliche Details", group_ids=[GROUP_ID_USER]
        )
        token = await self.hass.auth.async_create_refresh_token(
            user, client_id="http://localhost/"
        )
        headers = {
            "Authorization": "Bearer " + self.hass.auth.async_create_access_token(token)
        }
        async with ClientSession(headers=headers) as client:
            async with client.get(
                self.base + "/archive",
                params={"session_id": identity, "projection": "history"},
            ) as response:
                public = await response.json()
                self.assertEqual(response.status, 200)
                self.assertTrue(
                    all(
                        item["kind"] in {"measurement", "source_snapshot", "phase"}
                        for item in public["records"]
                    )
                )
                self.assertNotIn("configuration", public["session"])
                self.assertNotIn("configuration_model", public["session"])

    async def test_historical_model_uses_saved_configuration_not_current_hardware(self):
        at = self.runtime._clock()
        current = self.runtime.configuration.as_options()
        cases = tuple(
            (mode, source)
            for mode in ("automatic", "manual")
            for source in ("proxy", "ha_presence")
        )
        for mode, source in cases:
            options = copy.deepcopy(current)
            options["presence_source"] = source
            options["control_mode"] = mode
            options["bindings"].pop("lower_temperature", None)
            options["bindings"].pop("lower_humidity", None)
            options["bindings"]["presence"] = "binary_sensor.saved_presence"
            identity = f"historical-{mode}-{source}"
            self.runtime.archive.save_session(Session.create(identity, at), at, options)
        self.runtime.archive.save_session(
            Session.create("legacy-missing-config", at), at, {}
        )
        await self.runtime.archive.flush()
        async with ClientSession(headers=self.headers) as client:
            for mode, source in cases:
                async with client.get(
                    self.base + "/archive",
                    params={
                        "session_id": f"historical-{mode}-{source}",
                        "projection": "history",
                    },
                ) as response:
                    shown = await response.json()
                    self.assertEqual(response.status, 200, shown)
                    model = shown["session"]["configuration_model"]
                    self.assertEqual(model["measurement_positions"], ["upper"])
                    self.assertEqual(
                        model["presence"]["direct"], source == "ha_presence"
                    )
                    self.assertEqual(
                        model["regulation_causes"],
                        ["gang_heat", "provisional_door_heat", "cooling"]
                        if mode == "automatic" else [],
                    )
            async with client.get(
                self.base + "/archive",
                params={"session_id": "legacy-missing-config", "projection": "history"},
            ) as response:
                shown = await response.json()
                self.assertIsNone(shown["session"]["configuration_model"])
        self.assertEqual(self.runtime.configuration.as_options(), current)
