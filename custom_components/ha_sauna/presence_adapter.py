"""Lesender HA-Präsenzadapter mit eigenem Listener und stabilen Meldungs-IDs."""
from __future__ import annotations

from datetime import UTC, datetime

from homeassistant.helpers.event import async_track_state_change_event

from .core.presence import binary_presence


class HAPresenceAdapter:
    """callback übernimmt Serialisierung und Archivierung in der Runtime.

    Geräteklasse und tatsächliche Präsenzentität wählt die Konfiguration. Der
    FP300-PIR-Ausgang ist kein Ersatz für dessen Präsenz-Ausgang.
    """

    def __init__(self, hass, callback, entity_id=None, *, clock=None):
        self.hass = hass
        self.callback = callback
        self.entity_id = entity_id
        self.clock = clock or (lambda: datetime.now(UTC))
        self._unsubscribe = None
        self._generation = 0
        self._started = False
        self._last_report_id = None

    async def start(self):
        if self._started:
            return
        self._started = True
        if not self.entity_id:
            return
        if not self.entity_id.startswith("binary_sensor."):
            self._started = False
            raise ValueError("Präsenz benötigt eine binary_sensor-Entität")
        generation = self._generation

        async def changed(event):
            if generation != self._generation or not self._started:
                return
            await self._deliver(event.data.get("new_state"), self.clock(), event.time_fired)

        self._unsubscribe = async_track_state_change_event(
            self.hass, [self.entity_id], changed,
        )
        now = self.clock()
        await self._deliver(self.hass.states.get(self.entity_id), now, now)

    async def _deliver(self, state, received_at, missing_at):
        # last_changed identifies the semantic HA transition, even after reload.
        # No timer expires an unchanged valid ON/OFF state.
        report = binary_presence(
            self.entity_id, state.state if state is not None else None,
            state.last_changed if state is not None else missing_at, received_at,
            source_ref=None if state is not None else f"{self.entity_id}:missing:{missing_at.isoformat()}",
        )
        if report.report_id == self._last_report_id:
            return
        await self.callback(report)
        self._last_report_id = report.report_id

    async def rebind(self, entity_id):
        if entity_id is not None and not entity_id.startswith("binary_sensor."):
            raise ValueError("Präsenz benötigt eine binary_sensor-Entität")
        if entity_id == self.entity_id and self._started:
            return
        old_entity = self.entity_id
        self.close()
        if old_entity and old_entity != entity_id:
            now = self.clock()
            await self.callback(binary_presence(old_entity, "source_unbound", now, now))
        self.entity_id = entity_id
        self._last_report_id = None
        await self.start()

    def close(self):
        self._generation += 1
        self._started = False
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
