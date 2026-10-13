"""Real authenticated panel endpoints, session guard and HA options reload."""
import asyncio
from copy import deepcopy
from math import nextafter
import unittest
from unittest.mock import patch
from aiohttp import ClientSession
from homeassistant.auth.const import GROUP_ID_ADMIN, GROUP_ID_USER
from harness import advance_with_fresh_sources, create_sauna, start_hass
from test_settings import async_timeout
from custom_components.ha_sauna.appearance import appearance_options, validate_appearance
from custom_components.ha_sauna.core.defaults import section
from custom_components.ha_sauna.core.parameters import BY_KEY, Parameters


class PanelAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_button_session_gesture_permission_validation_and_session_lock(self):
        self.hass.config_entries.async_update_entry(self.entry, options={
            **self.entry.options, "control_input_mode": "button",
        })
        await self.hass.async_block_till_done()
        entity = self.entry.runtime_data.configuration.bindings.values["control_input"]
        original = self.hass.states.get(entity)
        self.hass.states.async_set(entity, original.state, {
            **original.attributes,
            "event_types": ["single_push", "double_push", "triple_push", "long_push"],
        })
        await self.hass.async_block_till_done()
        user = await self.hass.auth.async_create_user("Taster", group_ids=[GROUP_ID_USER])
        token = await self.hass.auth.async_create_refresh_token(user, client_id="http://localhost/")
        headers = {"Authorization": "Bearer " + self.hass.auth.async_create_access_token(token)}
        url = self.base + "/" + self.entry.entry_id
        async with ClientSession(headers=headers) as client:
            for gesture in ("double", "triple", "long"):
                async with client.post(url + "/button-gesture", json={"gesture": gesture}) as response:
                    self.assertEqual(response.status, 200, await response.text())
                    self.assertEqual((await response.json())["button_session_gesture"], gesture)
                await self.hass.async_block_till_done()
                self.assertEqual(self.entry.options["button_session_gesture"], gesture)
            for value in ("short", "single", 1, True, None, [], "quadruple"):
                async with client.post(url + "/button-gesture", json={"gesture": value}) as response:
                    self.assertEqual(response.status, 400, await response.text())
            async with client.get(url + "/state") as response:
                state = await response.json()
                self.assertEqual(state["configuration"]["button_session_gesture"], "long")
                self.assertEqual(state["button_session_gestures"], ["long", "double", "triple"])
            await self.entry.runtime_data.set_operation(True)
            async with client.post(url + "/button-gesture", json={"gesture": "double"}) as response:
                self.assertEqual(response.status, 409, await response.text())
        denied = await self.hass.auth.async_create_user("Kein Zugriff", group_ids=[])
        token = await self.hass.auth.async_create_refresh_token(denied, client_id="http://localhost/")
        headers = {"Authorization": "Bearer " + self.hass.auth.async_create_access_token(token)}
        async with ClientSession(headers=headers) as client:
            async with client.post(url + "/button-gesture", json={"gesture": "double"}) as response:
                self.assertEqual(response.status, 403)

    async def test_button_gestures_follow_reported_types_and_survive_unavailable(self):
        self.hass.config_entries.async_update_entry(self.entry, options={
            **self.entry.options, "control_input_mode": "button",
        })
        await self.hass.async_block_till_done()
        runtime = self.entry.runtime_data
        entity = runtime.configuration.bindings.values["control_input"]
        original = self.hass.states.get(entity)
        url = self.base + "/" + self.entry.entry_id
        self.hass.states.async_set(entity, original.state, {
            **original.attributes, "event_types": ["single_push", "long_push"],
        })
        await self.hass.async_block_till_done()
        async with ClientSession(headers=self.headers) as client:
            async with client.get(url + "/state") as response:
                self.assertEqual((await response.json())["button_session_gestures"], ["long"])
            async with client.post(url + "/button-gesture", json={"gesture": "double"}) as response:
                self.assertEqual(response.status, 400, await response.text())
            self.hass.states.async_set(entity, "unavailable")
            await self.hass.async_block_till_done()
            async with client.get(url + "/state") as response:
                self.assertEqual((await response.json())["button_session_gestures"], ["long"])
            self.hass.states.async_set(entity, original.state, {
                **original.attributes, "event_types": ["single_push"],
            })
            await self.hass.async_block_till_done()
            async with client.get(url + "/state") as response:
                self.assertEqual((await response.json())["button_session_gestures"], [])
            self.assertEqual(runtime.configuration.button_session_gesture, "long")

    async def test_archive_erase_permissions_session_guard_and_memory_invalidation(self):
        from harness import retain_session

        runtime = self.entry.runtime_data
        url = self.base + "/" + self.entry.entry_id + "/archive/erase"
        user = await self.hass.auth.async_create_user("Archive reader", group_ids=[GROUP_ID_USER])
        token = await self.hass.auth.async_create_refresh_token(user, client_id="http://localhost/")
        headers = {"Authorization": "Bearer " + self.hass.auth.async_create_access_token(token)}
        async with (
            ClientSession(headers=headers) as client,
            client.post(url, json={"reset": True}) as response,
        ):
            self.assertEqual(response.status, 403)
        identities = []
        async with ClientSession(headers=self.headers) as client:
            for _ in range(2):
                await runtime.set_operation(True)
                identities.append(runtime.session.session_id)
                retain_session(runtime)
                async with client.post(url, json={"reset": True}) as response:
                    self.assertEqual(response.status, 409)
                await runtime.set_operation(False)
                runtime.controller.finish_session(runtime._clock(), light_after_run=False)
                await runtime._cycle()
            await runtime.archive.flush()
            for body in ({}, {"reset": False}, {"reset": True, "session_id": "x"}):
                async with client.post(url, json=body) as response:
                    self.assertEqual(response.status, 400)
            async with client.post(url, json={"session_id": "missing"}) as response:
                self.assertEqual(response.status, 404)
            async with client.post(url, json={"session_id": identities[-1]}) as response:
                self.assertEqual(response.status, 200, await response.text())
                revision = (await response.json())["archive_revision"]
            self.assertIsNone(runtime.archive.read(identities[-1]))
            self.assertIsNotNone(runtime.archive.read(identities[0]))
            self.assertEqual([s.session_id for s in runtime.controller.completed_sessions], identities[:1])
            options = dict(self.entry.options)
            async with client.post(url, json={"reset": True}) as response:
                self.assertEqual(response.status, 200, await response.text())
                self.assertGreater((await response.json())["archive_revision"], revision)
            runtime.persist()
            await runtime.archive.flush()
            self.assertEqual(runtime.archive.read(), [])
            self.assertEqual(runtime.controller.completed_sessions, ())
            self.assertEqual(dict(self.entry.options), options)

    async def asyncSetUp(self):
        self.hass, self.temp = await start_hass()
        self.entry = await create_sauna(self.hass)
        self.base = f"http://127.0.0.1:{self.hass.http.server_port}/api/ha_sauna"
        user = await self.hass.auth.async_create_user("Panel test", group_ids=[GROUP_ID_ADMIN])
        token = await self.hass.auth.async_create_refresh_token(user, client_id="http://localhost/")
        self.headers = {"Authorization": "Bearer " + self.hass.auth.async_create_access_token(token)}

    async def asyncTearDown(self):
        # An options POST schedules HA's reload listener. Finish that real work
        # before stopping HA and deleting the isolated archive directory.
        await self.hass.async_block_till_done()
        await self.hass.async_stop(force=True)
        self.temp.cleanup()

    async def test_panel_control_parameters_and_server_side_session_lock(self):
        async with ClientSession(headers=self.headers) as client:
            async with client.get(self.base) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual((await response.json())[0]["entry_id"], self.entry.entry_id)
            url = self.base + "/" + self.entry.entry_id
            values = {**self.entry.options["parameters"], "target_temperature_c": 82}
            async with client.post(url + "/parameters", json=values) as response:
                self.assertEqual(response.status, 200, await response.text())
            await self.hass.async_block_till_done()
            self.assertEqual(self.entry.runtime_data.configuration.parameters.values["target_temperature_c"], 82)
            async with client.post(url + "/control", json={"enabled": True}) as response:
                self.assertEqual(response.status, 200, await response.text())
            identity = self.entry.runtime_data.session.session_id
            async with client.get(url + "/state") as response:
                self.assertEqual(response.status, 200, await response.text())
                state = await response.json()
                self.assertEqual(state["session"]["timeline"]["session_id"], identity)
                self.assertTrue(state["configuration_locked"])
            async with client.post(url + "/parameters", json={**values,"after_run_minutes":20,"oven_cooling_max_minutes":20}) as response:
                self.assertEqual(response.status, 409)
            async with client.post(url + "/control", json={"enabled": False}) as response:
                self.assertEqual(response.status, 200)
            async with client.get(url + "/state") as response:
                state = await response.json()
                self.assertNotIn("mechanical_timer", state)
                self.assertNotIn("mechanical_timer_ends_at", state)
            async with client.post(url + "/parameters", json={**values,"after_run_minutes":20,"oven_cooling_max_minutes":20}) as response:
                self.assertEqual(response.status, 409)

    async def test_override_parameter_api_enforces_the_catalog_limit_atomically(self):
        url = self.base + "/" + self.entry.entry_id
        definition = BY_KEY["manual_override_minutes"]
        maximum = definition.maximum
        async with ClientSession(headers=self.headers) as client:
            async with client.get(url + "/state") as response:
                state = await response.json()
                field = next(
                    item
                    for item in state["parameters"]
                    if item["key"] == "manual_override_minutes"
                )
                self.assertEqual(
                    (field["default"], field["maximum"], field["integer"]),
                    (definition.default, maximum, definition.integer),
                )
            for value in (nextafter(maximum, float("inf")), maximum * 2):
                before = dict(self.entry.options)
                runtime = self.entry.runtime_data
                configuration = runtime.configuration
                decision = runtime.controller.last_decision
                command = runtime.device.command
                async with client.post(
                    url + "/parameters",
                    json={
                        **before["parameters"],
                        "manual_override_minutes": value,
                    },
                ) as response:
                    self.assertEqual(response.status, 400, await response.text())
                await self.hass.async_block_till_done()
                self.assertEqual(dict(self.entry.options), before)
                self.assertIs(runtime.configuration, configuration)
                self.assertIs(runtime.controller.last_decision, decision)
                self.assertEqual(runtime.device.command, command)
                self.assertFalse(runtime.reconfiguring)
            for value in (maximum, maximum / 20):
                async with client.post(
                    url + "/parameters",
                    json={
                        **self.entry.options["parameters"],
                        "manual_override_minutes": value,
                    },
                ) as response:
                    self.assertEqual(response.status, 200, await response.text())
                await self.hass.async_block_till_done()
                self.assertEqual(
                    self.entry.options["parameters"]["manual_override_minutes"], value
                )
                self.assertEqual(
                    self.entry.runtime_data.configuration.parameters.values[
                        "manual_override_minutes"
                    ],
                    value,
                )

    async def test_loaded_override_controls_real_heater_and_light_deadlines(self):
        from datetime import UTC, datetime, timedelta
        from homeassistant.setup import async_setup_component
        from test_device_path import TestHeater, TestLight

        await async_setup_component(self.hass, "switch", {})
        await async_setup_component(self.hass, "light", {})
        heater, light = TestHeater(), TestLight()
        await self.hass.data["switch"].async_add_entities([heater])
        await self.hass.data["light"].async_add_entities([light])
        self.hass.states.async_set("binary_sensor.actual_heating", "off")
        baseline = {
            **self.entry.options,
            "bindings": {
                **self.entry.options["bindings"],
                "heater": heater.entity_id,
                "light": light.entity_id,
            },
        }
        url = self.base + "/" + self.entry.entry_id
        maximum = BY_KEY["manual_override_minutes"].maximum
        # This is the formerly stored value whose migration is under test,
        # not a copy of the current runtime limit.
        legacy = 20
        legacy_effective = min(legacy, maximum)
        shorter = maximum / 20
        async with ClientSession(headers=self.headers) as client:
            for stored, mode, seconds, effective in (
                (legacy, "automatic", legacy_effective * 60, legacy_effective),
                (shorter, "automatic", shorter * 60, shorter),
                (legacy, "manual", legacy_effective * 60 + 1, legacy_effective),
            ):
                with self.subTest(stored=stored, mode=mode):
                    # Each case starts from an acknowledged outside-light OFF.
                    # The preceding case can leave its last visible 80% at the
                    # start of a fade; choosing that same value is a valid no-op
                    # and would not exercise creation of an override deadline.
                    await self.hass.services.async_call(
                        "light", "turn_off", {"entity_id": light.entity_id},
                        blocking=True,
                    )
                    await self.hass.async_block_till_done()
                    self.assertFalse(light.is_on)
                    for role in ("upper_temperature", "lower_temperature"):
                        entity = baseline["bindings"][role]
                        self.hass.states.async_set(
                            entity, str(baseline["parameters"]["light_reference_temperature_c"]),
                            self.hass.states.get(entity).attributes,
                        )
                    self.hass.config_entries.async_update_entry(
                        self.entry,
                        options={
                            **baseline,
                            "control_mode": mode,
                            "bindings": {
                                **baseline["bindings"],
                                "heater_feedback": "binary_sensor.actual_heating",
                            },
                            "parameters": {
                                **baseline["parameters"],
                                "manual_override_minutes": stored,
                            },
                        },
                    )
                    await self.hass.async_block_till_done()
                    runtime = self.entry.runtime_data
                    start = datetime.now(UTC)
                    clock = [start]
                    runtime._clock = lambda: clock[0]
                    self.assertEqual(
                        runtime.configuration.parameters.values[
                            "manual_override_minutes"
                        ],
                        effective,
                    )
                    self.assertEqual(
                        runtime.configuration.parameters.values[
                            "light_brightness_scale"
                        ],
                        baseline["parameters"]["light_brightness_scale"],
                    )
                    async with client.get(url + "/state") as response:
                        state = await response.json()
                        self.assertEqual(
                            state["configuration"]["parameters"][
                                "manual_override_minutes"
                            ],
                            effective,
                        )
                    if mode == "automatic":
                        await runtime.set_operation(True)
                    await runtime.set_heater_override(mode == "manual")
                    await runtime.set_light_override(80)
                    await self.hass.async_block_till_done()
                    recording_id = runtime.session.session_id
                    ends_at = (
                        start + timedelta(seconds=seconds)
                        if mode == "automatic"
                        else None
                    )
                    self.assertEqual(
                        runtime.controller.heater_override_ends_at, ends_at
                    )
                    self.assertEqual(
                        runtime.device.light_output.manual_ends_at, ends_at
                    )
                    self.assertEqual(heater.is_on, mode == "manual")
                    self.assertEqual(light.brightness, 204)
                    self.assertEqual(light.calls[-1][1]["brightness"], 204)
                    before = dict(self.entry.options)
                    calls = (list(heater.calls), list(light.calls))
                    for value in (nextafter(maximum, float("inf")), maximum * 2):
                        async with client.post(
                            url + "/parameters",
                            json={
                                **before["parameters"],
                                "manual_override_minutes": value,
                            },
                        ) as response:
                            self.assertEqual(
                                response.status, 400, await response.text()
                            )
                    self.assertEqual(dict(self.entry.options), before)
                    self.assertEqual((heater.calls, light.calls), calls)
                    await advance_with_fresh_sources(
                        self.hass, runtime, start + timedelta(seconds=seconds - 1),
                        lambda at: clock.__setitem__(0, at),
                    )
                    self.assertEqual(
                        runtime.controller.heater_override, mode == "manual"
                    )
                    self.assertEqual(runtime.device.light_output.manual_brightness, 80)
                    self.assertEqual(heater.is_on, mode == "manual")
                    self.assertEqual(light.brightness, 204)
                    await advance_with_fresh_sources(
                        self.hass, runtime, start + timedelta(seconds=seconds),
                        lambda at: clock.__setitem__(0, at),
                    )
                    if mode == "automatic":
                        self.assertIsNone(runtime.controller.heater_override)
                        self.assertIsNone(runtime.device.light_output.manual_brightness)
                        self.assertIsNone(runtime.controller.heater_override_ends_at)
                        self.assertIsNone(runtime.device.light_output.manual_ends_at)
                        self.assertEqual(runtime.controller.phase, "aufheizen")
                        self.assertTrue(heater.is_on)
                        self.assertTrue(heater.calls[-1])
                        light_values = runtime.configuration.parameters.values
                        transition_seconds = light_values["light_transition_seconds"]
                        cold_percent = light_values["cooling_brightness_percent"]
                        self.assertEqual(
                            runtime.device.light_output.last_automatic_brightness,
                            80 if transition_seconds else cold_percent,
                        )
                        await advance_with_fresh_sources(
                            self.hass, runtime, clock[0] + timedelta(seconds=
                                transition_seconds),
                            lambda at: clock.__setitem__(0, at),
                        )
                        self.assertEqual(
                            runtime.device.light_output.last_automatic_brightness,
                            cold_percent,
                        )
                        cold_brightness = round(255 * round(cold_percent) / 100)
                        self.assertEqual(light.brightness, cold_brightness)
                        self.assertEqual(light.calls[-1][0], "on")
                        self.assertEqual(light.calls[-1][1]["brightness"], cold_brightness)
                    else:
                        self.assertTrue(runtime.controller.heater_override)
                        self.assertEqual(runtime.session.session_id, recording_id)
                        self.assertTrue(heater.is_on)
                        self.assertEqual(
                            runtime.device.light_output.manual_brightness, 80
                        )
                        self.assertEqual(light.brightness, 204)
                        self.assertEqual(runtime.configuration.control_mode, "manual")
                    if stored == legacy and mode == "automatic":
                        await advance_with_fresh_sources(
                            self.hass, runtime, clock[0] + timedelta(seconds=
                                runtime.configuration.parameters.seconds("minimum_heating_minutes")),
                            lambda at: clock.__setitem__(0, at),
                        )
                        await runtime.set_heater_override(False)
                        await runtime.set_light_override(80)
                        for role in ("upper_temperature", "lower_temperature"):
                            entity = baseline["bindings"][role]
                            self.hass.states.async_set(
                                entity, str(runtime.controller.thermostat_target),
                                self.hass.states.get(entity).attributes,
                            )
                        await self.hass.async_block_till_done()
                        self.assertEqual(runtime.controller.phase, "bereit")
                        self.assertIsNone(runtime.controller.heater_override)
                        self.assertIsNone(runtime.device.light_output.manual_brightness)
                        self.assertFalse(heater.is_on)
                # subTest consumes assertion failures before this cleanup, so
                # a failed case cannot leave a session locking the next reload.
                runtime = self.entry.runtime_data
                if not runtime.closed and runtime.session is not None:
                    if runtime.session.operation_enabled:
                        await runtime.set_operation(False)
                    if runtime.session is not None:
                        runtime.controller.finish_session(runtime._clock(), light_after_run=False)
                        await runtime._cycle()
                await self.hass.async_block_till_done()

    async def test_unauthenticated_and_unpermitted_writes_are_rejected(self):
        url = self.base + "/" + self.entry.entry_id
        async with ClientSession() as client:
            async with client.get(url + "/state") as response:
                self.assertEqual(response.status, 401)
            async with client.post(url + "/finish_phase", json={"purpose":"after_run","token":"old"}) as response:
                self.assertEqual(response.status, 401)
        user = await self.hass.auth.async_create_user("Read only", group_ids=[])
        token = await self.hass.auth.async_create_refresh_token(user, client_id="http://localhost/")
        headers = {"Authorization": "Bearer " + self.hass.auth.async_create_access_token(token)}
        async with ClientSession(headers=headers) as client:
            async with client.post(url + "/control", json={"enabled": True}) as response:
                self.assertEqual(response.status, 403)
            async with client.post(url + "/finish_phase", json={"purpose":"after_run","token":"old"}) as response:
                self.assertEqual(response.status, 403)
            for route in ("/heater", "/light"):
                async with client.post(url + route, json={"value": True}) as response:
                    self.assertEqual(response.status, 403)
        self.assertIsNone(self.entry.runtime_data.session)

    async def test_instance_read_permission_and_normal_control_history_projection(self):
        url = self.base + "/" + self.entry.entry_id
        denied = await self.hass.auth.async_create_user("No entity read", group_ids=[])
        token = await self.hass.auth.async_create_refresh_token(denied, client_id="http://localhost/")
        denied_headers = {"Authorization": "Bearer " + self.hass.auth.async_create_access_token(token)}
        async with ClientSession(headers=denied_headers) as client:
            async with client.get(self.base) as response:
                self.assertEqual(await response.json(), [])
            for suffix in ("/state", "/archive"):
                async with client.get(url + suffix) as response:
                    self.assertEqual(response.status, 403)

        normal = await self.hass.auth.async_create_user("Normal panel read", group_ids=[GROUP_ID_USER])
        token = await self.hass.auth.async_create_refresh_token(normal, client_id="http://localhost/")
        normal_headers = {"Authorization": "Bearer " + self.hass.auth.async_create_access_token(token)}
        runtime = self.entry.runtime_data
        await runtime.set_operation(True)
        session_id = runtime.session.session_id
        await runtime.archive.flush()
        async with ClientSession(headers=normal_headers) as client:
            async with client.get(self.base) as response:
                self.assertEqual((await response.json())[0]["entry_id"], self.entry.entry_id)
            async with client.get(url + "/state") as response:
                self.assertEqual(response.status, 200, await response.text())
                state = await response.json()
                self.assertEqual(state["session"]["timeline"]["session_id"], session_id)
                self.assertIn("measurements", state)
                self.assertIn("measurement_status", state)
                self.assertEqual(state["measurement_positions"], ["upper", "lower"])
                self.assertIn("regulation_temperature_position", state)
                self.assertEqual(state["thermostat_restart_temperature"],
                                 runtime.controller.thermostat_restart_temperature)
                self.assertEqual(state["thermostat_target"], runtime.controller.thermostat_target)
                self.assertIn("target_temperature_c", state["configuration"]["parameters"])
                self.assertNotIn("bindings", state["configuration"])
                self.assertNotIn("sensor_timeout_seconds", state["configuration"]["parameters"])
                self.assertNotIn("detector_trace", state)
                self.assertIn("target_temperature_c", {item["key"] for item in state["parameters"]})
                self.assertNotIn("sensor_timeout_seconds", {item["key"] for item in state["parameters"]})
                self.assertTrue(all("source" not in item for item in state["measurements"]))
                self.assertEqual(state["measurement_ttl_seconds"],
                                 runtime.configuration.parameters.values["sensor_timeout_seconds"])
            async with client.get(url + "/archive", params={"session_id": session_id}) as response:
                self.assertEqual(response.status, 200, await response.text())
                history = await response.json()
                self.assertNotIn("configuration", history["session"])
                self.assertEqual(history["session"]["measurement_ttl_seconds"],
                                 runtime.configuration.parameters.values["sensor_timeout_seconds"])
                self.assertTrue(all(record["kind"] in {"measurement", "source_snapshot", "phase"}
                                    for record in history["records"]))
                self.assertTrue(all("source" not in record["payload"] for record in history["records"]))
            # Pagination belongs to the raw archive, not to the permitted
            # subset. A full hidden page must still expose its continuation.
            original = runtime.archive.read(session_id)
            after = max(record["id"] for record in original["records"])
            for _ in range(runtime.configuration.parameters.values["archive_read_page_records"]):
                runtime.archive.append("detector_trace", runtime._clock(), {}, session_id)
            runtime.archive.append("measurement", runtime._clock(),
                                   {"position": "upper", "quantity": "temperature", "value": 73}, session_id)
            await runtime.archive.flush()
            async with client.get(url + "/archive", params={"session_id": session_id, "after": after}) as response:
                hidden_page = await response.json()
                self.assertEqual(hidden_page["records"], [])
                self.assertGreater(hidden_page["next_after"], after)
            async with client.get(url + "/archive", params={"session_id": session_id, "after": hidden_page["next_after"]}) as response:
                resumed = await response.json()
                self.assertEqual(resumed["records"][0]["payload"]["value"], 73)
            async with client.get(url + "/archive", params={"session_id": session_id, "after": after, "projection": "history"}) as response:
                self.assertEqual(response.status, 200)
                projected = await response.json()
                self.assertEqual(
                    projected["records"],
                    [
                        {
                            **record,
                            "display": {
                                "absolute_humidity": {
                                    "value": None,
                                    "received_at": None,
                                    "valid_until": None,
                                },
                            },
                        }
                        for record in resumed["records"]
                    ],
                )
                self.assertNotIn("configuration", projected["session"])
                self.assertIsNone(projected["next_after"])

        async with ClientSession(headers=self.headers) as client:
            async with client.get(url + "/archive", params={"session_id": session_id, "projection": "history"}) as response:
                self.assertEqual(response.status, 200)
                projected = await response.json()
                kinds = {record["kind"] for record in projected["records"]}
                self.assertIn("measurement", kinds)
                self.assertIn("detector_trace", kinds)
                self.assertIn("session", kinds)
                for record in projected["records"]:
                    if record["kind"] == "session":
                        self.assertEqual(record["projection"], "session_state")
                        self.assertNotIn("timeline", record["payload"])
                self.assertIn("configuration", projected["session"])
            async with client.get(url + "/archive", params={"session_id": session_id, "projection": "invalid"}) as response:
                self.assertEqual(response.status, 400)

    def energy_transport_fixture(self):
        from datetime import timedelta

        from harness import retain_session
        from test_foundation import T0
        from custom_components.ha_sauna.core.timeline import Event, Kind

        runtime = self.entry.runtime_data
        controller = runtime.controller
        clock = [T0]
        runtime._clock = lambda: clock[0]
        controller.begin_session("api-energy-original", T0)
        for kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE):
            controller.process(Event(
                f"fixture:{runtime.session.session_id}:{kind.value}",
                runtime.session.session_id, kind, T0, T0,
            ))
        # This established fixture confirms a round through timeline.apply;
        # last_session and archive retention both require that confirmation.
        retain_session(runtime)
        p = runtime.configuration.parameters
        self.assertEqual(BY_KEY["nominal_power_kw"].unit, "kW")
        self.assertEqual(BY_KEY["sensor_timeout_seconds"].unit, "s")
        seconds = p.values["sensor_timeout_seconds"] / 4
        span = timedelta(seconds=seconds)
        nominal_kw = p.values["nominal_power_kw"]
        measured_w = nominal_kw * 1000 / 3
        measured_kw = measured_w / 1000
        controller.report_fallback_heating(True, T0)
        controller.report_heating(True, T0)
        controller.report_power(measured_w, T0 + span, T0)
        clock[0] = T0 + span * 2
        controller.advance(clock[0], evaluate=False)
        runtime.archive.save_session(
            runtime.session, clock[0], runtime.configuration.as_snapshot()
        )
        controller.report_power(None, None, clock[0])
        controller.report_heating(False, clock[0])
        clock[0] = T0 + span * 3
        controller.advance(clock[0], evaluate=False)
        controller.report_heating(None, clock[0])
        controller.report_fallback_heating(None, clock[0])
        clock[0] = T0 + span * 4
        controller.advance(clock[0], evaluate=False)
        runtime.archive.save_session(
            runtime.session, clock[0], runtime.configuration.as_snapshot()
        )
        # These boundaries/rates follow the inputs above, independently of the
        # archive serializer or energy_interval_delta transport helper.
        intervals = [
            {
                "started_at": (T0 + span * index).isoformat(),
                "ended_at": (T0 + span * (index + 1)).isoformat(),
                "source": source,
                "power_kw": rate,
            }
            for index, (source, rate) in enumerate((
                ("measured", measured_kw), ("estimated", nominal_kw),
                ("estimated", 0), ("unknown", None),
            ))
        ]
        self.assertAlmostEqual(runtime.session.energy.measured_kwh, measured_kw * seconds / 3600)
        self.assertAlmostEqual(runtime.session.energy.estimated_kwh, nominal_kw * seconds / 3600)
        self.assertEqual(
            (runtime.session.energy.measured_seconds,
             runtime.session.energy.estimated_seconds,
             runtime.session.energy.unknown_seconds),
            (seconds, seconds * 2, seconds),
        )
        return runtime, clock, span, nominal_kw, intervals

    async def test_state_energy_cursors_bind_current_and_last_session_ids(self):
        from custom_components.ha_sauna.archive import plain
        from test_foundation import T0

        runtime, clock, span, nominal_kw, last_intervals = self.energy_transport_fixture()
        last_id = runtime.session.session_id
        last_end = clock[0]
        runtime.controller.finish_session(last_end, light_after_run=False)
        runtime.persist()
        last = runtime.controller.completed_sessions[-1]
        self.assertGreater(last.timeline.gang_count, 0)
        clock[0] = last_end + span
        current_start = clock[0]
        runtime.controller.begin_session("api-energy-current", current_start)
        current_w = nominal_kw * 1000 / 2
        current_kw = current_w / 1000
        runtime.controller.report_power(current_w, current_start + span, current_start)
        clock[0] = current_start + span
        runtime.controller.advance(clock[0], evaluate=False)
        current = runtime.session
        original_current, original_last = current.energy, last.energy
        current_intervals = [{
            "started_at": current_start.isoformat(), "ended_at": clock[0].isoformat(),
            "source": "measured", "power_kw": current_kw,
        }]
        bootstrap = {
            "session": {"from": None, "to": clock[0].isoformat(), "intervals": current_intervals},
            "last_session": {"from": None, "to": last_end.isoformat(), "intervals": last_intervals},
        }
        url = self.base + "/" + self.entry.entry_id + "/state"
        async with ClientSession(headers=self.headers) as client:
            async def read(query=None):
                async with client.get(url, params=query) as response:
                    self.assertEqual(response.status, 200, await response.text())
                    return await response.json()

            initial = await read()
            for key, expected in bootstrap.items():
                self.assertEqual(initial[key]["energy"]["interval_delta"], expected)
                self.assertNotIn("intervals", initial[key]["energy"])
            self.assertEqual(initial["session"]["timeline"]["session_id"], current.session_id)
            self.assertEqual(initial["last_session"]["timeline"]["session_id"], last_id)
            self.assertAlmostEqual(
                initial["session"]["energy"]["measured_kwh"],
                current_kw * span.total_seconds() / 3600,
            )
            current_cursor, last_cursor = current_start + span / 2, T0 + span / 2
            continued = await read({
                "energy_session_id": current.session_id, "energy_after": current_cursor.isoformat(),
                "last_energy_session_id": last_id, "last_energy_after": last_cursor.isoformat(),
            })
            for key, cursor in (("session", current_cursor), ("last_session", last_cursor)):
                expected = bootstrap[key]
                clipped = [
                    {**expected["intervals"][0], "started_at": cursor.isoformat()},
                    *expected["intervals"][1:],
                ]
                self.assertEqual(continued[key]["energy"]["interval_delta"], {
                    "from": cursor.isoformat(), "to": expected["to"], "intervals": clipped,
                })
            unchanged = await read({
                "energy_session_id": current.session_id, "energy_after": clock[0].isoformat(),
                "last_energy_session_id": last_id, "last_energy_after": last_end.isoformat(),
            })
            for key, expected in bootstrap.items():
                self.assertEqual(unchanged[key]["energy"]["interval_delta"], {
                    "from": expected["to"], "to": expected["to"], "intervals": [],
                })
            mismatched = await read({
                "energy_session_id": last_id, "energy_after": clock[0].isoformat(),
                "last_energy_session_id": current.session_id, "last_energy_after": last_end.isoformat(),
            })
            unbound = await read({
                "energy_after": clock[0].isoformat(), "last_energy_after": last_end.isoformat(),
            })
            for result in (mismatched, unbound):
                for key, expected in bootstrap.items():
                    self.assertEqual(result[key]["energy"]["interval_delta"], expected)
        self.assertEqual(runtime.session.energy, original_current)
        self.assertEqual(last.energy, original_last)
        for key, owner in (("session", current), ("last_session", last)):
            self.assertEqual(
                {name: value for name, value in initial[key]["energy"].items() if name != "interval_delta"},
                plain(owner.energy),
            )

    async def test_state_energy_omission_skips_helper_and_keeps_scalars_and_door_cycles(self):
        from custom_components.ha_sauna.archive import energy_interval_delta

        runtime, clock, span, _, _ = self.energy_transport_fixture()
        runtime.controller.finish_session(clock[0], light_after_run=False)
        runtime.persist()
        clock[0] += span
        runtime.controller.begin_session("api-energy-current", clock[0])
        url = self.base + "/" + self.entry.entry_id + "/state"
        async with ClientSession(headers=self.headers) as client:
            async with client.get(url) as response:
                self.assertEqual(response.status, 200, await response.text())
                complete = await response.json()
            self.assertTrue(complete["last_session"]["door_cycles"])
            with patch("custom_components.ha_sauna.api.energy_interval_delta", wraps=energy_interval_delta) as helper:
                async with client.get(url, params={"include_energy_intervals": "false"}) as response:
                    self.assertEqual(response.status, 200, await response.text())
                    omitted = await response.json()
                helper.assert_not_called()
            for key in ("session", "last_session"):
                self.assertIn("interval_delta", complete[key]["energy"])
                self.assertEqual(omitted[key]["energy"], {
                    name: value for name, value in complete[key]["energy"].items()
                    if name != "interval_delta"
                })
                self.assertEqual(omitted[key]["door_cycles"], complete[key]["door_cycles"])
            self.assertEqual(omitted["energy_kwh"], complete["energy_kwh"])
            self.assertEqual(omitted["energy_source"], complete["energy_source"])

    async def test_archive_energy_cursor_is_independent_of_history_pages_and_public_filter(self):
        import json

        from test_foundation import T0

        runtime, clock, span, nominal_kw, intervals = self.energy_transport_fixture()
        session_id = runtime.session.session_id
        ended_at = clock[0]
        private_source = runtime.configuration.bindings.values["upper_temperature"]
        runtime.archive.append("detector_trace", ended_at, {"entity_id": private_source}, session_id)
        runtime.archive.append("measurement", ended_at, {
            "position": "upper", "quantity": "temperature",
            "value": runtime.configuration.parameters.values["sauna_min_temperature_c"],
            "source": private_source, "received_at": ended_at,
            "valid_until": ended_at + span,
        }, session_id)
        runtime.controller.finish_session(ended_at, light_after_run=False)
        runtime.persist()
        clock[0] = ended_at + span
        runtime.controller.begin_session("api-energy-unrelated", clock[0])
        runtime.controller.report_power(nominal_kw * 1000, clock[0] + span, clock[0])
        clock[0] += span
        runtime.controller.advance(clock[0], evaluate=False)
        runtime.persist()
        await runtime.archive.flush()
        user = await self.hass.auth.async_create_user("Public energy history", group_ids=[GROUP_ID_USER])
        token = await self.hass.auth.async_create_refresh_token(user, client_id="http://localhost/")
        headers = {"Authorization": "Bearer " + self.hass.auth.async_create_access_token(token)}
        url = self.base + "/" + self.entry.entry_id
        query = {"session_id": session_id, "projection": "history"}
        bootstrap = {"from": None, "to": ended_at.isoformat(), "intervals": intervals}
        async with ClientSession(headers=self.headers) as admin, ClientSession(headers=headers) as public:
            async def read(client, params):
                async with client.get(url + "/archive", params=params) as response:
                    self.assertEqual(response.status, 200, await response.text())
                    return await response.json()

            async with admin.get(url + "/state") as response:
                self.assertEqual(response.status, 200, await response.text())
                live = await response.json()
            self.assertEqual(live["session"]["timeline"]["session_id"], "api-energy-unrelated")
            self.assertEqual(live["session"]["energy"]["accounted_at"], clock[0].isoformat())
            administrative = await read(admin, query)
            published = await read(public, query)
            for result in (administrative, published):
                self.assertEqual(result["session"]["energy"]["interval_delta"], bootstrap)
                self.assertEqual(result["session"]["energy"]["accounted_at"], ended_at.isoformat())
                self.assertAlmostEqual(
                    result["session"]["energy"]["measured_kwh"],
                    nominal_kw / 3 * span.total_seconds() / 3600,
                )
                self.assertAlmostEqual(
                    result["session"]["energy"]["estimated_kwh"],
                    nominal_kw * span.total_seconds() / 3600,
                )
            self.assertEqual(published["session"]["energy"], administrative["session"]["energy"])
            self.assertNotIn("configuration", published["session"])
            self.assertNotIn(private_source, json.dumps(published))
            self.assertTrue(published["records"])
            self.assertTrue(all(row["kind"] in {"measurement", "source_snapshot", "phase"}
                                for row in published["records"]))
            raw = await read(admin, {"session_id": session_id})
            originals = {row["id"]: row for row in raw["records"] if row["kind"] == "session"}
            projected = [row for row in administrative["records"] if row["kind"] == "session"]
            self.assertTrue(projected)
            for row in projected:
                self.assertEqual(row["projection"], "session_state")
                original = originals[row["id"]]["payload"]["energy"]
                self.assertIn("interval_delta", original)
                self.assertEqual(row["payload"]["energy"], {
                    name: value for name, value in original.items() if name != "interval_delta"
                })
                self.assertNotIn("intervals", row["payload"]["energy"])
            self.assertTrue(any(
                row["payload"]["energy"]["interval_delta"] == {
                    "from": (T0 + span * 2).isoformat(),
                    "to": ended_at.isoformat(), "intervals": intervals[2:],
                }
                for row in originals.values()
            ))
            record_after = max(row["id"] for row in raw["records"])
            cursor = T0 + span / 2
            suffix_query = {
                **query, "after": str(record_after), "energy_after": cursor.isoformat(),
            }
            suffix = {
                "from": cursor.isoformat(), "to": ended_at.isoformat(),
                "intervals": [{**intervals[0], "started_at": cursor.isoformat()}, *intervals[1:]],
            }
            for client in (admin, public):
                result = await read(client, suffix_query)
                self.assertEqual(result["records"], [])
                self.assertEqual(result["session"]["energy"]["interval_delta"], suffix)
                self.assertEqual(result["session"]["door_cycles"], published["session"]["door_cycles"])

    async def test_archive_measurement_context_keeps_originals_until_window_complete(self):
        import json
        from datetime import timedelta

        from harness import retain_session
        from custom_components.ha_sauna.core.history import HISTORY_CONTEXT_SECONDS

        runtime = self.entry.runtime_data
        base = runtime._clock()
        clock = [base]
        runtime._clock = lambda: clock[0]
        sensor = runtime.configuration.bindings.values["upper_temperature"]
        originals = []

        async def measure(seconds, value):
            clock[0] = base + timedelta(seconds=seconds)
            self.hass.states.async_set(
                sensor, str(value), self.hass.states.get(sensor).attributes,
            )
            await self.hass.async_block_till_done()
            originals.append((clock[0].isoformat(), value))

        await measure(0, 31)
        clock[0] = base + timedelta(seconds=10)
        await runtime.set_operation(True)
        session_id = runtime.session.session_id
        retain_session(runtime)
        await measure(20, 32)
        clock[0] = base + timedelta(seconds=30)
        await runtime.set_operation(False)
        runtime.controller.finish_session(runtime._clock(), light_after_run=False)
        await runtime._cycle()
        self.assertIsNone(runtime.session)

        user = await self.hass.auth.async_create_user("Context reader", group_ids=[GROUP_ID_USER])
        token = await self.hass.auth.async_create_refresh_token(user, client_id="http://localhost/")
        headers = {"Authorization": "Bearer " + self.hass.auth.async_create_access_token(token)}
        url = self.base + "/" + self.entry.entry_id + "/archive"
        query = {"session_id": session_id, "projection": "history"}

        async with ClientSession(headers=headers) as public, ClientSession(headers=self.headers) as admin:
            async def read(client, params):
                async with client.get(url, params=params) as response:
                    self.assertEqual(response.status, 200, await response.text())
                    return await response.json()

            ended = await read(public, query)
            window = ended["measurement_window"]
            self.assertFalse(window["complete"])
            self.assertEqual(window["started_at"], (
                base + timedelta(seconds=10 - HISTORY_CONTEXT_SECONDS)
            ).isoformat())
            self.assertEqual(window["ended_at"], (
                base + timedelta(seconds=30 + HISTORY_CONTEXT_SECONDS)
            ).isoformat())
            cursor = max(record["id"] for record in ended["records"])

            await measure(40, 33)
            continued = await read(public, {**query, "after": cursor})
            measurements = [record for record in continued["records"] if record["kind"] == "measurement"]
            self.assertEqual([
                (record["payload"]["received_at"], record["payload"]["value"])
                for record in measurements
            ], originals[-1:])
            self.assertFalse(continued["measurement_window"]["complete"])

            complete = await read(public, query)
            rows = complete["records"]
            self.assertEqual(len({row["id"] for row in rows}), len(rows))
            self.assertEqual([
                (row["payload"]["received_at"], row["payload"]["value"])
                for row in rows if row["kind"] == "measurement"
                and row["payload"]["position"] == "upper"
                and row["payload"]["quantity"] == "temperature"
            ], originals)
            self.assertNotIn(sensor, json.dumps(complete))
            self.assertTrue(all("source" not in row["payload"] for row in rows))
            private = await read(admin, query)
            self.assertEqual([
                row["payload"]["source"] for row in private["records"]
                if row["kind"] == "measurement"
            ], [sensor] * len(originals))

            clock[0] = base + timedelta(seconds=30 + HISTORY_CONTEXT_SECONDS - 1)
            self.assertFalse((await read(public, query))["measurement_window"]["complete"])
            clock[0] += timedelta(seconds=1)
            final = await read(public, {**query, "after": max(row["id"] for row in rows)})
            self.assertTrue(final["measurement_window"]["complete"])
            self.assertEqual(final["records"], [])
            await measure(31 + HISTORY_CONTEXT_SECONDS, 34)
            outside = await read(public, {**query, "after": max(row["id"] for row in rows)})
            self.assertTrue(outside["measurement_window"]["complete"])
            self.assertEqual(outside["records"], [])

    async def test_direct_presence_ids_are_private_in_live_and_archived_public_responses(self):
        import json
        from datetime import timedelta

        from custom_components.ha_sauna.core.presence import binary_presence
        from custom_components.ha_sauna.core.timeline import Event, Kind
        from custom_components.ha_sauna.archive import plain

        entity_id = "binary_sensor.private_presence"
        self.hass.states.async_set(entity_id, "off", {"device_class": "occupancy"})
        options = dict(self.entry.options)
        options["presence_source"] = "ha_presence"
        options["bindings"] = {**options["bindings"], "presence": entity_id}
        for role in ("upper_temperature", "lower_temperature"):
            sensor = options["bindings"][role]
            self.hass.states.async_set(sensor, "80", self.hass.states.get(sensor).attributes)
        self.hass.config_entries.async_update_entry(self.entry, options=options)
        await self.hass.async_block_till_done()
        runtime = self.entry.runtime_data
        start = runtime._clock()
        clock = [start]
        runtime._clock = lambda: clock[0]
        await runtime.set_operation(True)
        session_id = runtime.session.session_id

        async def door(name, kind, milliseconds):
            clock[0] = start + timedelta(milliseconds=milliseconds)
            await runtime.receive(Event(name, session_id, kind, clock[0], clock[0]))

        async def presence(state, milliseconds):
            clock[0] = start + timedelta(milliseconds=milliseconds)
            await runtime.accept_presence(binary_presence(
                entity_id, state, clock[0], clock[0],
            ))

        await door("entry-open", Kind.DOOR_OPEN, 10)
        await presence("on", 20)
        await door("entry-close", Kind.DOOR_CLOSE, 30)

        def expected_cycles(gang_id, *, completed=False):
            pairs = [("entry", 10, 30)] + ([("exit", 40, 60)] if completed else [])
            return [{
                "id": role + "-open",
                "event_ids": [role + "-open", role + "-close"],
                "started_at": (start + timedelta(milliseconds=opened)).isoformat(),
                "ended_at": (start + timedelta(milliseconds=closed)).isoformat(),
                "open": False,
                "role": role,
                "gang_id": gang_id,
                "gang_links": [{"gang_id": gang_id, "role": role}],
            } for role, opened, closed in pairs]

        user = await self.hass.auth.async_create_user("Public presence", group_ids=[GROUP_ID_USER])
        token = await self.hass.auth.async_create_refresh_token(user, client_id="http://localhost/")
        headers = {"Authorization": "Bearer " + self.hass.auth.async_create_access_token(token)}
        url = self.base + "/" + self.entry.entry_id
        async with ClientSession(headers=headers) as public, ClientSession(headers=self.headers) as admin:
            live_session = runtime.session
            original_live = plain(live_session)
            async with public.get(url + "/state") as response:
                self.assertEqual(response.status, 200)
                state = await response.json()
            self.assertNotIn(entity_id, json.dumps(state))
            active = state["session"]["timeline"]["active"]
            recognition = next(item for item in state["session"]["timeline"]["processed"]
                               if item["kind"] == "presence_confirmed")
            self.assertEqual(active["recognition_event_id"], recognition["event_id"])
            self.assertEqual(active["session_id"], session_id)
            self.assertEqual(state["session"]["door_cycles"], expected_cycles(active["gang_id"]))
            async with admin.get(url + "/state") as response:
                administrative = await response.json()
            raw_id = runtime.session.timeline.active.gang_id
            self.assertIn(entity_id, raw_id)
            self.assertEqual(administrative["session"]["timeline"]["active"]["gang_id"], raw_id)
            self.assertEqual(administrative["session"]["door_cycles"], expected_cycles(raw_id))
            self.assertIs(runtime.session, live_session)
            self.assertEqual(plain(runtime.session), original_live)

            await door("exit-open", Kind.DOOR_OPEN, 40)
            await presence("off", 50)
            self.assertIsNotNone(runtime.session.timeline.active)
            self.assertIsNone(runtime.session.after_run)
            await door("exit-ventilation", Kind.VENTILATION, 55)
            await door("exit-close", Kind.DOOR_CLOSE, 60)
            clock[0] = start + timedelta(milliseconds=70)
            runtime.persist()
            await runtime.archive.flush()
            live_session = runtime.session
            original_live = plain(live_session)
            original_archive = runtime.archive.read(session_id, now=clock[0], limit=10000)
            for query in ({"session_id": session_id},
                          {"session_id": session_id, "projection": "history"}):
                async with public.get(url + "/archive", params=query) as response:
                    self.assertEqual(response.status, 200)
                    archived = await response.json()
                self.assertNotIn(entity_id, json.dumps(archived))
                completed = archived["session"]["timeline"]["completed"][0]
                self.assertEqual(completed["gang_id"], active["gang_id"])
                self.assertEqual(completed["recognition_event_id"], recognition["event_id"])
                self.assertEqual(archived["session"]["door_cycles"],
                                 expected_cycles(completed["gang_id"], completed=True))
                self.assertEqual({item["source_id"] for item in archived["phase_projection"]["intervals"]
                                  if item["source_id"]}, {active["gang_id"]})
            async with admin.get(url + "/archive", params={"session_id": session_id}) as response:
                administrative = await response.json()
            self.assertEqual(administrative["session"]["timeline"]["completed"][0]["gang_id"], raw_id)
            self.assertEqual(administrative["session"]["door_cycles"], expected_cycles(raw_id, completed=True))
            self.assertEqual(original_archive["session"]["timeline"]["completed"][0]["gang_id"], raw_id)
            self.assertNotIn("door_cycles", original_archive["session"])

            async with public.get(url + "/state") as response:
                self.assertEqual(response.status, 200)
                state = await response.json()
            self.assertNotIn(entity_id, json.dumps(state))
            self.assertEqual(state["session"]["door_cycles"],
                             expected_cycles(active["gang_id"], completed=True))
            async with admin.get(url + "/state") as response:
                self.assertEqual((await response.json())["session"]["door_cycles"],
                                 expected_cycles(raw_id, completed=True))
            self.assertIs(runtime.session, live_session)
            self.assertEqual(plain(runtime.session), original_live)
            administrative["session"]["door_cycles"][0]["gang_links"][0]["gang_id"] = "response copy"
            self.assertEqual(runtime.archive.read(session_id, now=clock[0], limit=10000), original_archive)
            raw_phase_id = runtime.session.after_run.phase_id
            public_token = state["session"]["after_run"]["phase_id"]
            self.assertIn(entity_id, raw_phase_id)
            self.assertNotEqual(public_token, raw_phase_id)
            async with public.post(url + "/finish_phase", json={
                "purpose": "after_run", "token": "public:" + "0" * 64,
            }) as response:
                self.assertEqual(response.status, 409, await response.text())
            self.assertEqual(runtime.session.after_run.phase_id, raw_phase_id)
            async with public.post(url + "/finish_phase", json={
                "purpose": "after_run", "token": public_token,
            }) as response:
                self.assertEqual(response.status, 200, await response.text())
            self.assertIsNone(runtime.session.after_run)
            self.assertEqual(runtime.session.session_id, session_id)
            self.assertTrue(runtime.session.operation_enabled)
            async with public.post(url + "/finish_phase", json={
                "purpose": "after_run", "token": public_token,
            }) as response:
                self.assertEqual(response.status, 409, await response.text())
            self.assertEqual(len(runtime.session.after_run_history), 1)
            await runtime.archive.flush()
            archived = await asyncio.to_thread(runtime.archive.read, session_id, limit=10000)
            manual_ends = [row for row in archived["records"] if row["kind"] == "manual_phase_end"]
            self.assertEqual(len(manual_ends), 1)
            self.assertEqual(manual_ends[0]["payload"]["token"], raw_phase_id)

            # Finish this same productive session so the last_session consumer
            # reads its retained timeline; no second recognition fixture is needed.
            await runtime.set_operation(False)
            runtime.controller.finish_session(clock[0], light_after_run=False)
            await runtime._cycle()
            await runtime.archive.flush()
            completed_session = runtime.controller.completed_sessions[-1]
            original_completed = plain(completed_session)
            original_archive = runtime.archive.read(session_id, now=clock[0], limit=10000)
            for client, expected_id in ((public, active["gang_id"]), (admin, raw_id)):
                async with client.get(url + "/state") as response:
                    self.assertEqual(response.status, 200, await response.text())
                    state = await response.json()
                self.assertIsNone(state["session"])
                self.assertEqual(state["last_session"]["timeline"]["completed"][0]["gang_id"], expected_id)
                self.assertEqual(state["last_session"]["door_cycles"],
                                 expected_cycles(expected_id, completed=True))
                async with client.get(url + "/archive", params={"session_id": session_id}) as response:
                    self.assertEqual(response.status, 200, await response.text())
                    archived = await response.json()
                self.assertEqual(archived["session"]["door_cycles"], state["last_session"]["door_cycles"])
                if client is public:
                    self.assertNotIn(entity_id, json.dumps(state))
                    self.assertNotIn(entity_id, json.dumps(archived))
                    self.assertNotIn("configuration", archived["session"])
            self.assertEqual(plain(completed_session), original_completed)
            self.assertEqual(runtime.archive.read(session_id, now=clock[0], limit=10000), original_archive)

    async def test_malformed_json_is_a_client_error_across_write_endpoints(self):
        url = self.base + "/" + self.entry.entry_id
        original_options = dict(self.entry.options)
        async with ClientSession(headers=self.headers) as client:
            for suffix in (
                "/control", "/finish_phase", "/finish-session", "/parameters",
                "/temperature", "/appearance", "/program", "/programs",
                "/standards",
                "/button-program", "/control-mode", "/light", "/heater", "/logging",
            ):
                with self.subTest(suffix=suffix):
                    async with client.post(url + suffix, data="{", headers={"Content-Type": "application/json"}) as response:
                        self.assertEqual(response.status, 400, await response.text())
                        self.assertIn("error", await response.json())
        self.assertEqual(dict(self.entry.options), original_options)
        self.assertIsNone(self.entry.runtime_data.session)

    async def test_full_settings_and_next_gang_target_preserve_free_program(self):
        from datetime import timedelta
        from custom_components.ha_sauna.core.timeline import Event, Kind

        url = self.base + "/" + self.entry.entry_id
        async with ClientSession(headers=self.headers) as client:
            async with client.post(url + "/program", json={"temperature_steps": [80, 86, 90]}) as response:
                self.assertEqual(response.status, 200, await response.text())
            for technical_change in (False, True):
                values = dict(self.entry.options["parameters"])
                if technical_change:
                    values["nominal_power_kw"] = 5
                async with client.post(url + "/parameters", json=values) as response:
                    self.assertEqual(response.status, 200, await response.text())
                await self.hass.async_block_till_done()
                self.assertEqual(tuple(self.entry.options["temperature_steps"]), (80, 86, 90))
                self.assertEqual(self.entry.runtime_data.configuration.program_mode, "progressive")
            runtime = self.entry.runtime_data
            temperature_entity = self.entry.options["bindings"]["upper_temperature"]
            temperature_state = self.hass.states.get(temperature_entity)
            self.hass.states.async_set(temperature_entity, "70", temperature_state.attributes, force_update=True)
            await self.hass.async_block_till_done()
            await runtime.set_operation(True)
            start = runtime._clock()
            current = [start]
            runtime._clock = lambda: current[0]
            self.assertEqual(runtime.session.timeline.door, "closed")
            for index, kind in enumerate((Kind.DOOR_OPEN, Kind.DOOR_CLOSE, Kind.INFUSION, Kind.DOOR_OPEN, Kind.VENTILATION)):
                at = start + timedelta(seconds=index)
                current[0] = at
                runtime.controller.process(Event(str(index), runtime.session.session_id, kind, at, at))
            self.assertEqual(runtime.session.timeline.gang_count, 1)
            self.assertEqual(runtime.controller.target_temperature, 86)
            options = dict(self.entry.options)
            configuration = runtime.configuration
            async with client.post(url + "/temperature", json={"target_temperature_c": 80}) as response:
                self.assertEqual(response.status, 200, await response.text())
                result = await response.json()
            await self.hass.async_block_till_done()
            self.assertIs(runtime.configuration, configuration)
            self.assertEqual(dict(self.entry.options), options)
            self.assertEqual(result["target_temperature"], 80)
            self.assertEqual(result["next_gang_temperature"], 80)
            self.assertEqual(runtime.configuration.program_mode, "progressive")
            self.assertEqual(runtime.configuration.temperature_steps, (80, 86, 90))

    async def test_live_target_changes_one_next_gang_and_resumes_selected_program(self):
        from datetime import timedelta
        from custom_components.ha_sauna.core.timeline import Event, Kind

        catalog = list(self.entry.options["temperature_programs"])
        catalog.append({"id": "next-gang-test", "name": "Testfolge",
                        "temperature_steps": [76, 83, 91]})
        self.hass.config_entries.async_update_entry(
            self.entry, options={**self.entry.options, "temperature_programs": catalog}
        )
        await self.hass.async_block_till_done()
        runtime = self.entry.runtime_data
        temperature = runtime.configuration.bindings.values["upper_temperature"]
        minimum = str(runtime.configuration.parameters.values["sauna_min_temperature_c"])
        self.hass.states.async_set(
            temperature, minimum, self.hass.states.get(temperature).attributes
        )
        await self.hass.async_block_till_done()
        start = runtime._clock()
        now = start
        runtime._clock = lambda: now
        sequence = 0

        async def events(*kinds):
            nonlocal now, sequence
            for kind in kinds:
                sequence += 1
                now = start + timedelta(milliseconds=sequence)
                await runtime.receive(Event(
                    f"next-gang:{sequence}", runtime.session.session_id, kind, now, now
                ))

        url = self.base + "/" + self.entry.entry_id
        async with ClientSession(headers=self.headers) as client:
            async with client.post(url + "/program", json={"profile": "next-gang-test"}) as response:
                self.assertEqual(response.status, 200, await response.text())
            await runtime.set_operation(True)
            await events(Kind.DOOR_OPEN, Kind.DOOR_CLOSE, Kind.INFUSION)
            active = runtime.session.timeline.active
            self.assertIsNotNone(active)
            options = dict(self.entry.options)
            configuration = runtime.configuration
            async with client.post(url + "/temperature", json={"target_temperature_c": 88}) as response:
                self.assertEqual(response.status, 200, await response.text())
                result = await response.json()
            await self.hass.async_block_till_done()
            self.assertEqual(result["target_temperature"], 76)
            self.assertEqual(result["next_gang_temperature"], 88)
            self.assertEqual(runtime.session.timeline.active, active)
            self.assertIs(runtime.configuration, configuration)
            self.assertEqual(dict(self.entry.options), options)
            self.assertEqual(runtime.configuration.selected_program_id, "next-gang-test")
            async with client.get(url + "/state") as response:
                state = await response.json()
                self.assertEqual((state["target_temperature"], state["next_gang_temperature"]), (76, 88))
            await events(Kind.DOOR_OPEN, Kind.VENTILATION)
            self.assertEqual(runtime.controller.target_temperature, 88)
            async with client.post(url + "/finish_phase", json={
                "purpose": "after_run", "token": runtime.session.after_run.phase_id,
            }) as response:
                self.assertEqual(response.status, 200, await response.text())
            self.assertIsNone(runtime.session.after_run)
            # Recognition requires a fresh value at or above the fixture's
            # configured minimum, independently of the program's target.
            self.hass.states.async_set(
                temperature, minimum, self.hass.states.get(temperature).attributes,
                force_update=True,
            )
            await self.hass.async_block_till_done()
            # The exit opening belongs to the preceding gang/cooling context.
            # Start the next gang through its own complete entry episode.
            await events(Kind.DOOR_CLOSE, Kind.DOOR_OPEN, Kind.DOOR_CLOSE, Kind.INFUSION)
            self.assertIsNotNone(runtime.session.timeline.active)
            self.assertEqual(runtime.controller.target_temperature, 88)
            await events(Kind.DOOR_OPEN, Kind.VENTILATION)
            self.assertEqual(runtime.session.timeline.gang_count, 2)
            self.assertEqual(runtime.controller.target_temperature, 91)
            self.assertEqual(runtime.configuration.temperature_steps, (76, 83, 91))

    async def test_archive_cursor_rejects_sqlite_overflow(self):
        runtime = self.entry.runtime_data
        await runtime.set_operation(True)
        url = self.base + "/" + self.entry.entry_id + "/archive"
        async with ClientSession(headers=self.headers) as client:
            for after, status in ((2**63 - 1, 200), (2**63, 400), (-(10**100), 200)):
                async with client.get(url, params={"session_id": runtime.session.session_id, "after": str(after)}) as response:
                    self.assertEqual(response.status, status, await response.text())

    async def test_minimum_validates_free_interior_stage_before_persisting(self):
        url = self.base + "/" + self.entry.entry_id
        async with ClientSession(headers=self.headers) as client:
            async with client.post(url + "/programs", json={"programs": []}) as response:
                self.assertEqual(response.status, 200, await response.text())
            for middle, expected in ((65, 400), (70, 200)):
                async with client.post(url + "/program", json={"temperature_steps": [80, middle, 90]}) as response:
                    self.assertEqual(response.status, 200, await response.text())
                before = dict(self.entry.options)
                values = {**before["parameters"], "sauna_min_temperature_c": 70, "preset_start_c": 70}
                async with client.post(url + "/parameters", json=values) as response:
                    self.assertEqual(response.status, expected, await response.text())
                await self.hass.async_block_till_done()
                if expected == 400:
                    self.assertEqual(dict(self.entry.options), before)
                    self.assertFalse(self.entry.runtime_data.reconfiguring)
                else:
                    self.assertEqual(self.entry.runtime_data.configuration.parameters.values["sauna_min_temperature_c"], 70)
                    self.assertEqual(self.entry.runtime_data.configuration.temperature_steps, (80, 70, 90))

    async def test_finish_light_requires_current_gap_token_and_preserves_recording(self):
        from datetime import UTC, datetime

        url = self.base + "/" + self.entry.entry_id + "/finish-session"
        async with ClientSession() as client:
            async with client.post(url, json={"token": "old"}) as response:
                self.assertEqual(response.status, 401)

        denied = await self.hass.auth.async_create_user("No session control", group_ids=[])
        denied_token = await self.hass.auth.async_create_refresh_token(denied, client_id="http://localhost/")
        denied_headers = {"Authorization": "Bearer " + self.hass.auth.async_create_access_token(denied_token)}
        async with ClientSession(headers=denied_headers) as client:
            async with client.post(url, json={"token": "old"}) as response:
                self.assertEqual(response.status, 403)

        async with ClientSession(headers=self.headers) as client:
            for body in ([], {}, {"token": []}, {"token": "old", "extra": True}):
                async with client.post(url, json=body) as response:
                    self.assertEqual(response.status, 400, await response.text())

        user = await self.hass.auth.async_create_user("Session control", group_ids=[GROUP_ID_USER])
        user_token = await self.hass.auth.async_create_refresh_token(user, client_id="http://localhost/")
        headers = {"Authorization": "Bearer " + self.hass.auth.async_create_access_token(user_token)}
        runtime = self.entry.runtime_data
        runtime._clock = lambda: datetime.now(UTC)
        await runtime.set_operation(True)
        await runtime.set_operation(False)
        gap_token = next(deadline.token for deadline in runtime.session.deadlines if deadline.purpose == "session_gap")

        async with ClientSession(headers=headers) as client:
            await runtime.set_operation(True)
            resumed_session = runtime.session
            async with client.post(url, json={"token": gap_token}) as response:
                self.assertEqual(response.status, 409, await response.text())
            self.assertIs(runtime.session, resumed_session)
            self.assertTrue(runtime.session.operation_enabled)

            await runtime.set_operation(False)
            current_gap = next(deadline for deadline in runtime.session.deadlines if deadline.purpose == "session_gap")
            current_gap_token = current_gap.token
            async with client.post(url, json={"token": current_gap_token}) as response:
                self.assertEqual(response.status, 200, await response.text())
            async with client.get(self.base + "/" + self.entry.entry_id + "/state") as response:
                state = await response.json()
                self.assertEqual(state["session"]["timeline"]["session_id"], resumed_session.session_id)
                self.assertFalse(state["session"]["operation_enabled"])
                self.assertTrue(state["configuration_locked"])
                self.assertTrue(state["control_mode_change_allowed"])
                self.assertIn(current_gap, runtime.session.deadlines)
                self.assertLessEqual(runtime.controller.light_after_run.ends_at, runtime._clock())

    async def test_write_acknowledges_replacement_runtime_after_waiting_for_body(self):
        from custom_components.ha_sauna import api

        url = self.base + "/" + self.entry.entry_id
        cases = (
            ("/control-mode", {"mode": "manual"}, "control_mode", "manual"),
            (
                "/button-program",
                {"profile": "gipfelstuermer"},
                "button_program",
                "gipfelstuermer",
            ),
        )
        read_body = api.json_body
        async with ClientSession(headers=self.headers) as client:
            for suffix, body, key, expected in cases:
                with self.subTest(endpoint=suffix):
                    previous = self.entry.runtime_data
                    entered, release = asyncio.Event(), asyncio.Event()

                    async def waiting_body(request):
                        entered.set()
                        await release.wait()
                        return await read_body(request)

                    async def send():
                        async with client.post(url + suffix, json=body) as response:
                            return response.status, await response.json()

                    with patch.object(api, "json_body", side_effect=waiting_body):
                        pending = asyncio.create_task(send())
                        try:
                            await asyncio.wait_for(entered.wait(), async_timeout("service"))
                            reloaded = await asyncio.wait_for(
                                self.hass.config_entries.async_reload(self.entry.entry_id),
                                async_timeout("reload"),
                            )
                            self.assertTrue(reloaded)
                            self.assertTrue(previous.closed)
                            self.assertIsNot(self.entry.runtime_data, previous)
                        except BaseException as error:
                            release.set()
                            try:
                                await asyncio.wait_for(pending, async_timeout("reload"))
                            except BaseException as cleanup_error:
                                error.add_note(f"HTTP-Abschluss fehlgeschlagen: {cleanup_error!r}")
                            raise
                        else:
                            release.set()
                            status, result = await asyncio.wait_for(pending, async_timeout("reload"))
                        self.assertEqual(status, 200, result)
                        self.assertEqual(result[key], expected)
                    self.assertEqual(
                        getattr(self.entry.runtime_data.configuration, key), expected
                    )
                    self.assertEqual(self.entry.options[key], expected)
                    self.assertNotEqual(getattr(previous.configuration, key), expected)
                    await self.hass.async_block_till_done()

    async def test_admin_program_and_mode_endpoints_persist_and_lock_with_the_session(self):
        url = self.base + "/" + self.entry.entry_id
        catalog = list(self.entry.options["temperature_programs"])
        catalog.append({"id": "test_program", "name": "Testprogramm", "start_c": 76,
                        "end_c": 88, "distribution_gangs": 4})
        profile = "test_program"
        async with ClientSession(headers=self.headers) as client:
            async with client.post(url + "/control-mode", json={"mode": "manual"}) as response:
                self.assertEqual(response.status, 200, await response.text())
                self.assertEqual((await response.json())["control_mode"], "manual")
            self.assertEqual(self.entry.runtime_data.configuration.control_mode, "manual")
            async with client.post(url + "/programs", json={"programs": catalog}) as response:
                self.assertEqual(response.status, 200, await response.text())
            async with client.post(url + "/button-program", json={"profile": profile}) as response:
                self.assertEqual(response.status, 200, await response.text())
                self.assertEqual((await response.json())["button_program"], profile)
            await self.hass.async_block_till_done()
            self.assertEqual(self.entry.options["button_program"], profile)
            self.assertEqual(self.entry.options["temperature_programs"], catalog)

            async with client.post(url + "/control", json={"enabled": True}) as response:
                self.assertEqual(response.status, 409, await response.text())
            self.assertIsNone(self.entry.runtime_data.session)
            async with client.post(url + "/control-mode", json={"mode": "automatic"}) as response:
                self.assertEqual(response.status, 200, await response.text())
            async with client.post(url + "/control", json={"enabled": True}) as response:
                self.assertEqual(response.status, 200, await response.text())
            for suffix, body in (("/control-mode", {"mode": "manual"}),
                                 ("/programs", {"programs": catalog}),
                                 ("/button-program", {"profile": "current"})):
                async with client.post(url + suffix, json=body) as response:
                    self.assertEqual(response.status, 409, await response.text())

        user = await self.hass.auth.async_create_user("No program admin", group_ids=[])
        token = await self.hass.auth.async_create_refresh_token(user, client_id="http://localhost/")
        headers = {"Authorization": "Bearer " + self.hass.auth.async_create_access_token(token)}
        async with ClientSession(headers=headers) as client:
            async with client.post(url + "/control-mode", json={"mode": "automatic"}) as response:
                self.assertEqual(response.status, 403)
            async with client.post(url + "/heater", json={"value": False}) as response:
                self.assertEqual(response.status, 403)
            async with client.post(url + "/programs", json={"programs": catalog}) as response:
                self.assertEqual(response.status, 403)
            async with client.post(url + "/button-program", json={"profile": profile}) as response:
                self.assertEqual(response.status, 403)

    async def attach_reporting_heater(self):
        """Use the shared real switch fixture where assertions depend on feedback."""
        from homeassistant.setup import async_setup_component
        from test_device_path import TestHeater

        await async_setup_component(self.hass, "switch", {})
        heater = TestHeater()
        await self.hass.data["switch"].async_add_entities([heater])
        self.hass.config_entries.async_update_entry(
            self.entry,
            options={
                **self.entry.options,
                "bindings": {**self.entry.options["bindings"], "heater": heater.entity_id},
            },
        )
        await self.hass.async_block_till_done()
        return heater

    async def test_standard_user_sees_manual_enablement_separately_from_heater_demand(self):
        await self.attach_reporting_heater()
        user = await self.hass.auth.async_create_user(
            "Manual thermostat", group_ids=[GROUP_ID_USER]
        )
        token = await self.hass.auth.async_create_refresh_token(
            user, client_id="http://localhost/"
        )
        headers = {
            "Authorization": "Bearer " + self.hass.auth.async_create_access_token(token)
        }
        runtime = self.entry.runtime_data
        target = runtime.controller.target_temperature
        parameters = runtime.configuration.parameters.values
        temperatures = (
            (target + parameters["readiness_offset_c"], False),
            (target - parameters["readiness_hysteresis_c"], True),
        )
        url = self.base + "/" + self.entry.entry_id
        async with ClientSession(headers=headers) as client:
            async with client.post(url + "/control-mode", json={"mode": "manual"}) as response:
                self.assertEqual(response.status, 200, await response.text())
            for temperature, commanded in temperatures:
                for role in ("upper_temperature", "lower_temperature"):
                    entity_id = runtime.configuration.bindings.values[role]
                    state = self.hass.states.get(entity_id)
                    self.hass.states.async_set(entity_id, str(temperature), state.attributes)
                await self.hass.async_block_till_done()
                async with client.post(url + "/heater", json={"value": True}) as response:
                    self.assertEqual(response.status, 200, await response.text())
                    heater = (await response.json())["manual_controls"]["heater"]
                    self.assertIs(heater["manual"], True)
                    self.assertIs(heater["commanded"], commanded)
                await self.hass.async_block_till_done()
                async with client.get(url + "/state") as response:
                    self.assertEqual(response.status, 200, await response.text())
                    state = await response.json()
                    self.assertNotIn("decision", state)
                    heater = state["manual_controls"]["heater"]
                    self.assertIs(heater["manual"], True)
                    self.assertIs(heater["commanded"], commanded)
                    self.assertIs(heater["observation"]["on"], commanded)

    async def test_manual_light_feedback_target_uses_device_resolution(self):
        from custom_components.ha_sauna.settings import async_set_parameters
        from custom_components.ha_sauna.presentation import public_state

        await async_set_parameters(
            self.hass, self.entry, {"light_brightness_scale": 10}, partial=True
        )
        await self.hass.async_block_till_done()
        url = self.base + "/" + self.entry.entry_id
        async with ClientSession(headers=self.headers) as client:
            async with client.post(url + "/control-mode", json={"mode": "manual"}) as response:
                self.assertEqual(response.status, 200, await response.text())
            async with client.post(url + "/light", json={"value": 42}) as response:
                self.assertEqual(response.status, 200, await response.text())
                light = (await response.json())["manual_controls"]["light"]
                self.assertEqual(light["manual"], 42)
                self.assertEqual(light["manual_feedback_target"], 40)
            async with client.get(url + "/state") as response:
                state = await response.json()
                self.assertEqual(
                    public_state(state)["manual_controls"]["light"]["manual_feedback_target"], 40
                )
            async with client.post(url + "/light", json={"value": None}) as response:
                self.assertEqual(response.status, 200, await response.text())
                self.assertIsNone((await response.json())["manual_controls"]["light"]["manual_feedback_target"])

    async def test_standard_user_manual_controls_explicit_mode_and_override_active_session(self):
        await self.attach_reporting_heater()
        user = await self.hass.auth.async_create_user(
            "Manual controller", group_ids=[GROUP_ID_USER]
        )
        token = await self.hass.auth.async_create_refresh_token(
            user, client_id="http://localhost/"
        )
        headers = {
            "Authorization": "Bearer " + self.hass.auth.async_create_access_token(token)
        }
        runtime = self.entry.runtime_data
        url = self.base + "/" + self.entry.entry_id
        async with ClientSession(headers=headers) as client:
            async with client.post(url + "/heater", json={"value": True}) as response:
                self.assertEqual(response.status, 409, await response.text())
            self.assertEqual(runtime.configuration.control_mode, "automatic")
            async with client.post(url + "/control-mode", json={"mode": "manual"}) as response:
                self.assertEqual(response.status, 200, await response.text())
            async with client.post(url + "/light", json={"value": True}) as response:
                self.assertEqual(response.status, 200, await response.text())
            self.assertEqual(runtime.configuration.control_mode, "manual")
            self.assertIsNone(runtime.session)
            async with client.post(url + "/heater", json={"value": True}) as response:
                self.assertEqual(response.status, 200, await response.text())
            self.assertIsNotNone(runtime.session)
            self.assertTrue(runtime.controller.heater_override)
            async with client.post(url + "/control", json={"enabled": True}) as response:
                self.assertEqual(response.status, 409, await response.text())
            self.assertIsNotNone(runtime.session)
            await runtime.set_operation(False)
            async with client.post(url + "/control-mode", json={"mode": "automatic"}) as response:
                self.assertEqual(response.status, 200, await response.text())
            await runtime.set_operation(True)
            identity = runtime.session.session_id
            for value in (False, True, None):
                async with client.post(url + "/heater", json={"value": value}) as response:
                    self.assertEqual(response.status, 200, await response.text())
                self.assertEqual(runtime.session.session_id, identity)
                self.assertEqual(runtime.configuration.control_mode, "automatic")
                self.assertIs(runtime.controller.heater_override, value)

    async def test_standard_user_controls_only_the_permitted_panel_routes(self):
        """The normal HA user inherits control of the integration switch."""
        from datetime import UTC, datetime, timedelta

        await self.attach_reporting_heater()
        user = await self.hass.auth.async_create_user("Standard user", group_ids=[GROUP_ID_USER])
        token = await self.hass.auth.async_create_refresh_token(user, client_id="http://localhost/")
        headers = {"Authorization": "Bearer " + self.hass.auth.async_create_access_token(token)}
        runtime = self.entry.runtime_data
        now = datetime.now(UTC)
        runtime._clock = lambda: now
        url = self.base + "/" + self.entry.entry_id
        catalog = list(self.entry.options["temperature_programs"])
        catalog.append(
            {
                "id": "standard_program",
                "name": "Standardprogramm",
                "start_c": 76,
                "end_c": 88,
                "distribution_gangs": 4,
            }
        )

        async with ClientSession(headers=headers) as client:
            async with client.post(url + "/programs", json={"programs": catalog}) as response:
                self.assertEqual(response.status, 200, await response.text())
            async with client.post(
                url + "/button-program",
                json={"profile": "constant"},
            ) as response:
                self.assertEqual(response.status, 200, await response.text())
                self.assertEqual((await response.json())["button_program"], "constant")
            async with client.get(url + "/state") as response:
                self.assertNotIn(
                    "button_temperature_c", (await response.json())["configuration"]
                )
            self.assertNotIn("button_temperature_c", self.entry.options)
            for body in (
                {"profile": "constant", "temperature_c": 81},
                {"profile": "constant", "temperature_c": "81"},
                {"profile": "constant", "temperature_c": 1000},
                {"profile": "standard_program", "temperature_c": 81},
            ):
                async with client.post(url + "/button-program", json=body) as response:
                    self.assertEqual(response.status, 400, await response.text())
            async with client.post(url + "/button-program", json={"profile": "standard_program"}) as response:
                self.assertEqual(response.status, 200, await response.text())
            self.assertEqual(runtime.configuration.button_program, "standard_program")
            async with client.post(url + "/control-mode", json={"mode": "manual"}) as response:
                self.assertEqual(response.status, 200, await response.text())
            self.assertEqual(runtime.configuration.control_mode, "manual")
            async with client.post(url + "/control", json={"enabled": True}) as response:
                self.assertEqual(response.status, 409, await response.text())
            self.assertIsNone(runtime.session)
            async with client.post(url + "/control-mode", json={"mode": "automatic"}) as response:
                self.assertEqual(response.status, 200, await response.text())
            async with client.post(url + "/control", json={"enabled": True}) as response:
                self.assertEqual(response.status, 200, await response.text())
            self.assertIsNotNone(runtime.session)
            identity = runtime.session.session_id
            async with client.post(url + "/control-mode", json={"mode": "manual"}) as response:
                self.assertEqual(response.status, 409, await response.text())
            async with client.post(url + "/programs", json={"programs": catalog}) as response:
                self.assertEqual(response.status, 409, await response.text())
            async with client.post(url + "/button-program", json={"profile": "constant"}) as response:
                self.assertEqual(response.status, 409, await response.text())
            async with client.post(url + "/heater", json={"value": False}) as response:
                self.assertEqual(response.status, 200, await response.text())
            async with client.post(url + "/heater", json={"value": True}) as response:
                self.assertEqual(response.status, 200, await response.text())
                self.assertTrue((await response.json())["manual_controls"]["heater"]["manual"])
            async with client.post(url + "/heater", json={"value": False}) as response:
                self.assertEqual(response.status, 200, await response.text())
                self.assertFalse((await response.json())["manual_controls"]["heater"]["manual"])
            async with client.post(url + "/control", json={"enabled": False}) as response:
                self.assertEqual(response.status, 200, await response.text())
            self.assertEqual(runtime.session.session_id, identity)
            self.assertFalse(runtime.session.operation_enabled)

            async with client.post(url + "/temperature", json={"target_temperature_c": 81}) as response:
                self.assertEqual(response.status, 200, await response.text())
            async with client.post(url + "/program", json={"profile": "constant"}) as response:
                self.assertEqual(response.status, 200, await response.text())
            for value in ("normal", True, False, None):
                async with client.post(url + "/light", json={"value": value}) as response:
                    self.assertEqual(response.status, 200, await response.text())
            async with client.get(url + "/state") as response:
                self.assertEqual(response.status, 200, await response.text())
                permissions = (await response.json())["permissions"]
                self.assertTrue(permissions["control"])
                self.assertTrue(permissions["heater"])
            async with client.get(url + "/archive") as response:
                self.assertEqual(response.status, 200, await response.text())

            async with client.post(url + "/finish_phase", json={"purpose": "after_run", "token": "stale"}) as response:
                self.assertEqual(response.status, 409, await response.text())
            self.assertEqual(runtime.session.session_id, identity)
            self.assertFalse(runtime.session.operation_enabled)
            session = runtime.session

            async def forbidden(request):
                async with request as response:
                    self.assertEqual(response.status, 403, await response.text())
                self.assertIs(runtime.session, session)
                self.assertEqual(runtime.session.session_id, identity)
                self.assertFalse(runtime.session.operation_enabled)

            async with client.post(url + "/light", json={"value": 42}) as response:
                self.assertEqual(response.status, 200, await response.text())
            self.assertEqual(runtime.device.light_output.manual_brightness, 42)
            self.assertEqual(runtime.session.session_id, identity)
            session = runtime.session
            await forbidden(client.post(url + "/parameters", json=dict(self.entry.options["parameters"])))
            await forbidden(client.post(url + "/logging", json={"level": "INFO"}))
            await forbidden(client.get(url + "/export"))

            now = next(d.due_at for d in runtime.session.deadlines
                       if d.purpose == "session_gap")
            await runtime.tick()
            self.assertIsNone(runtime.session)
            async with client.post(url + "/control-mode", json={"mode": "automatic"}) as response:
                self.assertEqual(response.status, 200, await response.text())
            async with client.get(url + "/state") as response:
                permissions = (await response.json())["permissions"]
                self.assertTrue(permissions["control"])
                self.assertTrue(permissions["heater"])
            async with client.post(url + "/heater", json={"value": False}) as response:
                self.assertEqual(response.status, 200, await response.text())
            self.assertEqual(runtime.configuration.control_mode, "automatic")
            self.assertIsNone(runtime.session)

    async def test_normal_user_phase_end_checks_permission_payload_and_replay(self):
        from datetime import datetime, UTC, timedelta
        from custom_components.ha_sauna.core.timeline import Event, Kind

        user = await self.hass.auth.async_create_user(
            "Cooling control", group_ids=[GROUP_ID_USER]
        )
        refresh = await self.hass.auth.async_create_refresh_token(
            user, client_id="http://localhost/"
        )
        headers = {
            "Authorization": "Bearer "
            + self.hass.auth.async_create_access_token(refresh)
        }
        runtime = self.entry.runtime_data
        base = now = datetime.now(UTC)
        runtime._clock = lambda: now
        temperature_entity = self.entry.options["bindings"]["upper_temperature"]
        temperature_state = self.hass.states.get(temperature_entity)
        self.hass.states.async_set(temperature_entity, "70", temperature_state.attributes, force_update=True)
        await self.hass.async_block_till_done()
        await runtime.set_operation(True)
        identity = runtime.session.session_id
        self.assertEqual(runtime.session.timeline.door, "closed")
        for second, kind in ((0,Kind.DOOR_OPEN),(1,Kind.DOOR_CLOSE),(2,Kind.INFUSION),(3,Kind.DOOR_OPEN),(4,Kind.VENTILATION)):
            now = base + timedelta(seconds=second)
            await runtime.receive(Event(f"api-phase:{second}",identity,kind,now,now))
        token = runtime.session.after_run.phase_id
        base_url = self.base + "/" + self.entry.entry_id
        url = base_url + "/finish_phase"
        denied = await self.hass.auth.async_create_user("No cooling control", group_ids=[])
        refresh = await self.hass.auth.async_create_refresh_token(
            denied, client_id="http://localhost/"
        )
        denied_headers = {
            "Authorization": "Bearer "
            + self.hass.auth.async_create_access_token(refresh)
        }
        async with ClientSession(headers=denied_headers) as client:
            async with client.post(url, json={"purpose":"after_run","token":token}) as response:
                self.assertEqual(response.status, 403, await response.text())
        self.assertEqual(runtime.session.after_run.phase_id, token)
        async with ClientSession(headers=headers) as client:
            async with client.get(base_url + "/state") as response:
                self.assertEqual(response.status, 200, await response.text())
                state = await response.json()
                self.assertFalse(state["permissions"]["admin"])
                self.assertTrue(state["permissions"]["control"])
                self.assertEqual(state["session"]["after_run"]["phase_id"], token)
            for body in ([], {}, {"purpose":[],"token":token}, {"purpose":"session_gap","token":token},
                         {"purpose":"forced_cooling","token":token}, {"purpose":"confirmation","token":token},
                         {"purpose":"after_run","token":[]}, {"purpose":"after_run","token":""},
                         {"purpose":"after_run","token":token,"extra":1}):
                async with client.post(url, json=body) as response:
                    self.assertEqual(response.status,400,await response.text())
            async with client.post(url, json={"purpose":"after_run","token":"stale"}) as response:
                self.assertEqual(response.status,409)
            self.assertEqual(runtime.session.after_run.phase_id,token)
            now = base + timedelta(seconds=10)
            async with client.post(url, json={"purpose":"after_run","token":token}) as response:
                self.assertEqual(response.status,200,await response.text())
            self.assertIsNone(runtime.session.after_run)
            self.assertEqual(runtime.session.after_run_history[-1].ends_at,now)
            self.assertEqual(runtime.session.timeline.gang_count,1)
            self.assertEqual(runtime.session.session_id,identity)
            self.assertTrue(runtime.session.operation_enabled)
            async with client.post(url, json={"purpose":"after_run","token":token}) as response:
                self.assertEqual(response.status,409)
            self.assertEqual(len(runtime.session.after_run_history), 1)
            async with client.post(base_url + "/control", json={"enabled":False}) as response:
                self.assertEqual(response.status, 200, await response.text())
            async with client.post(url, json={"purpose":"after_run","token":token}) as response:
                self.assertEqual(response.status, 409, await response.text())
            self.assertFalse(runtime.session.operation_enabled)
            await runtime.archive.flush()
            archived = await asyncio.to_thread(runtime.archive.read, identity, limit=10000)
            manual_ends = [row for row in archived["records"] if row["kind"] == "manual_phase_end"]
            self.assertEqual(len(manual_ends), 1)
            self.assertEqual(manual_ends[0]["payload"]["token"], token)

    async def test_manual_heating_does_not_pause_cooling_before_explicit_api_end(self):
        from datetime import datetime, UTC, timedelta
        from custom_components.ha_sauna.core.timeline import Event, Kind
        import asyncio

        runtime = self.entry.runtime_data
        base = now = datetime.now(UTC)
        runtime._clock = lambda: now
        temperature_entity = self.entry.options["bindings"]["upper_temperature"]
        temperature_state = self.hass.states.get(temperature_entity)
        self.hass.states.async_set(temperature_entity, "70", temperature_state.attributes, force_update=True)
        await self.hass.async_block_till_done()
        await runtime.set_operation(True)
        identity = runtime.session.session_id
        runtime.controller.report_heating(True, now)
        self.assertEqual(runtime.session.timeline.door, "closed")
        for second, kind in ((0, Kind.DOOR_OPEN), (1, Kind.DOOR_CLOSE), (2, Kind.INFUSION),
                             (3, Kind.DOOR_OPEN), (4, Kind.VENTILATION)):
            now = base + timedelta(seconds=second)
            await runtime.receive(Event(f"api-paused-phase:{second}", identity, kind, now, now))
        token = runtime.session.after_run.phase_id
        now = base + timedelta(seconds=10)
        await runtime.tick()
        runtime.controller.set_heater_override(False, now)
        original_phase = runtime.session.after_run
        original_deadline = runtime.controller.heater_override_ends_at
        url = self.base + "/" + self.entry.entry_id
        async with ClientSession(headers=self.headers) as client:
            async with client.get(url + "/state") as response:
                state = await response.json()
                reason = state["manual_controls"]["heater"]["blocked_on_reason"]
                self.assertIn("Ofenkühlung", reason)
                from custom_components.ha_sauna.presentation import public_state
                self.assertEqual(public_state(state)["manual_controls"]["heater"]["blocked_on_reason"], reason)
            async with client.post(url + "/heater", json={"value": True}) as response:
                self.assertEqual(response.status, 409, await response.text())
                self.assertEqual((await response.json())["error"], reason)
            self.assertIs(runtime.controller.heater_override, False)
            self.assertEqual(runtime.controller.heater_override_ends_at, original_deadline)
            self.assertEqual(runtime.session.after_run, original_phase)
            async with client.post(url + "/heater", json={"value": None}) as response:
                self.assertEqual(response.status, 200, await response.text())
            self.assertIsNone(runtime.controller.heater_override)
            self.assertEqual(runtime.session.after_run, original_phase)
            phase = runtime.session.after_run
            planned_end = phase.ends_at
            self.assertIsNotNone(planned_end)
            self.assertIsNone(phase.paused_at)
            self.assertEqual(phase.elapsed_seconds, 6)
            self.assertFalse(runtime.controller.last_decision.heat)
            phase_entity = next(state for state in self.hass.states.async_all("sensor")
                                if state.attributes.get("session_id") == identity
                                and "after_run_paused" in state.attributes)
            self.assertEqual(phase_entity.attributes["after_run_ends_at"], planned_end.isoformat())
            self.assertFalse(phase_entity.attributes["after_run_paused"])
            self.assertEqual(phase_entity.attributes["after_run_remaining_seconds"], phase.remaining_seconds)
            now = base + timedelta(seconds=20)
            async with client.post(url + "/finish_phase", json={"purpose": "after_run", "token": token}) as response:
                self.assertEqual(response.status, 200, await response.text())

        self.assertIsNone(runtime.session.after_run)
        self.assertEqual(runtime.session.after_run_history[-1].elapsed_seconds, 16)
        self.assertIsNone(runtime.session.cooling)
        self.assertIsNone(runtime.controller.heater_override)
        self.assertEqual(runtime.session.cooling_history, ())
        self.assertTrue(runtime.controller.last_decision.heat)
        await runtime.archive.flush()
        archived = await asyncio.to_thread(runtime.archive.read, identity, limit=10000)
        record = next(row for row in archived["records"] if row["kind"] == "manual_phase_end")
        self.assertEqual(record["session_id"], identity)
        self.assertEqual(record["payload"], {"purpose": "after_run", "token": token,
                                             "planned_ends_at": planned_end.isoformat(), "ended_at": now.isoformat()})

    async def test_logging_is_live_persistent_and_does_not_reload_or_end_session(self):
        import logging
        runtime = self.entry.runtime_data
        await runtime.set_operation(True)
        identity = runtime.session.session_id
        url = self.base + "/" + self.entry.entry_id
        async with ClientSession(headers=self.headers) as client:
            for level in ("DEBUG", "ERROR", "INFO"):
                async with client.post(url + "/logging", json={"level": level}) as response:
                    self.assertEqual(response.status, 200, await response.text())
                await self.hass.async_block_till_done()
                self.assertIs(self.entry.runtime_data, runtime)
                self.assertEqual(runtime.session.session_id, identity)
                self.assertTrue(runtime.session.operation_enabled)
                self.assertEqual(self.entry.options["log_level"], level)
                self.assertEqual(runtime.log.logger.level, getattr(logging, level))
            async with client.post(url + "/logging", json={"level": "VERBOSE"}) as response:
                self.assertEqual(response.status, 400)
            async with client.post(url + "/logging", json={"level": []}) as response:
                self.assertEqual(response.status, 400)

    async def test_old_incomplete_settings_receive_defaults_and_can_start(self):
        url = self.base + "/" + self.entry.entry_id
        values = dict(self.entry.options["parameters"])
        for key in ("sensor_timeout_seconds", "feedback_timeout_seconds", "fault_confirmation_seconds"):
            values.pop(key)
        self.hass.config_entries.async_update_entry(self.entry, options={**self.entry.options,"parameters":values})
        await self.hass.async_block_till_done()
        # The fixture is a saved older installation. Exercise loading it;
        # deleting values equal to live defaults is not a live config change.
        self.assertTrue(await self.hass.config_entries.async_reload(self.entry.entry_id))
        await self.hass.async_block_till_done()
        defaults = Parameters({}).values
        for key in ("sensor_timeout_seconds", "feedback_timeout_seconds", "fault_confirmation_seconds"):
            self.assertEqual(self.entry.options["parameters"][key], defaults[key])
        async with ClientSession(headers=self.headers) as client:
            async with client.get(url + "/state") as response:
                state = await response.json()
                self.assertIsNone(state["session"])
                self.assertFalse(state["configuration_locked"])
                self.assertEqual(state["start_errors"],[])
            async with client.post(url + "/control", json={"enabled": True}) as response:
                self.assertEqual(response.status, 200, await response.text())
            self.assertTrue(self.entry.runtime_data.session.operation_enabled)

    async def test_stale_measurement_rejects_start_with_useful_german_reason(self):
        from datetime import UTC, datetime, timedelta
        runtime=self.entry.runtime_data
        now=datetime.now(UTC)+timedelta(seconds=
            runtime.configuration.parameters.values["sensor_timeout_seconds"] + 1)
        runtime._clock=lambda:now
        async with ClientSession(headers=self.headers) as client:
            async with client.post(self.base+"/"+self.entry.entry_id+"/control",json={"enabled":True}) as response:
                self.assertEqual(response.status,409)
                error=(await response.json())["error"]
                self.assertIn("Start nicht möglich",error)
                self.assertIn("Temperaturwert",error)
                self.assertNotIn("upper_temperature",error)
        self.assertIsNone(runtime.session)

    async def test_live_temperature_api_preserves_session_detector_and_deadlines(self):
        from custom_components.ha_sauna.core.timeline import Event, Kind
        runtime=self.entry.runtime_data
        temperature = self.entry.options["bindings"]["upper_temperature"]
        self.hass.states.async_set(temperature, "80", self.hass.states.get(temperature).attributes)
        await self.hass.async_block_till_done()
        await runtime.set_operation(True)
        session_id=runtime.session.session_id
        now=runtime._clock()
        runtime._clock = lambda: now
        for kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE, Kind.INFUSION):
            await runtime.receive(Event(kind.value, session_id, kind, now, now))
        gang=runtime.session.timeline.active
        self.assertIsNotNone(gang)
        detector=runtime.detector
        url=self.base+"/"+self.entry.entry_id
        async with ClientSession(headers=self.headers) as client:
            for values in ({"target_temperature_c":81,"final_temperature_c":95}, {"final_temperature_c":None}):
                async with client.post(url+"/temperature",json=values) as response:
                    self.assertEqual(response.status,200,await response.text())
                await self.hass.async_block_till_done()
                self.assertIs(self.entry.runtime_data,runtime)
                self.assertIs(runtime.detector,detector)
                self.assertEqual(runtime.session.timeline.active,gang)
                self.assertEqual(runtime.controller.target_temperature,81)
                self.assertTrue(runtime.session.operation_enabled)
            async with client.post(url+"/temperature",json={"temperature_increase_c":2}) as response:
                self.assertEqual(response.status,400)
            async with client.post(url+"/temperature",json={"removed_cooling_option":0}) as response:
                self.assertEqual(response.status,400)
            await runtime.set_operation(False)
            self.assertIsNone(runtime.session.after_run)
            deadlines = runtime.session.deadlines
            async with client.post(url+"/temperature",json={"target_temperature_c":95}) as response:
                self.assertEqual(response.status,200,await response.text())
            self.assertIsNone(runtime.session.after_run)
            self.assertEqual(runtime.session.deadlines, deadlines)
            self.assertFalse(runtime.controller.last_decision.heat)
            self.assertFalse(runtime.session.operation_enabled)

    async def test_external_options_writer_cannot_reload_away_active_session(self):
        runtime=self.entry.runtime_data
        await runtime.set_operation(True)
        identity=runtime.session.session_id
        options=dict(self.entry.options)
        self.hass.config_entries.async_update_entry(self.entry,options={**options,"parameters":{**options["parameters"],"after_run_minutes":999}})
        await self.hass.async_block_till_done()
        self.assertIs(self.entry.runtime_data,runtime)
        self.assertEqual(runtime.session.session_id,identity)
        self.assertEqual(dict(self.entry.options),options)

    async def test_program_and_manual_api_report_their_selected_and_automatic_values(self):
        """The browser routes use the same serialized runtime as HA entities."""
        runtime = self.entry.runtime_data
        url = self.base + "/" + self.entry.entry_id
        async with ClientSession(headers=self.headers) as client:
            free = {"target_temperature_c": 76, "final_temperature_c": 91,
                    "temperature_gangs": 5}
            async with client.post(url + "/program", json=free) as response:
                self.assertEqual(response.status, 200, await response.text())
                self.assertEqual((await response.json())["program_mode"], "progressive")
            self.assertEqual(runtime.configuration.parameters.values["target_temperature_c"], 76)
            self.assertEqual(runtime.configuration.parameters.values["final_temperature_c"], 91)
            async with client.post(url + "/program", json={"profile": "constant"}) as response:
                self.assertEqual(response.status, 200, await response.text())
            self.assertEqual(runtime.configuration.program_mode, "constant")
            async with client.post(url + "/program", json={"target_temperature_c": 76,
                                                             "final_temperature_c": None,
                                                             "temperature_gangs": 5}) as response:
                self.assertEqual(response.status, 400)
            async with client.post(url + "/light", json={"value": 42}) as response:
                self.assertEqual(response.status, 200, await response.text())
                self.assertEqual((await response.json())["manual_controls"]["light"]["manual"], 42)
            async with client.post(url + "/heater", json={"value": False}) as response:
                self.assertEqual(response.status, 200, await response.text())
                self.assertFalse((await response.json())["manual_controls"]["heater"]["manual"])
            async with client.get(url + "/state") as response:
                state = await response.json()
                self.assertIn("permissions", state)
                self.assertIn("manual_controls", state)
                self.assertFalse(state["manual_controls"]["heater"]["manual"])
            async with client.post(url + "/heater", json={"value": None}) as response:
                self.assertEqual(response.status, 200, await response.text())
            self.assertIsNone(runtime.controller.heater_override)

    async def test_constant_program_api_applies_standard_then_preserves_explicit_choice(self):
        """Follow the panel action through persistence, live control and reload."""
        options = dict(self.entry.options)
        standard_definition = BY_KEY["standard_temperature_c"]
        saved_standard = standard_definition.default + standard_definition.number_step
        self.hass.config_entries.async_update_entry(self.entry, options={
            **options,
            "parameters": {**options["parameters"], "standard_temperature_c": 87},
            "field_standards": {"parameters.standard_temperature_c": saved_standard},
        })
        await self.hass.async_block_till_done()
        runtime = self.entry.runtime_data
        await runtime.set_operation(True)
        session_id = runtime.session.session_id
        url = self.base + "/" + self.entry.entry_id
        program = runtime.configuration.temperature_programs[0]
        async with ClientSession(headers=self.headers) as client:
            async with client.post(url + "/program", json={"profile": program.id}) as response:
                self.assertEqual(response.status, 200, await response.text())
            self.assertEqual(runtime.controller.target_temperature, program.start_c)
            async with client.post(url + "/program", json={"profile": "constant"}) as response:
                self.assertEqual(response.status, 200, await response.text())
                result = await response.json()
            self.assertEqual(result["parameters"]["target_temperature_c"], 87)
            self.assertEqual(runtime.controller.target_temperature, 87)
            self.assertEqual(runtime.configuration.parameters.values["target_temperature_c"], 87)
            self.assertEqual(self.entry.options["parameters"]["target_temperature_c"], 87)
            self.assertIsNone(runtime.configuration.selected_program_id)
            self.assertEqual(runtime.session.session_id, session_id)
            async with client.post(url + "/program", json={"profile": program.id}) as response:
                self.assertEqual(response.status, 200, await response.text())
            async with client.post(url + "/program", json={"target_temperature_c": 82}) as response:
                self.assertEqual(response.status, 200, await response.text())
                result = await response.json()
            self.assertEqual(result["program_mode"], "constant")
            self.assertIsNone(result["selected_program_id"])
            self.assertIsNone(result["temperature_steps"])
            await runtime.tick()
            self.assertEqual(runtime.controller.target_temperature, 82)
            self.assertEqual(runtime.configuration.parameters.values["standard_temperature_c"], 87)
            self.assertEqual(runtime.configuration.field_standards["parameters.standard_temperature_c"], saved_standard)
            async with client.get(url + "/state") as response:
                self.assertEqual(response.status, 200, await response.text())
                self.assertEqual((await response.json())["target_temperature"], 82)
        self.assertTrue(await self.hass.config_entries.async_reload(self.entry.entry_id))
        await self.hass.async_block_till_done()
        self.assertEqual(self.entry.runtime_data.controller.target_temperature, 82)
        self.assertEqual(self.entry.options["parameters"]["standard_temperature_c"], 87)
        self.assertEqual(self.entry.options["field_standards"], {"parameters.standard_temperature_c": saved_standard})

    async def test_program_post_reports_runtime_selection_for_every_program_form(self):
        runtime = self.entry.runtime_data
        url = self.base + "/" + self.entry.entry_id
        catalog = list(self.entry.options["temperature_programs"])
        catalog.append(
            {"id": "custom_steps", "name": "Stufen", "temperature_steps": [76, 82, 91]}
        )
        catalog.extend(
            {"id": identity, "name": identity, "temperature_steps": [76, 83, 90]}
            for identity in ("individual", "custom:one", "custom:one:two")
        )
        async with ClientSession(headers=self.headers) as client:
            async with client.post(url + "/programs", json={"programs": catalog}) as response:
                self.assertEqual(response.status, 200, await response.text())

            cases = (
                ({"profile": "genusszeit"}, "genusszeit", "progressive", None),
                ({"profile": "custom_steps"}, "custom_steps", "progressive", [76, 82, 91]),
                ({"profile": "individual"}, "individual", "progressive", [76, 83, 90]),
                ({"profile": "custom:one"}, "custom:one", "progressive", [76, 83, 90]),
                ({"profile": "custom:one:two"}, "custom:one:two", "progressive", [76, 83, 90]),
                ({"profile": "progressive"}, None, "progressive", None),
                ({"temperature_steps": [75, 80, 87]}, None, "progressive", [75, 80, 87]),
                (
                    {"target_temperature_c": 77, "final_temperature_c": 90,
                     "temperature_gangs": 4},
                    None, "progressive", None,
                ),
                ({"profile": "constant"}, None, "constant", None),
            )
            for body, selected_id, mode, steps in cases:
                with self.subTest(body=body):
                    async with client.post(url + "/program", json=body) as response:
                        self.assertEqual(response.status, 200, await response.text())
                        result = await response.json()
                    self.assertTrue(result["success"])
                    self.assertEqual(result["selected_program_id"], selected_id)
                    self.assertEqual(result["program_mode"], mode)
                    self.assertEqual(result["temperature_steps"], steps)
                    self.assertEqual(result["parameters"], runtime.configuration.parameters.as_dict())
                    self.assertEqual(result["selected_program_id"], runtime.configuration.selected_program_id)
                    self.assertEqual(self.entry.options["selected_program_id"], selected_id)
                    self.assertEqual(list(runtime.configuration.temperature_steps or []), steps or [])

            async with client.post(url + "/program", json={"profile": "individual"}) as response:
                self.assertEqual(response.status, 200, await response.text())
            self.assertTrue(await self.hass.config_entries.async_reload(self.entry.entry_id))
            await self.hass.async_block_till_done()
            self.assertEqual(self.entry.runtime_data.configuration.selected_program_id, "individual")
            self.assertEqual(self.entry.runtime_data.configuration.temperature_steps, (76, 83, 90))
            async with client.get(url + "/state") as response:
                self.assertEqual(response.status, 200, await response.text())
                configuration = (await response.json())["configuration"]
            self.assertEqual(configuration["selected_program_id"], "individual")
            self.assertEqual(configuration["temperature_steps"], [76, 83, 90])

    async def test_catalog_reorder_preserves_selected_and_button_program_ids(self):
        runtime = self.entry.runtime_data
        url = self.base + "/" + self.entry.entry_id
        async with ClientSession(headers=self.headers) as client:
            async with client.post(url + "/program", json={"profile": "genusszeit"}) as response:
                self.assertEqual(response.status, 200, await response.text())
                self.assertEqual((await response.json())["selected_program_id"], "genusszeit")
            async with client.post(url + "/button-program", json={"profile": "gipfelstuermer"}) as response:
                self.assertEqual(response.status, 200, await response.text())
            reordered = list(reversed(self.entry.options["temperature_programs"]))
            async with client.post(url + "/programs", json={"programs": reordered}) as response:
                self.assertEqual(response.status, 200, await response.text())
                self.assertEqual(
                    [program["id"] for program in (await response.json())["programs"]],
                    [program["id"] for program in reordered],
                )
            await self.hass.async_block_till_done()
            self.assertEqual(runtime.configuration.selected_program_id, "genusszeit")
            self.assertEqual(runtime.configuration.button_program, "gipfelstuermer")
            self.assertEqual(self.entry.options["selected_program_id"], "genusszeit")
            self.assertEqual(self.entry.options["button_program"], "gipfelstuermer")
            async with client.get(url + "/state") as response:
                self.assertEqual(response.status, 200, await response.text())
                configuration = (await response.json())["configuration"]
                self.assertEqual(configuration["selected_program_id"], "genusszeit")
                self.assertEqual(configuration["button_program"], "gipfelstuermer")

    async def test_program_post_auth_and_errors_do_not_change_selection(self):
        url = self.base + "/" + self.entry.entry_id + "/program"
        async with ClientSession() as client:
            async with client.post(url, json={"profile": "constant"}) as response:
                self.assertEqual(response.status, 401)
        denied = await self.hass.auth.async_create_user("No program control", group_ids=[])
        token = await self.hass.auth.async_create_refresh_token(denied, client_id="http://localhost/")
        headers = {"Authorization": "Bearer " + self.hass.auth.async_create_access_token(token)}
        async with ClientSession(headers=headers) as client:
            async with client.post(url, json={"profile": "constant"}) as response:
                self.assertEqual(response.status, 403)
        async with ClientSession(headers=self.headers) as client:
            async with client.post(url, json={"profile": "genusszeit"}) as response:
                self.assertEqual(response.status, 200, await response.text())
            for body in ({"profile": "missing"}, {"profile": []},
                         {"temperature_steps": [75, 1000]}, {"profile": "constant", "extra": True}):
                with self.subTest(body=body):
                    async with client.post(url, json=body) as response:
                        self.assertEqual(response.status, 400, await response.text())
                    self.assertEqual(self.entry.runtime_data.configuration.selected_program_id, "genusszeit")
            runtime = self.entry.runtime_data
            runtime.reconfiguring = True
            try:
                async with client.post(url, json={"profile": "constant"}) as response:
                    self.assertEqual(response.status, 409, await response.text())
                self.assertEqual(runtime.configuration.selected_program_id, "genusszeit")
            finally:
                runtime.reconfiguring = False

    async def test_field_standards_keep_factory_saved_and_active_values_separate(self):
        from custom_components.ha_sauna.runtime import Configuration

        definition = BY_KEY["nominal_power_kw"]
        field = "parameters.nominal_power_kw"
        factory = definition.default
        active, saved, draft = (factory + count * definition.number_step for count in (1, 2, 3))
        standards = {field: saved, "appearance.colors.event_door": "#102030"}
        self.hass.config_entries.async_update_entry(self.entry, options={
            **self.entry.options,
            "parameters": {**self.entry.options["parameters"], definition.key: active},
            "field_standards": standards,
        })
        await self.hass.async_block_till_done()
        runtime = self.entry.runtime_data
        configuration = runtime.configuration
        original = deepcopy(dict(self.entry.options))
        snapshot = configuration.as_snapshot()
        url = self.base + "/" + self.entry.entry_id
        request = {"action": "overwrite_standard", "field": field, "value": draft}
        async with ClientSession(headers=self.headers) as client:
            with patch.object(self.hass.config_entries, "async_update_entry",
                              wraps=self.hass.config_entries.async_update_entry) as write, \
                    patch.object(runtime, "_cycle", wraps=runtime._cycle) as cycle:
                async with client.get(url + "/standards") as response:
                    self.assertEqual(response.status, 200, await response.text())
                    inventory = await response.json()
                row = next(item for item in inventory["fields"] if item["id"] == field)
                self.assertEqual((row["factory"], row["standard"], row["active"], row["has_standard"]),
                                 (factory, saved, active, True))
                async with client.post(url + "/standards", json=request) as response:
                    self.assertEqual(response.status, 200, await response.text())
                    preview = await response.json()
                self.assertEqual(len(preview["fields"]), 1)
                self.assertEqual((preview["fields"][0]["target"], preview["fields"][0]["target_standard"]),
                                 (active, draft))
                write.assert_not_called()
                cycle.assert_not_called()
            self.assertEqual(dict(self.entry.options), original)
            self.assertIs(runtime.configuration, configuration)
            self.assertEqual(configuration.as_snapshot(), snapshot)
            inventory["fields"][0]["active"] = "unowned response edit"
            async with client.get(url + "/standards") as response:
                self.assertNotIn("unowned response edit", str(await response.json()))
            async with client.post(url + "/standards", json={
                **request, "confirmation": preview["confirmation"],
            }) as response:
                self.assertEqual(response.status, 200, await response.text())
                self.assertEqual(await response.json(), {**preview, "applied": True})
            await self.hass.async_block_till_done()
            standards = {**standards, field: draft}
            self.assertEqual(self.entry.options["field_standards"], standards)
            self.assertEqual(self.entry.options["parameters"][definition.key], active)
            self.assertEqual(configuration.as_snapshot(), snapshot)
            async with client.patch(url + "/parameters", json={definition.key: factory + 4 * definition.number_step}) as response:
                self.assertEqual(response.status, 200, await response.text())
            await self.hass.async_block_till_done()
            async with client.post(url + "/appearance", json={"colors": {"event_door": "#304050"}}) as response:
                self.assertEqual(response.status, 200, await response.text())
            await self.hass.async_block_till_done()
            self.assertEqual(self.entry.options["field_standards"], standards)
        self.assertTrue(await self.hass.config_entries.async_reload(self.entry.entry_id))
        await self.hass.async_block_till_done()
        restored = self.entry.runtime_data.configuration
        self.assertEqual(dict(restored.field_standards), standards)
        self.assertEqual(restored.parameters.values[definition.key], factory + 4 * definition.number_step)
        self.assertEqual(restored.appearance["colors"]["event_door"], "#304050")
        self.assertEqual(Configuration.from_options(restored.as_options()).as_options(), restored.as_options())
        async with ClientSession(headers=self.headers) as client:
            async with client.get(url + "/state") as response:
                state = await response.json()
            self.assertEqual(state["configuration"]["field_standards"], standards)
            self.assertEqual(state["configuration"]["parameters"][definition.key], factory + 4 * definition.number_step)
            self.assertNotIn("standards", state)

    async def test_http_electricity_price_standards_restore_null_and_zero_by_scope(self):
        key = "electricity_price_eur_per_kwh"
        field = "parameters." + key
        positive = BY_KEY[key].number_step
        url = self.base + "/" + self.entry.entry_id + "/standards"
        initial = deepcopy(dict(self.entry.options))
        initial["parameters"][key] = positive
        initial["field_standards"].pop(field, None)
        async with ClientSession(headers=self.headers) as client:
            for saved in (None, 0):
                with self.subTest(saved=saved):
                    self.hass.config_entries.async_update_entry(
                        self.entry, options=deepcopy(initial),
                    )
                    await self.hass.async_block_till_done()
                    request = {"action": "overwrite_standard", "field": field, "value": saved}
                    async with client.post(url, json=request) as response:
                        self.assertEqual(response.status, 200, await response.text())
                        preview = await response.json()
                    row = preview["fields"][0]
                    self.assertEqual(row["id"], field)
                    self.assertIsNone(row["factory"])
                    self.assertEqual(
                        (
                            row["target"], row["target_standard"],
                            row["target_has_standard"],
                        ),
                        (positive, saved, True),
                    )
                    preview_confirmation = preview["confirmation"]
                    async with client.post(url, json={
                        **request, "confirmation": preview_confirmation,
                    }) as response:
                        self.assertEqual(response.status, 200, await response.text())
                        self.assertEqual(
                            await response.json(), {**preview, "applied": True},
                        )
                    await self.hass.async_block_till_done()
                    self.assertIn(field, self.entry.options["field_standards"])
                    self.assertEqual(
                        self.entry.options["field_standards"][field], saved,
                    )
                    self.assertEqual(self.entry.options["parameters"][key], positive)
                    baseline = deepcopy(dict(self.entry.options))
                    for scope in (
                        {"field": field}, {"group": "operation"}, {"all": True},
                    ):
                        self.hass.config_entries.async_update_entry(
                            self.entry, options=deepcopy(baseline),
                        )
                        await self.hass.async_block_till_done()
                        for action in ("restore_standard", "restore_factory"):
                            with self.subTest(scope=scope, action=action):
                                target = saved if action == "restore_standard" else None
                                request = {"action": action, **scope}
                                async with client.post(
                                    url, json=request,
                                ) as response:
                                    self.assertEqual(response.status, 200, await response.text())
                                    preview = await response.json()
                                row = next(
                                    item for item in preview["fields"]
                                    if item["id"] == field
                                )
                                self.assertEqual(preview["scope"], scope)
                                self.assertEqual(
                                    (
                                        row["factory"], row["standard"],
                                        row["has_standard"],
                                    ),
                                    (None, saved, True),
                                )
                                self.assertEqual(
                                    (
                                        row["target"], row["target_standard"],
                                        row["target_has_standard"],
                                    ),
                                    (target, target, action == "restore_standard"),
                                )
                                preview_confirmation = preview["confirmation"]
                                async with client.post(url, json={
                                    **request, "confirmation": preview_confirmation,
                                }) as response:
                                    self.assertEqual(response.status, 200, await response.text())
                                    self.assertEqual(
                                        await response.json(),
                                        {**preview, "applied": True},
                                    )
                                await self.hass.async_block_till_done()
                                self.assertIn(key, self.entry.options["parameters"])
                                self.assertEqual(
                                    self.entry.options["parameters"][key], target,
                                )
                                configuration = self.entry.runtime_data.configuration
                                self.assertEqual(
                                    configuration.parameters.values[key], target,
                                )
                                if action == "restore_standard":
                                    self.assertIn(
                                        field, self.entry.options["field_standards"],
                                    )
                                    self.assertEqual(
                                        self.entry.options["field_standards"][field],
                                        saved,
                                    )
                                    self.assertIn(field, configuration.field_standards)
                                    self.assertEqual(
                                        configuration.field_standards[field], saved,
                                    )
                                else:
                                    self.assertNotIn(
                                        field, self.entry.options["field_standards"],
                                    )
                                    self.assertNotIn(
                                        field, configuration.field_standards,
                                    )

    async def test_field_standard_restores_apply_complete_scopes_and_preserve_excluded_owners(self):
        from harness import retain_session

        runtime = self.entry.runtime_data
        await runtime.set_operation(True)
        retain_session(runtime)
        archived_id = runtime.session.session_id
        await runtime.set_operation(False)
        runtime.controller.finish_session(runtime._clock(), light_after_run=False)
        await runtime._cycle()
        await runtime.archive.flush()
        archive_clock = runtime._clock()
        original_archive = runtime.archive.read(archived_id, now=archive_clock, limit=10000)
        power_definition = BY_KEY["nominal_power_kw"]
        other = await create_sauna(self.hass, parameter_overrides={
            power_definition.key: power_definition.default + 4 * power_definition.number_step,
        }, binding_overrides={"heater": "switch.restore_other_heater"})
        other_options = deepcopy(dict(other.options))
        other_configuration = other.runtime_data.configuration
        presence = "binary_sensor.restore_presence"
        self.hass.states.async_set(presence, "off", {"device_class": "occupancy"})
        hidden = "parameters.strong_window_seconds"
        fallback = "parameters.sensor_timeout_seconds"
        power = "parameters.nominal_power_kw"
        color = "appearance.colors.event_door"
        unchanged_factory = "appearance.colors.text"
        hidden_definition = BY_KEY["strong_window_seconds"]
        increment = BY_KEY["person_step_seconds"].default
        catalog = deepcopy(self.entry.options["temperature_programs"])
        catalog.append({**catalog[0], "id": "retained_standard_program", "name": "Erhaltenes Programm"})
        program = catalog[-1]
        stages = program.get("temperature_steps") or [program["start_c"], program["end_c"]]
        baseline = {
            **deepcopy(dict(self.entry.options)),
            "presence_source": "ha_presence",
            "bindings": {**self.entry.options["bindings"], "presence": presence},
            "temperature_programs": catalog,
            "selected_program_id": "retained_standard_program",
            "button_program": "retained_standard_program",
            "temperature_steps": stages,
            "parameters": {
                **self.entry.options["parameters"],
                "strong_window_seconds": hidden_definition.default + increment,
                "sensor_timeout_seconds": BY_KEY["sensor_timeout_seconds"].default + BY_KEY["sensor_timeout_seconds"].number_step,
                "nominal_power_kw": power_definition.default + power_definition.number_step,
            },
            "appearance": {"colors": {"event_door": "#010203"}},
            "field_standards": {
                hidden: hidden_definition.default + 2 * increment,
                power: power_definition.default + 2 * power_definition.number_step,
                color: "#040506",
                unchanged_factory: "#AABBCC",
            },
        }
        sensor_fields = {"parameters." + spec["key"] for spec in section("parameters")
                         if spec["settings_group"] == "sensors" and spec["configuration_surface"] != "catalog"}
        excluded = ("bindings", "temperature_programs", "selected_program_id", "button_program",
                    "temperature_steps", "presence_source", "program_mode", "control_mode")
        url = self.base + "/" + self.entry.entry_id + "/standards"
        async with ClientSession(headers=self.headers) as client:
            for action in ("restore_standard", "restore_factory"):
                for scope in ({"field": hidden}, {"group": "sensors"}, {"all": True}):
                    with self.subTest(action=action, scope=scope):
                        self.hass.config_entries.async_update_entry(self.entry, options=deepcopy(baseline))
                        await self.hass.async_block_till_done()
                        before_options = deepcopy(dict(self.entry.options))
                        async with client.get(url) as response:
                            self.assertEqual(response.status, 200, await response.text())
                            before = {row["id"]: row for row in (await response.json())["fields"]}
                        self.assertFalse(before[hidden]["applicable"])
                        self.assertFalse(before[fallback]["has_standard"])
                        self.assertEqual(before[unchanged_factory]["active"], before[unchanged_factory]["factory"])
                        expected_scope = ({hidden} if "field" in scope else
                                          sensor_fields if "group" in scope else set(before))
                        request = {"action": action, **scope}
                        async with client.post(url, json=request) as response:
                            self.assertEqual(response.status, 200, await response.text())
                            preview = await response.json()
                        self.assertEqual({row["id"] for row in preview["fields"]}, expected_scope)
                        self.assertEqual(preview["scope"], scope)
                        self.assertEqual(dict(self.entry.options), before_options)
                        for row in preview["fields"]:
                            self.assertEqual(row["target"], before[row["id"]][
                                "factory" if action == "restore_factory" else "standard"
                            ])
                        async with client.post(url, json={**request, "confirmation": preview["confirmation"]}) as response:
                            self.assertEqual(response.status, 200, await response.text())
                            self.assertEqual(await response.json(), {**preview, "applied": True})
                        await self.hass.async_block_till_done()
                        self.assertFalse(self.entry.runtime_data.reconfiguring)
                        async with client.get(url) as response:
                            self.assertEqual(response.status, 200, await response.text())
                            after = {row["id"]: row for row in (await response.json())["fields"]}
                        for name, row in before.items():
                            if name in expected_scope:
                                self.assertEqual(after[name]["active"], row[
                                    "factory" if action == "restore_factory" else "standard"
                                ])
                                self.assertEqual(after[name]["has_standard"],
                                                 action != "restore_factory" and row["has_standard"])
                            else:
                                self.assertEqual(after[name], row)
                        expected_standards = {
                            name: value for name, value in baseline["field_standards"].items()
                            if action != "restore_factory" or name not in expected_scope
                        }
                        self.assertEqual(self.entry.options["field_standards"], expected_standards)
                        for key in excluded:
                            self.assertEqual(self.entry.options[key], before_options[key])
                        for key in ("target_temperature_c", "final_temperature_c", "temperature_gangs"):
                            self.assertEqual(self.entry.options["parameters"].get(key),
                                             before_options["parameters"].get(key))
                        self.assertEqual(dict(other.options), other_options)
                        self.assertIs(other.runtime_data.configuration, other_configuration)
                        self.assertTrue(await self.hass.config_entries.async_reload(self.entry.entry_id))
                        await self.hass.async_block_till_done()
                        self.assertEqual(dict(self.entry.runtime_data.configuration.field_standards), expected_standards)
                        retained = self.entry.runtime_data.archive.read(
                            archived_id, now=archive_clock, limit=10000)
                        self.assertEqual(retained["session"], original_archive["session"])
                        original_ids = {row["id"] for row in original_archive["records"]}
                        self.assertEqual([row for row in retained["records"] if row["id"] in original_ids],
                                         original_archive["records"])

    async def test_field_standard_confirmation_rechecks_sources_and_rejects_invalid_targets_atomically(self):
        field = "appearance.scales.temperature.minimum"
        bounds = section("appearance")["scales"]["temperature"]["default"]
        maximum = bounds["maximum"]
        increment = (maximum - bounds["minimum"]) / 10
        active, saved, newer = (bounds["minimum"] + count * increment for count in (1, 2, 3))
        self.hass.config_entries.async_update_entry(self.entry, options={
            **self.entry.options,
            "appearance": {"scales": {"temperature": {"minimum": active, "maximum": maximum}}},
            "field_standards": {field: saved, "parameters.nominal_power_kw":
                                BY_KEY["nominal_power_kw"].default + BY_KEY["nominal_power_kw"].number_step},
        })
        await self.hass.async_block_till_done()
        url = self.base + "/" + self.entry.entry_id
        request = {"action": "restore_standard", "field": field}
        async with ClientSession(headers=self.headers) as client:
            async with client.post(url + "/standards", json=request) as response:
                self.assertEqual(response.status, 200, await response.text())
                old = await response.json()
            overwrite = {"action": "overwrite_standard", "field": field, "value": newer}
            async with client.post(url + "/standards", json=overwrite) as response:
                self.assertEqual(response.status, 200, await response.text())
                confirmation = (await response.json())["confirmation"]
            async with client.post(url + "/standards", json={**overwrite, "confirmation": confirmation}) as response:
                self.assertEqual(response.status, 200, await response.text())
            await self.hass.async_block_till_done()
            original = deepcopy(dict(self.entry.options))
            async with client.post(url + "/standards", json={**request, "confirmation": old["confirmation"]}) as response:
                self.assertEqual(response.status, 409, await response.text())
                current = (await response.json())["preview"]
            self.assertEqual(current["fields"][0]["target"], newer)
            self.assertNotEqual(current["confirmation"], old["confirmation"])
            self.assertEqual(dict(self.entry.options), original)
            async with client.post(url + "/appearance", json={
                "scales": {"temperature": {"minimum": active, "maximum": saved}},
            }) as response:
                self.assertEqual(response.status, 200, await response.text())
            await self.hass.async_block_till_done()
            original = deepcopy(dict(self.entry.options))
            configuration = self.entry.runtime_data.configuration
            with patch.object(self.hass.config_entries, "async_update_entry",
                              wraps=self.hass.config_entries.async_update_entry) as write:
                async with client.post(url + "/standards", json={**request, "confirmation": current["confirmation"]}) as response:
                    self.assertEqual(response.status, 409, await response.text())
                    invalid = (await response.json())["preview"]
                self.assertEqual(invalid["fields"][0]["target"], newer)
                self.assertIsNone(invalid["confirmation"])
                self.assertIn("validation_error", invalid)
                whole_request = {"action": "restore_standard", "all": True}
                async with client.post(url + "/standards", json=whole_request) as response:
                    self.assertEqual(response.status, 200, await response.text())
                    whole_preview = await response.json()
                whole_targets = {row["id"]: row["target"] for row in whole_preview["fields"]}
                self.assertEqual(whole_targets[field], newer)
                self.assertEqual(whole_targets["appearance.scales.temperature.maximum"], maximum)
                for body in (request,
                             {"action": "overwrite_standard", "field": field, "value": saved}):
                    async with client.post(url + "/standards", json=body) as response:
                        self.assertEqual(response.status, 400, await response.text())
                write.assert_not_called()
            self.assertEqual(dict(self.entry.options), original)
            self.assertIs(self.entry.runtime_data.configuration, configuration)
            # Each S scalar is individually valid. Restoring the complete pair
            # must still reject its contradictory minimum and maximum.
            maximum_field = "appearance.scales.temperature.maximum"
            overwrite_maximum = {"action": "overwrite_standard", "field": maximum_field, "value": saved}
            async with client.post(url + "/standards", json=overwrite_maximum) as response:
                self.assertEqual(response.status, 200, await response.text())
                confirmation = (await response.json())["confirmation"]
            async with client.post(url + "/standards", json={**overwrite_maximum, "confirmation": confirmation}) as response:
                self.assertEqual(response.status, 200, await response.text())
            await self.hass.async_block_till_done()
            self.assertEqual(self.entry.options["field_standards"][field], newer)
            self.assertEqual(self.entry.options["field_standards"][maximum_field], saved)
            original = deepcopy(dict(self.entry.options))
            configuration = self.entry.runtime_data.configuration
            with patch.object(self.hass.config_entries, "async_update_entry",
                              wraps=self.hass.config_entries.async_update_entry) as write:
                async with client.post(url + "/standards", json=whole_request) as response:
                    self.assertEqual(response.status, 400, await response.text())
                async with client.post(url + "/standards", json={
                    **whole_request, "confirmation": whole_preview["confirmation"],
                }) as response:
                    self.assertEqual(response.status, 409, await response.text())
                    invalid_whole = (await response.json())["preview"]
                invalid_targets = {row["id"]: row["target"] for row in invalid_whole["fields"]}
                self.assertEqual(invalid_targets[field], newer)
                self.assertEqual(invalid_targets[maximum_field], saved)
                self.assertIsNone(invalid_whole["confirmation"])
                self.assertIn("validation_error", invalid_whole)
                write.assert_not_called()
            self.assertEqual(dict(self.entry.options), original)
            self.assertIs(self.entry.runtime_data.configuration, configuration)

    async def test_field_standard_permissions_validation_and_runtime_guards_are_atomic(self):
        field = "parameters.nominal_power_kw"
        url = self.base + "/" + self.entry.entry_id + "/standards"
        definition = BY_KEY["nominal_power_kw"]
        value = definition.default + definition.number_step
        request = {"action": "overwrite_standard", "field": field, "value": value}
        original = deepcopy(dict(self.entry.options))
        runtime = self.entry.runtime_data
        configuration = runtime.configuration
        async with ClientSession(headers=self.headers) as admin:
            async with admin.post(url, json=request) as response:
                self.assertEqual(response.status, 200, await response.text())
                confirmed = {**request, "confirmation": (await response.json())["confirmation"]}
        user = await self.hass.auth.async_create_user("Standard control only", group_ids=[GROUP_ID_USER])
        token = await self.hass.auth.async_create_refresh_token(user, client_id="http://localhost/")
        headers = {"Authorization": "Bearer " + self.hass.auth.async_create_access_token(token)}
        for credentials, status in (({}, 401), (headers, 403)):
            async with ClientSession(headers=credentials) as client:
                async with client.get(url) as response:
                    self.assertEqual(response.status, status)
                for body in (request, confirmed):
                    async with client.post(url, json=body) as response:
                        self.assertEqual(response.status, status)
        invalid = [
            [], {}, {"action": None}, {"action": True}, {"action": ""},
            {**request, "extra": True}, {**request, "field": None},
            {**request, "confirmation": ""}, {**request, "value": None},
            {**request, "value": BY_KEY["nominal_power_kw"].maximum + 1},
            {"action": "missing", "field": field},
            {"action": "overwrite_standard", "field": field},
            {**request, "group": "operation"}, {**request, "all": True},
            {"action": "overwrite_standard", "group": "operation", "value": value},
            {"action": "restore_standard", "field": field, "value": value},
            {"action": "restore_factory", "group": "missing"},
            {"action": "restore_standard", "field": "bindings.heater"},
            {"action": "overwrite_standard", "field": "parameters.target_temperature_c", "value": BY_KEY["target_temperature_c"].default},
            {"action": "restore_factory", "field": "temperature_programs"},
        ]
        invalid += [{"action": "restore_standard", "all": value} for value in (False, 0, 1, None)]
        async with ClientSession(headers=self.headers) as client:
            with patch.object(self.hass.config_entries, "async_update_entry",
                              wraps=self.hass.config_entries.async_update_entry) as write:
                for body in invalid:
                    with self.subTest(body=body):
                        async with client.post(url, json=body) as response:
                            self.assertEqual(response.status, 400, await response.text())
                        self.assertEqual(dict(self.entry.options), original)
                async with client.get(self.base + "/missing/standards") as response:
                    self.assertEqual(response.status, 404)
                for attribute, status in (("reconfiguring", 409), ("closed", 503)):
                    with patch.object(runtime, attribute, True):
                        async with client.get(url) as response:
                            self.assertEqual(response.status, status)
                        for body in (request, confirmed):
                            async with client.post(url, json=body) as response:
                                self.assertEqual(response.status, status)
                write.assert_not_called()
            self.assertEqual(dict(self.entry.options), original)
            self.assertIs(runtime.configuration, configuration)
            self.assertIsNone(runtime.session)

    async def test_field_standard_and_appearance_actions_preserve_session_and_normalize_obsolete_standards(self):
        from custom_components.ha_sauna.archive import plain
        from custom_components.ha_sauna.runtime import Configuration

        field = "parameters.nominal_power_kw"
        color = "appearance.colors.event_door"
        definition = BY_KEY["nominal_power_kw"]
        active = definition.default + definition.number_step
        self.hass.config_entries.async_update_entry(self.entry, options={
            **self.entry.options,
            "parameters": {**self.entry.options["parameters"], definition.key: active},
            "appearance": {"colors": {"event_door": "#010203"}},
            "field_standards": {field: definition.default + 2 * definition.number_step, color: "#102030"},
        })
        await self.hass.async_block_till_done()
        runtime = self.entry.runtime_data
        now = runtime._clock()
        runtime._clock = lambda: now
        await runtime.set_operation(True)
        await self.hass.async_block_till_done()
        session = runtime.session
        session_plain = plain(session)
        controller, detector, device = runtime.controller, runtime.detector, runtime.device
        url = self.base + "/" + self.entry.entry_id
        request = {"action": "overwrite_standard", "field": field, "value": definition.default + 3 * definition.number_step}
        async with ClientSession(headers=self.headers) as client:
            with patch.object(device, "restore_light_ownership", wraps=device.restore_light_ownership) as light, \
                    patch.object(device, "restore_heater_ownership", wraps=device.restore_heater_ownership) as heater, \
                    patch.object(runtime.archive, "append", wraps=runtime.archive.append) as append, \
                    patch.object(runtime.archive, "save_session", wraps=runtime.archive.save_session) as save_session:
                async with client.post(url + "/standards", json=request) as response:
                    self.assertEqual(response.status, 200, await response.text())
                    confirmation = (await response.json())["confirmation"]
                async with client.post(url + "/standards", json={**request, "confirmation": confirmation}) as response:
                    self.assertEqual(response.status, 200, await response.text())
                await self.hass.async_block_till_done()
                self.hass.config_entries.async_update_entry(self.entry, options={
                    **self.entry.options,
                    "field_standards": {**self.entry.options["field_standards"], field: definition.default + 4 * definition.number_step},
                })
                await self.hass.async_block_till_done()
                light.assert_not_called()
                heater.assert_not_called()
                append.assert_not_called()
                save_session.assert_not_called()
            self.assertIs(runtime.session, session)
            self.assertEqual(plain(runtime.session), session_plain)
            self.assertEqual(runtime.configuration.parameters.values[definition.key], active)
            saved = deepcopy(self.entry.options["field_standards"])
            for body in ({"action": "restore_standard", "field": color},
                         {"action": "restore_factory", "field": color}):
                async with client.post(url + "/standards", json=body) as response:
                    self.assertEqual(response.status, 200, await response.text())
                    confirmation = (await response.json())["confirmation"]
                async with client.post(url + "/standards", json={**body, "confirmation": confirmation}) as response:
                    self.assertEqual(response.status, 200, await response.text())
                await self.hass.async_block_till_done()
                self.assertIs(runtime.session, session)
                self.assertEqual(plain(runtime.session), session_plain)
            self.assertNotIn(color, self.entry.options["field_standards"])
            self.assertEqual(self.entry.options["field_standards"][field], saved[field])
            for stopped in (False, True):
                if stopped:
                    await runtime.set_operation(False)
                before = deepcopy(dict(self.entry.options))
                current_session = runtime.session
                for action in ("restore_standard", "restore_factory"):
                    body = {"action": action, "field": field}
                    async with client.post(url + "/standards", json=body) as response:
                        self.assertEqual(response.status, 200, await response.text())
                        confirmation = (await response.json())["confirmation"]
                    async with client.post(url + "/standards", json={**body, "confirmation": confirmation}) as response:
                        self.assertEqual(response.status, 409, await response.text())
                    self.assertEqual(dict(self.entry.options), before)
                    self.assertIs(runtime.session, current_session)
            self.assertIs(runtime.controller, controller)
            self.assertIs(runtime.detector, detector)
            self.assertIs(runtime.device, device)
            self.assertIs(self.entry.runtime_data, runtime)
            current_session = runtime.session
            current_plain = plain(current_session)
            self.hass.config_entries.async_update_entry(self.entry, options={
                **self.entry.options,
                "field_standards": {
                    field: definition.maximum + 1, color: "#304050",
                    "appearance.preferences.history_live_poll_ms": section("appearance")["preferences"]["history_live_poll_ms"]["default"],
                    "unknown.field": 1,
                },
            })
            await self.hass.async_block_till_done()
            self.assertEqual(self.entry.options["field_standards"], {color: "#304050"})
            self.assertEqual(runtime.configuration.parameters.values[definition.key], active)
            self.assertIs(runtime.session, current_session)
            self.assertEqual(plain(runtime.session), current_plain)
            async with client.get(url + "/standards") as response:
                row = next(item for item in (await response.json())["fields"] if item["id"] == field)
            self.assertEqual((row["standard"], row["has_standard"], row["active"]),
                             (definition.default, False, active))
            self.assertEqual(dict(Configuration.from_options(self.entry.options).field_standards), {color: "#304050"})
            # A real external live-target update must also canonicalize S when
            # the accepted S set is otherwise identical to the runtime's set.
            target = runtime.configuration.parameters.values["target_temperature_c"] - BY_KEY["target_temperature_c"].number_step
            session_id = runtime.session.session_id
            timeline, deadlines = runtime.session.timeline, runtime.session.deadlines
            self.hass.config_entries.async_update_entry(self.entry, options={
                **self.entry.options,
                "parameters": {**self.entry.options["parameters"], "target_temperature_c": target},
                "field_standards": {color: "#304050", field: definition.maximum + 1},
            })
            await self.hass.async_block_till_done()
            self.assertIs(self.entry.runtime_data, runtime)
            self.assertEqual(runtime.session.session_id, session_id)
            self.assertEqual(runtime.session.timeline, timeline)
            self.assertEqual(runtime.session.deadlines, deadlines)
            self.assertEqual(runtime.controller.target_temperature, target)
            self.assertEqual(runtime.configuration.parameters.values[definition.key], active)
            self.assertEqual(self.entry.options["field_standards"], {color: "#304050"})
            self.assertEqual(self.entry.options["parameters"]["target_temperature_c"], target)
        runtime.controller.finish_session(runtime._clock(), light_after_run=False)
        await runtime._cycle()
        self.assertTrue(await self.hass.config_entries.async_reload(self.entry.entry_id))
        await self.hass.async_block_till_done()
        self.assertEqual(dict(self.entry.runtime_data.configuration.field_standards), {color: "#304050"})
        self.assertEqual(self.entry.runtime_data.configuration.parameters.values[definition.key], active)
        self.assertEqual(self.entry.runtime_data.configuration.parameters.values["target_temperature_c"], target)

    async def test_newer_standard_options_stop_stale_handoff_before_light_ownership_is_withdrawn(self):
        from homeassistant.setup import async_setup_component
        from test_device_path import TestLight

        await self.attach_reporting_heater()
        await async_setup_component(self.hass, "light", {})
        light = TestLight()
        await self.hass.data["light"].async_add_entities([light])
        self.hass.config_entries.async_update_entry(self.entry, options={
            **self.entry.options,
            "bindings": {**self.entry.options["bindings"],
                         "heater_feedback": "binary_sensor.actual_heating", "light": light.entity_id},
        })
        await self.hass.async_block_till_done()
        runtime = self.entry.runtime_data
        original = deepcopy(dict(self.entry.options))
        controller, detector, device = runtime.controller, runtime.detector, runtime.device
        phase = controller.phase
        completed_sessions = controller.completed_sessions
        self.assertIsNone(runtime.session)
        self.assertTrue(device._heater_owned)
        self.assertTrue(device._light_owned)
        entered, release, restored = asyncio.Event(), asyncio.Event(), asyncio.Event()
        prepare_heater = device.prepare_heater_handoff
        restore_heater = device.restore_heater_ownership

        async def paused_after_heater(**kwargs):
            finished = await prepare_heater(**kwargs)
            self.assertTrue(finished)
            entered.set()
            await release.wait()
            return finished

        def heater_restored():
            restore_heater()
            restored.set()

        definition = BY_KEY["nominal_power_kw"]
        field = "parameters." + definition.key
        standard = definition.default + 2 * definition.number_step
        newer = {**original, "field_standards": {field: standard}}
        url = self.base + "/" + self.entry.entry_id
        with (
            patch.object(device, "prepare_heater_handoff", side_effect=paused_after_heater),
            patch.object(device, "restore_heater_ownership", side_effect=heater_restored),
            patch.object(device, "prepare_light_handoff", wraps=device.prepare_light_handoff) as prepare_light,
            patch.object(device, "finish_session_light", wraps=device.finish_session_light) as finish_light,
            patch.object(self.hass.config_entries, "async_reload", wraps=self.hass.config_entries.async_reload) as reload,
        ):
            try:
                self.hass.config_entries.async_update_entry(self.entry, options={
                    **original,
                    "parameters": {**original["parameters"],
                                   definition.key: original["parameters"][definition.key] + definition.number_step},
                })
                await asyncio.wait_for(entered.wait(), async_timeout("service"))
                self.assertTrue(runtime.reconfiguring)
                self.assertFalse(device._heater_owned)
                self.assertTrue(device._light_owned)
                self.hass.config_entries.async_update_entry(self.entry, options=newer)
                await asyncio.wait_for(restored.wait(), async_timeout("service"))
                self.assertFalse(runtime.reconfiguring)
                self.assertTrue(device._heater_owned)
                self.assertTrue(device._light_owned)
                async with ClientSession(headers=self.headers) as client:
                    async with client.get(url + "/standards") as response:
                        self.assertEqual(response.status, 200, await response.text())
                        row = next(item for item in (await response.json())["fields"] if item["id"] == field)
                    self.assertEqual((row["active"], row["standard"]),
                                     (original["parameters"][definition.key], standard))
            finally:
                release.set()
                await asyncio.wait_for(self.hass.async_block_till_done(), async_timeout("reload"))
            prepare_light.assert_not_called()
            finish_light.assert_not_called()
            reload.assert_not_called()
        self.assertEqual(dict(self.entry.options), newer)
        self.assertEqual(runtime.configuration.as_options(), newer)
        self.assertIs(self.entry.runtime_data, runtime)
        self.assertFalse(runtime.closed)
        self.assertFalse(runtime.reconfiguring)
        self.assertIs(runtime.controller, controller)
        self.assertIs(runtime.detector, detector)
        self.assertIs(runtime.device, device)
        self.assertIsNone(runtime.session)
        self.assertEqual(controller.completed_sessions, completed_sessions)
        self.assertEqual(controller.phase, phase)
        self.assertTrue(device._heater_owned)
        self.assertTrue(device._light_owned)

    async def test_state_catalog_bootstrap_and_compact_poll_share_effective_preferences(self):
        url = self.base + "/" + self.entry.entry_id
        specifications = section("appearance")["preferences"]
        editable = {key for key, definition in specifications.items() if definition["configuration_surface"] == "panel"}
        preference_key = "detail_records_per_page"
        specification = specifications[preference_key]
        chosen = specification["default"] + specification["step"]
        internal = {
            "history_live_poll_ms", "chart_curve_width_px", "control_button_height_px",
            "primary_action_height_px", "control_radius_px", "surface_radius_px",
            "surface_gap_px", "surface_padding_px", "control_group_gap_px",
        }
        self.assertEqual(specifications["history_live_poll_ms"]["default"], 15000)
        self.assertTrue(all(specifications[key]["configuration_surface"] == "catalog"
                            for key in internal))
        self.hass.config_entries.async_update_entry(self.entry, options={
            **self.entry.options,
            "appearance": {"preferences": {
                key: specifications[key]["default"] + specifications[key]["step"]
                for key in internal
            }},
            "field_standards": {
                f"appearance.preferences.{key}": specifications[key]["default"]
                for key in internal
            },
        })
        await self.hass.async_block_till_done()
        async with ClientSession(headers=self.headers) as client:
            async with client.post(url + "/appearance", json={"preferences": {preference_key: chosen}}) as response:
                self.assertEqual(response.status, 200, await response.text())
            for query, includes_catalog in (("", True), ("?include_catalog=false", False)):
                async with client.get(url + "/state" + query) as response:
                    self.assertEqual(response.status, 200, await response.text())
                    state = await response.json()
                self.assertEqual("appearance_catalog" in state, includes_catalog)
                self.assertEqual(set(state["appearance"]["preferences"]), editable)
                self.assertEqual(state["appearance"]["preferences"][preference_key], chosen)
                self.assertEqual(state["configuration"]["appearance"], state["appearance"])
                self.assertEqual(set(state["frontend_defaults"]), set(section("frontend")))
                self.assertNotIn(preference_key, state["frontend_defaults"])
                for key in internal:
                    self.assertNotIn(key, state["appearance"]["preferences"])
                    if key in state["frontend_defaults"]:
                        self.assertEqual(state["frontend_defaults"][key], specifications[key]["default"])
                    if includes_catalog:
                        self.assertEqual(state["appearance_catalog"]["preferences"][key], specifications[key])
            self.assertEqual(set(self.entry.options["appearance"]["preferences"]), editable)
            self.assertEqual(
                set(self.entry.runtime_data.configuration.appearance["preferences"]),
                set(specifications),
            )
            for key in internal:
                self.assertEqual(self.entry.runtime_data.configuration.appearance["preferences"][key],
                                 specifications[key]["default"])
            self.assertEqual(
                self.entry.runtime_data.configuration.as_snapshot()["appearance"],
                self.entry.runtime_data.configuration.appearance,
            )
            async with client.get(url + "/standards") as response:
                self.assertEqual(response.status, 200, await response.text())
                standard_fields = {row["id"] for row in (await response.json())["fields"]}
            self.assertTrue({f"appearance.preferences.{key}" for key in internal}.isdisjoint(standard_fields))
            self.assertEqual(self.entry.options["field_standards"], {})
            original = deepcopy(dict(self.entry.options))
            for key in internal:
                with self.subTest(internal=key):
                    async with client.post(url + "/standards", json={
                        "action": "overwrite_standard", "field": f"appearance.preferences.{key}",
                        "value": specifications[key]["default"],
                    }) as response:
                        self.assertEqual(response.status, 400, await response.text())
            self.assertEqual(dict(self.entry.options), original)
            readonly = next((key, definition) for key, definition in specifications.items()
                            if definition["configuration_surface"] == "catalog" and definition["default"] + definition["step"] <= definition["maximum"])
            key, definition = readonly
            async with client.post(url + "/appearance", json={"preferences": {key: definition["default"] + definition["step"]}}) as response:
                self.assertEqual(response.status, 400, await response.text())

    async def test_appearance_updates_live_without_reloading_or_changing_control(self):
        from custom_components.ha_sauna.runtime import Configuration

        runtime = self.entry.runtime_data
        url = self.base + "/" + self.entry.entry_id
        await runtime.set_operation(True)
        session = runtime.session
        controller = runtime.controller
        detector = runtime.detector
        device = runtime.device
        deadlines = session.deadlines
        appearance = {
            "colors": {"phase_warmup": "#123ABC", "card_background": "#102030"},
            "instruments": {
                "default": "linear", "temperature": "round",
                "humidity": "inherit", "light": "round",
            },
            "scales": {
                "temperature": {"minimum": 35, "maximum": 120},
                "humidity": {"minimum": 5, "maximum": 85},
            },
        }
        appearance = appearance_options(appearance)
        async with ClientSession(headers=self.headers) as client:
            async with client.get(url + "/state") as response:
                state = await response.json()
                self.assertEqual(state["appearance"]["scales"]["temperature"],
                                 section("appearance")["scales"]["temperature"]["default"])
                self.assertIn("phase_warmup", {item["id"] for item in
                                               state["appearance_catalog"]["colors"]})
            async with client.post(url + "/appearance", json=appearance) as response:
                self.assertEqual(response.status, 200, await response.text())
                self.assertEqual(
                    (await response.json())["appearance"], validate_appearance(appearance),
                )
            await self.hass.async_block_till_done()
            self.assertIs(self.entry.runtime_data, runtime)
            self.assertIs(runtime.session, session)
            self.assertIs(runtime.controller, controller)
            self.assertIs(runtime.detector, detector)
            self.assertIs(runtime.device, device)
            self.assertEqual(session.deadlines, deadlines)
            self.assertEqual(runtime.configuration.appearance, validate_appearance(appearance))
            self.assertEqual(self.entry.options["appearance"], appearance)
            self.assertEqual(Configuration.from_options(self.entry.options).appearance,
                             validate_appearance(appearance))
            async with client.get(url + "/state") as response:
                state = await response.json()
                self.assertEqual(state["appearance"], appearance)
                self.assertEqual(state["configuration"]["appearance"], appearance)

            # Independent live temperature and external options writes preserve
            # the display setting and active session.
            async with client.post(url + "/temperature",
                                   json={"target_temperature_c": 81}) as response:
                self.assertEqual(response.status, 200, await response.text())
            await self.hass.async_block_till_done()
            self.assertEqual(runtime.configuration.appearance, validate_appearance(appearance))
            self.assertEqual(runtime.session.session_id, session.session_id)
            session = runtime.session
            deadlines = session.deadlines
            external = {"colors": {"status_warning": "#FEDCBA"}, "scales":
                        appearance["scales"], "instruments": appearance["instruments"]}
            external = appearance_options(external)
            self.hass.config_entries.async_update_entry(
                self.entry, options={**self.entry.options, "appearance": external})
            await self.hass.async_block_till_done()
            self.assertIs(self.entry.runtime_data, runtime)
            self.assertIs(runtime.session, session)
            self.assertEqual(runtime.configuration.appearance, validate_appearance(external))
            self.assertEqual(session.deadlines, deadlines)

    async def test_appearance_validation_permissions_and_full_replacement(self):
        from custom_components.ha_sauna.runtime import Configuration

        legacy = dict(self.entry.options)
        legacy.pop("appearance")
        self.assertEqual(Configuration.from_options(legacy).appearance["colors"], {})
        legacy["appearance"] = {"scales": {"humidity": {"maximum": 90}}}
        restored = Configuration.from_options(legacy).appearance
        instrument_defaults = {
            key: specification["default"]
            for key, specification in section("appearance")["instruments"].items()
        }
        self.assertEqual(restored["instruments"], instrument_defaults)
        self.assertEqual(restored["scales"]["temperature"],
                         section("appearance")["scales"]["temperature"]["default"])
        self.assertEqual(restored["scales"]["humidity"],
                         {**section("appearance")["scales"]["humidity"]["default"], "maximum": 90})
        url = self.base + "/" + self.entry.entry_id + "/appearance"
        async with ClientSession() as client:
            async with client.post(url, json={}) as response:
                self.assertEqual(response.status, 401)
        user = await self.hass.auth.async_create_user("Appearance nonadmin", group_ids=[GROUP_ID_USER])
        token = await self.hass.auth.async_create_refresh_token(user, client_id="http://localhost/")
        headers = {"Authorization": "Bearer " + self.hass.auth.async_create_access_token(token)}
        async with ClientSession(headers=headers) as client:
            async with client.post(url, json={}) as response:
                self.assertEqual(response.status, 403)

        valid = {"colors": {"phase_ready": "#ABC123"},
                 "scales": {"temperature": {"minimum": 25, "maximum": 125},
                            "humidity": {"minimum": 0, "maximum": 60}}}
        async with ClientSession(headers=self.headers) as client:
            async with client.post(url, json=valid) as response:
                self.assertEqual(response.status, 200, await response.text())
            options_before = dict(self.entry.options)
            invalid = (
                {"colors": {"unknown": "#112233"}},
                {"colors": {"phase_ready": "red"}},
                {"colors": {"phase_ready": None}},
                {"scales": {"humidity": {"minimum": -1}}},
                {"scales": {"humidity": {"maximum": 101}}},
                {"scales": {"temperature": {"minimum": 80, "maximum": 80}}},
                {"scales": {"temperature": {"minimum": True}}},
                {"scales": {"temperature": {"minimum": 10 ** 400}}},
                {"scales": {"temperature": {"minimum": -1e308, "maximum": 1e308}}},
                {"scales": {"temperature": {"minimum": -(10 ** 308), "maximum": 10 ** 308}}},
                {"scales": {"extra": {}}},
                {"instruments": {"default": "inherit"}},
                {"instruments": {"light": "invalid"}},
                {"instruments": {"extra": "round"}},
                {"instruments": {"temperature": None}},
                {"instruments": []},
                {"extra": True},
            )
            for value in invalid:
                with self.subTest(value=value):
                    async with client.post(url, json=value) as response:
                        self.assertEqual(response.status, 400, await response.text())
                    self.assertEqual(dict(self.entry.options), options_before)
            async with client.post(url, json={"colors": {"phase_warmup": "#010203"}}) as response:
                self.assertEqual(response.status, 200, await response.text())
                replaced = (await response.json())["appearance"]
            self.assertEqual(replaced["colors"], {"phase_warmup": "#010203"})
            self.assertEqual(replaced["instruments"], instrument_defaults)
            self.assertEqual(replaced["scales"], {
                key: specification["default"]
                for key, specification in section("appearance")["scales"].items()
            })
            self.assertEqual(self.entry.runtime_data.configuration.appearance, validate_appearance(replaced))

    async def test_appearance_survives_parameter_reset_and_other_settings(self):
        entity_id = "binary_sensor.reset_presence"
        self.hass.states.async_set(entity_id, "off", {"device_class": "occupancy"})
        self.hass.config_entries.async_update_entry(self.entry, options={
            **self.entry.options,
            "presence_source": "ha_presence",
            "bindings": {**self.entry.options["bindings"], "presence": entity_id},
            "field_standards": {"appearance.colors.event_door": "#AABBCC",
                                "parameters.sensor_timeout_seconds": BY_KEY["sensor_timeout_seconds"].default + 2 * BY_KEY["sensor_timeout_seconds"].number_step},
        })
        await self.hass.async_block_till_done()
        url = self.base + "/" + self.entry.entry_id
        appearance = {"colors": {"event_door": "#010203"},
                      "scales": {"temperature": {"minimum": 30, "maximum": 130}}}
        async with ClientSession(headers=self.headers) as client:
            async with client.post(url + "/appearance", json=appearance) as response:
                self.assertEqual(response.status, 200, await response.text())
                stored = (await response.json())["appearance"]
                self.assertEqual(stored, validate_appearance(appearance))
                saved = appearance_options(stored)
            async with client.post(url + "/logging", json={"level": "DEBUG"}) as response:
                self.assertEqual(response.status, 200, await response.text())
            async with client.post(url + "/program", json={"profile": "genusszeit"}) as response:
                self.assertEqual(response.status, 200, await response.text())
            definition = BY_KEY["sensor_timeout_seconds"]
            changed_timeout = definition.default + definition.number_step
            if changed_timeout > definition.maximum:
                changed_timeout = definition.default - definition.number_step
            async with client.post(url + "/parameters", json={
                **self.entry.options["parameters"],
                "sensor_timeout_seconds": changed_timeout,
            }) as response:
                self.assertEqual(response.status, 200, await response.text())
            await self.hass.async_block_till_done()
            runtime_before_reset = self.entry.runtime_data
            self.assertEqual(
                runtime_before_reset.configuration.parameters.values["sensor_timeout_seconds"],
                changed_timeout,
            )
            async with client.post(url + "/parameters/reset", json={}) as response:
                self.assertEqual(response.status, 200, await response.text())
            await self.hass.async_block_till_done()
            self.assertIsNot(self.entry.runtime_data, runtime_before_reset)
            self.assertEqual(
                self.entry.runtime_data.configuration.parameters.values["sensor_timeout_seconds"],
                definition.default,
            )
            self.assertEqual(self.entry.options["appearance"], saved)
            self.assertEqual(self.entry.runtime_data.configuration.appearance, stored)
            self.assertEqual(self.entry.options["field_standards"], {
                "appearance.colors.event_door": "#AABBCC",
                "parameters.sensor_timeout_seconds": BY_KEY["sensor_timeout_seconds"].default + 2 * BY_KEY["sensor_timeout_seconds"].number_step,
            })
            self.assertEqual(self.entry.options["presence_source"], "ha_presence")
            self.assertEqual(self.entry.options["bindings"]["presence"], entity_id)
            self.assertEqual(
                self.entry.runtime_data.configuration.presence_source, "ha_presence"
            )
            async with client.get(url + "/state") as response:
                self.assertEqual((await response.json())["appearance"], saved)

    async def test_appearance_listener_uses_latest_options_after_lock_wait(self):
        import asyncio

        runtime = self.entry.runtime_data
        await runtime.set_operation(True)
        session = runtime.session
        first = {"colors": {"phase_ready": "#111111"}}
        latest = {"colors": {"phase_ready": "#222222"}}
        standard_field = "appearance.colors.phase_ready"
        latest_standards = {standard_field: "#304050"}
        async with runtime._lock:
            self.hass.config_entries.async_update_entry(
                self.entry, options={**self.entry.options, "appearance": first,
                                     "field_standards": {standard_field: "obsolete color"}}
            )
            await asyncio.sleep(0)
            self.hass.config_entries.async_update_entry(
                self.entry, options={**self.entry.options, "appearance": latest,
                                     "field_standards": latest_standards}
            )
            await asyncio.sleep(0)
        await self.hass.async_block_till_done()
        self.assertIs(self.entry.runtime_data, runtime)
        self.assertIs(runtime.session, session)
        self.assertEqual(runtime.configuration.appearance["colors"], latest["colors"])
        self.assertEqual(self.entry.options["appearance"]["colors"], latest["colors"])
        self.assertEqual(self.entry.options["field_standards"], latest_standards)
        self.assertEqual(dict(runtime.configuration.field_standards), latest_standards)

    async def test_appearance_is_independent_for_each_entry(self):
        from harness import create_sauna

        other = await create_sauna(
            self.hass, binding_overrides={"heater": "switch.other_heater"}
        )
        url = self.base + "/" + self.entry.entry_id
        other_url = self.base + "/" + other.entry_id
        async with ClientSession(headers=self.headers) as client:
            async with client.post(
                url + "/appearance", json={"colors": {"phase_ready": "#123456"}}
            ) as response:
                self.assertEqual(response.status, 200, await response.text())
            async with client.get(other_url + "/state") as response:
                self.assertEqual(response.status, 200, await response.text())
                self.assertEqual((await response.json())["appearance"]["colors"], {})
        self.assertEqual(other.runtime_data.configuration.appearance["colors"], {})
