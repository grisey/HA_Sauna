"""Real authenticated panel endpoints, session guard and HA options reload."""
import unittest
from aiohttp import ClientSession
from homeassistant.auth.const import GROUP_ID_ADMIN, GROUP_ID_USER
from harness import create_sauna, start_hass


class PanelAPITests(unittest.IsolatedAsyncioTestCase):
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
            async with client.post(url + "/parameters", json={**values,"heating_minutes":20}) as response:
                self.assertEqual(response.status, 409)
            async with client.post(url + "/control", json={"enabled": False}) as response:
                self.assertEqual(response.status, 200)
            async with client.get(url + "/state") as response:
                state = await response.json()
                self.assertEqual(state["mechanical_timer"]["state"], "paused")
                self.assertIsNone(state["mechanical_timer_ends_at"])
            async with client.post(url + "/parameters", json={**values,"heating_minutes":20}) as response:
                self.assertEqual(response.status, 409)

    async def test_unauthenticated_and_non_admin_writes_are_rejected(self):
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

    async def test_standard_user_controls_only_the_permitted_panel_routes(self):
        """The normal HA user inherits control of the integration switch."""
        user = await self.hass.auth.async_create_user("Standard user", group_ids=[GROUP_ID_USER])
        token = await self.hass.auth.async_create_refresh_token(user, client_id="http://localhost/")
        headers = {"Authorization": "Bearer " + self.hass.auth.async_create_access_token(token)}
        runtime = self.entry.runtime_data
        url = self.base + "/" + self.entry.entry_id

        async with ClientSession(headers=headers) as client:
            async with client.post(url + "/control", json={"enabled": True}) as response:
                self.assertEqual(response.status, 200, await response.text())
            self.assertIsNotNone(runtime.session)
            identity = runtime.session.session_id
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
                self.assertTrue((await response.json())["permissions"]["control"])
            async with client.get(url + "/archive") as response:
                self.assertEqual(response.status, 200, await response.text())

            session = runtime.session

            async def forbidden(request):
                async with request as response:
                    self.assertEqual(response.status, 403, await response.text())
                self.assertIs(runtime.session, session)
                self.assertEqual(runtime.session.session_id, identity)
                self.assertFalse(runtime.session.operation_enabled)

            await forbidden(client.post(url + "/light", json={"value": 42}))
            await forbidden(client.post(url + "/heater", json={"value": False}))
            await forbidden(client.post(url + "/finish_phase", json={"purpose": "after_run", "token": "valid-token"}))
            await forbidden(client.post(url + "/parameters", json=dict(self.entry.options["parameters"])))
            await forbidden(client.post(url + "/logging", json={"level": "INFO"}))
            await forbidden(client.get(url + "/export"))

    async def test_manual_phase_end_checks_payload_identity_and_uses_real_runtime(self):
        from datetime import datetime, UTC, timedelta
        from custom_components.ha_sauna.core.timeline import Event, Kind
        runtime = self.entry.runtime_data
        base = now = datetime.now(UTC)
        runtime._clock = lambda: now
        await runtime.set_operation(True)
        identity = runtime.session.session_id
        for second, kind in ((1,Kind.DOOR_CLOSE),(2,Kind.INFUSION),(3,Kind.DOOR_OPEN),(4,Kind.VENTILATION)):
            now = base + timedelta(seconds=second)
            await runtime.receive(Event(f"api-phase:{second}",identity,kind,now,now))
        token = runtime.session.after_run.phase_id
        url = self.base + "/" + self.entry.entry_id + "/finish_phase"
        async with ClientSession(headers=self.headers) as client:
            for body in ([], {}, {"purpose":[],"token":token}, {"purpose":"session_gap","token":token},
                         {"purpose":"after_run","token":[]}, {"purpose":"after_run","token":token,"extra":1}):
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
            async with client.post(url, json={"purpose":"after_run","token":token}) as response:
                self.assertEqual(response.status,409)

    async def test_paused_after_run_ends_through_api_without_a_fictitious_deadline(self):
        from datetime import datetime, UTC, timedelta
        from custom_components.ha_sauna.core.timeline import Event, Kind
        import asyncio

        runtime = self.entry.runtime_data
        base = now = datetime.now(UTC)
        runtime._clock = lambda: now
        await runtime.set_operation(True)
        identity = runtime.session.session_id
        runtime.controller.report_heating(True, now)
        for second, kind in ((1, Kind.DOOR_CLOSE), (2, Kind.INFUSION),
                             (3, Kind.DOOR_OPEN), (4, Kind.VENTILATION)):
            now = base + timedelta(seconds=second)
            await runtime.receive(Event(f"api-paused-phase:{second}", identity, kind, now, now))
        token = runtime.session.after_run.phase_id
        now = base + timedelta(seconds=10)
        url = self.base + "/" + self.entry.entry_id
        async with ClientSession(headers=self.headers) as client:
            async with client.post(url + "/heater", json={"value": True}) as response:
                self.assertEqual(response.status, 200, await response.text())
            paused = runtime.session.after_run
            self.assertIsNone(paused.ends_at)
            self.assertEqual(paused.elapsed_seconds, 6)
            decisions_before_finish = len(runtime.controller.decisions)
            now = base + timedelta(seconds=20)
            async with client.post(url + "/finish_phase", json={"purpose": "after_run", "token": token}) as response:
                self.assertEqual(response.status, 200, await response.text())

        self.assertIsNone(runtime.session.after_run)
        self.assertEqual(runtime.session.after_run_history[-1].elapsed_seconds, 6)
        self.assertEqual(runtime.session.cooling.credited_seconds, 6)
        self.assertIsNone(runtime.controller.heater_override)
        self.assertIsNotNone(runtime.session.cooling.started_at)
        self.assertFalse(any(decision.heat for decision in runtime.controller.decisions[decisions_before_finish:]))
        await runtime.archive.flush()
        archived = await asyncio.to_thread(runtime.archive.read, identity, limit=10000)
        record = next(row for row in archived["records"] if row["kind"] == "manual_phase_end")
        self.assertEqual(record["session_id"], identity)
        self.assertEqual(record["payload"], {"purpose": "after_run", "token": token,
                                             "planned_ends_at": None, "ended_at": now.isoformat()})

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
        self.assertEqual(self.entry.options["parameters"]["sensor_timeout_seconds"],180)
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
            async with client.post(url+"/temperature",json={"forced_cooling_minutes":0}) as response:
                self.assertEqual(response.status,400)
            await runtime.set_operation(False)
            deadline=runtime.session.after_run
            async with client.post(url+"/temperature",json={"target_temperature_c":95}) as response:
                self.assertEqual(response.status,200,await response.text())
            self.assertEqual(runtime.session.after_run,deadline)
            self.assertFalse(runtime.controller.last_decision.heat)
            self.assertFalse(runtime.session.operation_enabled)

    async def test_external_options_writer_cannot_reload_away_active_session(self):
        runtime=self.entry.runtime_data
        await runtime.set_operation(True)
        identity=runtime.session.session_id
        options=dict(self.entry.options)
        self.hass.config_entries.async_update_entry(self.entry,options={**options,"parameters":{**options["parameters"],"heating_minutes":999}})
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
