"""HA-Geräteadapter: ausgewählte Quellen, reale Dienste, getrennte Rückmeldung."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from math import isfinite

from homeassistant.components import persistent_notification
from homeassistant.core import Context
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_state_report_event,
)

from .archive import plain
from .bindings import ROLE_BY_KEY
from .core import power
from .core.button import NATIVE_BUTTON_EVENTS
from .core.light import normal_brightness, phase_target
from .core.light_output import LightOutput, LightQuantizer
from .core.models import Measurement, Position, Quantity
from .core.timeline import Door, Kind
from .core.warmup import (
    HeatingProgressEpisode,
    WarmupEstimate,
    historical_heating_delay,
    historical_warmup_rate,
)
from .presentation import FAULTS, configuration_message


class HADevice:
    def __init__(self, hass, runtime):
        self.hass, self.runtime = hass, runtime
        self.bindings = {
            role: entity_id
            for role, entity_id in runtime.configuration.bindings.values.items()
            if not role.startswith("environment_")
        }
        self.values = runtime.configuration.parameters.values
        self.states = {}
        self._button_event_types = None
        self.source_received_at = {}
        self.measurements = {}
        self.last_valid_temperature = None
        self.last_valid_lower_temperature = None
        self.warmup = WarmupEstimate(self.values["warmup_estimation_minutes"] * 60)
        self._warmup_key = None
        self._warmup_target = None
        self._warmup_started_at = None
        self._warmup_last_received_at = None
        self._warmup_door_opening_id = None
        self._warmup_door_before_c = None
        self._heating_progress = None
        self._heating_progress_key = None
        self._heating_progress_started_at = None
        self._heating_progress_received_at = None
        self._heating_progress_notified = False
        self._heating_progress_notice_active = False
        self._heating_response_key = None
        self._heating_response_delay = None
        self._heating_response_task = None
        self._historical_warmup_rate = None
        self._historical_warmup_session_id = None
        self._historical_warmup_task = None
        self._historical_warmup_loaded = False
        self._historical_warmup_generation = 0
        self.fault_since = {}
        self.faults = {}
        self.command = None
        self.command_at = None
        self.last_sent_at = None
        self.command_error = False
        self.command_error_at = None
        self._heater_owned = True
        self._heater_service_task = None
        self._heater_service_heat = None
        self.light_output = LightOutput(self.runtime.configuration.parameters)
        self._light_quantizer = LightQuantizer()
        self._light_last_command_key = None
        self._expected_light_changes = []
        self._light_session_off_completed_key = None
        self._light_session_off_superseded_key = None
        self._light_override_dirty = False
        self._light_owned = True
        self._light_output_lock = asyncio.Lock()
        self._light_service_task = None
        self._light_service_name = None
        self._light_output_deferred = False
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
        self._button_hold_starting = False
        if runtime.controller.control_mode == "manual":
            self.set_light_override(0)

    async def start(self):
        now = self.runtime._clock()
        for role, entity_id in self.bindings.items():
            if role in {"presence", "audio_output"}:
                continue
            state = self.hass.states.get(entity_id)
            self.ingest(
                role, state, state.last_reported if state else now, initial=True
            )

        async def changed(event):
            await self.runtime.device_input(event)

        entities = [entity for role, entity in self.bindings.items()
                    if role not in {"presence", "audio_output"}]
        self.runtime.on_close(
            async_track_state_change_event(self.hass, entities, changed)
        )
        self.runtime.on_close(
            async_track_state_report_event(self.hass, entities, changed)
        )
        self.refresh(now)
        await self.send(False, now, force=True)

    def ingest(self, role, state, received_at, *, initial=False, defer_archive=False):
        self.states[role] = state
        if role == "control_input" and state is not None:
            event_types = state.attributes.get("event_types")
            if (
                isinstance(event_types, (list, tuple)) and event_types
                and all(isinstance(value, str) for value in event_types)
            ):
                self._button_event_types = tuple(event_types)
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
            if (
                self._heating_progress_key is not None
                and quantity == "temperature"
                and m.source == self._heating_progress_key[2]
                and (value is None or value >= self._heating_progress_key[3])
            ):
                self._reset_heating_progress()
            self.runtime.log.debug(
                "measurement",
                "Messwert %s: %s; empfangen: %s.",
                role,
                value,
                received_at,
            )
            if role == "upper_temperature" and value is not None:
                self.last_valid_temperature = m
            elif role == "lower_temperature" and value is not None:
                self.last_valid_lower_temperature = m
            session = self.runtime.session
            if self.runtime.archive and not initial and not defer_archive:
                self.runtime.archive.append(
                    "measurement", received_at, m, session.session_id if session else None
                )
            if self.runtime.detector and not initial:
                self.runtime.detector.accept(m)
            return m
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

    @property
    def available_button_session_gestures(self):
        possible = self.runtime.configuration.available_button_session_gestures
        if not possible:
            return ()
        if not self.bindings["control_input"].startswith("event."):
            return possible
        if self._button_event_types is None:
            # Keep the saved choice during startup; do not invent device capabilities.
            return (self.runtime.configuration.button_session_gesture,)
        reported = {NATIVE_BUTTON_EVENTS.get(value) for value in self._button_event_types}
        return tuple(gesture for gesture in possible if gesture in reported)

    def physical_action(self, event):
        if event.data["entity_id"] != self.bindings["control_input"]:
            return None
        old, new = event.data.get("old_state"), event.data.get("new_state")
        source = new if new is not None else old
        if (
            self.runtime.configuration.control_input_mode == "button"
            and source is not None
            and source.domain == "binary_sensor"
        ):
            if new is None or new.state in ("unknown", "unavailable"):
                return "unavailable"
            # A confirmed OFF closes a known hold even after a source gap.
            # Returning ON cannot establish an uninterrupted or fresh press.
            if new.state == "off" and (old is None or old.state != "off"):
                return "off"
            if old is not None and old.state == "off" and new.state == "on":
                return "on"
            return None
        if old is None or new is None or new.state in ("unknown", "unavailable"):
            return None
        if old.state == new.state:
            return None
        if new.domain == "binary_sensor":
            if old.state in ("unknown", "unavailable"):
                # A switch returning as OFF is a safe, idempotent stop. A
                # returning ON is not evidence of a fresh start request.
                return False if new.state == "off" else None
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
            native = NATIVE_BUTTON_EVENTS.get(event_type)
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
            or (now - received).total_seconds() >= ttl
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
        else:
            source, active = self.observe_fallback_heating()
        return {
            "source": source,
            "heating": active,
            "power_w": measured,
            "contactor": self.contactor_feedback(),
            "estimated": source == "contactor",
        }

    def observe_fallback_heating(self):
        """The independent feedback or relay used after optional power expires."""
        if (
            self.bindings.get("heater_feedback") != self.bindings["heater"]
            and (active := self.binary_state("heater_feedback")) is not None
        ):
            return "independent_feedback", active
        active = self.contactor_feedback()
        return ("contactor", active) if active is not None else ("unknown", None)

    def feedback(self):
        return self.observe_heating(self.runtime._clock())["heating"]

    def report_received_feedback(self, received_at):
        """Book a physical feedback edge before a delayed cycle reaches now.

        A preceding serialized cycle may already have advanced the controller;
        in that case its monotone clock is the earliest still-bookable instant.
        """
        controller = self.runtime.controller
        at = max(received_at, controller._last_at or received_at)
        timeout = self.values.get("sensor_timeout_seconds")
        observation = self.heating_observation = self.observe_heating(at)
        if self.contactor_feedback() is not True:
            self._reset_heating_progress()
        controller.report_contactor(self.contactor_feedback(), at)
        controller.report_fallback_heating(self.observe_fallback_heating()[1], at)
        controller.report_power(
            observation["power_w"],
            self.source_received_at["heater_power"] + timedelta(seconds=timeout)
            if self.source_received_at.get("heater_power") and timeout
            else None,
            at,
        )
        controller.report_heating(observation["heating"], at)
        observation_key = (
            controller.session.session_id if controller.session else None,
            observation["source"],
            observation["heating"],
        )
        if self.runtime.archive and observation_key != self._saved_heating_observation:
            self.runtime.archive.append(
                "heating_observation", at, observation, observation_key[0]
            )
            self._saved_heating_observation = observation_key

    def _reset_warmup(self):
        self.warmup.reset()
        self._warmup_key = None
        self._warmup_target = None
        self._warmup_started_at = None
        self._warmup_last_received_at = None
        self._warmup_door_opening_id = None
        self._warmup_door_before_c = None

    @property
    def _heating_progress_notification_id(self):
        instance = self.runtime.archive.entry_id if self.runtime.archive else self.bindings["heater"]
        return f"sauna_heating_progress_{instance}"

    def _dismiss_heating_progress(self):
        if self._heating_progress_notice_active:
            persistent_notification.async_dismiss(self.hass, self._heating_progress_notification_id)
            self._heating_progress_notice_active = False

    def _reset_heating_progress(self):
        self._dismiss_heating_progress()
        self._heating_progress = None
        self._heating_progress_key = None
        self._heating_progress_started_at = None
        self._heating_progress_received_at = None
        self._heating_progress_notified = False

    def _refresh_heating_progress(self, now):
        """Notify from new comparable observations; never alter heater control."""
        controller = self.runtime.controller
        session = controller.session
        measurement = self.regulation_measurement(now)
        target = controller.target_temperature
        if (
            controller.control_mode != "automatic"
            or session is None or not session.operation_enabled
            or self.contactor_feedback() is not True
            or measurement is None or target is None or measurement.value >= target
            or session.timeline.door == Door.OPEN
        ):
            self._reset_heating_progress()
            return
        role = f"{measurement.position.value}_temperature"
        if self.measurements.get(role) is not measurement:
            # A cached valid value may still regulate, but cannot bridge an
            # invalid incoming report in evidence of missing heating progress.
            self._reset_heating_progress()
            return
        door_event = next((event.event_id for event in reversed(session.timeline.processed)
                           if event.kind in (Kind.DOOR_OPEN, Kind.DOOR_CLOSE)), None)
        window = self.values["warmup_estimation_minutes"] * 60
        key = (session.session_id, measurement.position, measurement.source, target, window, door_event)
        previous = self._heating_progress_received_at
        if key != self._heating_progress_key or (
            previous is not None
            and (measurement.received_at - previous).total_seconds() > self.values["sensor_timeout_seconds"]
        ):
            self._reset_heating_progress()
            self._heating_progress_key = key
            self._heating_progress_started_at = now
            self._heating_progress = HeatingProgressEpisode(window, now)
        self._load_heating_response(key)
        self._heating_progress.startup_delay_seconds = self._heating_response_delay
        if measurement.received_at < self._heating_progress_started_at or (
            self._heating_progress_received_at is not None
            and measurement.received_at <= self._heating_progress_received_at
        ):
            return
        self._heating_progress_received_at = measurement.received_at
        evidence = self._heating_progress.accept(measurement.received_at, measurement.value)
        if evidence is None:
            return
        if evidence["rising"]:
            self._dismiss_heating_progress()
            return
        if not evidence["no_rise"] or self._heating_progress_notified:
            return
        current_text = f"{measurement.value:.1f}".replace(".", ",")
        target_text = f"{target:.1f}".replace(".", ",")
        persistent_notification.async_create(
            self.hass,
            "Bei eingeschaltetem Schütz ist kein Temperaturanstieg erkennbar. "
            f"Aktuell {current_text} °C, Soll {target_text} °C.",
            title="Sauna: kein Temperaturanstieg",
            notification_id=self._heating_progress_notification_id,
        )
        self._heating_progress_notified = True
        self._heating_progress_notice_active = True
        if self.runtime.archive:
            self.runtime.archive.append("notice", now, {
                "kind": "heating_no_temperature_rise", **evidence,
                "source": measurement.source, "target_temperature_c": target,
                "historical_startup_delay_seconds": self._heating_response_delay,
            }, session.session_id)

    def _load_heating_response(self, progress_key):
        """Read source-matched historical startup observations outside the loop."""
        session_id, position, source, _, window, _ = progress_key
        timeout = self.values["sensor_timeout_seconds"]
        heater_source = self.bindings["heater"]
        key = (session_id, position, source, heater_source, timeout, window)
        if key == self._heating_response_key:
            return
        if self._heating_response_task is not None:
            self._heating_response_task.cancel()
        self._heating_response_key = key
        self._heating_response_delay = None
        archive = self.runtime.archive
        if archive is None:
            return

        async def load():
            try:
                # Completed episodes may still be queued by the archive writer.
                await archive.flush()
                episodes = await asyncio.to_thread(
                    archive.heating_response_history,
                    source, position.value, heater_source, timeout,
                )
                delay = await asyncio.to_thread(historical_heating_delay, episodes, window)
                if self.runtime.closed or key != self._heating_response_key:
                    return
                self._heating_response_delay = delay
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.getLogger(__name__).exception("Historische Aufheizverzögerung konnte nicht gelesen werden")
            finally:
                if self._heating_response_task is asyncio.current_task():
                    self._heating_response_task = None

        self._heating_response_task = asyncio.create_task(load())

    def _remember_warmup_door_opening(self, session):
        opening = next(
            (
                event
                for event in reversed(session.timeline.processed)
                if event.kind == Kind.DOOR_OPEN
            ),
            None,
        )
        if opening is not None and opening.event_id != self._warmup_door_opening_id:
            self._warmup_door_opening_id = opening.event_id
            self._warmup_door_before_c = self.warmup.trend.temperature_before(
                opening.effective_at
            )

    def _current_temperature(self, role, now):
        measurement = (
            self.last_valid_temperature
            if role == "upper_temperature"
            else self.last_valid_lower_temperature
        )
        timeout = self.values.get("sensor_timeout_seconds")
        if (
            measurement is None
            or timeout is None
            or (age := (now - measurement.received_at).total_seconds()) < 0
            or age > timeout
        ):
            return None
        return measurement

    def _current_upper_temperature(self, now):
        return self._current_temperature("upper_temperature", now)

    def regulation_measurement(self, now):
        """Prefer the current upper value, otherwise use the current lower value."""
        return self._current_upper_temperature(now) or self._current_temperature(
            "lower_temperature", now
        )

    def regulation_valid_until(self, measurement):
        """Use the selected measurement's existing freshness deadline."""
        return (measurement.received_at + timedelta(seconds=self.values["sensor_timeout_seconds"])
                if measurement is not None else None)

    def _refresh_warmup(self, now):
        """Advance the display estimate from one selected, current temperature source."""
        controller = self.runtime.controller
        session = controller.session
        active_warmup = (
            session is not None
            and session.operation_enabled
            and controller.phase == "aufheizen"
            and self.heating_observation["heating"] is True
            and not controller.protection
            and not controller.inhibits
        )
        if not active_warmup:
            if (
                session is None
                or not session.operation_enabled
                or controller.phase != "aufheizen"
            ):
                self._reset_warmup()
            return
        measurement = self.regulation_measurement(now)
        if measurement is None:
            self._reset_warmup()
            return
        key = (
            session.session_id,
            self.heating_observation["source"],
            measurement.position,
            measurement.source,
        )
        target = controller.target_temperature
        if key != self._warmup_key or target != self._warmup_target:
            self._reset_warmup()
            self.invalidate_historical_warmup()
            self._warmup_key = key
            self._warmup_target = target
            self._warmup_started_at = now
            self._load_historical_warmup()
        self._remember_warmup_door_opening(session)
        if session.timeline.door == Door.OPEN:
            return
        # A value received before the confirmed heating stretch is a useful
        # controller input, but not evidence about this warm-up estimate.
        if (
            measurement.received_at < self._warmup_started_at
            or (
                self._warmup_last_received_at is not None
                and measurement.received_at <= self._warmup_last_received_at
            )
        ):
            return
        if self._warmup_door_before_c is not None:
            closing = next(
                (
                    event
                    for event in reversed(session.timeline.processed)
                    if event.kind == Kind.DOOR_CLOSE
                ),
                None,
            )
            if closing is None or measurement.received_at <= closing.effective_at:
                return
        door_loss_c = (
            max(0, self._warmup_door_before_c - measurement.value)
            if self._warmup_door_before_c is not None
            else None
        )
        self.warmup.accept(
            measurement.received_at,
            measurement.value,
            target_c=target,
            historical_rate=self._historical_warmup_rate,
            door_loss_c=door_loss_c,
        )
        self._warmup_last_received_at = measurement.received_at
        self._warmup_door_before_c = None

    def _load_historical_warmup(self):
        """Load one archived rate outside HA's event loop for this session."""
        if self._historical_warmup_loaded or self._historical_warmup_task is not None:
            return
        archive = self.runtime.archive
        if archive is None:
            return
        generation = self._historical_warmup_generation
        if self._warmup_key is None or self._warmup_key[2] != Position.UPPER:
            # No upper archive curve is applied to a lower-position live trend.
            # The lower position can still establish its own live ETA.
            return
        source = self._warmup_key[3]
        timeout = self.values.get("sensor_timeout_seconds")
        minimum_observation = self.warmup.trend.window_seconds

        async def load():
            try:
                # A just-ended session may still be in the writer queue.  The
                # fence runs asynchronously and makes this one-time read see it.
                await archive.flush()
                history = await asyncio.to_thread(
                    archive.latest_completed_warmup, source, timeout
                )
                if history:
                    rate = await asyncio.to_thread(
                        historical_warmup_rate,
                        history["measurements"],
                        minimum_observation_seconds=minimum_observation,
                    )
                    result = history["session_id"], rate
                else:
                    result = None
            except Exception:
                return
            else:
                if (
                    self.runtime.closed
                    or generation != self._historical_warmup_generation
                ):
                    return
                self._historical_warmup_loaded = True
                if result:
                    (
                        self._historical_warmup_session_id,
                        self._historical_warmup_rate,
                    ) = result
            finally:
                if self._historical_warmup_task is asyncio.current_task():
                    self._historical_warmup_task = None

        self._historical_warmup_task = asyncio.create_task(load())

    def invalidate_historical_warmup(self):
        """Forget a cached rate after a newly completed session was archived."""
        self._heating_response_key = None
        self._heating_response_delay = None
        if self._heating_response_task is not None:
            self._heating_response_task.cancel()
            self._heating_response_task = None
        self._historical_warmup_rate = None
        self._historical_warmup_session_id = None
        self._historical_warmup_loaded = False
        self._historical_warmup_generation += 1
        task = self._historical_warmup_task
        self._historical_warmup_task = None
        if task is not None:
            task.cancel()
        if self._warmup_key is not None and not self.runtime.closed:
            self._load_historical_warmup()

    def estimated_ready_seconds(self, now):
        """Return the cached ETA only while its selected source is current."""
        controller = self.runtime.controller
        session = controller.session
        measurement = self.regulation_measurement(now)
        if (
            session is None
            or not session.operation_enabled
            or controller.phase != "aufheizen"
            or self.heating_observation["heating"] is not True
            or controller.protection
            or controller.inhibits
            or session.timeline.door == Door.OPEN
            or measurement is None
            or self._warmup_key is None
            or self._warmup_key[2:] != (measurement.position, measurement.source)
        ):
            return None
        timeout = self.values.get("sensor_timeout_seconds")
        return self.warmup.remaining_seconds(now, maximum_age_seconds=timeout)

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
        regulation = self.regulation_measurement(now)
        temperature = regulation.value if regulation is not None else None
        # Ein kurz fehlendes Paket verwirft einen noch gültigen Messwert nicht.
        # Wenn die obere Höhe ausfällt, führt die frische untere Messung dieselbe
        # Regelung ohne Mittelung oder erfundenen Höhenoffset fort.
        controller.set_temperature(
            temperature, now, valid_until=self.regulation_valid_until(regulation),
        )
        self.report_received_feedback(now)
        contactor = self.contactor_feedback()
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
            since = (
                max(now, self.command_error_at)
                if key == "heater_service_unavailable" and self.command_error_at is not None
                else now
            )
            self.fault_since.setdefault(key, since)
        confirmation = self.values.get("fault_confirmation_seconds")
        monitoring = (
            bool(controller.session and controller.session.operation_enabled)
            or controller.heater_override is True
            or contactor is True
            or self.heating_observation["heating"] is True
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
        else:
            self.faults.pop("archive", None)
        self._refresh_warmup(now)
        self._refresh_heating_progress(now)
        controller.advance(now)

    async def send(self, heat, now, *, force=False, wait=True):
        if self.runtime.closed or (not self._heater_owned and (heat or not force)):
            return False
        ack = self.values.get("feedback_timeout_seconds")
        # An unknown budget cannot authorize heating, but OFF still owns its
        # real blocking service instead of dispatching an unobserved job.
        heat = bool(heat and ack is not None)
        previous = self._heater_service_task
        pending = previous is not None and not previous.done()
        if pending and (heat or self._heater_service_heat is False):
            return False
        same = self.command is heat
        matched = self.contactor_feedback() is heat
        if (
            not force
            and not pending
            and same
            and (
                matched
                or ack is None
                or (now - self.last_sent_at).total_seconds() < ack
            )
        ):
            return True
        # Bestätigungsfrist wird bei Wiederholungen desselben Befehls nicht neu
        # begonnen. Andernfalls könnte ein Dauerausfall nie bestätigt werden.
        if self.command is not heat or self.command_at is None:
            self.command, self.command_at = heat, now
        self.last_sent_at = now
        self.runtime._report_detector_heating(now)
        self.runtime.log.info(
            "heater_command",
            "Schaltbefehl an Heizschütz: %s.",
            "EIN" if heat else "AUS",
        )
        decision = self.runtime.controller.last_decision
        self._heater_service_heat = heat
        task = self._heater_service_task = asyncio.create_task(
            self._run_heater_service(
                heat, now, self.bindings["heater"], self.runtime.archive,
                plain(decision), decision.session_id if decision else None,
                previous if pending else None,
            )
        )
        task.add_done_callback(
            lambda finished: self._heater_service_finished(
                finished, previous if pending else None
            )
        )
        if not wait:
            return False
        return await self._wait_heater_service()

    async def _wait_heater_service(self):
        """Bound only the caller; the real transport remains owned."""
        task = self._heater_service_task
        if task is None:
            return True
        if not task.done():
            done, _ = await asyncio.wait(
                (task,), timeout=self.values.get("feedback_timeout_seconds") or 0
            )
            if task not in done:
                self._report_heater_service_result("TimeoutError")
                return False
        return not task.cancelled() and task.exception() is None and task.result()

    def _heater_service_finished(self, task, previous):
        if task.cancelled():
            if (previous is not None and not previous.done()
                    and self._heater_service_task is task):
                # Cancelling only the queued OFF must not lose the actual ON
                # that it was waiting for. A later attempt still follows it.
                self._heater_service_task = previous
                self._heater_service_heat = True
            return
        task.exception()

    def _report_heater_service_result(self, error):
        self.command_error = error is not None
        if error is None:
            self.command_error_at = None
            self.fault_since.pop("heater_service_unavailable", None)
        elif self.command_error_at is None:
            self.command_error_at = self.runtime._clock()
        self.runtime._report_detector_heating(self.runtime._clock())
        self.runtime.log.change(
            "heater_command_error",
            error,
            logging.ERROR if error else logging.INFO,
            "Ergebnis des Schaltbefehls: %s.",
            error
            or "Dienstaufruf abgeschlossen; Schützrückmeldung wird getrennt geprüft",
        )

    async def _run_heater_service(
        self, heat, planned_at, entity_id, archive, decision, session_id, previous
    ):
        # At most one final OFF can follow a pending ON. It stays alive after
        # timeout/caller cancellation and must run after that actual transport.
        if previous is not None:
            # Completion is the ordering boundary, including a driver that
            # raises CancelledError after applying ON. Cancellation of this
            # waiting task still propagates without cancelling its predecessor.
            await asyncio.wait((previous,))
        error = None
        sent_at = self.runtime._clock()
        try:
            await self.hass.services.async_call(
                "switch", "turn_on" if heat else "turn_off",
                {"entity_id": entity_id}, blocking=True,
            )
        except BaseException as exc:
            error = type(exc).__name__
            if not isinstance(exc, Exception):
                raise
        finally:
            self._report_heater_service_result(error)
            if archive is not None:
                try:
                    archive.append(
                        "command", self.runtime._clock(),
                        {
                            "heater": entity_id, "heat": heat,
                            "decision": decision, "service_error": error,
                            "planned_at": planned_at, "sent_at": sent_at,
                            "feedback_proves_heating": False,
                        }, session_id,
                    )
                except Exception as archive_error:  # noqa: BLE001 - preserve the actuator result
                    self.faults["archive"] = str(archive_error)
                    self.runtime.log.logger.error(
                        "Ofendienstabschluss konnte nicht archiviert werden: %s.",
                        archive_error,
                        extra={"sauna_event": "archive"},
                    )
        return error is None

    def restore_heater_ownership(self):
        self._heater_owned = True

    async def prepare_heater_handoff(self, *, restore_on_failure=True):
        """Keep this binding until its final OFF actually finishes successfully."""
        if self.runtime.closed:
            return True
        owned = self._heater_owned
        self._heater_owned = False
        try:
            task = self._heater_service_task
            off_finished = (
                task is not None and self._heater_service_heat is False
                and task.done() and not task.cancelled()
                and task.exception() is None and task.result()
            )
            # Concurrent handoffs and close share the already withdrawn
            # owner's final OFF; none may start another transport behind it.
            if owned or not off_finished:
                await self.send(False, self.runtime._clock(), force=True, wait=False)
            finished = await self._wait_heater_service()
        except BaseException:
            if owned and restore_on_failure:
                self.restore_heater_ownership()
            raise
        if not finished and owned and restore_on_failure:
            self.restore_heater_ownership()
        return finished

    async def apply(self, now):
        await self.send(self.runtime.controller.last_decision.heat, now)
        await self.apply_light(now)

    async def apply_light(self, now):
        if not self._light_owned:
            return
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
            and not after_run.pending_start
            and after_run.ends_at is None
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
                self.runtime.controller.target_temperature,
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
        # Kurve und Istwert behalten ihre Genauigkeit. Nur die gemeinsame
        # Befehlsgrundlage für Dienst, Echo und Archiv wird quantisiert.
        signature = self._light_state_signature(state)
        if signature is None or signature == ("on", None):
            self._light_quantizer.reset()
        brightness = self._light_quantizer.quantize(
            plan,
            self.runtime.configuration.parameters.values[
                "light_output_hysteresis_percent"
            ],
        )
        service = "turn_off" if brightness <= 0 else "turn_on"
        command_key = (key, service, brightness if service == "turn_on" else None)
        desired = self._light_command_signature(service, brightness)
        if (
            name == "aus"
            and plan.automatic
            and light_after_run is not None
            and self._light_session_off_completed_key
            == (
                "session_light",
                light_after_run.session_id,
                light_after_run.started_at,
            )
            and not self._light_service_is_pending()
            and self._light_state_signature(state) == desired
        ):
            # Das bestätigte Frist-AUS erfüllt auch den automatischen Endplan.
            # Seine neue Phasenkennung braucht keinen zweiten AUS-Dienst; die
            # gleichzeitig abgelaufene manuelle Wahl ist damit verarbeitet.
            self._light_override_dirty = False
            return
        already_sent = self._light_state_signature(state) == desired and (
            command_key == self._light_last_command_key or service == "turn_on"
        )
        if already_sent or (
            name == "aus" and plan.automatic and not self._light_override_dirty
        ):
            return
        # Genau die zuletzt angeforderte sichtbare Änderung kann wegen des
        # Gerätepfads noch unterwegs sein. Ein anderer Prozentpunkt erhält
        # einen anderen Befehlsschlüssel und bleibt ausführbar, auch wenn sein
        # Wert als ältere Erwartung noch vorhanden ist.
        if (
            not self._light_override_dirty
            and command_key == self._light_last_command_key
            and self._light_change_is_pending(now, service, brightness)
        ):
            return
        if await self._send_light_command(
            now,
            key=command_key,
            phase=name,
            service=service,
            brightness=brightness if service == "turn_on" else None,
            session_id=self._light_session_id(automatic=plan.automatic),
            ends_at=ends_at,
        ):
            self._light_override_dirty = False

    async def _finish_expired_session_light(self, now):
        """Send the due OFF until the bound light actually reports OFF."""
        light_after_run = self.runtime.controller.light_after_run
        if light_after_run is None or now < light_after_run.ends_at:
            return True
        if self._light_output_lock.locked():
            self._light_output_deferred |= self._light_service_is_pending()
            return False
        async with self._light_output_lock:
            if self.runtime.closed or not self._light_owned:
                return False
            light_after_run = self.runtime.controller.light_after_run
            if light_after_run is None or now < light_after_run.ends_at:
                return True
            key = ("session_light", light_after_run.session_id, light_after_run.started_at)
            if key in (
                self._light_session_off_completed_key,
                self._light_session_off_superseded_key,
            ):
                return True
            completed = not self._light_service_is_pending()
            if self.runtime.closed or not self._light_owned:
                return False
            # A completed actual output can fulfil the deadline. Read its
            # result, feedback and current phase before retrying OFF.
            light_after_run = self.runtime.controller.light_after_run
            checked_at = self.runtime._clock()
            if light_after_run is None or checked_at < light_after_run.ends_at:
                return True
            if not completed:
                self._light_output_deferred = True
                self.faults["session_light"] = "service_unavailable"
                return False
            key = ("session_light", light_after_run.session_id, light_after_run.started_at)
            if key in (
                self._light_session_off_completed_key,
                self._light_session_off_superseded_key,
            ):
                return True
            if self._light_off_is_confirmed():
                self._light_session_off_completed_key = key
                self.faults.pop("session_light", None)
                return True
            command_key = (key, "turn_off", None)
            if (
                self._light_last_command_key == command_key
                and self._light_change_is_pending(checked_at, "turn_off", None)
            ):
                return False
            unconfirmed = self._light_last_command_key == command_key
            if not await self._execute_light_command(
                now,
                key=command_key,
                phase="session_light",
                service="turn_off",
                brightness=None,
                session_id=light_after_run.session_id,
                ends_at=light_after_run.ends_at,
            ):
                return False
            if self.runtime.closed or not self._light_owned:
                return False
            if self.runtime.controller.light_after_run is not light_after_run:
                return True
            if self._light_off_is_confirmed():
                self._light_session_off_completed_key = key
                self.faults.pop("session_light", None)
                return True
            if unconfirmed:
                self.faults["session_light"] = "feedback_missing"
            return False

    def _light_session_id(self, *, automatic=True):
        """Only automatic after-run output belongs to an already ended session."""
        session = self.runtime.controller.session
        light_after_run = self.runtime.controller.light_after_run
        return (
            session.session_id
            if session is not None
            else (light_after_run.session_id
                  if automatic and light_after_run is not None else None)
        )

    async def finish_session_light(self, now, phase, *, purpose="light_reassignment"):
        """Beendet die Ausgabe an der bisherigen Leuchte vor einem Lichtwechsel.

        Der Aufrufer ersetzt anschließend die Gerätezuordnung. Der Dienstaufruf
        bleibt deshalb beim alten Adapter und wird wie jeder andere Lichtbefehl
        protokolliert.
        """
        # Give up this binding before any await. The old runtime can still
        # receive feedback or ticks while Home Assistant unloads its platforms.
        self.relinquish_light()
        try:
            async with asyncio.timeout(self.values["feedback_timeout_seconds"]):
                async with self._light_output_lock:
                    return await self._finish_session_light_locked(now, phase, purpose=purpose)
        except TimeoutError:
            task = self._light_service_task
            completed_off = (
                task is not None and task.done() and not task.cancelled()
                and task.exception() is None
                and (self._light_last_command_key or ())[-2:] == ("turn_off", None)
            )
            fault = self._light_fault("session_light" if phase is not None else purpose)
            self.faults[fault] = "feedback_missing" if completed_off else "service_unavailable"
            return False

    async def _finish_session_light_locked(self, now, phase, *, purpose):
        entity_id = self.bindings["light"]
        key = (
            ("session_light", phase.session_id, phase.started_at)
            if phase is not None else ("light_reassignment", entity_id)
        )
        phase_name = "session_light" if phase is not None else "light_reassignment"
        fault = self._light_fault(phase_name)
        # Even a currently OFF light can have an earlier ON still executing.
        # Reject the handoff when that call cannot finish within the usual
        # service deadline; it remains referenced for a subsequent attempt.
        if not await self._wait_light_service():
            self.faults[fault] = "service_unavailable"
            return False
        if self._light_off_is_confirmed():
            self.faults.pop(fault, None)
            return True
        loop = asyncio.get_running_loop()
        confirmed = loop.create_future()

        def observed(event):
            if (
                self._light_state_signature(event.data.get("new_state")) == ("off", None)
                and not confirmed.done()
            ):
                confirmed.set_result(None)

        unsubscribe = async_track_state_change_event(
            self.hass, [entity_id], observed
        )
        try:
            sent = await self._execute_light_command(
                now,
                key=(key, "turn_off", None),
                phase=phase_name,
                service="turn_off",
                brightness=None,
                session_id=phase.session_id if phase is not None else None,
                ends_at=phase.ends_at if phase is not None else None,
                purpose=purpose,
                entity_id=entity_id,
            )
            if not sent:
                return False
            if self._light_off_is_confirmed():
                self.faults.pop(fault, None)
                return True
            try:
                await asyncio.wait_for(confirmed, self.values["feedback_timeout_seconds"])
            except TimeoutError:
                self.faults[fault] = "feedback_missing"
                return False
            self.faults.pop(fault, None)
            return True
        finally:
            unsubscribe()

    def relinquish_light(self):
        """End all output ownership of the current binding before a reload wait."""
        self._light_owned = False
        # A rejected handoff can restore this owner before an earlier actual
        # service finishes. Keep its bounded context proof for the late echo.
        self._discard_expired_light_expectations(self.runtime._clock())

    def restore_light_ownership(self):
        """Resume the unchanged binding after a rejected reassignment."""
        if self._light_owned:
            return
        self._light_owned = True
        self._light_last_command_key = None
        self._light_override_dirty = True

    async def prepare_light_handoff(self, *, restore_on_failure=True):
        """Stop output and await its actual completion before any owner change."""
        owned = self._light_owned
        self.relinquish_light()
        try:
            # Include the output lock in the deadline. A caller may still be
            # waiting for feedback while its real service continues separately.
            async with asyncio.timeout(self.values["feedback_timeout_seconds"]):
                async with self._light_output_lock:
                    if not await self._wait_light_service():
                        raise TimeoutError
        except TimeoutError:
            if owned and restore_on_failure:
                self.restore_light_ownership()
            return False
        except BaseException:
            if owned and restore_on_failure:
                self.restore_light_ownership()
            raise
        return True

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
        entity_id=None,
    ):
        if self._light_output_lock.locked():
            self._light_output_deferred |= self._light_service_is_pending()
            return False
        async with self._light_output_lock:
            if not self._light_owned and purpose != "light_reassignment":
                return False
            if self._light_service_is_pending():
                self._light_output_deferred = True
                self.faults[self._light_fault(phase)] = "service_unavailable"
                return False
            return await self._execute_light_command(
                now, key=key, phase=phase, service=service,
                brightness=brightness, session_id=session_id, ends_at=ends_at,
                purpose=purpose, entity_id=entity_id,
            )

    async def _wait_light_service(self):
        """Bounded wait for the actual service task without cancelling it."""
        task = self._light_service_task
        if task is None or task.done():
            return True
        done, _ = await asyncio.wait(
            (task,), timeout=self.values["feedback_timeout_seconds"]
        )
        return task in done

    def _light_service_is_pending(self):
        task = self._light_service_task
        return task is not None and not task.done()

    def _light_off_is_confirmed(self):
        """An old OFF report cannot discharge a newer owned ON service."""
        task = self._light_service_task
        return (
            self._light_service_name != "turn_on"
            and (task is None or (
                task.done() and not task.cancelled() and task.exception() is None
            ))
            and self._light_state_signature(
                self.hass.states.get(self.bindings["light"])
            ) == ("off", None)
        )

    def _light_service_finished(self, task):
        # Timed-out or cancelled callers still leave an owned transport task.
        # Retrieve its eventual exception even if no later output is requested.
        if not task.cancelled():
            task.exception()
        if self._light_service_task is task and self._light_output_deferred:
            self._light_output_deferred = False
            if self._light_owned and not self.runtime.closed:
                # A normal cycle already skipped newer output while this
                # transport was pending. Revisit that output once at actual
                # completion; ordinary service failures do not create a loop.
                create_task = getattr(self.hass, "async_create_task", asyncio.create_task)
                create_task(self.runtime.tick())

    async def _run_light_service(
        self, service, data, context, expectation, archive, key, payload, session_id
    ):
        """Archive the actual service once, even when its waiting caller leaves."""
        error = None
        sent_at = self.runtime._clock()
        if expectation is not None:
            expectation["sent_at"] = sent_at
        try:
            await self.light_call(service, data, context=context)
        except BaseException as exc:
            error = type(exc).__name__
            self._report_light_service_error(payload["phase"], service, error)
            raise
        else:
            self._light_last_command_key = key
            for known_fault in (
                "session_light",
                "operation_light",
                "after_run_light",
            ):
                self.faults.pop(known_fault, None)
            self.runtime.log.change(
                "light_command",
                (payload["phase"], service),
                logging.INFO,
                "Lichtdienst für %s abgeschlossen: %s.",
                payload["phase"], service,
            )
            if service == "turn_on":
                self.runtime.log.debug(
                    "light_dimming", "Lichtwert für %s: %s %%.",
                    payload["phase"], payload["brightness_pct"],
                )
        finally:
            completed_at = self.runtime._clock()
            if expectation is not None:
                if error is None:
                    # Publish the successful echo window before the task can
                    # become done and wake another output or queued input.
                    expectation["completed_at"] = completed_at
                elif expectation in self._expected_light_changes:
                    self._expected_light_changes.remove(expectation)
            if archive is not None:
                try:
                    archive.append(
                        "light_command", completed_at,
                        {
                            **payload,
                            "sent_at": sent_at,
                            "completed_at": completed_at,
                            "service_error": error,
                        },
                        session_id,
                    )
                except Exception as archive_error:
                    # An archive error is separate from the actual actuator
                    # result; it must not replace either success or failure.
                    self.faults["archive"] = str(archive_error)
                    self.runtime.log.error(
                        "archive", "Lichtdienstabschluss konnte nicht archiviert werden: %s.",
                        archive_error,
                    )

    def _report_light_service_error(self, phase, service, error):
        self.faults[self._light_fault(phase)] = "service_unavailable"
        self.runtime.log.change(
            "light_command_error", (phase, service, error), logging.ERROR,
            "Lichtdienst für %s (%s) fehlgeschlagen: %s.", phase, service, error,
        )

    async def _execute_light_command(
        self, now, *, key, phase, service, brightness, session_id,
        ends_at=None, purpose=None, entity_id=None,
    ):
        """Führt einen geplanten Lichtdienst aus und hält Ergebnis und Archiv zusammen.

        Nur ein erfolgreich abgeschlossener Dienstaufruf wird als deduplizierbar
        gespeichert. Ein Fehler bleibt somit im nächsten Regelzyklus erneut
        ausführbar; der Lichtzustand ist keine Rückmeldung über den Dienst.
        """
        if self.runtime.closed or (
            not self._light_owned and purpose != "light_reassignment"
        ):
            return False
        entity_id = self.bindings["light"] if entity_id is None else entity_id
        self._light_output_deferred = False
        data = {"entity_id": entity_id}
        if service == "turn_on":
            data["brightness_pct"] = brightness
        state_before = self.hass.states.get(entity_id)
        error = None
        task, expectation = None, None
        context = Context()
        try:
            if self._light_state_signature(
                state_before
            ) != self._light_command_signature(service, brightness):
                expectation = self._expect_light_change(
                    self.runtime._clock(), service, brightness, context=context
                )
            self._light_service_name = service
            task = self._light_service_task = asyncio.create_task(
                self._run_light_service(
                    service, data, context, expectation, self.runtime.archive, key,
                    {
                        "planned_at": now,
                        "purpose": purpose
                        or ("session_end" if phase == "session_light" else phase),
                        "phase": phase,
                        "service": service,
                        "brightness_pct": brightness,
                        "ends_at": ends_at,
                    },
                    session_id,
                )
            )
            task.add_done_callback(self._light_service_finished)
            if expectation is not None:
                expectation["service_task"] = task
            if not await self._wait_light_service():
                raise TimeoutError
            task.result()
        except Exception as exc:
            if (
                expectation is not None
                and task is None
                and expectation in self._expected_light_changes
            ):
                self._expected_light_changes.remove(expectation)
            error = type(exc).__name__
            if task is None or not task.done():
                self._report_light_service_error(phase, service, error)
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

    def _light_command_signature(self, service, brightness):
        if service == "turn_off":
            return ("off", None)
        value = max(0, min(255, round(brightness * 255 / 100)))
        scale = self.values["light_brightness_scale"]
        if scale != 255 and value:
            # HA accepts 0..255; a light with a different native scale reports
            # the round-tripped value. Use the configured device resolution,
            # never a tolerance that could consume a distinct manual choice.
            native = max(1, round(value * scale / 255))
            value = round(native * 255 / scale)
        # Home Assistant treats a turn_on command with brightness 0 as off.
        return ("on", value) if value else ("off", None)

    def _discard_expired_light_expectations(self, now):
        """Keep the send window and context until shortly after actual completion."""
        timeout = self.values.get("feedback_timeout_seconds")
        if timeout is None:
            self._expected_light_changes.clear()
            return
        limit = timedelta(seconds=timeout)
        self._expected_light_changes[:] = [
            expected
            for expected in self._expected_light_changes
            if now - expected["sent_at"] < limit
            or (
                expected.get("context_id") is not None
                and expected.get("service_task") is not None
                and (
                    not expected["service_task"].done()
                    or (
                        expected.get("completed_at") is not None
                        and now - expected["completed_at"] < limit
                    )
                )
            )
        ]

    def _expect_light_change(
        self, now, service, brightness, *, context=None, service_task=None
    ):
        self._discard_expired_light_expectations(now)
        expected = {
            "signature": self._light_command_signature(service, brightness),
            "sent_at": now,
            "context_id": context.id if context is not None else None,
            "service_task": service_task,
            "completed_at": None,
        }
        self._expected_light_changes.append(expected)
        return expected

    def _light_change_is_pending(self, now, service, brightness):
        """Whether this exact visible change still awaits its feedback."""
        self._discard_expired_light_expectations(now)
        signature = self._light_command_signature(service, brightness)
        limit = timedelta(seconds=self.values["feedback_timeout_seconds"])
        return any(
            expected["signature"] == signature
            and (
                now - expected["sent_at"] < limit
                or (
                    expected.get("service_task") is self._light_service_task
                    and self._light_service_is_pending()
                )
            )
            for expected in self._expected_light_changes
        )

    def external_light_selection(self, event, received_at):
        """Return one actual external light selection, excluding own echoes.

        ``state_report`` events deliberately do not represent a new choice.
        Matching values within the feedback window are own echoes. After that
        interval only the retained service's HA context establishes ownership;
        state alone cannot distinguish a late echo from a physical selection.
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
        context_id = getattr(getattr(new, "context", None), "id", None)
        limit = timedelta(seconds=self.values["feedback_timeout_seconds"])
        for index, expected in enumerate(self._expected_light_changes):
            if expected["signature"] == new_signature and (
                received_at - expected["sent_at"] < limit
                or (context_id is not None and context_id == expected.get("context_id"))
            ):
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
        return ((session.session_id, phase), phase, None)

    @property
    def light_observation(self):
        """Expose measured brightness; an unavailable report is never zero."""
        entity_id = self.bindings.get("light")
        state = self.hass.states.get(entity_id) if entity_id else None
        signature = self._light_state_signature(state)
        brightness = (
            0.0 if signature == ("off", None)
            else signature[1] * 100 / 255
            if signature is not None and signature[1] is not None
            else None
        )
        return {"available": brightness is not None, "brightness_percent": brightness}

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
        }.get(phase, "operation_light")

    def set_light_override(self, value, *, at=None):
        """Manuelle Lichtwahl bis zum Rückkehrpunkt oder Fristablauf halten."""
        if value not in (None, True, False, "normal"):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or (isinstance(value, int) and not 0 <= value <= 100)
                or (isinstance(value, float) and not isfinite(value))
                or not 0 <= value <= 100
            ):
                raise ValueError("Lichtwert muss zwischen 0 und 100 Prozent liegen.")
        # Die Runtime ruft diese Methode vor ihrem nächsten Regelzyklus auf.
        # Deshalb muss der aktuelle Controller-Schlüssel hier mitgegeben
        # werden, statt den möglicherweise alten Planner-Schlüssel zu erben.
        now = self.runtime._clock() if at is None else at
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

    def begin_button_hold_light(self, session_id, *, starting=False):
        """Mark acknowledgement for regular output after the heater command."""
        self._button_hold_session_id = session_id
        self._button_hold_starting = starting
        self.light_output.return_to_automatic()

    async def show_button_hold_light(self, now, session_id):
        """Keep the long-press acknowledgement above all normal phases."""
        self._button_hold_session_id = session_id
        if not self._light_owned:
            return False
        brightness = (
            self.values["session_light_brightness_percent"]
            if self._button_hold_starting else None
        )
        service = "turn_on" if self._button_hold_starting else "turn_off"
        target = self._light_command_signature(service, brightness)
        key = ("button_hold", session_id, service, brightness)
        state = self.hass.states.get(self.bindings["light"])
        if (
            not self._light_service_is_pending()
            and self._light_state_signature(state) == target
        ):
            self.faults.pop("operation_light", None)
            return True
        if key == self._light_last_command_key and self._light_change_is_pending(
            self.runtime._clock(), service, brightness
        ):
            return True
        unconfirmed = key == self._light_last_command_key
        sent = await self._send_light_command(
            now,
            key=key,
            phase="button_hold",
            service=service,
            brightness=brightness,
            session_id=session_id,
            purpose="button_hold",
        )
        if sent and self._light_state_signature(
            self.hass.states.get(self.bindings["light"])
        ) != target and unconfirmed:
            self.faults["operation_light"] = "feedback_missing"
        return sent

    def finish_button_hold_light(self, session_id=None):
        """Only the matching release may hand light control back to the timer."""
        if session_id is None or self._button_hold_session_id == session_id:
            self._button_hold_session_id = None
            self._button_hold_starting = False

    def normal_light_brightness(self):
        """Return the current normal automatic brightness for UI consumers."""
        return normal_brightness(
            self._sun_elevation(), self.runtime.configuration.parameters
        )

    async def light_call(self, service, data, *, context=None):
        # The output owner bounds its wait and retains this task until the real
        # blocking service ends. Cancellation must not let a later OFF overtake
        # an actor call that continues in a platform or executor.
        await self.hass.services.async_call(
            "light", service, data, blocking=True, context=context
        )

    async def close(self):
        self._reset_heating_progress()
        if self._heating_response_task is not None:
            self._heating_response_task.cancel()
        if self._historical_warmup_task is not None:
            self._historical_warmup_task.cancel()
        if not await self.prepare_heater_handoff():
            raise RuntimeError("Ofen-AUS-Dienst nicht abgeschlossen")
