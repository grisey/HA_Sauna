"""HA-Geräteadapter: ausgewählte Quellen, reale Dienste, getrennte Rückmeldung."""
from __future__ import annotations

import asyncio
from datetime import timedelta
from math import isfinite

from homeassistant.helpers.event import async_track_state_change_event, async_track_state_report_event
from homeassistant.components import persistent_notification

from .archive import plain
from .bindings import ROLE_BY_KEY
from .core.models import Measurement, Position, Quantity


class HADevice:
    def __init__(self, hass, runtime):
        self.hass, self.runtime = hass, runtime
        self.bindings = runtime.configuration.bindings.values
        self.values = runtime.configuration.parameters.values
        self.states = {}
        self.measurements = {}
        self.last_valid_temperature = None
        self.fault_since = {}
        self.faults = {}
        self.command = None
        self.command_at = None
        self.last_sent_at = None
        self.command_error = False
        self.light_before = None
        self.light_active = False
        self.notified = set()

    async def start(self):
        now = self.runtime._clock()
        for role, entity_id in self.bindings.items():
            state = self.hass.states.get(entity_id)
            self.ingest(role, state, state.last_reported if state else now, initial=True)
        async def changed(event):
            await self.runtime.device_input(event)
        entities = list(self.bindings.values())
        self.runtime.on_close(async_track_state_change_event(self.hass, entities, changed))
        self.runtime.on_close(async_track_state_report_event(self.hass, entities, changed))
        self.refresh(now)
        await self.send(False, now, force=True)

    def ingest(self, role, state, received_at, *, initial=False):
        self.states[role] = state
        if role in ("upper_temperature", "upper_humidity", "lower_temperature", "lower_humidity"):
            position, quantity = role.split("_", 1)
            value = None
            if state is not None:
                try:
                    value = float(state.state)
                    if not isfinite(value) or state.attributes.get("unit_of_measurement") != ROLE_BY_KEY[role].unit:
                        value = None
                    elif quantity == "humidity" and not 0 <= value <= 100:
                        value = None
                    elif quantity == "temperature" and value <= -273.15:
                        value = None
                except (ValueError, TypeError):
                    value = None
            m = Measurement(Position(position), Quantity(quantity), value,
                state.state if state else "unavailable", self.bindings[role], received_at)
            self.measurements[role] = m
            if role == "upper_temperature" and value is not None:
                self.last_valid_temperature = m
            session = self.runtime.session
            if self.runtime.archive and session and not initial:
                self.runtime.archive.append("measurement", received_at, m, session.session_id)
            if self.runtime.detector and not initial:
                self.runtime.detector.accept(m)
        elif self.runtime.archive and self.runtime.session and not initial:
            self.runtime.archive.append("source_state", received_at, {"role": role,
                "source": self.bindings[role], "state": state.state if state else None,
                "attributes": dict(state.attributes) if state else {}}, self.runtime.session.session_id)

    def physical_action(self, event):
        if event.data["entity_id"] != self.bindings["control_input"]:
            return None
        old, new = event.data.get("old_state"), event.data.get("new_state")
        if old is None or new is None or old.state in ("unknown", "unavailable") or new.state in ("unknown", "unavailable"):
            return None
        if old.state == new.state:
            return None
        if new.domain == "binary_sensor":
            return new.state == "on" if new.state in ("on", "off") else None
        # Ein ausgewähltes event-Entity liefert einen neuen Bedienimpuls.
        return not bool(self.runtime.session and self.runtime.session.operation_enabled)

    def feedback(self):
        state = self.states.get("heater_feedback")
        if state is None or state.state not in ("on", "off"):
            return None
        return state.state == "on"

    def refresh(self, now):
        controller = self.runtime.controller
        missing = [key for key in ("target_temperature_c", "sensor_timeout_seconds",
            "feedback_timeout_seconds", "fault_confirmation_seconds") if key not in self.values]
        if "heater_feedback" not in self.bindings:
            missing.append("heater_feedback")
        controller.inhibits = {"configuration_required:" + ",".join(missing)} if missing else set()
        timeout = self.values.get("sensor_timeout_seconds")
        upper = self.last_valid_temperature
        temperature = upper.value if upper is not None and timeout is not None and (now - upper.received_at).total_seconds() <= timeout else None
        # Ein kurz fehlendes Paket verwirft einen noch gültigen Messwert nicht.
        # Nach Gültigkeitsende gibt es keinen erfundenen Ersatz der unteren Höhe.
        controller.set_temperature(temperature, now)
        feedback = self.feedback()
        controller.report_heating(feedback, now)
        problems = set()
        for role, m in self.measurements.items():
            if m.value is None or timeout is None or (now - m.received_at).total_seconds() > timeout:
                self.faults[role] = "measurement_unavailable"
            else:
                self.faults.pop(role, None)
        if temperature is None:
            problems.add("regulation_temperature_unavailable")
        if feedback is None and "heater_feedback" in self.bindings:
            problems.add("heater_feedback_unavailable")
        ack = self.values.get("feedback_timeout_seconds")
        if (self.command is not None and self.command_at is not None and ack is not None
                and (now - self.command_at).total_seconds() >= ack
                and feedback is not self.command):
            # Nur tatsächlicher Befehl und Rückmeldung bestimmen diese Diagnose.
            # Die geschätzte mechanische Timerstellung hat keinerlei Steuerwirkung.
            problems.add("heater_feedback_mismatch")
        if self.command_error:
            problems.add("heater_service_unavailable")
        for key in tuple(self.fault_since):
            if key not in problems:
                del self.fault_since[key]
        for key in problems:
            self.fault_since.setdefault(key, now)
        confirmation = self.values.get("fault_confirmation_seconds")
        monitoring = bool(controller.session and controller.session.operation_enabled) or feedback is True
        if monitoring:
            controller.protection.update(key for key, since in self.fault_since.items()
                if confirmation is not None and (now - since).total_seconds() >= confirmation)
        else:
            self.fault_since.clear()
        for key in ("regulation_temperature_unavailable", "heater_feedback_unavailable", "heater_feedback_mismatch", "heater_service_unavailable"):
            self.faults.pop(key, None)
        self.faults.update({key: "confirmed" if key in controller.protection else "pending" for key in problems})
        self.faults.update({key: "confirmed" for key in controller.protection})
        if missing:
            self.faults["configuration"] = ", ".join(missing)
        else:
            self.faults.pop("configuration", None)
        if self.runtime.archive and self.runtime.archive.failure:
            self.faults["archive"] = str(self.runtime.archive.failure)
        controller.advance(now)

    async def send(self, heat, now, *, force=False):
        ack = self.values.get("feedback_timeout_seconds")
        same = self.command is heat
        matched = self.feedback() is heat
        if not force and same and (matched or ack is None or (now - self.last_sent_at).total_seconds() < ack):
            return
        # Bestätigungsfrist wird bei Wiederholungen desselben Befehls nicht neu
        # begonnen. Andernfalls könnte ein Dauerausfall nie bestätigt werden.
        if self.command is not heat or self.command_at is None:
            self.command, self.command_at = heat, now
        self.last_sent_at = now
        try:
            if ack is None:
                await self.hass.services.async_call("switch", "turn_off",
                    {"entity_id": self.bindings["heater"]}, blocking=False)
            else:
                async with asyncio.timeout(ack):
                    await self.hass.services.async_call("switch", "turn_on" if heat else "turn_off",
                        {"entity_id": self.bindings["heater"]}, blocking=True)
            self.command_error = False
            error = None
        except Exception as exc:
            self.command_error = True
            error = type(exc).__name__
        if self.runtime.archive:
            self.runtime.archive.append("command", now, {"heater": self.bindings["heater"],
                "heat": heat, "decision": plain(self.runtime.controller.last_decision),
                "service_error": error, "feedback_proves_heating": False},
                self.runtime.session.session_id if self.runtime.session else None)

    async def apply(self, now):
        await self.send(self.runtime.controller.last_decision.heat, now)
        cooling = bool(self.runtime.session and self.runtime.session.cooling
            and self.runtime.session.cooling.started_at)
        if cooling and not self.light_active:
            brightness = self.values.get("cooling_brightness_percent")
            if brightness is None:
                self.faults["cooling_light"] = "brightness_required"
            else:
                state = self.hass.states.get(self.bindings["light"])
                self.light_before = (state.state, state.attributes.get("brightness")) if state else None
                try:
                    await self.hass.services.async_call("light", "turn_on", {
                        "entity_id": self.bindings["light"], "brightness_pct": brightness}, blocking=True)
                    self.light_active = True
                    self.faults.pop("cooling_light", None)
                except Exception:
                    self.faults["cooling_light"] = "service_unavailable"
        elif not cooling and self.light_active:
            await self.restore_light()
        ends = self.runtime.controller.mechanical_timer_ends_at
        if ends:
            lead = self.values.get("mechanical_timer_warning_minutes", 0) * 60
            phase = "expired" if now >= ends else "warning" if now >= ends - timedelta(seconds=lead) else None
            key = (self.runtime.session.session_id, phase)
            if phase and key not in self.notified:
                self.notified.add(key)
                message = ("Die geschätzte Laufzeit des mechanischen Ofentimers ist abgelaufen. Bitte den Drehschalter erneut einstellen."
                    if phase == "expired" else "Der mechanische Ofentimer erreicht voraussichtlich bald sein Ende.")
                message += " Seine tatsächliche Stellung wird nicht gemessen; die Heizrückmeldung bleibt maßgeblich."
                persistent_notification.async_create(self.hass, message,
                    title="Sauna: mechanischer Ofentimer", notification_id=f"sauna_timer_{self.runtime.session.session_id}")
                if self.runtime.archive:
                    self.runtime.archive.append("notice", now, {"kind": "mechanical_timer_" + phase,
                        "estimated_ends_at": ends}, self.runtime.session.session_id)

    async def restore_light(self):
        self.light_active = False
        if self.light_before is None:
            return
        state, brightness = self.light_before
        data = {"entity_id": self.bindings["light"]}
        if state == "on" and brightness is not None:
            data["brightness"] = brightness
        try:
            await self.hass.services.async_call("light", "turn_on" if state == "on" else "turn_off", data, blocking=True)
        except Exception:
            self.faults["cooling_light"] = "restore_failed"

    async def close(self):
        await self.send(False, self.runtime._clock(), force=True)
        await self.restore_light()
