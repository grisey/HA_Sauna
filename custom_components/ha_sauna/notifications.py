"""Existing HA notification routes and observational Companion replies.

A completed call proves transport handoff only. Native data and action identifiers
are not rewritten; client rendering and permission remain client responsibilities.
"""

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType

from homeassistant.components.notify import NotifyEntityFeature
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .core.notification_exchange import (
    NotificationEnvelope,
    RegistrationHandle,
    ReplyAssociation,
    ReplyCorrelator,
    ReplyDisposition,
    ReplyEnvelope,
    ReplyKey,
    TransportHandoff,
)
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
    for device in dr.async_get(hass).devices:
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


class _ReplyIngress:
    """One HA-wide listener judges native identifiers across all active entries."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.adapters: set["HomeAssistantNotifications"] = set()
        self.correlator = ReplyCorrelator()
        self.unsubscribe = hass.bus.async_listen(
            "mobile_app_notification_action",
            self.receive,
        )

    @callback
    def receive(self, event: Event) -> None:
        action = event.data.get("action")
        if not isinstance(action, str):
            return
        association = self.correlator.receive(
            ReplyEnvelope(
                reply_id=None,
                message_id=None,
                correlation_id=None,
                entry_id=None,
                source=None,
                actor=ActorRef(event.context.user_id, "HA event context"),
                payload=MappingProxyType(deepcopy(dict(event.data))),
                context_id=event.context.id,
                observed_at=event.time_fired,
                reply_key=ReplyKey(TRANSPORT, action),
            )
        )
        if association.disposition is ReplyDisposition.UNKNOWN:
            return
        notification = association.notification
        reply = association.reply
        adapter = next(
            (
                item
                for item in self.adapters
                if notification is not None and item.entry_id == notification.entry_id
            ),
            None,
        )
        wire_payload = deepcopy(dict(reply.payload))
        if adapter is not None and adapter._reply_callback is not None:
            adapter._reply_callback(association)
        self.hass.bus.async_fire(
            EVENT_REPLY,
            {
                "entry_id": adapter.entry_id if adapter else None,
                "context_id": reply.context_id,
                "time_fired": reply.observed_at.isoformat(),
                "actor_user_id": reply.actor.user_id,
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
                "ambiguous": association.ambiguous,
                "handoff_accepted": association.handoff_accepted,
                "data": wire_payload,
            },
            context=event.context,
        )


@dataclass(frozen=True)
class _PreparedHandoff:
    notification: NotificationEnvelope
    ingress: _ReplyIngress | None
    registration: RegistrationHandle | None


class HomeAssistantNotifications:
    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        reply_callback: Callable[[ReplyAssociation], None] | None = None,
    ) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self._reply_callback = reply_callback
        self._ingress: _ReplyIngress | None = None
        self._closed = False

    async def async_start(self) -> None:
        self._start()

    def _start(self) -> None:
        if not self._closed and self._ingress is None:
            ingress = self.hass.data.get(_REPLY_INGRESS)
            if ingress is None:
                ingress = self.hass.data[_REPLY_INGRESS] = _ReplyIngress(self.hass)
            ingress.adapters.add(self)
            self._ingress = ingress

    async def async_stop(self) -> None:
        self.close()

    def close(self) -> None:
        """Release this entry; remove the shared listener on the last unload."""
        self._closed = True
        if self._ingress is not None:
            ingress = self._ingress
            ingress.correlator.forget_entry(self.entry_id)
            ingress.adapters.discard(self)
            self._ingress = None
            if not ingress.adapters:
                ingress.unsubscribe()
                self.hass.data.pop(_REPLY_INGRESS, None)

    def forget(self, message_id: str) -> bool:
        """Explicitly release this entry's message associations, including sessionless."""
        return bool(
            self._ingress is not None
            and self._ingress.correlator.forget_message(self.entry_id, message_id)
        )

    def forget_session(self, session_id: str) -> None:
        if self._ingress is not None:
            self._ingress.correlator.forget_session(self.entry_id, session_id)

    async def handoff(self, notification: NotificationEnvelope) -> TransportHandoff:
        """Register before dispatch; a pending reply never claims accepted handoff."""
        return await self.dispatch(self.prepare_handoff(notification))

    def prepare_handoff(
        self, notification: NotificationEnvelope, *, correlate: bool = True
    ) -> _PreparedHandoff:
        """Register at admission, before transport work can yield to lifecycle cleanup."""
        self._start()
        ingress = self._ingress
        payload = notification.payload
        data = payload.get("data", {}) if isinstance(payload, Mapping) else {}
        native_actions = data.get("actions", ()) if isinstance(data, Mapping) else ()
        actions = (
            frozenset(
                value
                for item in native_actions
                if isinstance(item, Mapping)
                for value in (item.get("action"), item.get("identifier"))
                if isinstance(value, str)
            )
            if isinstance(native_actions, (list, tuple))
            else frozenset()
        )
        registration = (
            ingress.correlator.register(
                notification,
                reply_keys=(ReplyKey(TRANSPORT, action) for action in actions),
            )
            if actions and correlate and ingress is not None
            else None
        )
        return _PreparedHandoff(notification, ingress, registration)

    async def dispatch(self, prepared: _PreparedHandoff) -> TransportHandoff:
        """Complete an admitted send without starting or registering the adapter."""
        notification = prepared.notification
        ingress = prepared.ingress
        registration = prepared.registration
        if self._closed:
            return TransportHandoff(
                notification, False, "Notification adapter is closed"
            )
        try:
            await self._send(notification)
        except BaseException as err:
            # Cancellation also removes only this in-flight registration.
            if registration is not None:
                assert ingress is not None
                ingress.correlator.rollback(registration)
            if not isinstance(err, Exception):
                raise
            return TransportHandoff(notification, False, f"{type(err).__name__}: {err}")
        if registration is not None:
            assert ingress is not None
            ingress.correlator.mark_accepted(registration)
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
            if self._closed:
                raise RuntimeError("Notification adapter is closed")
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
        if self._closed:
            raise RuntimeError("Notification adapter is closed")
        await self.hass.services.async_call(
            "notify",
            "send_message",
            service_data,
            target={"entity_id": endpoint},
            blocking=True,
        )
