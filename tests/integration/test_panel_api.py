"""Real authenticated panel endpoints, session guard and HA options reload."""
import asyncio
import unittest
from unittest.mock import patch
from aiohttp import ClientSession
from homeassistant.auth.const import GROUP_ID_ADMIN, GROUP_ID_USER
from harness import create_sauna, start_hass
from custom_components.ha_sauna.core.defaults import section
from custom_components.ha_sauna.core.parameters import Parameters


class PanelAPITests(unittest.IsolatedAsyncioTestCase):
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
                token = next(d.token for d in runtime.session.deadlines if d.purpose == "session_gap")
                await runtime.finish_session_gap(token)
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
        self.entry = await create_sauna(self.hass, parameter_overrides={"sensor_timeout_seconds": 60})
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
                self.assertEqual(state["mechanical_timer"]["state"], "paused")
                self.assertIsNone(state["mechanical_timer_ends_at"])
            async with client.post(url + "/parameters", json={**values,"after_run_minutes":20,"oven_cooling_max_minutes":20}) as response:
                self.assertEqual(response.status, 409)

    async def test_override_parameter_api_enforces_the_catalog_limit_atomically(self):
        url = self.base + "/" + self.entry.entry_id
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
                    (10, 10, False),
                )
            for value in (10.000000000000002, 20):
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
            for value in (10, 0.5):
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
        async with ClientSession(headers=self.headers) as client:
            for stored, mode, seconds, effective in (
                (20, "automatic", 600, 10),
                (0.5, "automatic", 30, 0.5),
                (20, "manual", 601, 10),
            ):
                with self.subTest(stored=stored, mode=mode):
                    for role in ("upper_temperature", "lower_temperature"):
                        entity = baseline["bindings"][role]
                        self.hass.states.async_set(
                            entity, "25", self.hass.states.get(entity).attributes
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
                                "minimum_heating_minutes": 0,
                                "thermostat_cooldown_minutes": 0,
                                "sensor_timeout_seconds": 1800,
                                "feedback_timeout_seconds": 60,
                                "light_transition_seconds": 0,
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
                        255,
                    )
                    async with client.get(url + "/state") as response:
                        state = await response.json()
                        self.assertEqual(
                            state["configuration"]["parameters"][
                                "manual_override_minutes"
                            ],
                            effective,
                        )
                    await runtime.set_operation(True)
                    await runtime.set_heater_override(mode == "manual")
                    await runtime.set_light_override(80)
                    await self.hass.async_block_till_done()
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
                    for value in (10.000000000000002, 20):
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
                    clock[0] = start + timedelta(seconds=seconds - 1)
                    await runtime.tick()
                    await self.hass.async_block_till_done()
                    self.assertEqual(
                        runtime.controller.heater_override, mode == "manual"
                    )
                    self.assertEqual(runtime.device.light_output.manual_brightness, 80)
                    self.assertEqual(heater.is_on, mode == "manual")
                    self.assertEqual(light.brightness, 204)
                    clock[0] = start + timedelta(seconds=seconds)
                    await runtime.tick()
                    await self.hass.async_block_till_done()
                    if mode == "automatic":
                        self.assertIsNone(runtime.controller.heater_override)
                        self.assertIsNone(runtime.device.light_output.manual_brightness)
                        self.assertIsNone(runtime.controller.heater_override_ends_at)
                        self.assertIsNone(runtime.device.light_output.manual_ends_at)
                        self.assertEqual(runtime.controller.phase, "aufheizen")
                        self.assertTrue(heater.is_on)
                        self.assertTrue(heater.calls[-1])
                        self.assertEqual(
                            runtime.device.light_output.last_automatic_brightness,
                            runtime.configuration.parameters.values["cooling_brightness_percent"]
                        )
                        cold_brightness = round(255 * runtime.configuration.parameters.values[
                            "cooling_brightness_percent"
                        ] / 100)
                        self.assertEqual(light.brightness, cold_brightness)
                        self.assertEqual(light.calls[-1][0], "on")
                        self.assertEqual(light.calls[-1][1]["brightness"], cold_brightness)
                    else:
                        self.assertTrue(runtime.controller.heater_override)
                        self.assertTrue(heater.is_on)
                        self.assertEqual(
                            runtime.device.light_output.manual_brightness, 80
                        )
                        self.assertEqual(light.brightness, 204)
                        self.assertEqual(runtime.configuration.control_mode, "manual")
                    if stored == 20 and mode == "automatic":
                        await runtime.set_heater_override(False)
                        await runtime.set_light_override(80)
                        for role in ("upper_temperature", "lower_temperature"):
                            entity = baseline["bindings"][role]
                            self.hass.states.async_set(
                                entity, "85", self.hass.states.get(entity).attributes
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
                        token = next(
                            deadline.token
                            for deadline in runtime.session.deadlines
                            if deadline.purpose == "session_gap"
                        )
                        await runtime.finish_session_gap(token)
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
                self.assertIn("target_temperature_c", state["configuration"]["parameters"])
                self.assertNotIn("bindings", state["configuration"])
                self.assertNotIn("sensor_timeout_seconds", state["configuration"]["parameters"])
                self.assertNotIn("detector_trace", state)
                self.assertIn("target_temperature_c", {item["key"] for item in state["parameters"]})
                self.assertNotIn("sensor_timeout_seconds", {item["key"] for item in state["parameters"]})
                self.assertTrue(all("source" not in item for item in state["measurements"]))
                self.assertEqual(state["measurement_ttl_seconds"], 60)
            async with client.get(url + "/archive", params={"session_id": session_id}) as response:
                self.assertEqual(response.status, 200, await response.text())
                history = await response.json()
                self.assertNotIn("configuration", history["session"])
                self.assertEqual(history["session"]["measurement_ttl_seconds"], 60)
                self.assertTrue(all(record["kind"] in {"measurement", "source_snapshot", "phase"}
                                    for record in history["records"]))
                self.assertTrue(all("source" not in record["payload"] for record in history["records"]))
            # Pagination belongs to the raw archive, not to the permitted
            # subset. A full hidden page must still expose its continuation.
            original = runtime.archive.read(session_id)
            after = max(record["id"] for record in original["records"])
            for _ in range(1000):
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
                self.assertEqual(projected["records"], resumed["records"])
                self.assertNotIn("configuration", projected["session"])
                self.assertIsNone(projected["next_after"])

        async with ClientSession(headers=self.headers) as client:
            async with client.get(url + "/archive", params={"session_id": session_id, "projection": "history"}) as response:
                self.assertEqual(response.status, 200)
                projected = await response.json()
                kinds = {record["kind"] for record in projected["records"]}
                self.assertIn("measurement", kinds)
                self.assertIn("detector_trace", kinds)
                self.assertNotIn("session", kinds)
                self.assertNotIn("source_state", kinds)
                self.assertIn("configuration", projected["session"])
            async with client.get(url + "/archive", params={"session_id": session_id, "projection": "invalid"}) as response:
                self.assertEqual(response.status, 400)

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
        token = next(d.token for d in runtime.session.deadlines if d.purpose == "session_gap")
        await runtime.finish_session_gap(token)
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
        user = await self.hass.auth.async_create_user("Public presence", group_ids=[GROUP_ID_USER])
        token = await self.hass.auth.async_create_refresh_token(user, client_id="http://localhost/")
        headers = {"Authorization": "Bearer " + self.hass.auth.async_create_access_token(token)}
        url = self.base + "/" + self.entry.entry_id
        async with ClientSession(headers=headers) as public, ClientSession(headers=self.headers) as admin:
            async with public.get(url + "/state") as response:
                self.assertEqual(response.status, 200)
                state = await response.json()
            self.assertNotIn(entity_id, json.dumps(state))
            active = state["session"]["timeline"]["active"]
            recognition = next(item for item in state["session"]["timeline"]["processed"]
                               if item["kind"] == "presence_confirmed")
            self.assertEqual(active["recognition_event_id"], recognition["event_id"])
            self.assertEqual(active["session_id"], session_id)
            async with admin.get(url + "/state") as response:
                administrative = await response.json()
            raw_id = runtime.session.timeline.active.gang_id
            self.assertIn(entity_id, raw_id)
            self.assertEqual(administrative["session"]["timeline"]["active"]["gang_id"], raw_id)

            await door("exit-open", Kind.DOOR_OPEN, 40)
            await presence("off", 50)
            await door("exit-close", Kind.DOOR_CLOSE, 60)
            clock[0] = start + timedelta(milliseconds=70)
            runtime.persist()
            await runtime.archive.flush()
            for query in ({"session_id": session_id},
                          {"session_id": session_id, "projection": "history"}):
                async with public.get(url + "/archive", params=query) as response:
                    self.assertEqual(response.status, 200)
                    archived = await response.json()
                self.assertNotIn(entity_id, json.dumps(archived))
                completed = archived["session"]["timeline"]["completed"][0]
                self.assertEqual(completed["gang_id"], active["gang_id"])
                self.assertEqual(completed["recognition_event_id"], recognition["event_id"])
                self.assertEqual({item["source_id"] for item in archived["phase_projection"]["intervals"]
                                  if item["source_id"]}, {active["gang_id"]})
            async with admin.get(url + "/archive", params={"session_id": session_id}) as response:
                administrative = await response.json()
            self.assertEqual(administrative["session"]["timeline"]["completed"][0]["gang_id"], raw_id)
            self.assertEqual(runtime.archive.read(session_id)["session"]["timeline"]["completed"][0]["gang_id"], raw_id)

            async with public.get(url + "/state") as response:
                self.assertEqual(response.status, 200)
                state = await response.json()
            self.assertNotIn(entity_id, json.dumps(state))
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

    async def test_malformed_json_is_a_client_error_across_write_endpoints(self):
        url = self.base + "/" + self.entry.entry_id
        original_options = dict(self.entry.options)
        async with ClientSession(headers=self.headers) as client:
            for suffix in (
                "/control", "/finish_phase", "/finish-session", "/parameters",
                "/temperature", "/appearance", "/program", "/programs",
                "/button-program", "/control-mode", "/light", "/heater", "/logging",
            ):
                with self.subTest(suffix=suffix):
                    async with client.post(url + suffix, data="{", headers={"Content-Type": "application/json"}) as response:
                        self.assertEqual(response.status, 400, await response.text())
                        self.assertIn("error", await response.json())
        self.assertEqual(dict(self.entry.options), original_options)
        self.assertIsNone(self.entry.runtime_data.session)

    async def test_full_settings_preserve_free_program_but_direct_target_replaces_it(self):
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
            for index, kind in enumerate((Kind.INFUSION, Kind.DOOR_OPEN, Kind.VENTILATION), 2):
                at = start + timedelta(seconds=index)
                current[0] = at
                runtime.controller.process(Event(str(index), runtime.session.session_id, kind, at, at))
            self.assertEqual(runtime.session.timeline.gang_count, 1)
            self.assertEqual(runtime.controller.target_temperature, 86)
            async with client.post(url + "/temperature", json={"target_temperature_c": 80}) as response:
                self.assertEqual(response.status, 200, await response.text())
            self.assertEqual(runtime.configuration.program_mode, "constant")
            self.assertIsNone(runtime.configuration.temperature_steps)

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

    async def test_finish_session_requires_the_current_gap_token_and_control_permission(self):
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
            current_gap_token = next(deadline.token for deadline in runtime.session.deadlines if deadline.purpose == "session_gap")
            async with client.post(url, json={"token": current_gap_token}) as response:
                self.assertEqual(response.status, 200, await response.text())
            async with client.get(self.base + "/" + self.entry.entry_id + "/state") as response:
                state = await response.json()
                self.assertIsNone(state["session"])
                self.assertFalse(state["configuration_locked"])

    async def test_write_acknowledges_replacement_runtime_after_waiting_for_body(self):
        from custom_components.ha_sauna import api

        url = self.base + "/" + self.entry.entry_id
        cases = (
            ("/control-mode", {"mode": "manual"}, "control_mode", "manual"),
            (
                "/button-program",
                {"profile": "constant", "temperature_c": 74},
                "button_temperature_c",
                74,
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
                            await asyncio.wait_for(entered.wait(), 3)
                            reloaded = await asyncio.wait_for(
                                self.hass.config_entries.async_reload(self.entry.entry_id),
                                10,
                            )
                            self.assertTrue(reloaded)
                            self.assertTrue(previous.closed)
                            self.assertIsNot(self.entry.runtime_data, previous)
                        except BaseException as error:
                            release.set()
                            try:
                                await asyncio.wait_for(pending, 10)
                            except BaseException as cleanup_error:
                                error.add_note(f"HTTP-Abschluss fehlgeschlagen: {cleanup_error!r}")
                            raise
                        else:
                            release.set()
                            status, result = await asyncio.wait_for(pending, 10)
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
                self.assertEqual(response.status, 200, await response.text())
            for suffix, body in (("/control-mode", {"mode": "automatic"}),
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

    async def test_standard_user_controls_only_the_permitted_panel_routes(self):
        """The normal HA user inherits control of the integration switch."""
        from datetime import UTC, datetime, timedelta

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
                json={"profile": "constant", "temperature_c": 81},
            ) as response:
                self.assertEqual(response.status, 200, await response.text())
                self.assertEqual((await response.json())["button_temperature_c"], 81)
            async with client.get(url + "/state") as response:
                self.assertEqual(
                    (await response.json())["configuration"]["button_temperature_c"],
                    81,
                )
            for body in (
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
                self.assertEqual(response.status, 200, await response.text())
            self.assertIsNotNone(runtime.session)
            identity = runtime.session.session_id
            async with client.post(url + "/control-mode", json={"mode": "automatic"}) as response:
                self.assertEqual(response.status, 409, await response.text())
            async with client.post(url + "/programs", json={"programs": catalog}) as response:
                self.assertEqual(response.status, 409, await response.text())
            async with client.post(url + "/button-program", json={"profile": "constant"}) as response:
                self.assertEqual(response.status, 409, await response.text())
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

            await forbidden(client.post(url + "/light", json={"value": 42}))
            await forbidden(client.post(url + "/parameters", json=dict(self.entry.options["parameters"])))
            await forbidden(client.post(url + "/logging", json={"level": "INFO"}))
            await forbidden(client.get(url + "/export"))

            now += timedelta(seconds=150)
            await runtime.tick()
            self.assertIsNone(runtime.session)
            async with client.post(url + "/control-mode", json={"mode": "automatic"}) as response:
                self.assertEqual(response.status, 200, await response.text())
            async with client.get(url + "/state") as response:
                permissions = (await response.json())["permissions"]
                self.assertTrue(permissions["control"])
                self.assertFalse(permissions["heater"])
            async with client.post(url + "/heater", json={"value": False}) as response:
                self.assertEqual(response.status, 403, await response.text())

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
        for second, kind in ((2,Kind.INFUSION),(3,Kind.DOOR_OPEN),(4,Kind.VENTILATION)):
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
        for second, kind in ((2, Kind.INFUSION),
                             (3, Kind.DOOR_OPEN), (4, Kind.VENTILATION)):
            now = base + timedelta(seconds=second)
            await runtime.receive(Event(f"api-paused-phase:{second}", identity, kind, now, now))
        token = runtime.session.after_run.phase_id
        now = base + timedelta(seconds=10)
        url = self.base + "/" + self.entry.entry_id
        async with ClientSession(headers=self.headers) as client:
            async with client.post(url + "/heater", json={"value": True}) as response:
                self.assertEqual(response.status, 200, await response.text())
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
        self.assertEqual(self.entry.options["parameters"]["sensor_timeout_seconds"],
                         Parameters({}).values["sensor_timeout_seconds"])
        self.assertEqual(self.entry.options["parameters"]["feedback_timeout_seconds"],10)
        self.assertEqual(self.entry.options["parameters"]["fault_confirmation_seconds"],60)
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
        now=datetime.now(UTC)+timedelta(seconds=61)
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
        await runtime.set_operation(True)
        session_id=runtime.session.session_id
        await runtime.tick()  # Erste Messauswertung setzt den belegten Türkontext.
        now=runtime._clock()
        await runtime.receive(Event("infusion",session_id,Kind.INFUSION,now,now))
        gang=runtime.session.timeline.active
        detector=runtime.detector
        timer=runtime.controller.mechanical_timer
        url=self.base+"/"+self.entry.entry_id
        async with ClientSession(headers=self.headers) as client:
            for values in ({"target_temperature_c":81,"final_temperature_c":95}, {"final_temperature_c":None}):
                async with client.post(url+"/temperature",json=values) as response:
                    self.assertEqual(response.status,200,await response.text())
                await self.hass.async_block_till_done()
                self.assertIs(self.entry.runtime_data,runtime)
                self.assertIs(runtime.detector,detector)
                self.assertEqual(runtime.session.timeline.active,gang)
                self.assertEqual(runtime.controller.mechanical_timer,timer)
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
            "scales": {
                "temperature": {"minimum": 35, "maximum": 120},
                "humidity": {"minimum": 5, "maximum": 85},
            },
        }
        async with ClientSession(headers=self.headers) as client:
            async with client.get(url + "/state") as response:
                state = await response.json()
                self.assertEqual(state["appearance"]["scales"]["temperature"],
                                 section("appearance")["scales"]["temperature"]["default"])
                self.assertIn("phase_warmup", {item["id"] for item in
                                               state["appearance_catalog"]["colors"]})
            async with client.post(url + "/appearance", json=appearance) as response:
                self.assertEqual(response.status, 200, await response.text())
                self.assertEqual((await response.json())["appearance"], appearance)
            await self.hass.async_block_till_done()
            self.assertIs(self.entry.runtime_data, runtime)
            self.assertIs(runtime.session, session)
            self.assertIs(runtime.controller, controller)
            self.assertIs(runtime.detector, detector)
            self.assertIs(runtime.device, device)
            self.assertEqual(session.deadlines, deadlines)
            self.assertEqual(runtime.configuration.appearance, appearance)
            self.assertEqual(self.entry.options["appearance"], appearance)
            self.assertEqual(Configuration.from_options(self.entry.options).appearance,
                             appearance)
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
            self.assertEqual(runtime.configuration.appearance, appearance)
            self.assertEqual(runtime.session.session_id, session.session_id)
            session = runtime.session
            deadlines = session.deadlines
            external = {"colors": {"status_warning": "#FEDCBA"}, "scales":
                        appearance["scales"]}
            self.hass.config_entries.async_update_entry(
                self.entry, options={**self.entry.options, "appearance": external})
            await self.hass.async_block_till_done()
            self.assertIs(self.entry.runtime_data, runtime)
            self.assertIs(runtime.session, session)
            self.assertEqual(runtime.configuration.appearance, external)
            self.assertEqual(session.deadlines, deadlines)

    async def test_appearance_validation_permissions_and_full_replacement(self):
        from custom_components.ha_sauna.runtime import Configuration

        legacy = dict(self.entry.options)
        legacy.pop("appearance")
        self.assertEqual(Configuration.from_options(legacy).appearance["colors"], {})
        legacy["appearance"] = {"scales": {"humidity": {"maximum": 90}}}
        restored = Configuration.from_options(legacy).appearance
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
            self.assertEqual(replaced["scales"], {
                key: specification["default"]
                for key, specification in section("appearance")["scales"].items()
            })
            self.assertEqual(self.entry.runtime_data.configuration.appearance, replaced)

    async def test_appearance_survives_parameter_reset_and_other_settings(self):
        url = self.base + "/" + self.entry.entry_id
        appearance = {"colors": {"event_door": "#010203"},
                      "scales": {"temperature": {"minimum": 30, "maximum": 130}}}
        async with ClientSession(headers=self.headers) as client:
            async with client.post(url + "/appearance", json=appearance) as response:
                self.assertEqual(response.status, 200, await response.text())
                stored = (await response.json())["appearance"]
            runtime_before_reset = self.entry.runtime_data
            async with client.post(url + "/logging", json={"level": "DEBUG"}) as response:
                self.assertEqual(response.status, 200, await response.text())
            async with client.post(url + "/program", json={"profile": "genusszeit"}) as response:
                self.assertEqual(response.status, 200, await response.text())
            async with client.post(url + "/parameters/reset", json={}) as response:
                self.assertEqual(response.status, 200, await response.text())
            await self.hass.async_block_till_done()
            self.assertIsNot(self.entry.runtime_data, runtime_before_reset)
            self.assertEqual(self.entry.options["appearance"], stored)
            async with client.get(url + "/state") as response:
                self.assertEqual((await response.json())["appearance"], stored)

    async def test_appearance_listener_uses_latest_options_after_lock_wait(self):
        import asyncio

        runtime = self.entry.runtime_data
        await runtime.set_operation(True)
        session = runtime.session
        first = {"colors": {"phase_ready": "#111111"}}
        latest = {"colors": {"phase_ready": "#222222"}}
        async with runtime._lock:
            self.hass.config_entries.async_update_entry(
                self.entry, options={**self.entry.options, "appearance": first}
            )
            await asyncio.sleep(0)
            self.hass.config_entries.async_update_entry(
                self.entry, options={**self.entry.options, "appearance": latest}
            )
            await asyncio.sleep(0)
        await self.hass.async_block_till_done()
        self.assertIs(self.entry.runtime_data, runtime)
        self.assertIs(runtime.session, session)
        self.assertEqual(runtime.configuration.appearance["colors"], latest["colors"])
        self.assertEqual(self.entry.options["appearance"]["colors"], latest["colors"])

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
