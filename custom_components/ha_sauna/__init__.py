"""Einrichtbares HA-Sauna-Grundgerüst ohne Geräteansteuerung."""
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

    try:
        configuration = Configuration.from_options(entry.options)
    except (ValueError, TypeError) as error:
        raise ConfigEntryError("Ungültige HA-Sauna-Konfiguration") from error
    entry.runtime_data = SaunaRuntime(configuration)
    await hass.config_entries.async_forward_entry_setups(entry, ["number", "sensor"])
    entry.async_on_unload(entry.add_update_listener(async_options_updated))
    return True


async def async_options_updated(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry[SaunaRuntime]) -> bool:
    """Laufzeit schließen; niemals einen Heizbefehl beim Entladen erzeugen."""
    if not await hass.config_entries.async_unload_platforms(entry, ["number", "sensor"]):
        return False
    await entry.runtime_data.close()
    return True
