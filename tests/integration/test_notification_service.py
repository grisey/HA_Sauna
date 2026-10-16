"""Explicit send uses saved mappings and the existing operation permission."""

import asyncio
import json
import unittest
from dataclasses import replace
from datetime import timedelta
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp import ClientSession
from homeassistant.auth.const import GROUP_ID_USER
from homeassistant.core import Context, callback
from homeassistant.helpers import entity_registry as er

import test_device_path as device_path
from custom_components.ha_sauna.core.notification_exchange import (
    ReplyAssociation,
    ReplyDisposition,
    ReplyEnvelope,
    ReplyKey,
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

        async def terminal_send(notification):
            if not notification.target.endpoint.endswith("1"):
                raise RuntimeError("Synthetic terminal rejection")

        runtime.notification_transport._send = AsyncMock(side_effect=terminal_send)
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
        self.assertEqual(runtime.notification_transport._send.await_count, 2)
        for call in runtime.notification_transport._send.call_args_list:
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

    async def configure_recipients(self, assignments):
        """Exercise saved mapping validation and the real options reload."""
        for user, references in assignments:
            flow = await self.hass.config_entries.options.async_init(self.entry.entry_id)
            flow = await self.hass.config_entries.options.async_configure(
                flow["flow_id"], {"next_step_id": "notification_user"}
            )
            flow = await self.hass.config_entries.options.async_configure(
                flow["flow_id"], {"user_id": user.id}
            )
            self.assertEqual(flow["step_id"], "notification_targets")
            flow = await self.hass.config_entries.options.async_configure(
                flow["flow_id"], {"recipients": [
                    f"{reference.kind}:{reference.reference_id}"
                    for reference in references
                ]}
            )
            self.assertEqual((flow["type"], flow["step_id"]), ("menu", "init"))
            await self.hass.async_block_till_done()
        self.runtime = self.entry.runtime_data
        # Options changes reload the runtime with later real source times.
        # Follow the device-path fixture's existing post-reload clock rule.
        self.base = self.now = max(
            self.now, self.runtime.device.input_started_at + timedelta(seconds=1)
        )
        self.runtime._clock = lambda: self.now

    async def start_session(self, user):
        actor = Context(user_id=user.id)
        await self.hass.services.async_call(
            "switch", "turn_on", {"entity_id": self.operation},
            blocking=True, context=actor,
        )
        await self.hass.async_block_till_done()
        self.assertTrue(self.runtime.session.operation_enabled)
        self.assertEqual(self.hass.states.get(self.operation).state, "on")
        self.assertTrue(self.heater.is_on)
        return actor

    async def prepare_session_recipient(self):
        user = await self.hass.auth.async_create_user(
            "Synthetic notification session starter", group_ids=[GROUP_ID_USER]
        )
        entity = er.async_get(self.hass).async_get_or_create(
            "notify", "synthetic", "terminal", suggested_object_id="synthetic_terminal"
        )
        self.hass.states.async_set(entity.entity_id, "unknown", {})
        reference = RecipientRef("entity", entity.entity_id, "home_assistant")
        await self.configure_recipients(((user, (reference,)),))
        actor = await self.start_session(user)
        target = TargetIdentity("home_assistant", entity.entity_id)
        self.assertEqual(
            self.runtime.notification_recipients.targets, frozenset((target,))
        )
        return user, entity.entity_id, actor

    async def mobile_references(self, count):
        """Reuse the native-registration contract from the HA transport fixture.

        Only final notify services are synthetic; HA loads and executes its
        actual mobile device action against these isolated registrations.
        """
        from homeassistant.components.device_automation import (
            DeviceAutomationType,
            async_get_device_automation_platform,
        )
        from homeassistant.config_entries import ConfigEntry, ConfigEntryState
        from homeassistant.helpers import device_registry as dr

        await async_get_device_automation_platform(
            self.hass, "mobile_app", DeviceAutomationType.ACTION
        )
        from homeassistant.components.mobile_app import const

        registrations, references, devices, entries, targets = {}, [], {}, {}, {}
        for index in range(count):
            name = f"synthetic-phone-{index}"
            webhook = f"synthetic-webhook-{index}"
            entry = ConfigEntry(
                version=1, minor_version=1, domain="mobile_app",
                title=f"Synthetic phone {index}",
                data={const.ATTR_APP_DATA: {const.ATTR_PUSH_WEBSOCKET_CHANNEL: True}},
                source="user", options={}, unique_id=name,
                discovery_keys=MappingProxyType({}), subentries_data=[],
                state=ConfigEntryState.LOADED,
            )
            self.hass.config_entries._entries[entry.entry_id] = entry
            device = dr.async_get(self.hass).async_get_or_create(
                config_entry_id=entry.entry_id, identifiers={("mobile_app", name)},
            )
            entity = er.async_get(self.hass).async_get_or_create(
                "notify", "mobile_app", name, device_id=device.id, config_entry=entry,
            )
            self.hass.states.async_set(entity.entity_id, "unknown", {})
            registrations[f"synthetic_mobile_{index}"] = webhook
            devices[webhook], entries[webhook] = device, entry
            targets[device.id] = webhook
            references.append(RecipientRef("device", device.id, "home_assistant"))
        self.hass.config.components.add("mobile_app")
        self.hass.data[const.DOMAIN] = {
            const.DATA_DEVICES: devices, const.DATA_CONFIG_ENTRIES: entries,
            const.DATA_NOTIFY: SimpleNamespace(registered_targets=registrations),
        }
        return tuple(references), registrations, targets

    async def prepare_native_session(self, terminal_sink, *, participants=1):
        references, registrations, targets = await self.mobile_references(
            max(2, participants)
        )
        for service in registrations:
            self.hass.services.async_register("notify", service, terminal_sink)
        users = [await self.hass.auth.async_create_user(
            f"Synthetic mapped user {index}", group_ids=[GROUP_ID_USER]
        ) for index in range(participants)]
        assignments = (
            ((users[0], references),) if participants == 1 else
            tuple((user, (reference,)) for user, reference in zip(
                users, references, strict=True
            ))
        )
        await self.configure_recipients(assignments)
        await self.start_session(users[0])
        return users, references, targets

    def start_native_send(self, user):
        return asyncio.create_task(self.hass.services.async_call(
            "ha_sauna", "send_notification",
            {
                "entry_id": self.entry.entry_id,
                "message": "Synthetic native session notification",
                "audience": "session",
                "data": {"actions": [{"action": "SYNTHETIC_REPLY", "title": "Reply"}]},
            },
            blocking=True, return_response=True, context=Context(user_id=user.id),
        ))

    def observe_replies(self):
        wire, observed = [], asyncio.Event()

        @callback
        def receive(event):
            wire.append(dict(event.data))
            observed.set()

        self.addCleanup(self.hass.bus.async_listen("ha_sauna_notification_reply", receive))
        return wire, observed

    def fire_native_reply(self):
        self.hass.bus.async_fire(
            "mobile_app_notification_action", {"action": "SYNTHETIC_REPLY"}
        )

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

    async def test_session_end_during_first_native_send_preserves_targets_without_old_replies(self):
        entered, release = asyncio.Event(), asyncio.Event()
        calls, trace = [], []
        wire, observed = self.observe_replies()

        async def terminal_sink(call):
            calls.append({"service": call.service, "data": dict(call.data)})
            if len(calls) == 1:
                self.fire_native_reply()
                await asyncio.wait_for(observed.wait(), async_timeout("service"))
                trace.append("first_native_send_pending")
                entered.set()
                await release.wait()
            trace.append("native_send_returned")

        users, references, targets = await self.prepare_native_session(terminal_sink)
        session_id = self.runtime.session.session_id
        sending = self.start_native_send(users[0])
        ending = None
        try:
            await asyncio.wait_for(entered.wait(), async_timeout("service"))
            self.assertEqual(len(wire), 1)
            self.assertFalse(wire[0]["ambiguous"])
            self.assertIsNone(wire[0]["handoff_accepted"])
            self.assertEqual(wire[0]["session_id"], session_id)

            async def end_session():
                await self.hass.services.async_call(
                    "switch", "turn_off", {"entity_id": self.operation},
                    blocking=True, context=Context(user_id=users[0].id),
                )
                deadline = next(
                    item for item in self.runtime.session.deadlines
                    if item.purpose == "session_gap"
                )
                self.now = deadline.due_at
                await self.runtime.tick()
                trace.append("session_ended_before_notify_release")

            ending = asyncio.create_task(end_session())
            await asyncio.wait_for(asyncio.shield(ending), async_timeout("service"))
            self.assertIsNone(self.runtime.session)
            self.assertIsNone(self.runtime.notification_recipients)
            self.assertFalse(self.heater.is_on)
            self.assertFalse(release.is_set())
            self.assertFalse(sending.done())
            self.fire_native_reply()
        finally:
            trace.append("synthetic_notify_released")
            release.set()
            tasks = [sending] + ([ending] if ending is not None else [])
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), async_timeout("service")
            )
        response = sending.result()
        if ending is not None:
            ending.result()
        self.fire_native_reply()
        await self.hass.async_block_till_done()
        expected = sorted(reference.reference_id for reference in references)
        self.assertEqual(
            [item["target"]["endpoint"] for item in response["handoffs"]], expected
        )
        self.assertTrue(all(item["accepted"] for item in response["handoffs"]))
        self.assertEqual([call["data"]["target"] for call in calls], [
            targets[endpoint] for endpoint in expected
        ])
        self.assertEqual(len(wire), 1, "Ended-session reply registration reappeared")
        print("NOTIFICATION_EVIDENCE " + json.dumps({
            "selector": self.id(), "synthetic_terminal_sink": True,
            "endpoint_receipt": "not observed",
            "session_id": session_id, "causal_order": trace,
            "captured_targets": expected, "observed_service_calls": calls,
            "response": response, "public_reply_events": wire,
            "post_session_reply_events": len(wire) - 1,
        }, sort_keys=True))

    async def test_unload_during_first_native_send_rejects_later_targets_without_reopening(self):
        entered, release = asyncio.Event(), asyncio.Event()
        calls, trace = [], []
        wire, observed = self.observe_replies()

        async def terminal_sink(call):
            calls.append({"service": call.service, "data": dict(call.data)})
            self.fire_native_reply()
            await asyncio.wait_for(observed.wait(), async_timeout("service"))
            trace.append("first_native_send_pending")
            entered.set()
            await release.wait()
            trace.append("first_native_send_returned")

        users, references, _ = await self.prepare_native_session(terminal_sink)
        runtime = self.runtime
        transport = runtime.notification_transport
        sending = self.start_native_send(users[0])
        unloading = None
        try:
            await asyncio.wait_for(entered.wait(), async_timeout("service"))
            self.assertEqual(len(wire), 1)
            self.assertFalse(wire[0]["ambiguous"])
            self.assertIsNone(wire[0]["handoff_accepted"])
            unloading = asyncio.create_task(
                self.hass.config_entries.async_unload(self.entry.entry_id)
            )
            self.assertTrue(await asyncio.wait_for(
                asyncio.shield(unloading), async_timeout("reload")
            ))
            trace.append("entry_unloaded_before_notify_release")
            self.assertTrue(runtime.closed)
            self.assertIsNone(transport._ingress)
            self.assertNotIn("ha_sauna_notification_reply_ingress", self.hass.data)
            self.assertFalse(self.heater.is_on)
            self.assertFalse(release.is_set())
            self.assertFalse(sending.done())
        finally:
            trace.append("synthetic_notify_released")
            release.set()
            tasks = [sending] + ([unloading] if unloading is not None else [])
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), async_timeout("reload")
            )
        response = sending.result()
        if unloading is not None:
            unloading.result()
        self.fire_native_reply()
        await self.hass.async_block_till_done()
        expected = sorted(reference.reference_id for reference in references)
        self.assertEqual(
            [item["target"]["endpoint"] for item in response["handoffs"]], expected
        )
        self.assertEqual([item["accepted"] for item in response["handoffs"]], [True, False])
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(wire), 1)
        self.assertIsNone(transport._ingress)
        self.assertNotIn("ha_sauna_notification_reply_ingress", self.hass.data)
        print("NOTIFICATION_EVIDENCE " + json.dumps({
            "selector": self.id(), "synthetic_terminal_sink": True,
            "endpoint_receipt": "not observed", "causal_order": trace,
            "captured_targets": expected, "observed_service_calls": calls,
            "response": response, "public_reply_events": wire,
            "ingress_reopened": transport._ingress is not None,
        }, sort_keys=True))

    async def test_authenticated_panel_participants_reach_native_session_send_and_public_reply(self):
        calls = []

        async def terminal_sink(call):
            calls.append({"service": call.service, "data": dict(call.data)})

        users, references, targets = await self.prepare_native_session(
            terminal_sink, participants=6
        )
        session_id = self.runtime.session.session_id
        mutations = []
        url = (
            f"http://127.0.0.1:{self.hass.http.server_port}"
            f"/api/ha_sauna/{self.entry.entry_id}"
        )
        for index, (route, body) in enumerate((
            ("program", {"target_temperature_c": 82}),
            ("temperature", {"target_temperature_c": 83}),
            ("heater", {"value": False}),
            ("light", {"value": 37}),
            ("control-mode", {"mode": "manual"}),
        ), start=1):
            user, reference = users[index], references[index]
            if route == "control-mode":
                # Mode changes are supported only after automatic operation OFF;
                # the recording and its accumulated recipients remain alive.
                await self.hass.services.async_call(
                    "switch", "turn_off", {"entity_id": self.operation},
                    blocking=True, context=Context(user_id=users[0].id),
                )
                await self.hass.async_block_till_done()
            target = TargetIdentity("home_assistant", reference.reference_id)
            before = self.runtime.notification_recipients.targets
            self.assertNotIn(target, before)
            token = await self.hass.auth.async_create_refresh_token(
                user, client_id="http://localhost/"
            )
            headers = {"Authorization": "Bearer " + self.hass.auth.async_create_access_token(token)}
            async with ClientSession(headers=headers) as client:
                async with client.post(url + "/" + route, json=body) as response:
                    self.assertEqual(response.status, 200, await response.text())
                    result = await response.json()
            await self.hass.async_block_till_done()
            self.assertEqual(self.runtime.session.session_id, session_id)
            self.assertEqual(self.runtime.notification_recipients.targets, before | {target})
            if route == "heater":
                self.assertFalse(self.heater.is_on)
            elif route == "light":
                self.assertAlmostEqual(self.runtime.device.light_output.manual_brightness, 37)
            mutations.append({
                "route": route, "actor_user_id": user.id, "request": body,
                "target_added": target.endpoint, "success": result["success"],
            })
        self.assertFalse(self.heater.is_on)
        wire, _ = self.observe_replies()
        response = await self.start_native_send(users[0])
        expected = sorted(reference.reference_id for reference in references)
        self.assertEqual(
            [item["target"]["endpoint"] for item in response["handoffs"]], expected
        )
        self.assertTrue(all(item["accepted"] for item in response["handoffs"]))
        self.assertEqual([call["data"]["target"] for call in calls], [
            targets[endpoint] for endpoint in expected
        ])
        # The same native identifier is ambiguous across actual target sends.
        self.fire_native_reply()
        await self.hass.async_block_till_done()
        self.assertEqual(len(wire), 1)
        self.assertTrue(wire[0]["ambiguous"])
        for item in response["handoffs"][:-1]:
            await self.hass.services.async_call(
                "ha_sauna", "forget_notification",
                {"entry_id": self.entry.entry_id, "message_id": item["message_id"]},
                blocking=True, context=Context(user_id=users[0].id),
            )
        self.fire_native_reply()
        await self.hass.async_block_till_done()
        self.assertEqual(len(wire), 2)
        self.assertFalse(wire[-1]["ambiguous"])
        self.assertTrue(wire[-1]["handoff_accepted"])
        self.assertEqual(wire[-1]["message_id"], response["handoffs"][-1]["message_id"])
        self.assertEqual(wire[-1]["session_id"], session_id)
        self.assertEqual(wire[-1]["target"]["endpoint"], expected[-1])
        print("NOTIFICATION_EVIDENCE " + json.dumps({
            "selector": self.id(), "synthetic_terminal_sink": True,
            "endpoint_receipt": "not observed", "session_id": session_id,
            "starter_user_id": users[0].id, "authenticated_mutations": mutations,
            "captured_targets": expected, "observed_service_calls": calls,
            "response": response, "public_reply_events": wire,
        }, sort_keys=True))

    async def test_authenticated_template_switch_input_starts_mapped_native_session(self):
        from homeassistant.setup import async_setup_component

        self.assertTrue(await async_setup_component(self.hass, "input_boolean", {
            "input_boolean": {"synthetic_notification_control": {
                "name": "Synthetic notification control", "initial": False,
            }},
        }))
        self.assertTrue(await async_setup_component(self.hass, "template", {
            "template": [{"binary_sensor": [{
                "name": "Synthetic notification input",
                "unique_id": "synthetic_notification_input",
                "state": "{{ is_state('input_boolean.synthetic_notification_control', 'on') }}",
            }]}],
        }))
        await self.hass.async_block_till_done()
        control_input = er.async_get(self.hass).async_get_entity_id(
            "binary_sensor", "template", "synthetic_notification_input"
        )
        self.assertIsNotNone(control_input)
        self.assertEqual(self.hass.states.get(control_input).state, "off")
        self.hass.config_entries.async_update_entry(self.entry, options={
            **self.entry.options, "control_input_mode": "switch",
            "bindings": {**self.entry.options["bindings"], "control_input": control_input},
        })
        await self.hass.async_block_till_done()
        references, registrations, targets = await self.mobile_references(2)
        calls = []

        async def terminal_sink(call):
            calls.append({"service": call.service, "data": dict(call.data)})

        for service in registrations:
            self.hass.services.async_register("notify", service, terminal_sink)
        user = await self.hass.auth.async_create_user(
            "Synthetic authenticated source operator", group_ids=[GROUP_ID_USER]
        )
        await self.configure_recipients(((user, (references[0],)),))
        self.assertIsNone(self.runtime.session)
        context = Context(user_id=user.id)
        await self.hass.services.async_call(
            "input_boolean", "turn_on",
            {"entity_id": "input_boolean.synthetic_notification_control"},
            blocking=True, context=context,
        )
        await self.hass.async_block_till_done()
        source = self.hass.states.get("input_boolean.synthetic_notification_control")
        derived = self.hass.states.get(control_input)
        self.assertEqual(source.context.user_id, user.id)
        self.assertEqual(derived.context.user_id, user.id)
        self.assertTrue(self.runtime.session.operation_enabled)
        self.assertEqual(self.hass.states.get(self.operation).state, "on")
        self.assertTrue(self.heater.is_on)
        accumulated = sorted(
            target.endpoint for target in self.runtime.notification_recipients.targets
        )
        wire, _ = self.observe_replies()
        response = await self.start_native_send(user)
        self.fire_native_reply()
        await self.hass.async_block_till_done()
        print("NOTIFICATION_EVIDENCE " + json.dumps({
            "selector": self.id(), "synthetic_terminal_sink": True,
            "endpoint_receipt": "not observed",
            "path": "authenticated input_boolean.turn_on -> real template binary_sensor -> bound switch-mode control_input -> session recipients -> native send -> public reply",
            "source_context_user_id": source.context.user_id,
            "derived_context_user_id": derived.context.user_id,
            "derived_context_id": derived.context.id,
            "session_id": self.runtime.session.session_id,
            "operation_enabled": self.runtime.session.operation_enabled,
            "accumulated_targets": accumulated,
            "observed_service_calls": calls, "response": response,
            "public_reply_events": wire,
        }, sort_keys=True))
        self.assertEqual(accumulated, [references[0].reference_id])
        self.assertEqual(len(response["handoffs"]), 1)
        self.assertTrue(response["handoffs"][0]["accepted"])
        self.assertEqual([call["data"]["target"] for call in calls], [
            targets[references[0].reference_id]
        ])
        self.assertEqual(len(wire), 1)
        self.assertEqual(wire[0]["session_id"], self.runtime.session.session_id)
        self.assertEqual(wire[0]["message_id"], response["handoffs"][0]["message_id"])
        self.assertTrue(wire[0]["handoff_accepted"])
