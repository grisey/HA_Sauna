"""Gemeinsame Identität und Aktualisierung der eigenen HA-Entitäten."""

from homeassistant.helpers.entity import DeviceInfo, Entity

from .const import DOMAIN


class SaunaEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry, key):
        self.entry = entry
        self.runtime = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="HA Sauna",
            model="Sessionsteuerung",
        )

    async def async_added_to_hass(self):
        self.async_on_remove(self.runtime.subscribe(self.async_write_ha_state))
