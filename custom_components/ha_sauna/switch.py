"""Ein Bedienweg für den logischen Saunabetrieb, unabhängig vom Heizrelais."""

from homeassistant.components.switch import SwitchEntity

from .core.notification_recipients import ActorRef
from .entity import SaunaEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(entities_for_entry(entry))


def entities_for_entry(entry):
    return [SaunaOperation(entry)]


class SaunaOperation(SaunaEntity, SwitchEntity):
    def __init__(self, entry):
        super().__init__(entry, "operation")
        self._attr_name = "Saunabetrieb"

    @property
    def is_on(self):
        return self.runtime.controller.automatic_operation_enabled

    async def async_turn_on(self, **kwargs):
        await self.runtime.set_operation(True, actor=ActorRef(self._context.user_id, "ha_context"))

    async def async_turn_off(self, **kwargs):
        await self.runtime.set_operation(False, actor=ActorRef(self._context.user_id, "ha_context"))
