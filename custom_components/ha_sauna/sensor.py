"""Lesbare Zustände aus dem führenden Ablaufmodell."""
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity

from .entity import SaunaEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([SaunaPhase(entry), SaunaEnergy(entry)])


class SaunaEnergy(SaunaEntity, SensorEntity):
    _attr_name = "Sessionenergie"
    _attr_native_unit_of_measurement = "kWh"
    _attr_device_class = SensorDeviceClass.ENERGY

    def __init__(self, entry):
        super().__init__(entry, "session_energy")

    @property
    def native_value(self):
        session = self.runtime.session
        return round(session.energy.total_kwh, 4) if session else None

    @property
    def extra_state_attributes(self):
        session = self.runtime.session
        return {"session_id": session.session_id if session else None,
            "source": session.energy.source if session else None,
            "measured_kwh": session.energy.measured_kwh if session else 0,
            "estimated_kwh": session.energy.estimated_kwh if session else 0,
            "unobserved_seconds": session.energy.unknown_seconds if session else 0,
            "nominal_power_kw": self.runtime.configuration.parameters.values["nominal_power_kw"]}


class SaunaPhase(SaunaEntity, SensorEntity):
    def __init__(self, entry):
        super().__init__(entry, "phase")
        self._attr_name = "Phase"

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
            "heating_limit_seconds": self.runtime.controller.heating_limit_seconds,
            "readiness_target": self.runtime.controller.readiness_target,
            "after_run_ends_at": session.after_run.ends_at.isoformat() if session and session.after_run else None,
            "cooling_ends_at": session.cooling.ends_at.isoformat() if session and session.cooling and session.cooling.ends_at else None,
            "cooling_wait_until": self.runtime.controller.cooling_wait_until.isoformat() if self.runtime.controller.cooling_wait_until else None,
            "mechanical_timer_ends_at": self.runtime.controller.mechanical_timer_ends_at.isoformat() if session else None,
            "mechanical_timer_estimated": True,
            "faults": dict(self.runtime.device.faults) if self.runtime.device else {},
            "heating_feedback": self.runtime.controller.feedback,
            "heating_observation": dict(self.runtime.device.heating_observation) if self.runtime.device else None,
            "decision_reason": self.runtime.controller.last_decision.reason if self.runtime.controller.last_decision else None,
            "detection_channels": [p.value for p in self.runtime.detector.active_positions] if self.runtime.detector else [],
            "gang_duration_seconds": active.elapsed_seconds(self.runtime._clock()) if active else None,
        }
