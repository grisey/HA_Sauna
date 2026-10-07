"""Gemeinsame Identität und Aktualisierung der eigenen HA-Entitäten."""

from copy import deepcopy

from homeassistant.helpers.entity import DeviceInfo, Entity

from .const import DOMAIN


class SaunaEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry, key):
        self.entry = entry
        self.runtime = entry.runtime_data
        self._last_runtime_state = None
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="HA Sauna",
            model="Sessionsteuerung",
        )

    async def async_added_to_hass(self):
        self.async_on_remove(self.runtime.subscribe(self._async_write_changed_state))

    def _async_write_changed_state(self):
        # Include platform attributes (e.g. number limits and climate target)
        # and availability, not merely the displayed state string. Time-based
        # attributes continue to update whenever their exposed values change.
        state = (
            self.available, self.state, self.state_attributes,
            self.extra_state_attributes,
        )
        if state != self._last_runtime_state:
            self._last_runtime_state = deepcopy(state)
            self.async_write_ha_state()
