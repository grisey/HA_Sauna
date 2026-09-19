"""HA-Sauna-Integration mit sicherem Aus-Start und explizitem Saunabetrieb."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from .runtime import SaunaRuntime


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry[SaunaRuntime]) -> bool:
    """Eine konfigurierte Instanz laden, ohne einen Saunabetrieb zu starten."""
    from homeassistant.exceptions import ConfigEntryError
    from .runtime import Configuration, SaunaRuntime
    from datetime import timedelta
    from homeassistant.helpers.event import async_track_time_interval

    try:
        configuration = Configuration.from_options(entry.options)
    except (ValueError, TypeError) as error:
        raise ConfigEntryError("Ungültige HA-Sauna-Konfiguration") from error
    entry.runtime_data = SaunaRuntime(configuration)
    await entry.runtime_data.start_archive(hass.config.path("ha_sauna", f"{entry.entry_id}.sqlite"), entry.entry_id)
    from .api import register
    register(hass)
    if entry.options != configuration.as_options():
        hass.config_entries.async_update_entry(entry, options=configuration.as_options())
    await hass.config_entries.async_forward_entry_setups(entry, ["number", "sensor", "switch", "climate", "button"])
    from .device import HADevice
    entry.runtime_data.device = HADevice(hass, entry.runtime_data)
    await entry.runtime_data.device.start()
    entry.runtime_data.on_close(async_track_time_interval(hass, entry.runtime_data.tick, timedelta(seconds=1)))
    async def stopped(_event):
        await entry.runtime_data.close()
    entry.runtime_data.on_close(hass.bus.async_listen_once("homeassistant_stop", stopped))
    entry.async_on_unload(entry.add_update_listener(async_options_updated))
    return True


async def async_options_updated(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry[SaunaRuntime]) -> bool:
    """Beim Entladen Ofen ausschalten, Listener lösen und Archiv abschließen."""
    if not await hass.config_entries.async_unload_platforms(entry, ["number", "sensor", "switch", "climate", "button"]):
        return False
    await entry.runtime_data.close()
    return True
