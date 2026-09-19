"""Lesbare Zustände aus dem führenden Ablaufmodell."""
from homeassistant.components.sensor import SensorEntity

from .entity import SaunaEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([SaunaPhase(entry)])


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
            "mechanical_timer_ends_at": self.runtime.controller.mechanical_timer_ends_at.isoformat() if session else None,
            "mechanical_timer_estimated": True,
            "faults": dict(self.runtime.device.faults) if self.runtime.device else {},
            "heating_feedback": self.runtime.controller.feedback,
            "decision_reason": self.runtime.controller.last_decision.reason if self.runtime.controller.last_decision else None,
            "detection_channels": [p.value for p in self.runtime.detector.active_positions] if self.runtime.detector else [],
            "gang_duration_seconds": active.elapsed_seconds(self.runtime._clock()) if active else None,
        }
