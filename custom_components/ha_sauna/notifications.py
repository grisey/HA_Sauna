"""Existing HA notification routes and observational Companion replies.

A completed call proves transport handoff only. Native data and action identifiers
are not rewritten; client rendering and permission remain client responsibilities.
"""

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any

from homeassistant.components.notify import NotifyEntityFeature
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .core.notification_exchange import NotificationEnvelope, TransportHandoff
from .core.notification_recipients import (
    ActorRef,
    CapabilityEvidence,
    RecipientCapability,
    RecipientRef,
    RecipientResolution,
    ResolvedRecipient,
    TargetIdentity,
)

TRANSPORT = "home_assistant"
EVENT_REPLY = "ha_sauna_notification_reply"
_REPLY_INGRESS = "ha_sauna_notification_reply_ingress"


async def _mobile_actions(hass: HomeAssistant) -> dict[str, dict]:
    actions = {}
    if "mobile_app" not in hass.config.components:
        return actions
    devices = []
    for device in dr.async_get(hass).devices.values():
        _, entry = dr.async_get_device_and_config_entry_for_domain(
            hass, device.id, domain="mobile_app"
        )
        if entry is not None and entry.state is ConfigEntryState.LOADED:
            devices.append(device)
    if not devices:
        return actions
    # Load the existing action through HA only for a loaded client integration.
    from homeassistant.components.device_automation import (
        DeviceAutomationType,
        InvalidDeviceAutomationConfig,
        async_get_device_automation_platform,
    )

    try:
        platform = await async_get_device_automation_platform(
            hass,
            "mobile_app",
            DeviceAutomationType.ACTION,
        )
    except InvalidDeviceAutomationConfig:
        return actions
    for device in devices:
        for action in await platform.async_get_actions(hass, device.id):
            if action.get("domain") == "mobile_app" and action.get("type") == "notify":
                actions[device.id] = action
                break
    return actions


async def async_resolve_recipients(
    hass: HomeAssistant,
) -> tuple[RecipientResolution, ...]:
    """Discover registered notify entities and currently offered mobile actions."""
    actions = await _mobile_actions(hass)
    resolutions = []
    mobile = RecipientCapability(
        send=CapabilityEvidence(
            "mobile_app device action", "message/title/native data"
        ),
        reply=CapabilityEvidence(
            "mobile_app_notification_action", "observational action event"
        ),
    )
    for device_id in sorted(actions):
        ref = RecipientRef("device", device_id, TRANSPORT)
        resolutions.append(
            RecipientResolution(
                ref,
                ResolvedRecipient(
                    ref,
                    TargetIdentity(TRANSPORT, device_id),
                    mobile,
                ),
            )
        )
    for entity in sorted(
        er.async_get(hass).entities.values(), key=lambda item: item.entity_id
    ):
        if entity.domain != "notify" or entity.disabled_by is not None:
            continue
        ref = RecipientRef("entity", entity.entity_id, TRANSPORT)
        state = hass.states.get(entity.entity_id)
        if state is None or not hass.services.has_service("notify", "send_message"):
            resolutions.append(RecipientResolution(ref))
            continue
        alias = entity.platform == "mobile_app" and entity.device_id in actions
        capability = (
            mobile
            if alias
            else RecipientCapability(
                send=CapabilityEvidence(
                    "registered notify entity",
                    "message/title when NotifyEntityFeature.TITLE",
                )
            )
        )
        target = TargetIdentity(
            TRANSPORT, entity.device_id if alias else entity.entity_id
        )
        resolutions.append(
            RecipientResolution(ref, ResolvedRecipient(ref, target, capability))
        )
    return tuple(resolutions)


@dataclass(frozen=True)
class NotificationReplyObservation:
    """Native context/time and association; no authenticated device claim.

    context_id identifies the HA context, not a unique event or delivery.
    """

    context_id: str
    time_fired: datetime
    actor: ActorRef
    payload: Mapping[str, Any]
    source: TargetIdentity | None = None
    notification: NotificationEnvelope | None = None
    ambiguous: bool = False
    handoff_accepted: bool | None = None


@dataclass
class _NotificationRegistration:
    notification: NotificationEnvelope
    actions: frozenset[str]
    accepted: bool = False


