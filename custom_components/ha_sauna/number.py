"""Parameterentitäten schreiben ausschließlich ConfigEntry.options."""
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory

from .core.parameters import DEFINITIONS, Parameters, ParameterError
from .entity import SaunaEntity
from .presentation import parameter_error


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(SaunaNumber(entry, definition) for definition in DEFINITIONS)


class SaunaNumber(SaunaEntity, NumberEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = 1000000
    _attr_native_step = 0.01

    def __init__(self, entry, definition):
        super().__init__(entry, definition.key)
        self.definition = definition
        self._attr_name = definition.label
        self._attr_native_unit_of_measurement = definition.unit
        self._attr_native_max_value = definition.maximum
        self._attr_native_min_value = definition.minimum if definition.minimum is not None else 0
        self._attr_native_step = 1 if definition.integer else 0.01

    @property
    def native_value(self):
        return self.runtime.configuration.parameters.values.get(self.definition.key)

    async def async_set_native_value(self, value):
        self.runtime.check_configuration_change()
        try:
            parameters = Parameters({
                **self.entry.options["parameters"], self.definition.key: value,
            })
        except ParameterError as error:
            raise ValueError(parameter_error(error)) from None
        self.hass.config_entries.async_update_entry(self.entry, options={
            **self.entry.options, "parameters": parameters.as_dict(),
        })
