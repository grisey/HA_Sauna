"""Lesbare Zustände aus dem führenden Ablaufmodell."""

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.helpers.entity import EntityCategory

from .core.defaults import section
from .core.models import Position
from .core.moisture import current_absolute_humidity
from .entity import SaunaEntity

DISPLAY_PRECISION = section("appearance")["precision"]


async def async_setup_entry(hass, entry, async_add_entities):
    bindings = entry.runtime_data.configuration.bindings.values
    async_add_entities(
        [
            SaunaPhase(entry),
            SaunaEnergy(entry),
            *(
                SaunaAbsoluteHumidity(entry, position)
                for position in Position
                if f"{position.value}_temperature" in bindings
            ),
        ]
    )


class SaunaAbsoluteHumidity(SaunaEntity, SensorEntity):
    _attr_native_unit_of_measurement = "g/m³"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = DISPLAY_PRECISION["absolute_humidity"]

    def __init__(self, entry, position: Position):
        if not isinstance(position, Position):
            raise ValueError("position must be a Position")
        super().__init__(entry, f"absolute_humidity_{position.value}")
        self.position = position
        self._attr_name = f"Absoluter Wassergehalt {'oben' if position is Position.UPPER else 'unten'}"

    @property
    def available(self):
        return not self.runtime.closed and self.native_value is not None

    @property
    def native_value(self):
        device = self.runtime.device
        if device is None:
            return None
        value = current_absolute_humidity(
            device.measurements,
            self.position,
            self.runtime._clock(),
            self.runtime.configuration.parameters.values.get("sensor_timeout_seconds"),
        )
        return round(value, self._attr_suggested_display_precision) if value is not None else None

    @property
    def extra_state_attributes(self):
        bindings = self.runtime.configuration.bindings.values
        return {
            "temperature_source": bindings[f"{self.position.value}_temperature"],
            "humidity_source": bindings[f"{self.position.value}_humidity"],
        }


class SaunaEnergy(SaunaEntity, SensorEntity):
    _attr_name = "Energieverbrauch der Saunasitzung"
    _attr_native_unit_of_measurement = "kWh"
    _attr_device_class = SensorDeviceClass.ENERGY

    def __init__(self, entry):
        super().__init__(entry, "session_energy")

    @property
    def native_value(self):
        session = self.runtime.session
        return round(session.energy.total_kwh, DISPLAY_PRECISION["energy"]) if session else None

    @property
    def extra_state_attributes(self):
        session = self.runtime.session
        return {
            "session_id": session.session_id if session else None,
            "source": session.energy.source if session else None,
            "measured_kwh": session.energy.measured_kwh if session else 0,
            "estimated_kwh": session.energy.estimated_kwh if session else 0,
            "unobserved_seconds": session.energy.unknown_seconds if session else 0,
            "nominal_power_kw": self.runtime.configuration.parameters.values[
                "nominal_power_kw"
            ],
        }


class SaunaPhase(SaunaEntity, SensorEntity):
    def __init__(self, entry):
        super().__init__(entry, "phase")
        self._attr_name = "Betriebszustand"

    @property
    def native_value(self):
        return self.runtime.controller.phase

    @property
    def extra_state_attributes(self):
        session = self.runtime.session
        active = session.timeline.active if session else None
        return {
            "session_id": session.session_id if session else None,
            "gang_id": active.gang_id if active else None,
            "confirmation": active.confirmation if active else None,
            "gang_count": session.timeline.gang_count if session else 0,
            "heating_seconds": session.heating.elapsed_seconds if session else 0,
            "thermostat_target": self.runtime.controller.thermostat_target,
            "thermostat_restart_temperature": self.runtime.controller.thermostat_restart_temperature,
            "after_run_ends_at": session.after_run.ends_at.isoformat()
            if session and session.after_run and session.after_run.ends_at
            else None,
            "after_run_paused": bool(
                session and session.after_run and session.after_run.paused_at
            ),
            "oven_cooling_pending": bool(
                session and session.after_run and session.after_run.pending_start
            ),
            "oven_cooling_waiting_for_off": bool(
                session and session.after_run and session.after_run.ends_at is None
            ),
            "after_run_remaining_seconds": session.after_run.remaining_seconds
            if session and session.after_run and not session.after_run.pending_start
            else None,
            "faults": dict(self.runtime.device.faults) if self.runtime.device else {},
            "heating_feedback": self.runtime.controller.feedback,
            "heating_observation": dict(self.runtime.device.heating_observation)
            if self.runtime.device
            else None,
            "decision_reason": self.runtime.controller.last_decision.reason
            if self.runtime.controller.last_decision
            else None,
            "detection_channels": [
                p.value for p in self.runtime.detector.active_positions
            ]
            if self.runtime.detector
            else [],
            "gang_duration_seconds": active.elapsed_seconds(self.runtime._clock())
            if active
            else None,
        }
