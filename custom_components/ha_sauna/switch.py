"""Ein Bedienweg für den logischen Saunabetrieb, unabhängig vom Heizrelais."""
from homeassistant.components.switch import SwitchEntity

from .entity import SaunaEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([SaunaOperation(entry)])


class SaunaOperation(SaunaEntity, SwitchEntity):
    def __init__(self, entry):
        super().__init__(entry, "operation")
        self._attr_name = "Betrieb"

    @property
    def is_on(self):
        session = self.runtime.session
        return bool(session and session.operation_enabled)

    async def async_turn_on(self, **kwargs):
        await self.runtime.set_operation(True)

    async def async_turn_off(self, **kwargs):
        await self.runtime.set_operation(False)
