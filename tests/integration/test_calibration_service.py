"""Calibration service, panel views, native send and reply ingress (W-2, W-2a, W-2c).

Replies never operate the sauna.
"""

import asyncio
import unittest

import test_device_path as device_path
import test_notification_service as notification_service
from aiohttp import ClientSession
from homeassistant.auth.const import GROUP_ID_USER
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError, Unauthorized

Lifecycle = notification_service.NotificationServiceLifecycleTests


class CalibrationServiceTests(unittest.IsolatedAsyncioTestCase):
    """Real service, options mapping and mobile device action; only the notify sink is synthetic."""

    asyncSetUp = device_path.DevicePathTests.asyncSetUp
    configure_recipients = Lifecycle.configure_recipients
    start_session = Lifecycle.start_session
    mobile_references = Lifecycle.mobile_references
    prepare_native_session = Lifecycle.prepare_native_session
    observe_replies = Lifecycle.observe_replies

    async def settle(self):
        while self.runtime._calibration_tasks:
            await asyncio.gather(*tuple(self.runtime._calibration_tasks))
            await asyncio.sleep(0)
        await self.hass.async_block_till_done()

    async def calibrate(self, service, user, **data):
        return await self.hass.services.async_call(
            "ha_sauna",
            service,
            {"entry_id": self.entry.entry_id, **data},
            blocking=True,
            return_response=True,
            context=Context(user_id=user.id),
        )

    async def reply(self, action, user):
        self.hass.bus.async_fire(
            "mobile_app_notification_action",
            {"action": action},
            context=Context(user_id=user.id),
        )
        await self.hass.async_block_till_done()
        await self.settle()

    async def test_single_module_reply_reaches_the_run_without_operating_the_sauna(self):
        calls = []

        async def terminal_sink(call):
            calls.append(dict(call.data))

        users, references, _ = await self.prepare_native_session(terminal_sink)
        stranger = await self.hass.auth.async_create_user(
            "Synthetic user without Companion target", group_ids=[GROUP_ID_USER]
        )
        with self.assertRaises(HomeAssistantError):
            await self.calibrate("start_calibration", stranger, module="A1")
        session_id = self.runtime.session.session_id
        before = (self.heater.is_on, self.runtime.controller.control_mode)
        wire, _ = self.observe_replies()
        response = await self.calibrate("start_calibration", users[0], module="A1")
        self.assertEqual(
            (response["kind"], response["session_id"], response["modules"]),
            ("module", session_id, ["A1"]),
        )
        await self.settle()
        # Each Companion target of the starting user receives its own copy.
        self.assertEqual(len(calls), len(references))
        self.assertEqual({call["message"] for call in calls}, {"Gang A1: hinein, Tür zu."})
        first, second = (call["data"]["actions"] for call in calls)
        self.assertEqual(
            [action["title"] for action in first], ["Drin, Tür zu", "Abweichend", "Abbrechen"]
        )
        self.assertNotEqual(first[0]["action"], second[0]["action"])
        await self.reply(first[0]["action"], users[0])
        await self.reply(second[0]["action"], users[0])
        run = self.runtime.calibration_runs[0]
        self.assertEqual(
            [(step["step"], step["outcome"], step["user_id"]) for step in run.steps],
            [("entry", "reply", users[0].id)],
        )
        # Replies remain public events and operate nothing.
        self.assertEqual(len(wire), 2)
        self.assertEqual((self.heater.is_on, self.runtime.controller.control_mode), before)
        self.assertEqual(self.runtime.session.session_id, session_id)
        self.assertTrue(self.runtime.session.operation_enabled)
        await self.calibrate("cancel_calibration", users[0])
        await self.settle()
        self.assertEqual(run.phase, "invalid")
        self.assertTrue(self.runtime.session.operation_enabled)
        # Only the notice of invalidity follows the cancellation.
        self.assertEqual(
            [call["message"] for call in calls[len(references):]],
            ["Kalibrierung ungültig: abgebrochen. Sie wird nicht ausgewertet."]
            * len(references),
        )
        await self.runtime.archive.flush()
        records = [
            record["payload"]
            for record in self.runtime.archive.read(session_id, kinds=("calibration",))[
                "records"
            ]
        ]
        self.assertEqual(records[0]["event"], "start")
        end = [record for record in records if record["event"] == "end"]
        self.assertEqual(
            [(record["reason"], record["valid"]) for record in end], [("cancelled", False)]
        )
        self.assertEqual(
            [record["disposition"] for record in records if record["event"] == "reply"],
            ["accepted", "duplicate"],
        )
        messages = [record for record in records if record["event"] == "message"]
        self.assertEqual(
            sorted(message["message_kind"] for message in messages),
            ["invalid"] * len(references) + ["step"] * len(references),
        )
        self.assertTrue(all(message["accepted"] for message in messages))

    async def headers(self, user):
        token = await self.hass.auth.async_create_refresh_token(
            user, client_id="http://localhost/"
        )
        return {
            "Authorization": "Bearer " + self.hass.auth.async_create_access_token(token)
        }

    async def test_panel_views_start_and_cancel_like_the_services(self):
        calls = []

        async def terminal_sink(call):
            calls.append(dict(call.data))

        users, references, _ = await self.prepare_native_session(terminal_sink)
        session_id = self.runtime.session.session_id
        url = (
            f"http://127.0.0.1:{self.hass.http.server_port}"
            f"/api/ha_sauna/{self.entry.entry_id}"
        )
        # Without the control grant on the operation entity: same boundary as the service.
        denied = await self.hass.auth.async_create_user("No control", group_ids=[])
        async with ClientSession(headers=await self.headers(denied)) as client:
            async with client.get(url + "/calibration") as response:
                self.assertEqual(response.status, 403)
            async with client.post(url + "/calibration", json={"module": "A1"}) as response:
                self.assertEqual(response.status, 403)
            async with client.post(url + "/calibration/cancel", json={}) as response:
                self.assertEqual(response.status, 403)
        with self.assertRaises(Unauthorized):
            await self.calibrate("start_calibration", denied, module="A1")
        # A user without a Companion target with replies is rejected with the reason.
        stranger = await self.hass.auth.async_create_user(
            "Synthetic user without Companion target", group_ids=[GROUP_ID_USER]
        )
        async with ClientSession(headers=await self.headers(stranger)) as client:
            async with client.get(url + "/calibration") as response:
                self.assertEqual(await response.json(), {"targets": 0})
            async with client.post(url + "/calibration", json={"module": "A1"}) as response:
                self.assertEqual(response.status, 409)
                self.assertIn("Companion-Ziel", (await response.json())["error"])
        self.assertEqual(self.runtime.calibration_runs, ())
        async with ClientSession(headers=await self.headers(users[0])) as client:
            async with client.get(url + "/calibration") as response:
                self.assertEqual(await response.json(), {"targets": len(references)})
            for body in (
                [], {"module": "X"}, {"module": ["A1"]}, {"modules": []},
                {"modules": ["A1", 2]}, {"module": "A1", "other": 1},
            ):
                async with client.post(url + "/calibration", json=body) as response:
                    self.assertEqual(response.status, 400, body)
            async with client.post(url + "/calibration", json={"module": "A1"}) as response:
                self.assertEqual(response.status, 200, await response.text())
                result = await response.json()
            self.assertEqual(
                (result["kind"], result["session_id"], result["modules"]),
                ("module", session_id, ["A1"]),
            )
            await self.settle()
            run = self.runtime.calibration_runs[0]
            # The starting user comes from the request.
            self.assertEqual(run.user_id, users[0].id)
            self.assertEqual(
                [call["message"] for call in calls],
                ["Gang A1: hinein, Tür zu."] * len(references),
            )
            async with client.post(url + "/calibration", json={"module": "A2"}) as response:
                self.assertEqual(response.status, 409)
                self.assertIn("bereits", (await response.json())["error"])
            async with client.get(url + "/state") as response:
                state = await response.json()
            # The first user is the owner and sees the full calibration state.
            self.assertTrue(users[0].is_admin)
            self.assertEqual(
                {key: state["calibration"]["active"][key]
                 for key in ("kind", "module", "step", "steps", "waiting")},
                {"kind": "module", "module": "A1", "step": 1, "steps": 4,
                 "waiting": "reply"},
            )
            self.assertIsNone(state["calibration"]["last"])
            self.assertEqual(
                [module["id"] for module in state["calibration"]["modules"]],
                ["A1", "A2", "A3", "CB", "D"],
            )
            async with (
                ClientSession(headers=await self.headers(stranger)) as other,
                other.get(url + "/state") as response,
            ):
                public = (await response.json())["calibration"]
            # A standard user sees the running status only.
            self.assertFalse(stranger.is_admin)
            self.assertEqual(public, {"active": state["calibration"]["active"]})
            async with client.post(url + "/calibration/cancel", json={"x": 1}) as response:
                self.assertEqual(response.status, 400)
            # L-W2c-3: the panel names the run it shows; another run stays untouched.
            self.assertEqual(state["calibration"]["active"]["run_id"], run.run_id)
            for body in ({"run_id": ""}, {"run_id": 1}):
                async with client.post(url + "/calibration/cancel", json=body) as response:
                    self.assertEqual(response.status, 400, body)
            async with client.post(url + "/calibration/cancel", json={"run_id": "other"}) as response:
                self.assertEqual(response.status, 409)
                self.assertIn("läuft nicht mehr", (await response.json())["error"])
            self.assertTrue(run.active)
            # The running run is not listed in the archive runs; its status shows it.
            async with client.get(url + "/archive/calibration") as response:
                self.assertEqual((await response.json())["runs"], [])
            async with client.post(url + "/calibration/cancel", json={}) as response:
                self.assertEqual(await response.json(), {"run_id": run.run_id})
            await self.settle()
            # B4e: the ended run comes from the archive with its reason.
            async with client.get(url + "/archive/calibration") as response:
                runs = (await response.json())["runs"]
            self.assertEqual([(item["run_id"], item["valid"], item["reason_text"]) for item in runs],
                             [(run.run_id, False, "abgebrochen")])
            async with client.post(url + "/calibration/cancel", json={}) as response:
                self.assertEqual(response.status, 409)
        self.assertEqual((run.phase, run.end_reason), ("invalid", "cancelled"))
        self.assertEqual(run.steps[-1]["user_id"], users[0].id)
        self.assertEqual(
            calls[-1]["message"],
            "Kalibrierung ungültig: abgebrochen. Sie wird nicht ausgewertet.",
        )
        self.assertTrue(self.runtime.session.operation_enabled)
        last = self.runtime.calibration_status["last"]
        self.assertEqual(
            (last["valid"], last["reason"], last["reason_text"]),
            (False, "cancelled", "abgebrochen"),
        )
