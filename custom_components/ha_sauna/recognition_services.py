"""Rückmeldung zu erkannten Ereignissen für das Nachlernen."""

import voluptuous as vol
from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .core.timeline import Kind

SERVICE_FEEDBACK = "recognition_feedback"
FEEDBACK_KINDS = (Kind.DOOR_OPEN.value, Kind.DOOR_CLOSE.value, Kind.VENTILATION.value,
                  Kind.VENTILATION_COMPLETE.value, Kind.INFUSION.value)
FEEDBACK_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): str,
        vol.Required("verdict"): vol.In(("reject", "confirm")),
        vol.Optional("event_id"): vol.All(str, vol.Length(min=1)),
        vol.Optional("kind"): vol.In(FEEDBACK_KINDS),
        vol.Optional("at"): cv.datetime,
        vol.Optional("session_id"): str,
    }
)


def register(hass):
    """Register once; each call is resolved against its current entry owner."""
    if hass.services.has_service(DOMAIN, SERVICE_FEEDBACK):
        return

    async def feedback(call):
        entry_id = call.data["entry_id"]
        entry = hass.config_entries.async_get_entry(entry_id)
        runtime = getattr(entry, "runtime_data", None) if entry else None
        if entry is None or entry.domain != DOMAIN or runtime is None or runtime.closed:
            raise HomeAssistantError("Sauna-Instanz ist nicht geladen")
        if call.context.user_id is not None:
            user = await hass.auth.async_get_user(call.context.user_id)
            operation = er.async_get(hass).async_get_entity_id("switch", DOMAIN, f"{entry_id}_operation")
            if user is None or not operation or not user.permissions.check_entity(operation, POLICY_CONTROL):
                raise Unauthorized()
        learning = runtime.recognition_learning
        if learning is None or runtime.recognition_feedback is None:
            raise HomeAssistantError("Nachlernen ist nicht verfügbar")
        at = call.data.get("at")
        if at is not None and at.tzinfo is None:
            at = at.replace(tzinfo=dt_util.get_default_time_zone())     # local time of the instance
        event_id = learning.find_event(
            event_id=call.data.get("event_id"), kind=call.data.get("kind"), at=at,
            session_id=call.data.get("session_id"),
        )
        if event_id is None:
            raise HomeAssistantError("Kein passendes erkanntes Ereignis mit Lernbelegen gefunden")
        await runtime.recognition_feedback(event_id, call.data["verdict"])
        return {"event_id": event_id, "verdict": call.data["verdict"]}

    hass.services.async_register(DOMAIN, SERVICE_FEEDBACK, feedback, schema=FEEDBACK_SCHEMA,
                                 supports_response=SupportsResponse.OPTIONAL)
