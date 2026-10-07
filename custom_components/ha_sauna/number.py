"""Parameterentitäten schreiben ausschließlich ConfigEntry.options."""

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory
from homeassistant.exceptions import Unauthorized

from .core.parameters import EDITABLE_DEFINITIONS, LIVE_TEMPERATURE_KEYS
from .entity import SaunaEntity
from .settings import async_set_entity_parameter


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        SaunaNumber(entry, definition) for definition in EDITABLE_DEFINITIONS
    )


class SaunaNumber(SaunaEntity, NumberEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX

    def __init__(self, entry, definition):
        super().__init__(entry, definition.key)
        self.definition = definition
        self._attr_name = definition.label
        self._attr_native_unit_of_measurement = definition.unit
        self._attr_native_max_value = definition.maximum
        self._attr_native_step = definition.number_step

    @property
    def native_min_value(self):
        return self.runtime.configuration.parameters.minimum_for(self.definition.key)

    @property
    def native_value(self):
        if self.definition.key == "target_temperature_c":
            return self.runtime.controller.target_temperature
        return self.runtime.configuration.parameters.values.get(self.definition.key)

    async def async_set_native_value(self, value):
        context = self._context
        if self.definition.key not in LIVE_TEMPERATURE_KEYS and context and context.user_id:
            user = await self.hass.auth.async_get_user(context.user_id)
            if user is None or not user.is_admin:
                raise Unauthorized(context=context, entity_id=self.entity_id)
        await async_set_entity_parameter(
            self.hass, self.entry, self.definition.key, value
        )
