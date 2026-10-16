"""Real HA registries, device action, service calls and reply event bus.

Only the final notify service is replaced with an observing external-send sink.
The mobile registration fixture follows HA's own util contract, not production
adapter discovery through private hass.data structures.
"""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
from types import MappingProxyType, SimpleNamespace
import unittest

HA_AVAILABLE = importlib.util.find_spec("homeassistant") is not None


@unittest.skipUnless(HA_AVAILABLE, "Home Assistant ist lokal nicht installiert")
class NotificationTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        sys.path.append(str(Path(__file__).resolve().parents[1] / "integration"))
        from harness import start_hass
        from homeassistant.core import callback
        from custom_components.ha_sauna.notifications import HomeAssistantNotifications
        self.hass, self.temp = await start_hass()
        self.calls = []
        self.replies = []
        self.wire = []

        @callback
        def capture(call):
            self.calls.append(dict(call.data))

        self.hass.services.async_register("notify", "send_message", capture)
        self.hass.services.async_register("notify", "synthetic_mobile", capture)
        self.hass.bus.async_listen(
            "ha_sauna_notification_reply", lambda event: self.wire.append(dict(event.data)),
        )
        self.adapter = HomeAssistantNotifications(self.hass, "sauna", self.replies.append)
        await self.adapter.async_start()

    async def asyncTearDown(self):
        self.adapter.close()
        await self.hass.async_stop(force=True)
        self.temp.cleanup()

    def notification(self, endpoint, payload, message_id="message", session_id="session"):
        from custom_components.ha_sauna.core.notification_exchange import NotificationEnvelope
        from custom_components.ha_sauna.core.notification_recipients import TargetIdentity
        return NotificationEnvelope(
            message_id, "correlation", "sauna",
            TargetIdentity("home_assistant", endpoint), payload, session_id,
        )

    def plain(self, *, title=True):
        from homeassistant.components.notify import NotifyEntityFeature
        from homeassistant.helpers import entity_registry as er
        entity = er.async_get(self.hass).async_get_or_create("notify", "synthetic", "plain")
        self.hass.states.async_set(entity.entity_id, "unknown", {
            "supported_features": int(NotifyEntityFeature.TITLE) if title else 0,
        })
        return entity.entity_id

    async def mobile(self):
        from homeassistant.components.device_automation import (
            DeviceAutomationType, async_get_device_automation_platform,
        )
        from homeassistant.config_entries import ConfigEntry, ConfigEntryState
        # Exercise HA's platform loader; its optional dependencies are test pins.
        await async_get_device_automation_platform(
            self.hass, "mobile_app", DeviceAutomationType.ACTION,
        )
        from homeassistant.components.mobile_app import const
        from homeassistant.helpers import device_registry as dr, entity_registry as er
        entry = ConfigEntry(
            version=1, minor_version=1, domain="mobile_app", title="Synthetic phone",
            data={const.ATTR_APP_DATA: {const.ATTR_PUSH_WEBSOCKET_CHANNEL: True}},
            source="user", options={}, unique_id="synthetic-phone",
            discovery_keys=MappingProxyType({}), subentries_data=[],
            state=ConfigEntryState.LOADED,
        )
        # Register fixture ownership without executing unrelated mobile-app setup.
        self.hass.config_entries._entries[entry.entry_id] = entry
        self.hass.config.components.add("mobile_app")
        device = dr.async_get(self.hass).async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={("mobile_app", "synthetic-phone")},
        )
        self.hass.data[const.DOMAIN] = {
            const.DATA_DEVICES: {"synthetic-webhook": device},
            const.DATA_CONFIG_ENTRIES: {"synthetic-webhook": entry},
            const.DATA_NOTIFY: SimpleNamespace(
                registered_targets={"synthetic_mobile": "synthetic-webhook"},
            ),
        }
        entity = er.async_get(self.hass).async_get_or_create(
            "notify", "mobile_app", "synthetic-phone", device_id=device.id,
            config_entry=entry,
        )
        self.hass.states.async_set(entity.entity_id, "unknown", {})
        return device.id, entity.entity_id

    async def test_registered_plain_title_and_payload_rejection(self):
        from custom_components.ha_sauna.notifications import async_resolve_recipients
        endpoint = self.plain()
        resolutions = await async_resolve_recipients(self.hass)
        self.assertTrue(next(item.recipient.capability.sendable for item in resolutions
                             if item.reference.reference_id == endpoint))
        handoff = await self.adapter.handoff(self.notification(endpoint, {
            "message": "Synthetic message", "title": "Synthetic title",
        }))
        self.assertTrue(handoff.accepted)
        self.assertEqual(self.calls, [{
            "entity_id": endpoint, "message": "Synthetic message", "title": "Synthetic title",
        }])
        rejected = await self.adapter.handoff(self.notification(endpoint, {
            "message": "Synthetic", "data": {"actions": [{"action": "native"}]},
        }))
        self.assertFalse(rejected.accepted)
        self.assertEqual(len(self.calls), 1)
        no_title = self.plain(title=False)
        rejected = await self.adapter.handoff(self.notification(no_title, {
            "message": "Synthetic", "title": "Not supported",
        }))
        self.assertFalse(rejected.accepted)
        print("NOTIFICATION_EVIDENCE " + json.dumps({
            "path": "notify.send_message", "observed_calls": self.calls,
            "handoff_accepted": handoff.accepted, "unsupported_title": rejected.accepted,
        }, sort_keys=True))

    async def test_native_device_action_payload_alias_and_observational_reply(self):
        from homeassistant.core import Context
        from custom_components.ha_sauna.notifications import async_resolve_recipients
        device_id, entity_id = await self.mobile()
        resolutions = await async_resolve_recipients(self.hass)
        targets = {item.recipient.target for item in resolutions
                   if item.reference.reference_id in {device_id, entity_id}}
        self.assertEqual(len(targets), 1)
        data = {
            "actions": [{"action": "URI", "title": "Open", "uri": "https://example.invalid"},
                        {"action": "REPLY", "title": "Answer"}],
            "tag": "synthetic-tag", "custom": {"nested": [1, "unchanged"]},
        }
        envelope = self.notification(device_id, {"message": "Synthetic", "data": data})
        handoff = await self.adapter.handoff(envelope)
        self.assertTrue(handoff.accepted, handoff.evidence)
        self.assertEqual(self.calls, [{
            "target": "synthetic-webhook", "message": "Synthetic", "data": data,
        }])
        self.assertEqual(self.replies, [])
        self.hass.bus.async_fire("mobile_app_notification_action", {"action": "URI"})
        await self.hass.async_block_till_done()
        self.assertEqual(self.wire, [])
        native = {"action": "REPLY", "reply_text": "Native response", "device_id": "untrusted"}
        context = Context(user_id="synthetic-user")
        self.hass.bus.async_fire("mobile_app_notification_action", native, context=context)
        await self.hass.async_block_till_done()
        observation = self.replies[0]
        self.assertEqual(dict(observation.payload), native)
        self.assertEqual(observation.actor.user_id, "synthetic-user")
        self.assertIsNone(observation.source)
        self.assertIs(observation.notification, envelope)
        self.assertFalse(observation.ambiguous)
        print("NOTIFICATION_EVIDENCE " + json.dumps({
            "path": "mobile_app device action -> real HA service registry",
            "observed_calls": self.calls, "reply_events": self.wire,
            "handoff_accepted": handoff.accepted,
        }, sort_keys=True))

    async def test_ambiguous_unknown_and_cleanup_reply_paths(self):
        device_id, _ = await self.mobile()
        payload = {"message": "Synthetic", "data": {"actions": [{"action": "REPLY"}]}}
        for message_id in ("first", "second"):
            self.assertTrue((await self.adapter.handoff(
                self.notification(device_id, payload, message_id),
            )).accepted)
        self.hass.bus.async_fire("mobile_app_notification_action", {"action": "foreign"})
        await self.hass.async_block_till_done()
        self.assertFalse(self.replies)
        self.hass.bus.async_fire("mobile_app_notification_action", {"action": "REPLY"})
        await self.hass.async_block_till_done()
        self.assertFalse(self.replies)
        self.assertTrue(self.wire[-1]["ambiguous"])
        self.assertIsNone(self.wire[-1]["entry_id"])
        self.assertIsNone(self.wire[-1]["message_id"])
        self.assertIsNone(self.wire[-1]["actor_user_id"])
        self.adapter.forget("first")
        self.hass.bus.async_fire("mobile_app_notification_action", {"action": "REPLY"})
        await self.hass.async_block_till_done()
        self.assertEqual(self.replies[-1].notification.message_id, "second")
        self.adapter.forget_session("session")
        before = len(self.replies)
        self.hass.bus.async_fire("mobile_app_notification_action", {"action": "REPLY"})
        await self.hass.async_block_till_done()
        self.assertEqual(len(self.replies), before)
        self.adapter.close()
        self.assertFalse(self.adapter._sent)


    async def test_global_ambiguity_has_one_unknown_scope_event_and_explicit_forget(self):
        from custom_components.ha_sauna.notifications import HomeAssistantNotifications
        from dataclasses import replace
        device_id, _ = await self.mobile()
        other_replies = []
        other = HomeAssistantNotifications(self.hass, "other-sauna", other_replies.append)
        await other.async_start()
        self.addCleanup(other.close)
        self.assertIs(self.adapter._ingress, other._ingress)
        payload = {"message": "Synthetic", "data": {"actions": [{"action": "REPLY"}]}}
        first = self.notification(device_id, payload, "first", session_id=None)
        second = replace(first, entry_id="other-sauna", message_id="second")
        self.assertTrue((await self.adapter.handoff(first)).accepted)
        self.assertTrue((await other.handoff(second)).accepted)
        self.hass.bus.async_fire("mobile_app_notification_action", {
            "action": "REPLY", "reply_text": "Shared native identifier",
        })
        await self.hass.async_block_till_done()
        self.assertEqual(self.replies, [])
        self.assertEqual(other_replies, [])
        self.assertEqual(len(self.wire), 1)
        ambiguous = self.wire[0]
        for field in ("entry_id", "message_id", "correlation_id", "target", "source"):
            self.assertIsNone(ambiguous[field])
        self.assertTrue(ambiguous["ambiguous"])
        self.assertTrue(self.adapter.forget("first"))
        self.assertFalse(self.adapter.forget("first"))
        self.hass.bus.async_fire("mobile_app_notification_action", {"action": "REPLY"})
        await self.hass.async_block_till_done()
        self.assertEqual(self.replies, [])
        self.assertEqual(len(other_replies), 1)
        self.assertIs(other_replies[0].notification, second)
        self.assertEqual(self.wire[-1]["entry_id"], "other-sauna")
        other.close()
        self.hass.bus.async_fire("mobile_app_notification_action", {"action": "REPLY"})
        await self.hass.async_block_till_done()
        self.assertEqual(len(self.wire), 2)
        self.assertIn("ha_sauna_notification_reply_ingress", self.hass.data)
        self.adapter.close()
        self.assertNotIn("ha_sauna_notification_reply_ingress", self.hass.data)
        print("NOTIFICATION_EVIDENCE " + json.dumps({
            "path": "HA-wide native action matching; explicit sessionless forget",
            "observed_calls": self.calls, "reply_events": self.wire,
            "entry_callbacks": len(self.replies), "other_callbacks": len(other_replies),
        }, sort_keys=True))

    async def test_early_response_observes_pending_handoff_then_acceptance(self):
        from test_settings import async_timeout
        device_id, _ = await self.mobile()
        observed = asyncio.Event()

        def receive(observation):
            self.replies.append(observation)
            observed.set()

        self.adapter._reply_callback = receive

        async def respond_during_send(call):
            self.calls.append(dict(call.data))
            self.hass.bus.async_fire("mobile_app_notification_action", {
                "action": "REPLY", "reply_text": "Before transport returns",
            })
            await asyncio.wait_for(observed.wait(), async_timeout("service"))
            self.assertEqual(len(self.replies), 1)
            self.assertIsNone(self.replies[0].handoff_accepted)

        self.hass.services.async_register("notify", "synthetic_mobile", respond_during_send)
        payload = {"message": "Synthetic", "data": {"actions": [{"action": "REPLY"}]}}
        envelope = self.notification(device_id, payload)
        handoff = await self.adapter.handoff(envelope)
        self.assertTrue(handoff.accepted, handoff.evidence)
        self.assertIs(self.replies[0].notification, envelope)
        self.assertIsNone(self.replies[0].source)
        self.hass.bus.async_fire("mobile_app_notification_action", {"action": "REPLY"})
        await self.hass.async_block_till_done()
        self.assertTrue(self.replies[-1].handoff_accepted)
        print("NOTIFICATION_EVIDENCE " + json.dumps({
            "path": "actual mobile action service emits event before returning",
            "observed_calls": self.calls, "reply_events": self.wire,
            "handoff_accepted_after_call": handoff.accepted,
        }, sort_keys=True))

    async def test_failed_send_rolls_back_registration_after_early_observation(self):
        from homeassistant.exceptions import HomeAssistantError
        from test_settings import async_timeout
        device_id, _ = await self.mobile()
        observed = asyncio.Event()

        def receive(observation):
            self.replies.append(observation)
            observed.set()

        self.adapter._reply_callback = receive

        async def fail_during_send(call):
            self.calls.append(dict(call.data))
            self.hass.bus.async_fire("mobile_app_notification_action", {"action": "REPLY"})
            await asyncio.wait_for(observed.wait(), async_timeout("service"))
            raise HomeAssistantError("Synthetic external send failure")

        self.hass.services.async_register("notify", "synthetic_mobile", fail_during_send)
        payload = {"message": "Synthetic", "data": {"actions": [{"action": "REPLY"}]}}
        handoff = await self.adapter.handoff(self.notification(device_id, payload))
        self.assertFalse(handoff.accepted)
        self.assertEqual(len(self.replies), 1)
        self.assertIsNone(self.replies[0].handoff_accepted)
        self.assertFalse(self.adapter._sent)
        self.hass.bus.async_fire("mobile_app_notification_action", {"action": "REPLY"})
        await self.hass.async_block_till_done()
        self.assertEqual(len(self.replies), 1)
        print("NOTIFICATION_EVIDENCE " + json.dumps({
            "path": "early observation followed by failed HA call and rollback",
            "observed_calls": self.calls, "reply_events": self.wire,
            "handoff_accepted_after_call": handoff.accepted,
        }, sort_keys=True))

    async def test_navigation_only_payload_needs_no_reply_registration(self):
        device_id, _ = await self.mobile()
        payload = {"message": "Synthetic", "data": {
            "actions": [{"action": "URI", "uri": "https://example.invalid"}],
        }}
        handoff = await self.adapter.handoff(self.notification(device_id, payload))
        self.assertTrue(handoff.accepted, handoff.evidence)
        self.assertFalse(self.adapter._sent)
        self.assertEqual(self.calls[0]["data"], payload["data"])

    async def test_plain_route_does_not_load_optional_mobile_platform(self):
        from unittest.mock import AsyncMock, patch
        from homeassistant.config_entries import ConfigEntry
        from homeassistant.helpers import device_registry as dr
        from custom_components.ha_sauna.notifications import async_resolve_recipients
        endpoint = self.plain()
        with patch(
            "homeassistant.components.device_automation.async_get_device_automation_platform",
            new=AsyncMock(side_effect=AssertionError("Optional platform must stay unloaded")),
        ) as load_platform:
            resolutions = await async_resolve_recipients(self.hass)
            self.assertEqual(len(resolutions), 1)
            handoff = await self.adapter.handoff(self.notification(endpoint, {
                "message": "Plain synthetic notification",
            }))
            self.assertTrue(handoff.accepted, handoff.evidence)
            # A stored but unloaded mobile entry is not an available native route.
            entry = ConfigEntry(
                version=1, minor_version=1, domain="mobile_app", title="Unloaded phone",
                data={}, source="user", options={}, unique_id="unloaded-phone",
                discovery_keys=MappingProxyType({}), subentries_data=[],
            )
            self.hass.config_entries._entries[entry.entry_id] = entry
            dr.async_get(self.hass).async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={("mobile_app", "unloaded-phone")},
            )
            self.hass.config.components.add("mobile_app")
            resolutions = await async_resolve_recipients(self.hass)
            self.assertEqual(len(resolutions), 1)
            load_platform.assert_not_awaited()
