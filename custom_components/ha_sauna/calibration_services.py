"""Kalibrierung starten und abbrechen (V31); Antworten bedienen die Sauna nicht."""

import voluptuous as vol
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .core.calibration import MODULES
from .core.notification_recipients import ActorRef
from .notifications import async_resolve_recipients
from .recognition_services import _runtime as permitted_runtime

SERVICE_START = "start_calibration"
SERVICE_CANCEL = "cancel_calibration"
START_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): str,
        vol.Optional("module"): vol.In(tuple(MODULES)),
        vol.Optional("modules"): vol.All([vol.In(tuple(MODULES))], vol.Length(min=1)),
    }
)
CANCEL_SCHEMA = vol.Schema({vol.Required("entry_id"): str})


def register(hass):
    """Register once; each call is resolved against its current entry owner."""
    if hass.services.has_service(DOMAIN, SERVICE_START):
        return

    async def start(call):
        runtime = await permitted_runtime(hass, call)
        if call.context.user_id is None:
            raise HomeAssistantError("Die Kalibrierung braucht einen startenden Benutzer")
        resolutions = await async_resolve_recipients(hass)
        try:
            return await runtime.start_calibration(
                actor=ActorRef(call.context.user_id, "ha_context"),
                module=call.data.get("module"),
                modules=call.data.get("modules"),
                resolutions=resolutions,
            )
        except ValueError as error:
            raise HomeAssistantError(str(error)) from error

    async def cancel(call):
        runtime = await permitted_runtime(hass, call)
        try:
            return await runtime.cancel_calibration(
                actor=ActorRef(call.context.user_id, "ha_context"),
            )
        except ValueError as error:
            raise HomeAssistantError(str(error)) from error

    hass.services.async_register(DOMAIN, SERVICE_START, start, schema=START_SCHEMA,
                                 supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, SERVICE_CANCEL, cancel, schema=CANCEL_SCHEMA,
                                 supports_response=SupportsResponse.OPTIONAL)
