"""Explicit mapped-recipient notification service; no automatic sauna messages."""

from uuid import uuid4

import voluptuous as vol
from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .core.notification_exchange import NotificationEnvelope
from .core.notification_recipients import ActorRef, SessionRecipients
from .notifications import async_resolve_recipients

SERVICE_SEND = "send_notification"
SERVICE_FORGET = "forget_notification"
FORGET_SCHEMA = vol.Schema({
    vol.Required("entry_id"): str,
    vol.Required("message_id"): vol.All(str, vol.Length(min=1)),
})
SEND_SCHEMA = vol.Schema({
    vol.Required("entry_id"): str,
    vol.Required("message"): str,
    vol.Optional("title"): str,
    vol.Optional("data"): dict,
    vol.Optional("audience", default="session"): vol.In(("session", "users")),
    vol.Optional("user_ids", default=[]): [str],
})


def register(hass):
    """Register once; resolve each invocation against its current entry owner."""
    if hass.services.has_service(DOMAIN, SERVICE_SEND):
        return

    async def permitted_runtime(call):
        entry_id = call.data["entry_id"]
        entry = hass.config_entries.async_get_entry(entry_id)
        runtime = getattr(entry, "runtime_data", None) if entry else None
        if entry is None or entry.domain != DOMAIN or runtime is None or runtime.closed:
            raise HomeAssistantError("Sauna-Instanz ist nicht geladen")
        if call.context.user_id is not None:
            user = await hass.auth.async_get_user(call.context.user_id)
            operation = er.async_get(hass).async_get_entity_id(
                "switch", DOMAIN, f"{entry_id}_operation"
            )
            if user is None or not operation or not user.permissions.check_entity(
                operation, POLICY_CONTROL
            ):
                raise Unauthorized()
        return runtime

    async def forget(call):
        runtime = await permitted_runtime(call)
        async with runtime.serialized():
            runtime._require_open()
            if runtime.notification_transport is None:
                raise HomeAssistantError("Benachrichtigungstransport ist nicht verfügbar")
            runtime.notification_transport.forget(call.data["message_id"])

    async def send(call):
        runtime = await permitted_runtime(call)
        entry_id = call.data["entry_id"]
        resolutions = await async_resolve_recipients(hass)
        async with runtime.serialized():
            runtime._require_open()
            runtime.notification_resolutions = resolutions
            recipients = runtime.notification_recipients
            session_id = runtime.session.session_id if runtime.session else None
            if call.data["audience"] == "users":
                if not call.data["user_ids"]:
                    raise HomeAssistantError("user_ids ist für audience users erforderlich")
                selected = SessionRecipients(entry_id, session_id or "explicit-users")
                for user_id in call.data["user_ids"]:
                    selected = selected.accumulate(
                        ActorRef(user_id, "explicit_notification_audience"),
                        runtime.configuration.notification_assignments, resolutions,
                    )
                targets = selected.targets
            else:
                if call.data["user_ids"]:
                    raise HomeAssistantError("user_ids ist nur für audience users gültig")
                targets = recipients.targets if recipients else frozenset()
            transport = runtime.notification_transport
            if transport is None:
                raise HomeAssistantError("Benachrichtigungstransport ist nicht verfügbar")
            payload = {key: call.data[key] for key in ("message", "title", "data") if key in call.data}
            correlation_id = uuid4().hex
            results = []
            if call.data["audience"] == "users":
                selected_users = set(call.data["user_ids"])
                references = {
                    reference for assignment in runtime.configuration.notification_assignments
                    if assignment.user_id in selected_users for reference in assignment.recipients
                }
                resolved = {item.reference: item for item in resolutions}
                for reference in sorted(references, key=lambda item: (item.transport, item.reference_id)):
                    resolution = resolved.get(reference)
                    if resolution is None or resolution.recipient is None or resolution.recipient.capability.send is None:
                        results.append({
                            "reference": {"kind": reference.kind, "reference_id": reference.reference_id},
                            "accepted": False, "error": "Recipient is unresolved or has no send capability",
                            "evidence": "Current Home Assistant recipient resolution",
                        })
            for target in sorted(targets, key=lambda item: (item.transport, item.endpoint)):
                notification = NotificationEnvelope(
                    uuid4().hex, correlation_id, entry_id, target, payload, session_id,
                )
                try:
                    handoff = await transport.handoff(notification)
                    accepted, evidence = handoff.accepted, handoff.evidence
                except Exception as error:  # Continue independent mapped recipients.
                    accepted, evidence = False, f"{type(error).__name__}: {error}"
                results.append({
                    "target": {"transport": target.transport, "endpoint": target.endpoint},
                    "message_id": notification.message_id,
                    "accepted": accepted, "evidence": evidence,
                    "error": None if accepted else evidence,
                })
            return {"correlation_id": correlation_id, "handoffs": results}

    hass.services.async_register(
        DOMAIN, SERVICE_SEND, send, schema=SEND_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    hass.services.async_register(
        DOMAIN, SERVICE_FORGET, forget, schema=FORGET_SCHEMA,
    )
