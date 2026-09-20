"""Eigener Thermostat: Betrieb gemeinsam, Zielwert aus derselben Parameterquelle."""

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import UnitOfTemperature

from .core.parameters import BY_KEY
from .entity import SaunaEntity
from .settings import async_set_entity_parameter


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([SaunaThermostat(entry)])


class SaunaThermostat(SaunaEntity, ClimateEntity):
    _attr_name = "Thermostat"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_max_temp = BY_KEY["target_temperature_c"].maximum
    _attr_target_temperature_step = 0.1

    def __init__(self, entry):
        super().__init__(entry, "thermostat")

    @property
    def hvac_mode(self):
        return (
            HVACMode.HEAT
            if self.runtime.session and self.runtime.session.operation_enabled
            else HVACMode.OFF
        )

    @property
    def hvac_action(self):
        if self.runtime.controller.feedback is True:
            return HVACAction.HEATING
        return HVACAction.OFF if self.hvac_mode == HVACMode.OFF else HVACAction.IDLE

    @property
    def current_temperature(self):
        return self.runtime.controller.temperature

    @property
    def min_temp(self):
        return self.runtime.configuration.parameters.minimum_for("target_temperature_c")

    @property
    def target_temperature(self):
        return self.runtime.controller.target_temperature

    @property
    def extra_state_attributes(self):
        values = self.runtime.configuration.parameters.values
        return {
            "readiness_target": self.runtime.controller.readiness_target,
            "readiness_offset": values["readiness_offset_c"],
            "readiness_hysteresis": values["readiness_hysteresis_c"],
            "decision_reason": self.runtime.controller.last_decision.reason
            if self.runtime.controller.last_decision
            else None,
        }

    async def async_set_hvac_mode(self, hvac_mode):
        if hvac_mode not in self.hvac_modes:
            raise ValueError("Unbekannter Betriebsmodus")
        await self.runtime.set_operation(hvac_mode == HVACMode.HEAT)

    async def async_turn_on(self):
        await self.runtime.set_operation(True)

    async def async_turn_off(self):
        await self.runtime.set_operation(False)

    async def async_set_temperature(self, **kwargs):
        await async_set_entity_parameter(
            self.hass, self.entry, "target_temperature_c", kwargs["temperature"]
        )
