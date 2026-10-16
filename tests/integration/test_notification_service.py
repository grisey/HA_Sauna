"""Explicit send uses saved mappings and the existing operation permission."""

import asyncio
import json
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, patch

from homeassistant.auth.const import GROUP_ID_USER
from homeassistant.core import Context
from homeassistant.helpers import entity_registry as er

import test_device_path as device_path
from custom_components.ha_sauna.core.notification_exchange import (
    ReplyAssociation,
    ReplyDisposition,
    ReplyEnvelope,
    ReplyKey,
    TransportHandoff,
)
from custom_components.ha_sauna.core.notification_recipients import (
    CapabilityEvidence,
    RecipientAssignment,
    RecipientCapability,
    RecipientRef,
    RecipientResolution,
    ResolvedRecipient,
    TargetIdentity,
)
from harness import create_sauna, start_hass
from test_settings import async_timeout


class NotificationServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass, self.temp = await start_hass()
        self.entry = await create_sauna(self.hass)

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self.temp.cleanup()

    async def test_users_send_without_session_reports_each_handoff_and_preserves_data(
        self,
    ):
        user = await self.hass.auth.async_create_user(
            "Synthetic user", group_ids=[GROUP_ID_USER]
        )
        runtime = self.entry.runtime_data
        references = tuple(
            RecipientRef("entity", f"notify.synthetic_{i}", "home_assistant")
            for i in range(2)
        )
        resolutions = tuple(
            RecipientResolution(
                ref,
                ResolvedRecipient(
                    ref,
                    TargetIdentity("home_assistant", ref.reference_id),
                    RecipientCapability(send=CapabilityEvidence("test", "send")),
                ),
            )
            for ref in references
        )
        runtime.configuration = replace(
            runtime.configuration,
            notification_assignments=(
                RecipientAssignment(user.id, frozenset(references)),
            ),
        )

        async def handoff(notification):
            return TransportHandoff(
                notification,
                notification.target.endpoint.endswith("1"),
                "synthetic handoff",
            )

        runtime.notification_transport.handoff = AsyncMock(side_effect=handoff)
        payload = {"actions": [{"action": "URI", "uri": "/synthetic"}]}
        with patch(
            "custom_components.ha_sauna.notification_services.async_resolve_recipients",
            AsyncMock(return_value=resolutions),
        ):
            response = await self.hass.services.async_call(
                "ha_sauna",
                "send_notification",
                {
                    "entry_id": self.entry.entry_id,
                    "message": "Synthetic message",
                    "data": payload,
                    "audience": "users",
                    "user_ids": [user.id],
                },
                blocking=True,
                return_response=True,
                context=Context(user_id=user.id),
            )
        self.assertIsNone(runtime.session)
        self.assertEqual(
            [item["accepted"] for item in response["handoffs"]], [False, True]
        )
        self.assertEqual(runtime.notification_transport.handoff.await_count, 2)
        for call in runtime.notification_transport.handoff.call_args_list:
            self.assertEqual(call.args[0].payload["data"], payload)

    async def test_internal_automation_send_has_no_invented_actor(self):
        response = await self.hass.services.async_call(
            "ha_sauna",
            "send_notification",
            {
                "entry_id": self.entry.entry_id,
                "message": "Synthetic message",
            },
            blocking=True,
            return_response=True,
            context=Context(),
        )
        self.assertEqual(response["handoffs"], [])
        self.assertIsNone(self.entry.runtime_data.notification_recipients)

    async def test_unknown_context_user_cannot_send(self):
        from homeassistant.exceptions import Unauthorized

        with self.assertRaises(Unauthorized):
            await self.hass.services.async_call(
                "ha_sauna",
                "send_notification",
                {
                    "entry_id": self.entry.entry_id,
                    "message": "Synthetic message",
                },
                blocking=True,
                return_response=True,
                context=Context(user_id="unknown-synthetic-user"),
            )

    async def test_explicit_forget_removes_sessionless_reply_association(self):
        user = await self.hass.auth.async_create_user(
            "Synthetic user", group_ids=[GROUP_ID_USER]
        )
        runtime = self.entry.runtime_data
        reference = RecipientRef("device", "synthetic-mobile-device", "home_assistant")
        target = TargetIdentity("home_assistant", reference.reference_id)
        resolution = RecipientResolution(
            reference,
            ResolvedRecipient(
                reference,
                target,
                RecipientCapability(send=CapabilityEvidence("test", "send")),
            ),
        )
        runtime.configuration = replace(
            runtime.configuration,
            notification_assignments=(
                RecipientAssignment(user.id, frozenset((reference,))),
            ),
        )
        observed = []
        associations = []
        callback_patch = patch.object(
            runtime.notification_transport, "_reply_callback", associations.append
        )
        callback_patch.start()
        self.addCleanup(callback_patch.stop)
        unsubscribe = self.hass.bus.async_listen(
            "ha_sauna_notification_reply", observed.append
        )
        self.addCleanup(unsubscribe)
        with (
            patch(
                "custom_components.ha_sauna.notification_services.async_resolve_recipients",
                AsyncMock(return_value=(resolution,)),
            ),
            patch.object(runtime.notification_transport, "_send", AsyncMock()),
        ):
            response = await self.hass.services.async_call(
                "ha_sauna",
                "send_notification",
                {
                    "entry_id": self.entry.entry_id,
                    "message": "Synthetic message",
                    "audience": "users",
                    "user_ids": [user.id],
                    "data": {
                        "actions": [
                            {"action": "synthetic_forget_action", "title": "Synthetic"}
                        ]
                    },
                },
                blocking=True,
                return_response=True,
                context=Context(user_id=user.id),
            )
        message_id = response["handoffs"][0]["message_id"]
        self.assertTrue(response["handoffs"][0]["accepted"])
        self.assertIsNone(runtime.session)
        self.hass.bus.async_fire(
            "mobile_app_notification_action", {"action": "synthetic_forget_action"}
        )
        await self.hass.async_block_till_done()
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0].data["message_id"], message_id)
        self.assertEqual(len(associations), 1)
        association = associations[0]
        self.assertIsInstance(association, ReplyAssociation)
        self.assertEqual(association.disposition, ReplyDisposition.ACCEPTED)
        self.assertTrue(association.handoff_accepted)
        self.assertEqual(association.notification.message_id, message_id)
        reply = association.reply
        self.assertIsInstance(reply, ReplyEnvelope)
        self.assertEqual(
            reply.reply_key,
            ReplyKey("home_assistant", "synthetic_forget_action"),
        )
        self.assertIsNone(reply.reply_id)
        self.assertIsNone(reply.source)
        self.assertIsNone(reply.entry_id)
        self.assertIsNone(reply.message_id)
        self.assertIsNone(reply.correlation_id)
        self.assertIsNone(reply.session_id)
        self.assertEqual(reply.payload, observed[0].data["data"])
        self.assertEqual(reply.context_id, observed[0].data["context_id"])
        self.assertEqual(reply.observed_at.isoformat(), observed[0].data["time_fired"])
        self.assertIsNone(observed[0].data["source"])
        print(
            "NOTIFICATION_EVIDENCE "
            + json.dumps(
                {
                    "selector": "NotificationServiceTests.test_explicit_forget_removes_sessionless_reply_association",
                    "association_type": type(association).__name__,
                    "reply_type": type(reply).__name__,
                    "disposition": association.disposition.value,
                    "handoff_accepted": association.handoff_accepted,
                    "native_reply_id": reply.reply_id,
                    "native_source": reply.source,
                    "native_entry_id": reply.entry_id,
                    "native_message_id": reply.message_id,
                    "native_correlation_id": reply.correlation_id,
                    "native_session_id": reply.session_id,
                    "wire_matches_association": (
                        observed[0].data["message_id"]
                        == association.notification.message_id
                    ),
                    "callback_count": len(associations),
                    "event_count": len(observed),
                },
                sort_keys=True,
            )
        )
        await self.hass.services.async_call(
            "ha_sauna",
            "forget_notification",
            {
                "entry_id": self.entry.entry_id,
                "message_id": message_id,
            },
            blocking=True,
            context=Context(user_id=user.id),
        )
        self.hass.bus.async_fire(
            "mobile_app_notification_action", {"action": "synthetic_forget_action"}
        )
        await self.hass.async_block_till_done()
        self.assertEqual(len(observed), 1)
        self.assertEqual(len(associations), 1)

    async def test_forget_uses_the_same_user_permission_boundary(self):
        from homeassistant.exceptions import Unauthorized

        runtime = self.entry.runtime_data
        with patch.object(runtime.notification_transport, "forget") as forget:
            with self.assertRaises(Unauthorized):
                await self.hass.services.async_call(
                    "ha_sauna",
                    "forget_notification",
                    {
                        "entry_id": self.entry.entry_id,
                        "message_id": "synthetic-message",
                    },
                    blocking=True,
                    context=Context(user_id="unknown-synthetic-user"),
                )
            forget.assert_not_called()


