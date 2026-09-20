"""HA-Geräteadapter: ausgewählte Quellen, reale Dienste, getrennte Rückmeldung."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from math import isfinite

from homeassistant.components import persistent_notification
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_state_report_event,
)

from .archive import plain
from .bindings import ROLE_BY_KEY
from .core import power
from .core.light import normal_brightness, phase_target
from .core.light_output import LightOutput
from .core.models import Measurement, Position, Quantity
from .core.timeline import Door
from .core.warmup import WarmupTrend
from .presentation import FAULTS, configuration_message


class HADevice:
    def __init__(self, hass, runtime):
        self.hass, self.runtime = hass, runtime
        self.bindings = runtime.configuration.bindings.values
        self.values = runtime.configuration.parameters.values
        self.states = {}
        self.source_received_at = {}
        self.measurements = {}
        self.last_valid_temperature = None
        self.warmup = WarmupTrend(self.values["warmup_estimation_minutes"] * 60)
        self._warmup_key = None
        self._warmup_started_at = None
        self.fault_since = {}
        self.faults = {}
        self.command = None
        self.command_at = None
        self.last_sent_at = None
        self.command_error = False
        self.light_output = LightOutput(self.runtime.configuration.parameters)
        self._light_last_command_key = None
        self._expected_light_changes = []
        self._light_session_off_completed_key = None
        self._light_session_off_superseded_key = None
        self._light_override_dirty = False
        self.notified = set()
        self.heating_observation = {
            "source": "unknown",
            "heating": None,
            "power_w": None,
        }
        self._saved_heating_observation = None
        self.input_started_at = runtime._clock()
        self.last_input_event = None
        self.last_input_occurred_at = None
        self._button_hold_session_id = None

    async def start(self):
        now = self.runtime._clock()
        for role, entity_id in self.bindings.items():
            state = self.hass.states.get(entity_id)
            self.ingest(
                role, state, state.last_reported if state else now, initial=True
            )

        async def changed(event):
            await self.runtime.device_input(event)

        entities = list(self.bindings.values())
        self.runtime.on_close(
            async_track_state_change_event(self.hass, entities, changed)
        )
        self.runtime.on_close(
            async_track_state_report_event(self.hass, entities, changed)
        )
        self.refresh(now)
        await self.send(False, now, force=True)

    def ingest(self, role, state, received_at, *, initial=False):
        self.states[role] = state
        self.source_received_at[role] = received_at
        if initial and role == "control_input" and state is not None:
            self.last_input_event = state.state
        if role in (
            "upper_temperature",
            "upper_humidity",
            "lower_temperature",
            "lower_humidity",
        ):
            position, quantity = role.split("_", 1)
            value = None
            if state is not None:
                try:
                    value = float(state.state)
                    if (
                        not isfinite(value)
                        or state.attributes.get("unit_of_measurement")
                        != ROLE_BY_KEY[role].unit
                    ):
                        value = None
                    elif quantity == "humidity" and not 0 <= value <= 100:
                        value = None
                    elif quantity == "temperature" and value <= -273.15:
                        value = None
                except (ValueError, TypeError):
                    value = None
            m = Measurement(
                Position(position),
                Quantity(quantity),
                value,
                state.state if state else "unavailable",
                self.bindings[role],
                received_at,
            )
            self.measurements[role] = m
            self.runtime.log.debug(
                "measurement",
                "Messwert %s: %s; empfangen: %s.",
                role,
                value,
                received_at,
            )
            if role == "upper_temperature" and value is not None:
                self.last_valid_temperature = m
            session = self.runtime.session
            if self.runtime.archive and session and not initial:
                self.runtime.archive.append(
                    "measurement", received_at, m, session.session_id
                )
            if self.runtime.detector and not initial:
                self.runtime.detector.accept(m)
        elif self.runtime.archive and self.runtime.session and not initial:
            self.runtime.archive.append(
                "source_state",
                received_at,
                {
                    "role": role,
                    "source": self.bindings[role],
                    "state": state.state if state else None,
                    "attributes": dict(state.attributes) if state else {},
                },
                self.runtime.session.session_id,
            )

    def physical_action(self, event):
        if event.data["entity_id"] != self.bindings["control_input"]:
            return None
        old, new = event.data.get("old_state"), event.data.get("new_state")
        if old is None or new is None or new.state in ("unknown", "unavailable"):
            return None
        if old.state == new.state:
            return None
        if new.domain == "binary_sensor":
            if old.state in ("unknown", "unavailable"):
                return None
            if self.runtime.configuration.control_input_mode == "button":
                if old.state == "off" and new.state == "on":
                    return "on"
                if old.state == "on" and new.state == "off":
                    return "off"
                return None
            return new.state == "on" if new.state in ("on", "off") else None
        try:
            occurred = datetime.fromisoformat(new.state)
            if (
                occurred.tzinfo is None
                or occurred < self.input_started_at
                or (
                    self.last_input_occurred_at is not None
                    and occurred <= self.last_input_occurred_at
                )
            ):
                return None
        except (ValueError, TypeError):
            return None
        if new.state == self.last_input_event:
            return None
        self.last_input_event = new.state
        self.last_input_occurred_at = occurred
        event_type = new.attributes.get("event_type")
        if self.runtime.configuration.control_input_mode == "button":
            native = {
                "btn_down": "press",
                "btn_up": "release",
                "single_push": "short",
                "single": "short",
                "double_push": "double",
                "double": "double",
                "triple_push": "triple",
                "triple": "triple",
                "long_push": "long",
            }.get(event_type)
            if native is not None:
                return native
            selected = self.runtime.configuration.button_event_type
            return "short" if selected and event_type == selected else None
        selected = self.runtime.configuration.button_event_type
        if selected:
            if event_type != selected:
                return None
        elif event_type is not None and event_type not in ("single_push", "single"):
            # Shelly meldet Drücken, Loslassen und die fertige Klickauswertung.
            # Nur die kurze Klickauswertung umschalten, niemals dreifach.
            return None
        return not bool(self.runtime.session and self.runtime.session.operation_enabled)

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
        if (
            state is None
            or received is None
            or ttl is None
            or (now - received).total_seconds() > ttl
        ):
            return None
        if state.attributes.get("device_class") != "power":
            return None
        return power.watts(state.state, state.attributes.get("unit_of_measurement"))

    def observe_heating(self, now):
        measured = self.power_value(now)
        active = power.heating(measured, self.values.get("power_heating_threshold_w"))
        if active is not None:
            source = "power"
        elif (
            self.bindings.get("heater_feedback") != self.bindings["heater"]
            and (active := self.binary_state("heater_feedback")) is not None
        ):
            source = "independent_feedback"
        else:
            active = self.contactor_feedback()
            source = "contactor" if active is not None else "unknown"
        return {
            "source": source,
            "heating": active,
            "power_w": measured,
            "contactor": self.contactor_feedback(),
            "estimated": source == "contactor",
        }

    def feedback(self):
        return self.observe_heating(self.runtime._clock())["heating"]

    def _refresh_warmup(self, now, upper):
        """Keep a trend only during one uninterrupted, confirmed heating run."""
        controller = self.runtime.controller
        session = controller.session
        eligible = (
            session is not None
            and session.operation_enabled
            and controller.phase == "aufheizen"
            and self.heating_observation["heating"] is True
            and not controller.protection
            and not controller.inhibits
            and session.timeline.door != Door.OPEN
            and upper is not None
            and upper.value is not None
        )
        if not eligible:
            self.warmup.reset()
            self._warmup_key = None
            self._warmup_started_at = None
            return
        key = (session.session_id, self.heating_observation["source"])
        if key != self._warmup_key:
            self.warmup.reset()
            self._warmup_key = key
            self._warmup_started_at = now
        # A value received before the confirmed heating stretch is a useful
        # current controller input, but not evidence about this warm-up rate.
        if upper.received_at >= self._warmup_started_at:
            self.warmup.accept(upper.received_at, upper.value)

    def temperature_rate(self, now):
        """Return an ETA-safe rate, never a control input."""
        controller = self.runtime.controller
        session = controller.session
        if (
            session is None
            or not session.operation_enabled
            or controller.phase != "aufheizen"
            or self.heating_observation["heating"] is not True
            or controller.protection
            or controller.inhibits
            or controller.temperature is None
            or session.timeline.door == Door.OPEN
        ):
            return None
        timeout = self.values.get("sensor_timeout_seconds")
        return self.warmup.rate(now, maximum_age_seconds=timeout)

    @property
    def missing_configuration(self):
        keys = [
            "target_temperature_c",
            "sensor_timeout_seconds",
            "feedback_timeout_seconds",
            "fault_confirmation_seconds",
        ]
        if "heater_power" in self.bindings:
            keys.append("power_heating_threshold_w")
        return [key for key in keys if key not in self.values]

    def start_errors(self):
        errors = []
        if self.missing_configuration:
            errors.append(configuration_message(self.missing_configuration))
        if (
            self.runtime.controller.temperature is None
            and "sensor_timeout_seconds" not in self.missing_configuration
        ):
            errors.append(FAULTS["regulation_temperature_unavailable"])
        if self.contactor_feedback() is None:
            errors.append(FAULTS["heater_feedback_unavailable"])
        if self.runtime.controller.protection:
            errors.append(
                "Eine Schutzabschaltung ist verriegelt. Bitte die Störung prüfen und anschließend quittieren."
            )
        return errors

    def measurement_status(self, now):
        timeout = self.values.get("sensor_timeout_seconds")
        result = {}
        for role, measurement in self.measurements.items():
            age = max(0, (now - measurement.received_at).total_seconds())
            state = (
                "unavailable"
                if measurement.value is None
                else "validity_unconfigured"
                if timeout is None
                else "stale"
                if age > timeout
                else "current"
            )
            result[role] = {"state": state, "age_seconds": age}
        return result

    def refresh(self, now):
        controller = self.runtime.controller
        missing = self.missing_configuration
        controller.inhibits = (
            {"configuration_required:" + ",".join(missing)} if missing else set()
        )
        timeout = self.values.get("sensor_timeout_seconds")
        upper = self.last_valid_temperature
        temperature = (
            upper.value
            if upper is not None
            and timeout is not None
            and (now - upper.received_at).total_seconds() <= timeout
            else None
        )
        # Ein kurz fehlendes Paket verwirft einen noch gültigen Messwert nicht.
        # Nach Gültigkeitsende gibt es keinen erfundenen Ersatz der unteren Höhe.
        controller.set_temperature(temperature, now)
        contactor = self.contactor_feedback()
        controller.report_contactor(contactor, now)
        self.heating_observation = self.observe_heating(now)
        power_received = self.source_received_at.get("heater_power")
        controller.report_power(
            self.heating_observation["power_w"],
            power_received + timedelta(seconds=timeout)
            if power_received and timeout
            else None,
            now,
        )
        controller.report_heating(self.heating_observation["heating"], now)
        observation_key = (
            controller.session.session_id if controller.session else None,
            self.heating_observation["source"],
            self.heating_observation["heating"],
        )
        if self.runtime.archive and observation_key != self._saved_heating_observation:
            self.runtime.archive.append(
                "heating_observation", now, self.heating_observation, observation_key[0]
            )
            self._saved_heating_observation = observation_key
        problems = set()
        for role, status in self.measurement_status(now).items():
            if status["state"] != "current":
                self.faults[role] = {
                    "unavailable": "measurement_unavailable",
                    "stale": "measurement_stale",
                    "validity_unconfigured": "validity_unconfigured",
                }[status["state"]]
            else:
                self.faults.pop(role, None)
        if temperature is None:
            problems.add("regulation_temperature_unavailable")
        if contactor is None:
            problems.add("heater_feedback_unavailable")
        for role in ("heater_power", "heater_feedback"):
            unavailable = (
                self.heating_observation["power_w"] is None
                if role == "heater_power"
                else self.binary_state(role) is None
            )
            if role in self.bindings and unavailable:
                self.faults[role] = "measurement_unavailable"
            else:
                self.faults.pop(role, None)
        if contactor is True and self.heating_observation["heating"] is False:
            self.faults["heater_no_power"] = "measured"
        else:
            self.faults.pop("heater_no_power", None)
        ack = self.values.get("feedback_timeout_seconds")
        if (
            self.command is not None
            and self.command_at is not None
            and ack is not None
            and (now - self.command_at).total_seconds() >= ack
            and contactor is not self.command
        ):
            # Ein fehlender Schaltvollzug ist ein Aktorfehler. Fehlende Ofenleistung
            # bei angezogenem Schütz pausiert nur den Zähler, etwa bei Ofentimer-Aus.
            # Die geschätzte mechanische Timerstellung hat keinerlei Steuerwirkung.
            problems.add("heater_feedback_mismatch")
        if (
            self.command is False
            and self.command_at is not None
            and ack is not None
            and (now - self.command_at).total_seconds() >= ack
            and self.heating_observation["source"] in ("power", "independent_feedback")
            and self.heating_observation["heating"] is True
        ):
            problems.add("heater_still_heating")
        if self.command_error:
            problems.add("heater_service_unavailable")
        for key in tuple(self.fault_since):
            if key not in problems:
                del self.fault_since[key]
        for key in problems:
            self.fault_since.setdefault(key, now)
        confirmation = self.values.get("fault_confirmation_seconds")
        monitoring = (
            bool(controller.session and controller.session.operation_enabled)
            or contactor is True
        )
        if monitoring:
            controller.protection.update(
                key
                for key, since in self.fault_since.items()
                if confirmation is not None
                and (now - since).total_seconds() >= confirmation
            )
        else:
            self.fault_since.clear()
        for key in (
            "regulation_temperature_unavailable",
            "heater_feedback_unavailable",
            "heater_feedback_mismatch",
            "heater_service_unavailable",
            "heater_still_heating",
        ):
            self.faults.pop(key, None)
        self.faults.update(
            {
                key: "confirmed" if key in controller.protection else "pending"
                for key in problems
            }
        )
        self.faults.update({key: "confirmed" for key in controller.protection})
        if missing:
            self.faults["configuration"] = ", ".join(missing)
        else:
            self.faults.pop("configuration", None)
        if self.runtime.archive and self.runtime.archive.failure:
            self.faults["archive"] = str(self.runtime.archive.failure)
        self._refresh_warmup(now, upper if temperature is not None else None)
        controller.advance(now)

    async def send(self, heat, now, *, force=False):
        ack = self.values.get("feedback_timeout_seconds")
        same = self.command is heat
        matched = self.contactor_feedback() is heat
        if (
            not force
            and same
            and (
                matched
                or ack is None
                or (now - self.last_sent_at).total_seconds() < ack
            )
        ):
            return
        # Bestätigungsfrist wird bei Wiederholungen desselben Befehls nicht neu
        # begonnen. Andernfalls könnte ein Dauerausfall nie bestätigt werden.
        if self.command is not heat or self.command_at is None:
            self.command, self.command_at = heat, now
        self.last_sent_at = now
        self.runtime.log.info(
            "heater_command",
            "Schaltbefehl an Heizschütz: %s.",
            "EIN" if heat else "AUS",
        )
        try:
            if ack is None:
                await self.hass.services.async_call(
                    "switch",
                    "turn_off",
                    {"entity_id": self.bindings["heater"]},
                    blocking=False,
                )
            else:
                async with asyncio.timeout(ack):
                    await self.hass.services.async_call(
                        "switch",
                        "turn_on" if heat else "turn_off",
                        {"entity_id": self.bindings["heater"]},
                        blocking=True,
                    )
            self.command_error = False
            error = None
        except Exception as exc:
            self.command_error = True
            error = type(exc).__name__
        self.runtime.log.change(
            "heater_command_error",
            error,
            logging.ERROR if error else logging.INFO,
            "Ergebnis des Schaltbefehls: %s.",
            error
            or "Dienstaufruf abgeschlossen; Schützrückmeldung wird getrennt geprüft",
        )
        if self.runtime.archive:
            self.runtime.archive.append(
                "command",
                now,
                {
                    "heater": self.bindings["heater"],
                    "heat": heat,
                    "decision": plain(self.runtime.controller.last_decision),
                    "service_error": error,
                    "feedback_proves_heating": False,
                },
                self.runtime.session.session_id if self.runtime.session else None,
            )

    async def apply(self, now):
        await self.send(self.runtime.controller.last_decision.heat, now)
        await self.apply_light(now)
        self.notify_mechanical_timer(now)

    async def apply_light(self, now):
        # Optionen können außerhalb einer Sitzung ersetzt werden, ohne dass der
        # Planer dabei seinen laufenden Übergang oder Override verliert.
        self.light_output.parameters = self.runtime.configuration.parameters
        if self.runtime.controller.control_mode == "manual":
            self.light_output.keep_manual()
        if (
            self.runtime.controller.control_mode == "automatic"
            and self.light_output.expire_manual(now)
        ):
            self.runtime.log.info(
                "light_override_expired",
                "Manuelle Lichtwahl abgelaufen; Licht folgt wieder der Automatik.",
            )
            self._light_override_dirty = True
        if self._button_hold_session_id is not None:
            await self.show_button_hold_light(now, self._button_hold_session_id)
            return
        if not await self._finish_expired_session_light(now):
            return
        light_after_run = self.runtime.controller.light_after_run
        if (
            self.runtime.controller.control_mode == "manual"
            and (light_after_run is None or now >= light_after_run.ends_at)
            and self.light_output.manual_brightness is None
        ):
            return
        phase = self._light_phase(now)
        key, name, ends_at = phase
        after_run = (
            self.runtime.controller.session.after_run
            if self.runtime.controller.session
            else None
        )
        phase_paused = (
            name == "nachlauf"
            and after_run is not None
            and after_run.paused_at is not None
        )
        state = self.hass.states.get(self.bindings["light"])
        actual = self._light_brightness(state)
        if name == "aus":
            # Außerhalb der Sitzung ist die automatische Grundlage AUS. Das
            # verhindert, dass ein gerade abgelaufener 50-%-Nachlauf beim
            # Zurückkehren von einer manuellen Raumlichtwahl wieder auftaucht.
            target = 0.0
        elif name == "session_light":
            target = 0.0
        else:
            normal = self.normal_light_brightness()
            target = phase_target(
                name,
                self.runtime.controller.temperature,
                self.runtime.controller.readiness_target,
                self.runtime.configuration.parameters,
                normal,
            )
        plan = self.light_output.update(
            now,
            key,
            name,
            target,
            actual,
            ends_at,
            phase_paused=phase_paused,
            phase_brightness_percent=(
                self.runtime.controller.light_after_run.brightness_percent
                if name == "session_light"
                else None
            ),
        )
        # Der Plan enthält Fließkommawerte, HA erhält einen stabilen Prozentwert.
        brightness = round(plan.brightness_percent, 2)
        service = "turn_off" if brightness <= 0 else "turn_on"
        command_key = (key, service, brightness if service == "turn_on" else None)
        desired = self._light_command_signature(service, brightness)
        already_sent = self._light_state_signature(state) == desired and (
            command_key == self._light_last_command_key or service == "turn_on"
        )
        if already_sent or (
            name == "aus" and plan.automatic and not self._light_override_dirty
        ):
            return
        if await self._send_light_command(
            now,
            key=command_key,
            phase=name,
            service=service,
            brightness=brightness if service == "turn_on" else None,
            session_id=self._light_session_id(),
        ):
            self._light_override_dirty = False

    async def _finish_expired_session_light(self, now):
        """Sendet das fällige Timer-AUS bis es erfolgreich quittiert ist."""
        light_after_run = self.runtime.controller.light_after_run
        if light_after_run is None or now < light_after_run.ends_at:
            return True
        key = ("session_light", light_after_run.session_id, light_after_run.started_at)
        if key in (
            self._light_session_off_completed_key,
            self._light_session_off_superseded_key,
        ):
            return True
        if not await self._send_light_command(
            now,
            key=(key, "turn_off", None),
            phase="session_light",
            service="turn_off",
            brightness=None,
            session_id=light_after_run.session_id,
            ends_at=light_after_run.ends_at,
        ):
            return False
        self._light_session_off_completed_key = key
        return True

    def _light_session_id(self):
        session = self.runtime.controller.session
        light_after_run = self.runtime.controller.light_after_run
        return (
            session.session_id
            if session is not None
            else (light_after_run.session_id if light_after_run is not None else None)
        )

    async def finish_session_light(self, now, phase, *, purpose="light_reassignment"):
        """Beendet den bisherigen Lichtnachlauf vor einem Lichtwechsel.

        Der Aufrufer ersetzt anschließend die Gerätezuordnung. Der Dienstaufruf
        bleibt deshalb beim alten Adapter und wird wie jeder andere Lichtbefehl
        protokolliert.
        """
        key = ("session_light", phase.session_id, phase.started_at)
        return await self._send_light_command(
            now,
            key=(key, "turn_off", None),
            phase="session_light",
            service="turn_off",
            brightness=None,
            session_id=phase.session_id,
            ends_at=phase.ends_at,
            purpose=purpose,
        )

    async def _send_light_command(
        self,
        now,
        *,
        key,
        phase,
        service,
        brightness,
        session_id,
        ends_at=None,
        purpose=None,
    ):
        """Führt einen geplanten Lichtdienst aus und hält Ergebnis und Archiv zusammen.

        Nur ein erfolgreich abgeschlossener Dienstaufruf wird als deduplizierbar
        gespeichert. Ein Fehler bleibt somit im nächsten Regelzyklus erneut
        ausführbar; der Lichtzustand ist keine Rückmeldung über den Dienst.
        """
        fault = self._light_fault(phase)
        data = {"entity_id": self.bindings["light"]}
        if service == "turn_on":
            data["brightness_pct"] = brightness
        state_before = self.hass.states.get(self.bindings["light"])
        error = None
        try:
            await self.light_call(service, data)
        except Exception as exc:
            error = type(exc).__name__
            self.faults[fault] = "service_unavailable"
            self.runtime.log.change(
                "light_command_error",
                (phase, service, error),
                logging.ERROR,
                "Lichtdienst für %s (%s) fehlgeschlagen: %s.",
                phase,
                service,
                error,
            )
        else:
            self._light_last_command_key = key
            if self._light_state_signature(
                state_before
            ) != self._light_command_signature(service, brightness):
                self._expect_light_change(now, service, brightness)
            for known_fault in (
                "session_light",
                "operation_light",
                "after_run_light",
                "cooling_light",
            ):
                self.faults.pop(known_fault, None)
            self.runtime.log.change(
                "light_command",
                (phase, service),
                logging.INFO,
                "Lichtdienst für %s abgeschlossen: %s.",
                phase,
                service,
            )
            if service == "turn_on":
                self.runtime.log.debug(
                    "light_dimming", "Lichtwert für %s: %s %%.", phase, brightness
                )
        if self.runtime.archive:
            self.runtime.archive.append(
                "light_command",
                now,
                {
                    "purpose": purpose
                    or ("session_end" if phase == "session_light" else phase),
                    "phase": phase,
                    "service": service,
                    "brightness_pct": brightness,
                    "ends_at": ends_at,
                    "service_error": error,
                },
                session_id,
            )
        return error is None

    @staticmethod
    def _light_state_signature(state):
        """Return the user-visible on/off and brightness state, if usable."""
        if state is None or state.state not in ("on", "off"):
            return None
        if state.state == "off":
            return ("off", None)
        try:
            brightness = float(state.attributes.get("brightness"))
        except (TypeError, ValueError):
            return ("on", None)
        if not isfinite(brightness):
            return ("on", None)
        return ("on", max(0, min(255, round(brightness))))

    @staticmethod
    def _light_command_signature(service, brightness):
        if service == "turn_off":
            return ("off", None)
        value = max(0, min(255, round(brightness * 255 / 100)))
        # Home Assistant treats a turn_on command with brightness 0 as off.
        return ("on", value) if value else ("off", None)

    def _discard_expired_light_expectations(self, now):
        """Keep echo expectations only for the existing feedback interval."""
        timeout = self.values.get("feedback_timeout_seconds")
        if timeout is None:
            self._expected_light_changes.clear()
            return
        limit = timedelta(seconds=timeout)
        self._expected_light_changes[:] = [
            expected
            for expected in self._expected_light_changes
            if now - expected["sent_at"] <= limit
        ]

    def _expect_light_change(self, now, service, brightness):
        self._discard_expired_light_expectations(now)
        signature = self._light_command_signature(service, brightness)
        for expected in self._expected_light_changes:
            if expected["signature"] == signature:
                expected["sent_at"] = now
                return
        self._expected_light_changes.append(
            {
                "signature": signature,
                "sent_at": now,
            }
        )

    def external_light_selection(self, event, received_at):
        """Return one actual external light selection, excluding own echoes.

        ``state_report`` events deliberately do not represent a new choice.
        Expected automatic values are consumed only when their complete visible
        state arrives before the normal feedback timeout. Such an echo leaves
        a different physical dimmer choice intact. After that interval, state
        alone cannot distinguish a late echo from a new physical selection.
        """
        if (
            event.event_type != "state_changed"
            or event.data.get("entity_id") != self.bindings["light"]
        ):
            return None
        old, new = event.data.get("old_state"), event.data.get("new_state")
        if (
            old is None
            or new is None
            or old.state in ("unknown", "unavailable")
            or new.state in ("unknown", "unavailable")
        ):
            return None
        old_signature = self._light_state_signature(old)
        new_signature = self._light_state_signature(new)
        if new_signature is None or new_signature == old_signature:
            return None
        self._discard_expired_light_expectations(received_at)
        for index, expected in enumerate(self._expected_light_changes):
            if expected["signature"] == new_signature:
                del self._expected_light_changes[index]
                return None
        if new_signature[0] == "off":
            return False
        brightness = new_signature[1]
        return True if brightness is None else brightness * 100 / 255

    def _light_phase(self, now=None):
        """Führt Licht strikt aus dem Controllerzustand und seinen Objekten ab."""
        controller = self.runtime.controller
        light_after_run = controller.light_after_run
        if controller.control_mode == "manual" and (
            light_after_run is None
            or (now is not None and now >= light_after_run.ends_at)
        ):
            # Im manuellen Betrieb verändern Gang- und Heizphasen die explizite
            # Lichtwahl nicht. Nur der Betriebsartwechsel gibt sie wieder frei.
            return (("manual",), "manual", None)
        if light_after_run is not None:
            key = (
                "session_light",
                light_after_run.session_id,
                light_after_run.started_at,
            )
            # Das Objekt bleibt als Sitzungsnachweis erhalten, die logische
            # Lichtphase endet aber exakt mit seiner Frist. Ein noch
            # ausstehender OFF-Service wird unabhängig in ``apply_light``
            # abgewickelt, damit ein neuer manueller Befehl nicht den alten
            # ``session_light``-Schlüssel erbt.
            if now is not None and now >= light_after_run.ends_at:
                return (("aus",), "aus", None)
            return (key, "session_light", light_after_run.ends_at)
        session = controller.session
        if session is None or not session.operation_enabled:
            return (("aus",), "aus", None)
        phase = controller.phase
        if phase == "nachlauf" and session.after_run is not None:
            return (
                (session.session_id, phase, session.after_run.phase_id),
                phase,
                session.after_run.ends_at,
            )
        if phase == "zwangskühlung" and session.cooling is not None:
            return (
                (session.session_id, phase, session.cooling.cycle_id),
                phase,
                session.cooling.ends_at,
            )
        return ((session.session_id, phase), phase, None)

    @staticmethod
    def _light_brightness(state):
        if state is None or state.state != "on":
            return 0.0
        try:
            return max(
                0.0,
                min(100.0, float(state.attributes.get("brightness", 0)) * 100 / 255),
            )
        except (TypeError, ValueError):
            return 0.0

    def _sun_elevation(self):
        sun = self.hass.states.get("sun.sun")
        try:
            return float(sun.attributes["elevation"]) if sun is not None else None
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _light_fault(phase):
        return {
            "session_light": "session_light",
            "nachlauf": "after_run_light",
            "zwangskühlung": "cooling_light",
        }.get(phase, "operation_light")

    def set_light_override(self, value):
        """Manuelle Lichtwahl bis zum Rückkehrpunkt oder Fristablauf halten."""
        if value not in (None, True, False, "normal"):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
                or not 0 <= value <= 100
            ):
                raise ValueError("Lichtwert muss zwischen 0 und 100 Prozent liegen.")
        # Die Runtime ruft diese Methode vor ihrem nächsten Regelzyklus auf.
        # Deshalb muss der aktuelle Controller-Schlüssel hier mitgegeben
        # werden, statt den möglicherweise alten Planner-Schlüssel zu erben.
        now = self.runtime._clock()
        ends_at = (
            now + timedelta(minutes=self.values["manual_override_minutes"])
            if self.runtime.controller.control_mode == "automatic" and value is not None
            else None
        )
        phase_key = self._light_phase(now)[0]
        light_after_run = self.runtime.controller.light_after_run
        if (
            value is not None
            and light_after_run is not None
            and now >= light_after_run.ends_at
        ):
            # Eine neue Bedienung nach Ablauf gehört bereits zur Außenphase.
            # Sie löst den noch nicht ausgeführten Timer-AUS-Befehl ab, damit
            # der Adapter nicht erst AUS und unmittelbar danach EIN sendet.
            self._light_session_off_superseded_key = (
                "session_light",
                light_after_run.session_id,
                light_after_run.started_at,
            )
            # Der Timer ist fachlich beendet, auch wenn seine AUS-Ausgabe für
            # die neue manuelle Wahl übersprungen wird. LightOutput besitzt
            # dafür keine eigene Ablaufzeit und erhält den Grundwert hier vom
            # führenden Adapter.
            self.light_output.finish_automatic()
        if value is None:
            self.light_output.return_to_automatic()
        elif value is True:
            self.light_output.set_manual(
                self.values["session_light_brightness_percent"],
                phase_key=phase_key,
                ends_at=ends_at,
            )
        elif value is False:
            self.light_output.set_manual(0, phase_key=phase_key, ends_at=ends_at)
        elif value == "normal":
            self.light_output.set_manual(
                self.normal_light_brightness(),
                phase_key=phase_key,
                ends_at=ends_at,
            )
        else:
            self.light_output.set_manual(value, phase_key=phase_key, ends_at=ends_at)
        self._light_override_dirty = True

    def begin_button_hold_light(self, session_id):
        """Mark acknowledgement for regular output after the heater command."""
        self._button_hold_session_id = session_id
        self.light_output.return_to_automatic()

    async def show_button_hold_light(self, now, session_id):
        """Keep the required long-press acknowledgement above all normal phases."""
        self._button_hold_session_id = session_id
        brightness = self.values["button_hold_brightness_percent"]
        key = ("button_hold", session_id, "turn_on", brightness)
        if key == self._light_last_command_key:
            return True
        await self._send_light_command(
            now,
            key=key,
            phase="button_hold",
            service="turn_on",
            brightness=brightness,
            session_id=session_id,
            purpose="button_hold",
        )

    def finish_button_hold_light(self, session_id=None):
        """Only the matching release may hand light control back to the timer."""
        if session_id is None or self._button_hold_session_id == session_id:
            self._button_hold_session_id = None

    def normal_light_brightness(self):
        """Return the current normal automatic brightness for UI consumers."""
        return normal_brightness(
            self._sun_elevation(), self.runtime.configuration.parameters
        )

    def notify_mechanical_timer(self, now):
        ends = self.runtime.controller.mechanical_timer_ends_at
        if ends:
            lead = self.values.get("mechanical_timer_warning_minutes", 0) * 60
            phase = (
                "expired"
                if now >= ends
                else "warning"
                if now >= ends - timedelta(seconds=lead)
                else None
            )
            key = (self.runtime.controller.mechanical_timer.cycle_id, phase)
            if phase and key not in self.notified:
                self.notified.add(key)
                message = (
                    "Die geschätzte Laufzeit des mechanischen Ofentimers ist abgelaufen. Bitte den Drehschalter erneut einstellen."
                    if phase == "expired"
                    else "Der mechanische Ofentimer erreicht voraussichtlich bald sein Ende."
                )
                message += " Seine tatsächliche Stellung wird nicht gemessen; diese Erinnerung löst keine Steuerung aus."
                persistent_notification.async_create(
                    self.hass,
                    message,
                    title="Sauna: mechanischer Ofentimer",
                    notification_id=f"sauna_timer_{self.runtime.session.session_id}",
                )
                if self.runtime.archive:
                    self.runtime.archive.append(
                        "notice",
                        now,
                        {
                            "kind": "mechanical_timer_" + phase,
                            "estimated_ends_at": ends,
                        },
                        self.runtime.session.session_id,
                    )

    async def light_call(self, service, data):
        # Ein nicht antwortendes Licht darf den serialisierten Regelkreis nicht
        # unbegrenzt blockieren. Dieselbe Dienstfrist gilt für alle Aktoren.
        async with asyncio.timeout(self.values["feedback_timeout_seconds"]):
            await self.hass.services.async_call("light", service, data, blocking=True)

    async def close(self):
        await self.send(False, self.runtime._clock(), force=True)