class _ReplyIngress:
    """One HA-wide listener judges native identifiers across all active entries."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.adapters: set["HomeAssistantNotifications"] = set()
        self.unsubscribe = hass.bus.async_listen(
            "mobile_app_notification_action",
            self.receive,
        )

    @callback
    def receive(self, event: Event) -> None:
        action = event.data.get("action")
        if not isinstance(action, str):
            return
        matches = [
            (adapter, registration)
            for adapter in self.adapters
            for registration in adapter._sent
            if action in registration.actions
        ]
        if not matches:
            return
        adapter, registration = matches[0] if len(matches) == 1 else (None, None)
        notification = registration.notification if registration else None
        observation = NotificationReplyObservation(
            event.context.id,
            event.time_fired,
            ActorRef(event.context.user_id, "HA event context"),
            MappingProxyType(deepcopy(dict(event.data))),
            notification=notification,
            ambiguous=len(matches) > 1,
            handoff_accepted=True if registration and registration.accepted else None,
        )
        if adapter is not None and adapter._reply_callback is not None:
            adapter._reply_callback(observation)
        self.hass.bus.async_fire(
            EVENT_REPLY,
            {
                "entry_id": adapter.entry_id if adapter else None,
                "context_id": observation.context_id,
                "time_fired": observation.time_fired.isoformat(),
                "actor_user_id": observation.actor.user_id,
                "source": None,
                "message_id": notification.message_id if notification else None,
                "correlation_id": notification.correlation_id if notification else None,
                "session_id": notification.session_id if notification else None,
                "target": (
                    {
                        "transport": notification.target.transport,
                        "endpoint": notification.target.endpoint,
                    }
                    if notification
                    else None
                ),
                "ambiguous": observation.ambiguous,
                "handoff_accepted": observation.handoff_accepted,
                "data": deepcopy(dict(observation.payload)),
            },
            context=event.context,
        )


class HomeAssistantNotifications:
    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        reply_callback: Callable[[NotificationReplyObservation], None] | None = None,
    ) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self._reply_callback = reply_callback
        self._ingress: _ReplyIngress | None = None
        self._sent: list[_NotificationRegistration] = []

    async def async_start(self) -> None:
        if self._ingress is None:
            ingress = self.hass.data.get(_REPLY_INGRESS)
            if ingress is None:
                ingress = self.hass.data[_REPLY_INGRESS] = _ReplyIngress(self.hass)
            ingress.adapters.add(self)
            self._ingress = ingress

    async def async_stop(self) -> None:
        self.close()

    def close(self) -> None:
        """Release this entry; remove the shared listener on the last unload."""
        if self._ingress is not None:
            ingress = self._ingress
            ingress.adapters.discard(self)
            self._ingress = None
            if not ingress.adapters:
                ingress.unsubscribe()
                self.hass.data.pop(_REPLY_INGRESS, None)
        self._sent.clear()

    def forget(self, message_id: str) -> bool:
        """Explicitly release this entry's message associations, including sessionless."""
        previous = len(self._sent)
        self._sent = [
            item for item in self._sent if item.notification.message_id != message_id
        ]
        return len(self._sent) != previous

    def forget_session(self, session_id: str) -> None:
        self._sent = [
            item for item in self._sent if item.notification.session_id != session_id
        ]

    async def handoff(self, notification: NotificationEnvelope) -> TransportHandoff:
        """Register before dispatch; a pending reply never claims accepted handoff."""
        payload = notification.payload
        data = payload.get("data", {}) if isinstance(payload, Mapping) else {}
        native_actions = data.get("actions", ()) if isinstance(data, Mapping) else ()
        actions = (
            frozenset(
                item["action"]
                for item in native_actions
                if isinstance(item, Mapping)
                and isinstance(item.get("action"), str)
                and item["action"] != "URI"
            )
            if isinstance(native_actions, (list, tuple))
            else frozenset()
        )
        registration = _NotificationRegistration(notification, actions)
        if actions:
            self._sent.append(registration)
        try:
            await self._send(notification)
        except BaseException as err:
            # Cancellation also removes only this in-flight registration.
            self._sent = [item for item in self._sent if item is not registration]
            if not isinstance(err, Exception):
                raise
            return TransportHandoff(notification, False, f"{type(err).__name__}: {err}")
        registration.accepted = True
        return TransportHandoff(
            notification,
            True,
            "HA blocking call completed; client receipt unknown",
        )

    async def _send(self, notification: NotificationEnvelope) -> None:
        if (
            notification.entry_id != self.entry_id
            or notification.target.transport != TRANSPORT
        ):
            raise ValueError("Notification belongs to another entry or transport")
        payload = notification.payload
        if not isinstance(payload, Mapping) or set(payload) - {
            "message",
            "title",
            "data",
        }:
            raise ValueError("Payload requires message and optional title/data")
        if not isinstance(payload.get("message"), str):
            raise ValueError("message must be a string")
        if "title" in payload and not isinstance(payload["title"], str):
            raise ValueError("title must be a string")
        if "data" in payload and not isinstance(payload["data"], Mapping):
            raise ValueError("data must be a mapping")
        endpoint = notification.target.endpoint
        actions = await _mobile_actions(self.hass)
        if endpoint in actions:
            config = {**actions[endpoint], **deepcopy(dict(payload))}
            from homeassistant.components.device_automation import (
                DeviceAutomationType,
                async_get_device_automation_platform,
            )

            platform = await async_get_device_automation_platform(
                self.hass,
                "mobile_app",
                DeviceAutomationType.ACTION,
            )
            await platform.async_call_action_from_config(self.hass, config, {}, None)
            return
        registry = er.async_get(self.hass)
        entity = registry.async_get(endpoint)
        if (
            entity is None
            or entity.domain != "notify"
            or entity.disabled_by is not None
        ):
            raise ValueError("Notify target is no longer registered or enabled")
        if "data" in payload:
            raise ValueError("notify.send_message cannot forward native data")
        state = self.hass.states.get(endpoint)
        if state is None:
            raise ValueError("Notify target has no current state")
        if "title" in payload and not (
            state.attributes.get("supported_features", 0) & NotifyEntityFeature.TITLE
        ):
            raise ValueError("Notify target does not support title")
        service_data = {key: value for key, value in payload.items() if key != "data"}
        await self.hass.services.async_call(
            "notify",
            "send_message",
            service_data,
            target={"entity_id": endpoint},
            blocking=True,
        )