class NotificationServiceLifecycleTests(unittest.IsolatedAsyncioTestCase):
    """Real session controls stay usable while the terminal notify sink waits."""

    asyncSetUp = device_path.DevicePathTests.asyncSetUp

    async def prepare_session_recipient(self):
        user = await self.hass.auth.async_create_user(
            "Synthetic notification session starter", group_ids=[GROUP_ID_USER]
        )
        entity = er.async_get(self.hass).async_get_or_create(
            "notify", "synthetic", "terminal", suggested_object_id="synthetic_terminal"
        )
        self.hass.states.async_set(entity.entity_id, "unknown", {})
        # Use the actual options flow and production resolver before starting.
        flow = await self.hass.config_entries.options.async_init(self.entry.entry_id)
        flow = await self.hass.config_entries.options.async_configure(
            flow["flow_id"], {"next_step_id": "notification_user"}
        )
        flow = await self.hass.config_entries.options.async_configure(
            flow["flow_id"], {"user_id": user.id}
        )
        self.assertEqual(flow["step_id"], "notification_targets")
        flow = await self.hass.config_entries.options.async_configure(
            flow["flow_id"], {"recipients": [f"entity:{entity.entity_id}"]}
        )
        self.assertEqual((flow["type"], flow["step_id"]), ("menu", "init"))
        await self.hass.async_block_till_done()
        self.runtime = self.entry.runtime_data
        self.runtime._clock = lambda: self.now
        actor = Context(user_id=user.id)
        await self.hass.services.async_call(
            "switch", "turn_on", {"entity_id": self.operation},
            blocking=True, context=actor,
        )
        await self.hass.async_block_till_done()
        target = TargetIdentity("home_assistant", entity.entity_id)
        self.assertEqual(
            self.runtime.notification_recipients.targets, frozenset((target,))
        )
        self.assertTrue(self.runtime.session.operation_enabled)
        self.assertEqual(self.hass.states.get(self.operation).state, "on")
        self.assertTrue(self.heater.is_on)
        return user, entity.entity_id, actor

    async def test_pending_notify_handoff_does_not_block_authenticated_operation_off(self):
        entered, release = asyncio.Event(), asyncio.Event()
        trace, calls = [], []

        async def terminal_sink(call):
            calls.append(dict(call.data))
            trace.append("synthetic_notify_entered")
            entered.set()
            await release.wait()
            trace.append("synthetic_notify_returned")

        self.hass.services.async_register("notify", "send_message", terminal_sink)
        user, endpoint, actor = await self.prepare_session_recipient()
        session_id = self.runtime.session.session_id
        sending = asyncio.create_task(self.hass.services.async_call(
            "ha_sauna", "send_notification",
            {
                "entry_id": self.entry.entry_id,
                "message": "Synthetic held notification",
                "audience": "session",
            },
            blocking=True, return_response=True, context=Context(user_id=user.id),
        ))
        stopping = None
        try:
            await asyncio.wait_for(entered.wait(), async_timeout("service"))
            trace.append("authenticated_operation_off_requested")

            async def stop_operation():
                await self.hass.services.async_call(
                    "switch", "turn_off", {"entity_id": self.operation},
                    blocking=True, context=actor,
                )
                trace.append("authenticated_operation_off_returned")

            stopping = asyncio.create_task(stop_operation())
            done, _ = await asyncio.wait({stopping}, timeout=async_timeout("service"))
            before_release = {
                "operation_off_completed": stopping in done,
                "operation_state": self.hass.states.get(self.operation).state,
                "session_operation_enabled": self.runtime.session.operation_enabled,
                "heater_state": self.hass.states.get(self.heater.entity_id).state,
                "heater_is_on": self.heater.is_on,
                "heater_calls": list(self.heater.calls),
                "notify_still_pending": not sending.done(),
                "sink_released": release.is_set(),
            }
        finally:
            trace.append("synthetic_notify_released")
            release.set()
            tasks = [sending] + ([stopping] if stopping is not None else [])
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), async_timeout("service")
            )
        response = sending.result()
        if stopping is not None:
            stopping.result()
        print("NOTIFICATION_EVIDENCE " + json.dumps({
            "selector": self.id(),
            "path": "options mapping -> authenticated session start -> session send -> real HA notify.send_message registry -> synthetic terminal sink",
            "endpoint_receipt": "not observed; synthetic terminal sink only",
            "actor_user_id": actor.user_id,
            "session_id": session_id,
            "recipient": endpoint,
            "observed_service_calls": calls,
            "before_sink_release": before_release,
            "causal_order": trace,
            "response": response,
        }, sort_keys=True))
        self.assertTrue(
            before_release["operation_off_completed"],
            "Authenticated operation OFF waited for the pending notify sink",
        )
        self.assertEqual(before_release["operation_state"], "off")
        self.assertFalse(before_release["session_operation_enabled"])
        self.assertEqual(before_release["heater_state"], "off")
        self.assertFalse(before_release["heater_is_on"])
        self.assertFalse(before_release["heater_calls"][-1])
        self.assertTrue(before_release["notify_still_pending"])
        self.assertFalse(before_release["sink_released"])
        self.assertEqual(calls, [{
            "entity_id": endpoint, "message": "Synthetic held notification"
        }])
        self.assertEqual(len(response["handoffs"]), 1)
        self.assertTrue(response["handoffs"][0]["accepted"])
