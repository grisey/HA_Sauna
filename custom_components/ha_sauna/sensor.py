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
        session = self.runtime.session
        if session is None or not session.operation_enabled:
            return "aus"
        return "saunagang" if session.timeline.active else "bereit"

    @property
    def extra_state_attributes(self):
        session = self.runtime.session
        active = session.timeline.active if session else None
        return {
            "session_id": session.session_id if session else None,
            "gang_id": active.gang_id if active else None,
            "confirmation": active.confirmation if active else None,
            "gang_count": session.timeline.gang_count if session else 0,
        }
