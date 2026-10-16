"""Explicit send uses saved mappings and the existing operation permission."""

import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, patch

from homeassistant.auth.const import GROUP_ID_USER
from homeassistant.core import Context

from custom_components.ha_sauna.core.notification_exchange import TransportHandoff
from custom_components.ha_sauna.core.notification_recipients import (
    CapabilityEvidence, RecipientAssignment, RecipientCapability, RecipientRef,
    RecipientResolution, ResolvedRecipient, TargetIdentity,
)
from harness import create_sauna, start_hass


class NotificationServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass, self.temp = await start_hass()
        self.entry = await create_sauna(self.hass)

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self.temp.cleanup()

    async def test_users_send_without_session_reports_each_handoff_and_preserves_data(self):
        user = await self.hass.auth.async_create_user("Synthetic user", group_ids=[GROUP_ID_USER])
        runtime = self.entry.runtime_data
        references = tuple(RecipientRef("entity", f"notify.synthetic_{i}", "home_assistant") for i in range(2))
        resolutions = tuple(RecipientResolution(ref, ResolvedRecipient(
            ref, TargetIdentity("home_assistant", ref.reference_id),
            RecipientCapability(send=CapabilityEvidence("test", "send")),
        )) for ref in references)
        runtime.configuration = replace(runtime.configuration, notification_assignments=(
            RecipientAssignment(user.id, frozenset(references)),
        ))

        async def handoff(notification):
            return TransportHandoff(notification, notification.target.endpoint.endswith("1"), "synthetic handoff")

        runtime.notification_transport.handoff = AsyncMock(side_effect=handoff)
        payload = {"actions": [{"action": "URI", "uri": "/synthetic"}]}
        with patch("custom_components.ha_sauna.notification_services.async_resolve_recipients",
                   AsyncMock(return_value=resolutions)):
            response = await self.hass.services.async_call(
                "ha_sauna", "send_notification", {
                    "entry_id": self.entry.entry_id, "message": "Synthetic message",
                    "data": payload, "audience": "users", "user_ids": [user.id],
                }, blocking=True, return_response=True, context=Context(user_id=user.id),
            )
        self.assertIsNone(runtime.session)
        self.assertEqual([item["accepted"] for item in response["handoffs"]], [False, True])
        self.assertEqual(runtime.notification_transport.handoff.await_count, 2)
        for call in runtime.notification_transport.handoff.call_args_list:
            self.assertEqual(call.args[0].payload["data"], payload)

    async def test_internal_automation_send_has_no_invented_actor(self):
        response = await self.hass.services.async_call(
            "ha_sauna", "send_notification", {
                "entry_id": self.entry.entry_id, "message": "Synthetic message",
            }, blocking=True, return_response=True, context=Context(),
        )
        self.assertEqual(response["handoffs"], [])
        self.assertIsNone(self.entry.runtime_data.notification_recipients)

    async def test_unknown_context_user_cannot_send(self):
        from homeassistant.exceptions import Unauthorized

        with self.assertRaises(Unauthorized):
            await self.hass.services.async_call(
                "ha_sauna", "send_notification", {
                    "entry_id": self.entry.entry_id, "message": "Synthetic message",
                }, blocking=True, return_response=True,
                context=Context(user_id="unknown-synthetic-user"),
            )

    async def test_explicit_forget_removes_sessionless_reply_association(self):
        user = await self.hass.auth.async_create_user("Synthetic user", group_ids=[GROUP_ID_USER])
        runtime = self.entry.runtime_data
        reference = RecipientRef("device", "synthetic-mobile-device", "home_assistant")
        target = TargetIdentity("home_assistant", reference.reference_id)
        resolution = RecipientResolution(reference, ResolvedRecipient(
            reference, target, RecipientCapability(send=CapabilityEvidence("test", "send")),
        ))
        runtime.configuration = replace(runtime.configuration, notification_assignments=(
            RecipientAssignment(user.id, frozenset((reference,))),
        ))
        observed = []
        unsubscribe = self.hass.bus.async_listen("ha_sauna_notification_reply", observed.append)
        self.addCleanup(unsubscribe)
        with (
            patch("custom_components.ha_sauna.notification_services.async_resolve_recipients",
                  AsyncMock(return_value=(resolution,))),
            patch.object(runtime.notification_transport, "_send", AsyncMock()),
        ):
            response = await self.hass.services.async_call(
                "ha_sauna", "send_notification", {
                    "entry_id": self.entry.entry_id, "message": "Synthetic message",
                    "audience": "users", "user_ids": [user.id],
                    "data": {"actions": [{"action": "synthetic_forget_action", "title": "Synthetic"}]},
                }, blocking=True, return_response=True, context=Context(user_id=user.id),
            )
        message_id = response["handoffs"][0]["message_id"]
        self.assertTrue(response["handoffs"][0]["accepted"])
        self.assertIsNone(runtime.session)
        self.hass.bus.async_fire("mobile_app_notification_action", {"action": "synthetic_forget_action"})
        await self.hass.async_block_till_done()
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0].data["message_id"], message_id)
        await self.hass.services.async_call(
            "ha_sauna", "forget_notification", {
                "entry_id": self.entry.entry_id, "message_id": message_id,
            }, blocking=True, context=Context(user_id=user.id),
        )
        self.hass.bus.async_fire("mobile_app_notification_action", {"action": "synthetic_forget_action"})
        await self.hass.async_block_till_done()
        self.assertEqual(len(observed), 1)

    async def test_forget_uses_the_same_user_permission_boundary(self):
        from homeassistant.exceptions import Unauthorized

        runtime = self.entry.runtime_data
        with patch.object(runtime.notification_transport, "forget") as forget:
            with self.assertRaises(Unauthorized):
                await self.hass.services.async_call(
                    "ha_sauna", "forget_notification", {
                        "entry_id": self.entry.entry_id, "message_id": "synthetic-message",
                    }, blocking=True, context=Context(user_id="unknown-synthetic-user"),
                )
            forget.assert_not_called()
