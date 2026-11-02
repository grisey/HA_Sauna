"""Calibration service, native send and reply ingress (W-2); replies never operate."""

import asyncio
import unittest

import test_device_path as device_path
import test_notification_service as notification_service
from homeassistant.auth.const import GROUP_ID_USER
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError

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
        self.assertEqual(run.phase, "cancelled")
        self.assertTrue(self.runtime.session.operation_enabled)
        await self.runtime.archive.flush()
        records = [
            record["payload"]
            for record in self.runtime.archive.read(session_id, kinds=("calibration",))[
                "records"
            ]
        ]
        self.assertEqual((records[0]["event"], records[-1]["event"]), ("start", "end"))
        self.assertEqual(
            [record["disposition"] for record in records if record["event"] == "reply"],
            ["accepted", "duplicate"],
        )
        messages = [record for record in records if record["event"] == "message"]
        self.assertEqual(len(messages), len(references))
        self.assertTrue(all(message["accepted"] for message in messages))
