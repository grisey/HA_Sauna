"""HA-Geräteadapter: ausgewählte Quellen, reale Dienste, getrennte Rückmeldung."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from math import isfinite

from homeassistant.helpers.event import async_track_state_change_event, async_track_state_report_event
from homeassistant.components import persistent_notification

from .archive import plain
from .bindings import ROLE_BY_KEY
from .core.models import Measurement, Position, Quantity
from .core import power
from .presentation import configuration_message, FAULTS


class HADevice:
    def __init__(self, hass, runtime):
        self.hass, self.runtime = hass, runtime
        self.bindings = runtime.configuration.bindings.values
        self.values = runtime.configuration.parameters.values
        self.states = {}
        self.source_received_at = {}
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
        self.light_operation_enabled = False
        self.session_light_command = None
        self.notified = set()
        self.heating_observation = {"source": "unknown", "heating": None, "power_w": None}
        self._saved_heating_observation = None
        self.input_started_at = runtime._clock()
        self.last_input_event = None

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
        self.source_received_at[role] = received_at
        if initial and role == "control_input" and state is not None:
            self.last_input_event = state.state
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
            self.runtime.log.debug("measurement", "Messwert %s: %s; empfangen: %s.", role, value, received_at)
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
        if old is None or new is None or new.state in ("unknown", "unavailable"):
            return None
        if old.state == new.state:
            return None
        toggle = not bool(self.runtime.session and self.runtime.session.operation_enabled)
        if new.domain == "binary_sensor":
            if old.state in ("unknown", "unavailable"):
                return None
            if self.runtime.configuration.control_input_mode == "button":
                return toggle if old.state == "off" and new.state == "on" else None
            return new.state == "on" if new.state in ("on", "off") else None
        if new.state == self.last_input_event:
            return None
        try:
            occurred = datetime.fromisoformat(new.state)
            if occurred.tzinfo is None or occurred < self.input_started_at:
                return None
        except (ValueError, TypeError):
            return None
        self.last_input_event = new.state
        event_type = new.attributes.get("event_type")
        selected = self.runtime.configuration.button_event_type
        if selected:
            if event_type != selected:
                return None
        elif event_type is not None and event_type not in ("single_push", "single"):
            # Shelly meldet Drücken, Loslassen und die fertige Klickauswertung.
            # Nur die kurze Klickauswertung umschalten, niemals dreifach.
            return None
        return toggle

    def binary_state(self, role):
        state = self.states.get(role)
        if state is None or state.state not in ("on", "off"):
            return None
        return state.state == "on"

    def contactor_feedback(self):
        """Schützstellung bestätigt den Schaltbefehl, nicht die Ofenleistung."""
        return self.binary_state("heater")

    def power_value(self, now):
        state = self.states.get("heater_power")
        received = self.source_received_at.get("heater_power")
        ttl = self.values.get("sensor_timeout_seconds")
        if state is None or received is None or ttl is None or (now - received).total_seconds() > ttl:
            return None
        if state.attributes.get("device_class") != "power":
            return None
        return power.watts(state.state, state.attributes.get("unit_of_measurement"))

    def observe_heating(self, now):
        measured = self.power_value(now)
        active = power.heating(measured, self.values.get("power_heating_threshold_w"))
        if active is not None:
            source = "power"
        elif (self.bindings.get("heater_feedback") != self.bindings["heater"]
              and (active := self.binary_state("heater_feedback")) is not None):
            source = "independent_feedback"
        else:
            active = self.contactor_feedback()
            source = "contactor" if active is not None else "unknown"
        return {"source": source, "heating": active, "power_w": measured,
                "contactor": self.contactor_feedback(), "estimated": source == "contactor"}

    def feedback(self):
        return self.observe_heating(self.runtime._clock())["heating"]

    @property
    def missing_configuration(self):
        keys = ["target_temperature_c", "sensor_timeout_seconds",
                "feedback_timeout_seconds", "fault_confirmation_seconds"]
        if "heater_power" in self.bindings:
            keys.append("power_heating_threshold_w")
        return [key for key in keys if key not in self.values]

    def start_errors(self):
        errors = []
        if self.missing_configuration:
            errors.append(configuration_message(self.missing_configuration))
        if self.runtime.controller.temperature is None and "sensor_timeout_seconds" not in self.missing_configuration:
            errors.append(FAULTS["regulation_temperature_unavailable"])
        if self.contactor_feedback() is None:
            errors.append(FAULTS["heater_feedback_unavailable"])
        if self.runtime.controller.protection:
            errors.append("Eine Schutzabschaltung ist verriegelt. Bitte die Störung prüfen und anschließend quittieren.")
        return errors

    def measurement_status(self, now):
        timeout = self.values.get("sensor_timeout_seconds")
        result = {}
        for role, measurement in self.measurements.items():
            age = max(0, (now - measurement.received_at).total_seconds())
            state = ("unavailable" if measurement.value is None else "validity_unconfigured"
                     if timeout is None else "stale" if age > timeout else "current")
            result[role] = {"state": state, "age_seconds": age}
        return result

    def refresh(self, now):
        controller = self.runtime.controller
        missing = self.missing_configuration
        controller.inhibits = {"configuration_required:" + ",".join(missing)} if missing else set()
        timeout = self.values.get("sensor_timeout_seconds")
        upper = self.last_valid_temperature
        temperature = upper.value if upper is not None and timeout is not None and (now - upper.received_at).total_seconds() <= timeout else None
        # Ein kurz fehlendes Paket verwirft einen noch gültigen Messwert nicht.
        # Nach Gültigkeitsende gibt es keinen erfundenen Ersatz der unteren Höhe.
        controller.set_temperature(temperature, now)
        contactor = self.contactor_feedback()
        self.heating_observation = self.observe_heating(now)
        power_received = self.source_received_at.get("heater_power")
        controller.report_power(self.heating_observation["power_w"],
            power_received + timedelta(seconds=timeout) if power_received and timeout else None, now)
        controller.report_heating(self.heating_observation["heating"], now)
        observation_key = (controller.session.session_id if controller.session else None,
                           self.heating_observation["source"], self.heating_observation["heating"])
        if self.runtime.archive and observation_key != self._saved_heating_observation:
            self.runtime.archive.append("heating_observation", now,
                self.heating_observation, observation_key[0])
            self._saved_heating_observation = observation_key
        problems = set()
        for role, status in self.measurement_status(now).items():
            if status["state"] != "current":
                self.faults[role] = {"unavailable": "measurement_unavailable",
                    "stale": "measurement_stale", "validity_unconfigured": "validity_unconfigured"}[status["state"]]
            else:
                self.faults.pop(role, None)
        if temperature is None:
            problems.add("regulation_temperature_unavailable")
        if contactor is None:
            problems.add("heater_feedback_unavailable")
        for role in ("heater_power", "heater_feedback"):
            unavailable = (self.heating_observation["power_w"] is None if role == "heater_power"
                           else self.binary_state(role) is None)
            if role in self.bindings and unavailable:
                self.faults[role] = "measurement_unavailable"
            else:
                self.faults.pop(role, None)
        if contactor is True and self.heating_observation["heating"] is False:
            self.faults["heater_no_power"] = "measured"
        else:
            self.faults.pop("heater_no_power", None)
        ack = self.values.get("feedback_timeout_seconds")
        if (self.command is not None and self.command_at is not None and ack is not None
                and (now - self.command_at).total_seconds() >= ack
                and contactor is not self.command):
            # Ein fehlender Schaltvollzug ist ein Aktorfehler. Fehlende Ofenleistung
            # bei angezogenem Schütz pausiert nur den Zähler, etwa bei Ofentimer-Aus.
            # Die geschätzte mechanische Timerstellung hat keinerlei Steuerwirkung.
            problems.add("heater_feedback_mismatch")
        if (self.command is False and self.command_at is not None and ack is not None
                and (now - self.command_at).total_seconds() >= ack
                and self.heating_observation["source"] in ("power", "independent_feedback")
                and self.heating_observation["heating"] is True):
            problems.add("heater_still_heating")
        if self.command_error:
            problems.add("heater_service_unavailable")
        for key in tuple(self.fault_since):
            if key not in problems:
                del self.fault_since[key]
        for key in problems:
            self.fault_since.setdefault(key, now)
        confirmation = self.values.get("fault_confirmation_seconds")
        monitoring = bool(controller.session and controller.session.operation_enabled) or contactor is True
        if monitoring:
            controller.protection.update(key for key, since in self.fault_since.items()
                if confirmation is not None and (now - since).total_seconds() >= confirmation)
        else:
            self.fault_since.clear()
        for key in ("regulation_temperature_unavailable", "heater_feedback_unavailable", "heater_feedback_mismatch", "heater_service_unavailable", "heater_still_heating"):
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
        matched = self.contactor_feedback() is heat
        if not force and same and (matched or ack is None or (now - self.last_sent_at).total_seconds() < ack):
            return
        # Bestätigungsfrist wird bei Wiederholungen desselben Befehls nicht neu
        # begonnen. Andernfalls könnte ein Dauerausfall nie bestätigt werden.
        if self.command is not heat or self.command_at is None:
            self.command, self.command_at = heat, now
        self.last_sent_at = now
        self.runtime.log.info("heater_command", "Schaltbefehl an Heizschütz: %s.", "EIN" if heat else "AUS")
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
        self.runtime.log.change("heater_command_error", error, logging.ERROR if error else logging.INFO,
            "Ergebnis des Schaltbefehls: %s.", error or "Dienstaufruf abgeschlossen; Schützrückmeldung wird getrennt geprüft")
        if self.runtime.archive:
            self.runtime.archive.append("command", now, {"heater": self.bindings["heater"],
                "heat": heat, "decision": plain(self.runtime.controller.last_decision),
                "service_error": error, "feedback_proves_heating": False},
                self.runtime.session.session_id if self.runtime.session else None)

    async def apply(self, now):
        await self.send(self.runtime.controller.last_decision.heat, now)
        await self.apply_light(now)
        self.notify_mechanical_timer(now)

    async def apply_light(self, now):
        light_after_run = self.runtime.controller.light_after_run
        if light_after_run is not None:
            await self.apply_session_light(now, light_after_run)
            return
        self.session_light_command = None
        self.faults.pop("session_light", None)
        cooling = bool(self.runtime.session and self.runtime.session.cooling
            and self.runtime.session.cooling.started_at)
        after_run = bool(self.runtime.session and self.runtime.session.after_run)
        dim = "cooling" if cooling else "after_run" if after_run else None
        if dim and self.light_active != dim:
            brightness = self.values[f"{dim}_brightness_percent"]
            if not self.light_active:
                state = self.hass.states.get(self.bindings["light"])
                self.light_before = (state.state, state.attributes.get("brightness")) if state else None
            phase = self.runtime.session.cooling if cooling else self.runtime.session.after_run
            self.runtime.log.change("light_dim", (dim, phase.started_at), logging.INFO,
                "Saunalicht für %s auf %s %% dimmen.", "Zwangskühlung" if cooling else "Nachlauf", brightness)
            # Pro Phasenwechsel einmal versuchen. Ein Fehler darf weder jeden
            # Messzyklus blockieren noch den ursprünglichen Lichtzustand ersetzen.
            self.light_active = dim
            try:
                await self.light_call("turn_on", {
                    "entity_id": self.bindings["light"], "brightness_pct": brightness})
                self.faults.pop("cooling_light", None)
                self.faults.pop("after_run_light", None)
            except Exception:
                self.faults[f"{dim}_light"] = "service_unavailable"
        elif not dim and self.light_active:
            await self.restore_light()
        operation = bool(self.runtime.session and self.runtime.session.operation_enabled)
        if not operation:
            self.light_operation_enabled = False
            self.faults.pop("operation_light", None)
        elif not dim and not self.light_operation_enabled:
            # Nur beim Übergang in den Betrieb einschalten. Normale Messzyklen
            # und Thermostatpausen überschreiben keine manuelle Lichtbedienung.
            self.light_operation_enabled = True
            await self.start_light(now)
        light = self.states.get("light")
        if light is not None and light.state == "on":
            self.faults.pop("operation_light", None)

    def notify_mechanical_timer(self, now):
        ends = self.runtime.controller.mechanical_timer_ends_at
        if ends:
            lead = self.values.get("mechanical_timer_warning_minutes", 0) * 60
            phase = "expired" if now >= ends else "warning" if now >= ends - timedelta(seconds=lead) else None
            key = (self.runtime.controller.mechanical_timer.cycle_id, phase)
            if phase and key not in self.notified:
                self.notified.add(key)
                message = ("Die geschätzte Laufzeit des mechanischen Ofentimers ist abgelaufen. Bitte den Drehschalter erneut einstellen."
                    if phase == "expired" else "Der mechanische Ofentimer erreicht voraussichtlich bald sein Ende.")
                message += " Seine tatsächliche Stellung wird nicht gemessen; diese Erinnerung löst keine Steuerung aus."
                persistent_notification.async_create(self.hass, message,
                    title="Sauna: mechanischer Ofentimer", notification_id=f"sauna_timer_{self.runtime.session.session_id}")
                if self.runtime.archive:
                    self.runtime.archive.append("notice", now, {"kind": "mechanical_timer_" + phase,
                        "estimated_ends_at": ends}, self.runtime.session.session_id)

    async def apply_session_light(self, now, phase, *, finish=False):
        service = "turn_on" if not finish and now < phase.ends_at else "turn_off"
        key = (phase.session_id, service)
        if key == self.session_light_command:
            return
        # Ein neuer Sitzungsstart verwirft diese Frist. Keine alte Wiederherstellung
        # darf danach das Licht einer neuen Sitzung ausschalten oder aufhellen.
        self.session_light_command = key
        self.light_active = False
        self.light_before = None
        self.light_operation_enabled = False
        data = {"entity_id": self.bindings["light"]}
        if service == "turn_on":
            data["brightness_pct"] = phase.brightness_percent
        self.runtime.log.info("session_light", "Lichtnachlauf nach Sitzungsende: %s.",
            f"{phase.brightness_percent:g} % bis {phase.ends_at.isoformat()}" if service == "turn_on" else "Licht ausschalten")
        error = None
        try:
            await self.light_call(service, data)
            for fault in ("session_light", "operation_light", "cooling_light", "after_run_light"):
                self.faults.pop(fault, None)
        except Exception as exc:
            error = type(exc).__name__
            self.faults["session_light"] = service + "_failed"
        if self.runtime.archive:
            self.runtime.archive.append("light_command", now, {
                "purpose": "session_end", "service": service,
                "brightness_pct": data.get("brightness_pct"),
                "ends_at": phase.ends_at, "service_error": error}, phase.session_id)

    async def start_light(self, now):
        brightness = self.values["operation_brightness_percent"]
        self.runtime.log.info("light_command", "Lichtbefehl beim Start des Saunabetriebs: EIN mit %s %% Helligkeit.", brightness)
        error = None
        try:
            await self.light_call("turn_on", {
                "entity_id": self.bindings["light"], "brightness_pct": brightness})
            self.faults.pop("operation_light", None)
        except Exception as exc:
            error = type(exc).__name__
            self.faults["operation_light"] = "service_unavailable"
        if self.runtime.archive:
            self.runtime.archive.append("light_command", now, {
                "purpose": "operation_start", "service": "turn_on", "brightness_pct": brightness, "service_error": error},
                self.runtime.session.session_id)

    async def restore_light(self):
        if not self.light_active:
            return
        mode, self.light_active = self.light_active, False
        if self.light_before is None:
            return
        state, brightness = self.light_before
        data = {"entity_id": self.bindings["light"]}
        if state == "on" and brightness is not None:
            data["brightness"] = brightness
        try:
            await self.light_call("turn_on" if state == "on" else "turn_off", data)
        except Exception:
            self.faults[f"{mode}_light"] = "restore_failed"
        else:
            self.light_before = None
            self.faults.pop("cooling_light", None)
            self.faults.pop("after_run_light", None)

    async def light_call(self, service, data):
        # Ein nicht antwortendes Licht darf den serialisierten Regelkreis nicht
        # unbegrenzt blockieren. Dieselbe Dienstfrist gilt für alle Aktoren.
        async with asyncio.timeout(self.values["feedback_timeout_seconds"]):
            await self.hass.services.async_call("light", service, data, blocking=True)

    async def close(self):
        await self.send(False, self.runtime._clock(), force=True)
        await self.restore_light()
