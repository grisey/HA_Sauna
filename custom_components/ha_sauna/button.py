"""Quittierung eines bestätigten zentralen Ausfalls nach Ausschalten."""
from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from .entity import SaunaEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([SaunaReset(entry)])


class SaunaReset(SaunaEntity, ButtonEntity):
    _attr_name = "Schutzabschaltung quittieren"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry):
        super().__init__(entry, "reset_protection")

    async def async_press(self):
        await self.runtime.reset_protection()
