"""Live temperature choices; permanent settings have one owning HA-Sauna form."""

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .core.configuration_model import temperature_control
from .core.parameters import BY_KEY, LIVE_TEMPERATURE_KEYS
from .entity import SaunaEntity
from .settings import async_set_entity_parameter


async def async_setup_entry(hass, entry, async_add_entities):
    """Remove former permanent-setting numbers and preserve live operating choices."""
    registry = er.async_get(hass)
    live_ids = {f"{entry.entry_id}_{key}" for key in LIVE_TEMPERATURE_KEYS}
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            entity.platform == DOMAIN and entity.entity_id.startswith("number.")
            and entity.unique_id not in live_ids
        ):
            registry.async_remove(entity.entity_id)
    async_add_entities(entities_for_entry(entry))


def entities_for_entry(entry):
    return [SaunaNumber(entry, BY_KEY[key]) for key in sorted(LIVE_TEMPERATURE_KEYS)]


class SaunaNumber(SaunaEntity, NumberEntity):
    """Operate the current temperature program without a second standard editor."""

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
    def available(self):
        return super().available and self.runtime.configuration_model.parameter_applicable(
            self.definition
        )

    @property
    def native_value(self):
        if not self.available:
            return None
        if self.definition.key == "target_temperature_c":
            return temperature_control(self.runtime.controller).value
        return self.runtime.configuration.parameters.values[self.definition.key]

    @property
    def extra_state_attributes(self):
        if self.definition.key != "target_temperature_c":
            return None
        target = temperature_control(self.runtime.controller)
        return {
            "target_scope": target.scope,
            "current_target_temperature": target.current_target,
            "next_gang_temperature": target.next_target,
        }

    async def async_set_native_value(self, value):
        await async_set_entity_parameter(self.hass, self.entry, self.definition.key, value)
