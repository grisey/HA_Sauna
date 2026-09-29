"""Echter HA-Pfad: Entity -> Listener -> Detektor/Kern -> Service -> Aktorfeedback."""
import asyncio
from datetime import UTC, datetime, timedelta
import unittest
from unittest.mock import patch
from homeassistant.components.switch import SwitchEntity
from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from custom_components.ha_sauna.core.timeline import Door
from harness import create_sauna, start_hass


class TestHeater(SwitchEntity):
    _attr_name = "Test heater"
    _attr_unique_id = "isolated-heater"
    _attr_should_poll = False
    _attr_is_on = False
    entity_id = "switch.test_heater"

    def __init__(self):
        self.calls = []
        self.respond = True
        self.powered = True
        self.accept_commands = True

    async def async_turn_on(self, **kwargs):
        self.calls.append(True)
        if not self.accept_commands:
            return
        self._attr_is_on = True
        self.async_write_ha_state()
        if self.respond:
            self.hass.states.async_set("binary_sensor.actual_heating", "on" if self.powered else "off")

    async def async_turn_off(self, **kwargs):
        self.calls.append(False)
        if not self.accept_commands:
            return
        self._attr_is_on = False
        self.async_write_ha_state()
        if self.respond:
            self.hass.states.async_set("binary_sensor.actual_heating", "off")


class TestLight(LightEntity):
    _attr_name = "Test light"
    _attr_unique_id = "isolated-light"
    _attr_should_poll = False
    _attr_is_on = False
    _attr_brightness = 180
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_color_mode = ColorMode.BRIGHTNESS
    entity_id = "light.test_light"

    def __init__(self):
        self.calls = []
        self.fail_commands = False
        self.defer_state_writes = False
        self.percent_steps = False

    async def async_turn_on(self, **kwargs):
        self.calls.append(("on", kwargs))
        if self.fail_commands:
            from homeassistant.exceptions import HomeAssistantError
            raise HomeAssistantError("Synthetic light failure")
        self._attr_is_on = True
        brightness = kwargs.get("brightness", self._attr_brightness)
        if self.percent_steps:
            percentage = int(100 * (brightness + 1) / 255)
            brightness = round(255 * percentage / 100)
        self._attr_brightness = brightness
        if not self.defer_state_writes:
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self.calls.append(("off", kwargs))
        if self.fail_commands:
            from homeassistant.exceptions import HomeAssistantError
            raise HomeAssistantError("Synthetic light failure")
        self._attr_is_on = False
        if not self.defer_state_writes:
            self.async_write_ha_state()


class DevicePathTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass, self.temp = await start_hass(with_recorder=getattr(self, "with_recorder", False))
        self.addCleanup(self.temp.cleanup)
        self.addAsyncCleanup(DevicePathTests.cleanup_hass, self)
        assert await async_setup_component(self.hass, "switch", {})
        assert await async_setup_component(self.hass, "light", {})
        self.heater, self.light = TestHeater(), TestLight()
        await self.hass.data["switch"].async_add_entities([self.heater])
        await self.hass.data["light"].async_add_entities([self.light])
        self.hass.states.async_set("binary_sensor.actual_heating", "off")
        self.entry = await create_sauna(self.hass, binding_overrides={
            "heater_feedback": "binary_sensor.actual_heating", "control_input": "binary_sensor.operator"},
            parameter_overrides={"target_temperature_c": 80, "safety_temperature_c": 105,
                "sensor_timeout_seconds": 30, "feedback_timeout_seconds": 2,
                "fault_confirmation_seconds": 5, "minimum_heating_minutes": 0,
                "thermostat_cooldown_minutes": 0, "heating_minutes": 4,
                "heating_reduction_minutes": 1, "forced_cooling_minutes": 1,
                "after_run_minutes": .5, "confirmation_minutes": 13,
                "cooling_brightness_percent": 5, "mechanical_timer_minutes": 240})
        self.runtime = self.entry.runtime_data
        self.base = datetime.now(UTC)
        self.now = self.base
        self.runtime._clock = lambda: self.now
        self.light.async_write_ha_state()
        await self.hass.async_block_till_done()
        entities = er.async_entries_for_config_entry(er.async_get(self.hass), self.entry.entry_id)
        self.operation = next(e.entity_id for e in entities if e.unique_id.endswith("_operation"))
        self.climate = next(e.entity_id for e in entities if e.unique_id.endswith("_thermostat"))

    async def cleanup_hass(self):
        await self.hass.async_stop(force=True)
        if getattr(self, "heater", None) and self.heater.calls:
            self.assertFalse(self.heater.calls[-1])

    async def set_source(self, role, value):
        entity = self.entry.options["bindings"][role]
        old = self.hass.states.get(entity)
        self.hass.states.async_set(entity, str(value), old.attributes if old else {})
        await self.hass.async_block_till_done()

    async def time(self, seconds):
        self.now = self.base + timedelta(seconds=seconds)
        await self.runtime.tick()
        await self.hass.async_block_till_done()

    async def set_light_externally(self, on, brightness=None):
        """Simulate a directly linked physical light button or dimmer."""
        self.light._attr_is_on = on
        if brightness is not None:
            self.light._attr_brightness = brightness
        self.light.async_write_ha_state()
        await self.hass.async_block_till_done()

    async def test_full_measured_gang_and_cooling_chain_through_ha(self):
        self.assertTrue(self.heater.calls)
        self.assertFalse(any(self.heater.calls))
        self.assertIsNone(self.runtime.session)
        self.assertFalse(self.light.is_on)
        for position in ("upper", "lower"):
            await self.set_source(f"{position}_temperature", 70)
            await self.set_source(f"{position}_humidity", 40)
        await self.set_source("control_input", "on")
        session_id = self.runtime.session.session_id
        provisional = None
        confirmed = None
        measurements_fed = 0
        last_temperature = 70
        for second in range(451):
            self.now = self.base + timedelta(seconds=second)
            if second < 70:
                temperature, humidity = 70, 40
            elif second < 85:
                temperature, humidity = 70 - .3 * (second - 70), 40 - .08 * (second - 70)
            elif second < 150:
                temperature, humidity = 65.5, 38.8
            elif second < 260:
                temperature = 65.5 + .025 * (second - 150)
                humidity = 38.8 + .02 * (second - 150) + (3 if second >= 240 else 0)
            elif second < 280:
                temperature, humidity = 68.25, 44
            else:
                temperature, humidity = 68.25 - .05 * (second - 280), 44 - .06 * (second - 280)
            last_temperature = temperature
            for position in ("upper", "lower"):
                await self.set_source(f"{position}_temperature", temperature)
                await self.set_source(f"{position}_humidity", humidity)
                measurements_fed += 2
            await self.runtime.tick()
            await self.hass.async_block_till_done()
            gang = self.runtime.session.timeline.active
            if gang and not gang.infusion_events and provisional is None:
                provisional = gang
            if gang and gang.infusion_events and confirmed is None:
                self.assertIsNotNone(provisional)
                confirmed = gang
                self.assertEqual(gang.gang_id, provisional.gang_id)
                self.assertEqual(gang.started_at, provisional.started_at)
                self.assertTrue(self.heater.is_on)
            if self.runtime.controller.phase == "nachlauf":
                break
        self.assertIsNotNone(provisional)
        self.assertIsNotNone(confirmed)
        self.assertEqual(self.runtime.session.timeline.gang_count, 1)
        self.assertEqual(self.runtime.session.session_id, session_id)
        self.assertEqual(self.runtime.controller.phase, "nachlauf")
        self.assertFalse(self.heater.is_on)
        self.assertIsNotNone(self.runtime.session.after_run)
        end = self.runtime.session.after_run.ends_at
        # All preceding time is confirmed heat; no readiness idle occurred.
        # Integrate that known interval independently at the actual OFF time.
        from math import log
        heat_seconds = (self.runtime.session.after_run.started_at - self.base).total_seconds()
        expected_seconds = 30 + 900 / log(2) * (1 - 2 ** (-heat_seconds / 900)) / 2
        self.assertAlmostEqual(
            self.runtime.session.after_run.duration_seconds, expected_seconds, places=5
        )
        self.now = end
        await self.set_source("upper_temperature", last_temperature)
        await self.runtime.tick()
        await self.hass.async_block_till_done()
        self.assertIsNone(self.runtime.session.cooling)
        self.assertTrue(self.heater.is_on)
        self.assertGreater(self.runtime.session.heating.elapsed_seconds, 0)
        await self.runtime.archive.flush()
        import asyncio
        archived = await asyncio.to_thread(self.runtime.archive.read, session_id, limit=10000)
        originals = [r for r in archived["records"] if r["kind"] == "measurement"]
        self.assertGreaterEqual(len(originals), measurements_fed)
        self.assertTrue(any(r["kind"] == "command" and r["payload"]["heat"] for r in archived["records"]))
        self.assertTrue(any(r["kind"] == "detection" for r in archived["records"]))
        from custom_components.ha_sauna.core.timeline import Kind
        persons = [e for e in self.runtime.session.timeline.processed
                   if e.kind in (Kind.PERSON_STRONG, Kind.PERSON_WEAK)]
        self.assertEqual([e.event_id for e in persons], [provisional.recognition_event_id])
        self.assertGreater(self.runtime.session.heating.intervals[0].ended_at.timestamp() - self.base.timestamp(), 240)

    async def test_warmup_estimate_starts_from_history_and_moves_only_on_reports(self):
        """The real HA path blends archived and current upper measurements."""
        from dataclasses import replace
        from custom_components.ha_sauna.core.models import (
            Measurement,
            Position,
            Quantity,
            Session,
        )
        from custom_components.ha_sauna.core.timeline import Event, Kind

        source = self.entry.options["bindings"]["upper_temperature"]
        started = self.base - timedelta(seconds=360)
        completed = Session.create("completed-warmup", started)
        self.runtime.archive.append(
            "phase", started, {"phase": "aufheizen"}, completed.session_id
        )
        for second in range(0, 331, 30):
            value = 20 + second * 0.05
            measurement = Measurement(
                Position.UPPER,
                Quantity.TEMPERATURE,
                value,
                str(value),
                source,
                started + timedelta(seconds=second),
            )
            self.runtime.archive.append(
                "measurement",
                measurement.received_at,
                measurement,
                completed.session_id,
            )
        self.runtime.archive.append(
            "phase", self.base, {"phase": "bereit"}, completed.session_id
        )
        self.runtime.archive.save_session(
            replace(completed, ended_at=self.base),
            self.base,
            self.runtime.configuration.as_options(),
        )
        await self.runtime.archive.flush()

        for position in ("upper", "lower"):
            await self.set_source(f"{position}_temperature", 40)
            await self.set_source(f"{position}_humidity", 40)
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        task = self.runtime.device._historical_warmup_task
        if task is not None:
            await task
        self.now = self.base + timedelta(seconds=1)
        await self.set_source("upper_temperature", 40)
        await self.runtime.tick()
        historical = self.runtime.device.estimated_ready_seconds(self.now)
        self.assertAlmostEqual(historical, 800)

        self.now = self.base + timedelta(seconds=2)
        await self.runtime.tick()
        self.assertEqual(self.runtime.device.estimated_ready_seconds(self.now), historical)

        for second in range(30, 181, 30):
            self.now = self.base + timedelta(seconds=second)
            await self.set_source("upper_temperature", 40 + second * 0.1)
            await self.set_source("lower_temperature", 40)
            await self.runtime.tick()
            await self.hass.async_block_till_done()
        live = self.runtime.device.estimated_ready_seconds(self.now)
        self.assertIsNotNone(live)
        self.assertLess(live, historical)
        self.assertGreater(live, 220)  # Reine 0,1-°C/s-Live-ETA wäre 220 s.

        session_id = self.runtime.session.session_id
        self.now += timedelta(seconds=1)
        dropped = Measurement(
            Position.UPPER,
            Quantity.TEMPERATURE,
            50,
            "50",
            source,
            self.now,
        )
        self.runtime.device.measurements["upper_temperature"] = dropped
        self.runtime.device.last_valid_temperature = dropped
        await self.runtime.tick()

        self.now += timedelta(seconds=3)
        await self.runtime.receive(
            Event(
                "eta-door-open",
                session_id,
                Kind.DOOR_OPEN,
                self.base + timedelta(seconds=180),
                self.now,
            )
        )
        self.now += timedelta(seconds=1)
        await self.runtime.receive(
            Event("eta-door-close", session_id, Kind.DOOR_CLOSE, self.now, self.now)
        )
        self.now += timedelta(seconds=1)
        after_close = Measurement(
            Position.UPPER,
            Quantity.TEMPERATURE,
            50,
            "50",
            source,
            self.now,
        )
        self.runtime.device.measurements["upper_temperature"] = after_close
        self.runtime.device.last_valid_temperature = after_close
        await self.runtime.tick()
        after_door = self.runtime.device.estimated_ready_seconds(self.now)
        self.assertGreater(after_door, live)

        await self.runtime.tick()
        self.assertEqual(self.runtime.device.estimated_ready_seconds(self.now), after_door)

        self.now += timedelta(seconds=31)
        self.assertIsNone(self.runtime.device.estimated_ready_seconds(self.now))
        await self.set_source("lower_temperature", 49)
        self.assertEqual(self.runtime.device.regulation_measurement(self.now).position, Position.LOWER)
        self.assertEqual(self.runtime.device._warmup_key[2], Position.LOWER)
        self.assertIsNone(self.runtime.device.estimated_ready_seconds(self.now))

    async def test_infusion_confirms_presence_and_person_checks_stop_while_further_infusions_work(self):
        from custom_components.ha_sauna.core.timeline import Kind
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        first_gang = None
        for second in range(161):
            self.now = self.base + timedelta(seconds=second)
            temperature = 70 + second*.01
            humidity = 20 + second*.001 + max(0, second-70)*.02 + (2 if second>=70 else 0) + (2 if second>=140 else 0)
            for position in ("upper", "lower"):
                await self.set_source(position + "_temperature", temperature)
                await self.set_source(position + "_humidity", humidity)
            await self.runtime.tick()
            await self.hass.async_block_till_done()
            active = self.runtime.session.timeline.active
            if active and active.infusion_events:
                first_gang = first_gang or active
                self.assertEqual(active.gang_id, first_gang.gang_id)
                self.assertEqual(active.started_at, first_gang.started_at)
                if second % 5 == 0:
                    self.assertFalse(self.runtime.detector.diagnostic["checks"]["strong"])
                    self.assertNotIn("strong_temperature_slope", self.runtime.detector.diagnostic["metrics"]["upper"])
        self.assertIsNotNone(first_gang)
        self.assertEqual(first_gang.recognition_kind, Kind.INFUSION)
        self.assertEqual(len(self.runtime.session.timeline.active.infusion_events), 2)
        self.assertFalse(any(e.kind in (Kind.PERSON_STRONG,Kind.PERSON_WEAK)
                             for e in self.runtime.session.timeline.processed))
        self.assertTrue(self.heater.is_on)
        await self.runtime.archive.flush()
        import asyncio
        data = await asyncio.to_thread(self.runtime.archive.read, self.runtime.session.session_id, limit=10000)
        detections = [r["payload"]["event"]["kind"] for r in data["records"] if r["kind"] == "detection"]
        self.assertEqual(detections, [Kind.INFUSION,Kind.INFUSION])

    async def test_light_uses_real_service_for_temperature_curve_and_phase_ramps(self):
        from custom_components.ha_sauna.core.timeline import Event, Kind
        from custom_components.ha_sauna.settings import async_set_parameters
        await async_set_parameters(self.hass, self.entry, {"target_temperature_c": 80},
            partial=True, explicit_target=True, program_mode="constant")
        self.hass.states.async_set("sun.sun", "above_horizon", {"elevation": 10})
        await self.set_source("upper_temperature", 70)
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        # Bei 70 °C auf dem Weg zur Solltemperatur 80 °C folgt die Kurve:
        # 5 % am Kaltpunkt 30 °C, 40 % an der Solltemperatur. Das sind
        # 5 + (40 - 5) * (70 - 30) / (80 - 30) = 33 %.
        # Die Automatik übernimmt den vorhandenen Lichtwert erst über 30 s.
        self.assertAlmostEqual(self.light.brightness, 180, delta=1)
        await self.time(30)
        self.assertEqual(self.light.brightness, round(255 * .33))
        async def temperature(seconds):
            self.now = self.base + timedelta(seconds=seconds)
            await self.set_source("upper_temperature", 70)
            await self.runtime.tick()
            await self.hass.async_block_till_done()
        await temperature(30)
        session_id = self.runtime.session.session_id
        async def signal(kind, seconds):
            self.now=self.base+timedelta(seconds=seconds)
            await self.runtime.receive(Event(str(seconds),session_id,kind,self.now,self.now))
            await self.hass.async_block_till_done()
        self.assertEqual(self.runtime.session.timeline.door, Door.CLOSED)
        await signal(Kind.INFUSION,32)
        for second in range(60, 271, 30):
            await temperature(second)
        await temperature(271)
        normal = self.light.brightness
        self.assertAlmostEqual(normal, 255*.4, delta=1)
        await signal(Kind.DOOR_OPEN,272)
        await signal(Kind.VENTILATION,273)
        self.assertFalse(self.heater.is_on)
        self.assertAlmostEqual(self.light.brightness, normal, delta=1)
        await self.time(281)
        self.assertGreater(self.light.brightness, 255*.15)
        self.assertLess(self.light.brightness, normal)
        # Adaptive cooling is longer than the old fixed 30 s; its fade is
        # bounded by the configured 30-s light transition.
        cooling_end = (self.runtime.session.after_run.ends_at - self.base).total_seconds()
        await temperature(303)
        self.assertAlmostEqual(self.light.brightness,255*.15,delta=1)
        self.assertEqual(self.runtime.controller.phase, "nachlauf")
        await temperature(cooling_end)
        self.assertEqual(self.runtime.controller.phase,"aufheizen")
        self.assertIsNone(self.runtime.session.after_run)
        await temperature(cooling_end + 30)
        self.assertAlmostEqual(self.light.brightness, 255 * 33 / 100, delta=1)

    async def test_light_failure_is_reported_and_does_not_disable_heating(self):
        self.light.fail_commands=True
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        self.assertIn("operation_light",self.runtime.device.faults)
        self.assertTrue(self.heater.is_on)
        self.assertFalse(self.light.is_on)
        calls = len(self.light.calls)
        await self.time(1)
        # Die ersten Fade-Werte runden noch auf 0 %. Ein fehlgeschlagener
        # AUS-Dienst bleibt wiederholbar; er sperrt die Heizregelung nicht.
        self.assertEqual(len(self.light.calls), calls + 1)
        await self.time(2)
        self.assertEqual(len(self.light.calls), calls + 2)
        self.assertIn("operation_light", self.runtime.device.faults)
        self.assertTrue(self.heater.is_on)
        await self.runtime.set_operation(False)
        self.light.fail_commands=False
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        self.assertNotIn("operation_light",self.runtime.device.faults)
        await self.time(16)
        self.assertTrue(self.light.is_on)

    async def test_session_light_defaults_reach_real_light_service_and_switch_off(self):
        from custom_components.ha_sauna.core.display import phase_timer
        await self.runtime.set_operation(True)
        session_id = self.runtime.session.session_id
        await self.runtime.set_operation(False)
        await self.hass.async_block_till_done()
        self.heater.calls.clear()
        await self.time(30)
        self.assertAlmostEqual(self.light.brightness, 255 * 0.5, delta=1)
        await self.time(149)
        self.assertIsNotNone(self.runtime.session)
        self.assertAlmostEqual(self.light.brightness, 255 * 0.5, delta=1)
        await self.time(150)
        self.assertIsNone(self.runtime.session)
        self.assertFalse(self.light.is_on)
        self.assertEqual(self.runtime.device.light_output.last_automatic_brightness, 0)
        self.assertIsNone(phase_timer(self.runtime.controller, self.now))
        await self.time(751)
        self.assertNotIn(True, self.heater.calls)
        await self.runtime.archive.flush()
        stored = self.runtime.archive.read(session_id)
        commands = [r["payload"] for r in stored["records"] if r["kind"] == "light_command" and r["payload"]["purpose"] == "session_end"]
        self.assertTrue(any(c["service"] == "turn_on" and c["brightness_pct"] == 50
                            for c in commands))
        self.assertEqual(commands[-1]["service"], "turn_off")
        self.assertTrue(all(c["service_error"] is None for c in commands))

    async def _assert_session_light_expiry_stays_off(self, manual_minutes):
        from custom_components.ha_sauna.settings import async_set_parameters

        await async_set_parameters(self.hass, self.entry, {
            "session_gap_minutes": 1,
            "manual_override_minutes": 1 if manual_minutes is None else manual_minutes,
            "sensor_timeout_seconds": 180,
        }, partial=True)
        await self.hass.async_block_till_done()
        self.runtime = self.entry.runtime_data
        self.base = self.now = self.runtime._clock()
        self.runtime._clock = lambda: self.now
        # Let the initial automatic transition finish before the t1 OFF.
        await self.runtime.set_operation(True)
        self.base += timedelta(seconds=30)
        await self.time(0)
        self.assertTrue(self.light.is_on)
        self.now = self.base + timedelta(seconds=1)
        await self.runtime.set_operation(False)
        phase = self.runtime.controller.light_after_run
        self.assertEqual(phase.ends_at, self.base + timedelta(seconds=61))
        if manual_minutes is not None:
            await self.runtime.set_light_override(80)
            self.assertEqual(
                self.runtime.device.light_output.manual_ends_at,
                self.base + timedelta(seconds=1, minutes=manual_minutes),
            )
        for second in (2, 10, 20, 30, 40, 50, 60):
            await self.time(second)
            if manual_minutes == .5 and second >= 40:
                self.assertIsNone(self.runtime.device.light_output.manual_brightness)
                self.assertAlmostEqual(self.light.brightness, 255 * .5, delta=1)
        before = len(self.light.calls)
        for second in (61, 62, 65, 75, 90, 121):
            await self.time(second)
            self.assertFalse(self.light.is_on)
            self.assertEqual(self.hass.states.get(self.light.entity_id).state, "off")
            self.assertIsNone(self.runtime.device.light_output.manual_brightness)
            self.assertEqual(self.runtime.device.light_output.last_automatic_brightness, 0)
        self.assertEqual([call[0] for call in self.light.calls[before:]], ["off"])
        self.assertIsNone(self.runtime.session)
        self.assertEqual(self.runtime.controller.light_after_run.ends_at, phase.ends_at)
        # A fresh room-light choice after both deadlines still reaches the
        # service and its feedback; returning to automatic now means OFF.
        before = len(self.light.calls)
        await self.runtime.set_light_override(37)
        await self.hass.async_block_till_done()
        self.assertEqual([call[0] for call in self.light.calls[before:]], ["on"])
        self.assertAlmostEqual(self.light.brightness, 255 * .37, delta=1)
        self.assertEqual(self.hass.states.get(self.light.entity_id).state, "on")
        await self.runtime.set_light_override(None)
        await self.hass.async_block_till_done()
        self.assertFalse(self.light.is_on)
        self.assertEqual(self.hass.states.get(self.light.entity_id).state, "off")

    async def test_joint_session_light_and_manual_deadline_keeps_real_light_off(self):
        await self._assert_session_light_expiry_stays_off(1)

    async def test_session_light_deadline_without_manual_choice_keeps_real_light_off(self):
        await self._assert_session_light_expiry_stays_off(None)

    async def test_earlier_manual_expiry_resumes_session_light_then_keeps_real_light_off(self):
        await self._assert_session_light_expiry_stays_off(.5)

    async def _prepare_pending_session_light_expiry(self):
        from custom_components.ha_sauna.settings import async_set_parameters

        await async_set_parameters(self.hass, self.entry, {
            "session_gap_minutes": 1, "manual_override_minutes": 1,
            "feedback_timeout_seconds": .05, "sensor_timeout_seconds": 180,
            "light_transition_seconds": 0,
        }, partial=True)
        await self.hass.async_block_till_done()
        self.runtime = self.entry.runtime_data
        self.base = self.now = self.runtime._clock()
        self.runtime._clock = lambda: self.now
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        self.now = self.base + timedelta(seconds=1)
        await self.runtime.set_operation(False)
        await self.runtime.set_light_override(80)
        await self.hass.async_block_till_done()
        phase = self.runtime.controller.light_after_run
        self.assertEqual(phase.ends_at, self.base + timedelta(seconds=61))
        self.assertEqual(self.hass.states.get(self.light.entity_id).state, "on")
        self.light.calls.clear()
        return phase

    async def _assert_pending_due_off_completion(
        self, caller, *, early_feedback=False, fails=False, finishes_before_tick=False,
    ):
        import json
        import zipfile

        phase = await self._prepare_pending_session_light_expiry()
        adapter = self.runtime.device
        original_off, original_wait = self.light.async_turn_off, adapter._wait_light_service
        entered, release, waiting = asyncio.Event(), asyncio.Event(), asyncio.Event()
        service, following = None, None
        held_runtime_lock = False
        following_at = self.base + timedelta(seconds=62)

        async def paused_off(**kwargs):
            if not entered.is_set():
                entered.set()
                if early_feedback:
                    await original_off(**kwargs)
                await release.wait()
                if fails:
                    from homeassistant.exceptions import HomeAssistantError
                    raise HomeAssistantError("Synthetic completion failure after OFF feedback")
                if early_feedback:
                    return
            await original_off(**kwargs)

        async def observe_wait():
            if self.now == following_at and adapter._light_service_task is service:
                self.assertFalse(service.done())
                waiting.set()
            return await original_wait()

        self.now = planned_at = phase.ends_at
        with (
            patch.object(self.light, "async_turn_off", side_effect=paused_off),
            patch.object(adapter, "_wait_light_service", side_effect=observe_wait),
        ):
            first = asyncio.create_task(self.runtime.tick())
            try:
                await asyncio.wait_for(entered.wait(), 3)
                service = adapter._light_service_task
                if caller == "cancel":
                    first.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await first
                else:
                    await first
                    self.assertEqual(adapter.faults["session_light"], "service_unavailable")
                self.assertFalse(service.done())
                if finishes_before_tick:
                    # Queued HA feedback cycles may also request OFF. Keep
                    # them behind the runtime lock until the actual service
                    # has finished, so every following cycle sees done=True.
                    await self.runtime._lock.acquire()
                    held_runtime_lock = True
                else:
                    self.now = following_at
                    following = asyncio.create_task(self.runtime.tick())
                    await asyncio.wait_for(waiting.wait(), 3)
                    self.assertFalse(following.done())
                self.assertIsNone(adapter._light_session_off_completed_key)
                self.now = completed_at = self.base + timedelta(seconds=63)
                release.set()
                if fails:
                    from homeassistant.exceptions import HomeAssistantError
                    with self.assertRaises(HomeAssistantError):
                        await service
                else:
                    await service
                if finishes_before_tick:
                    self.assertTrue(service.done())
                    held_runtime_lock = False
                    self.runtime._lock.release()
                    following = asyncio.create_task(self.runtime.tick())
                await following
                await self.hass.async_block_till_done()
            finally:
                release.set()
                if held_runtime_lock:
                    held_runtime_lock = False
                    self.runtime._lock.release()
                await asyncio.gather(
                    *(task for task in (first, service, following) if task is not None),
                    return_exceptions=True,
                )
        expected_count = 2 if fails else 1
        self.assertEqual([kind for kind, _kwargs in self.light.calls], ["off"] * expected_count)
        self.assertEqual(self.hass.states.get(self.light.entity_id).state, "off")
        self.assertIsNone(adapter.light_output.manual_brightness)
        self.assertEqual(adapter.light_output.last_automatic_brightness, 0)
        self.assertEqual(adapter._light_session_off_completed_key, (
            "session_light", phase.session_id, phase.started_at,
        ))
        self.assertNotIn("session_light", adapter.faults)
        for second in (65, 90, 121):
            await self.time(second)
        self.assertEqual(len(self.light.calls), expected_count)
        await self.runtime.archive.flush()
        commands = [record for record in self.runtime.archive.read(phase.session_id)["records"]
                    if record["kind"] == "light_command"
                    and record["payload"]["phase"] == "session_light"
                    and record["payload"]["service"] == "turn_off"]
        self.assertEqual(len(commands), expected_count)
        command = commands[0]
        self.assertEqual(command["session_id"], phase.session_id)
        self.assertEqual(command["payload"], {
            "planned_at": planned_at.isoformat(), "purpose": "session_end",
            "phase": "session_light", "service": "turn_off", "brightness_pct": None,
            "ends_at": phase.ends_at.isoformat(), "sent_at": planned_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "service_error": "HomeAssistantError" if fails else None,
        })
        self.assertEqual(command["received_at"], completed_at.isoformat())
        if fails:
            self.assertEqual(commands[1]["payload"], {
                **command["payload"],
                "planned_at": (completed_at if finishes_before_tick else following_at).isoformat(),
                "sent_at": completed_at.isoformat(), "service_error": None,
            })
            self.assertEqual(commands[1]["received_at"], completed_at.isoformat())
        path = await self.runtime.archive.export()
        try:
            with zipfile.ZipFile(path) as archive:
                exported = [json.loads(line) for line in archive.read("records.jsonl").splitlines()]
            ids = {record["id"] for record in commands}
            self.assertEqual([record for record in exported if record["id"] in ids], commands)
        finally:
            path.unlink()

    async def test_cancelled_pending_due_off_is_consumed_after_wait(self):
        await self._assert_pending_due_off_completion("cancel")

    async def test_timed_out_pending_due_off_is_consumed_after_wait(self):
        await self._assert_pending_due_off_completion("timeout")

    async def test_pending_due_off_with_early_feedback_is_consumed_after_wait(self):
        await self._assert_pending_due_off_completion("cancel", early_feedback=True)

    async def test_pending_due_off_failure_after_feedback_still_requires_retry(self):
        await self._assert_pending_due_off_completion("cancel", early_feedback=True, fails=True)

    async def test_completed_due_off_failure_after_feedback_still_requires_retry(self):
        await self._assert_pending_due_off_completion(
            "cancel", early_feedback=True, fails=True, finishes_before_tick=True,
        )

    async def test_unconfirmed_due_off_waits_for_feedback_window_then_retries(self):
        phase = await self._prepare_pending_session_light_expiry()
        original_off = self.light.async_turn_off
        ignored = False

        async def ignored_first_off(**kwargs):
            nonlocal ignored
            if not ignored:
                ignored = True
                self.light.calls.append(("off", kwargs))
                return
            await original_off(**kwargs)

        with patch.object(self.light, "async_turn_off", side_effect=ignored_first_off):
            for second, state, count in ((61, "on", 1), (61.01, "on", 1), (61.06, "off", 2)):
                await self.time(second)
                self.assertEqual(self.hass.states.get(self.light.entity_id).state, state)
                self.assertEqual([kind for kind, _kwargs in self.light.calls], ["off"] * count)
        await self.time(62)
        self.assertEqual(len(self.light.calls), 2)
        await self.runtime.archive.flush()
        commands = [record["payload"]
                    for record in self.runtime.archive.read(phase.session_id)["records"]
                    if record["kind"] == "light_command"
                    and record["payload"]["phase"] == "session_light"
                    and record["payload"]["service"] == "turn_off"]
        self.assertEqual(len(commands), 2)
        self.assertEqual([command["sent_at"] for command in commands], [
            phase.ends_at.isoformat(), (self.base + timedelta(seconds=61.06)).isoformat(),
        ])
        self.assertTrue(all(command["ends_at"] == phase.ends_at.isoformat()
                            and command["service_error"] is None for command in commands))

    async def test_manual_session_finish_ends_the_gap_light_in_both_modes(self):
        from dataclasses import replace

        for mode in ("automatic", "manual"):
            with self.subTest(mode=mode):
                if mode == "manual":
                    self.runtime.controller.set_control_mode(mode)
                    self.runtime.configuration = replace(self.runtime.configuration, control_mode=mode)
                await self.runtime.set_operation(True)
                await self.runtime.set_light_override(50)
                await self.runtime.set_operation(False)
                token = next(deadline.token for deadline in self.runtime.session.deadlines if deadline.purpose == "session_gap")

                await self.runtime.finish_session_gap(token)
                await self.hass.async_block_till_done()
                self.assertIsNone(self.runtime.session)
                self.assertFalse(self.heater.is_on)
                self.assertFalse(self.light.is_on)
                await self.runtime.tick()
                self.assertFalse(self.light.is_on)

                await self.runtime.set_operation(True)
                await self.runtime.set_light_override(50)
                await self.runtime.set_operation(False)
                token = next(deadline.token for deadline in self.runtime.session.deadlines if deadline.purpose == "session_gap")
                self.light.fail_commands = True
                await self.runtime.finish_session_gap(token)
                self.assertIsNone(self.runtime.session)
                self.assertFalse(self.heater.is_on)
                self.light.fail_commands = False
                await self.runtime.tick()
                await self.hass.async_block_till_done()
                self.assertFalse(self.light.is_on)

    async def test_custom_session_light_survives_options_reload_and_new_start_cancels_it(self):
        from custom_components.ha_sauna.settings import async_set_parameters
        await async_set_parameters(self.hass, self.entry, {
            "session_gap_minutes": .6, "session_light_brightness_percent": 64}, partial=True)
        await self.hass.async_block_till_done()
        self.runtime = self.entry.runtime_data
        self.base = self.now = datetime.now(UTC)
        self.runtime._clock = lambda: self.now
        await self.runtime.set_operation(True)
        # An explicit session end permits settings while its light finishes;
        # ordinary OFF keeps the session resumable and settings locked.
        async with self.runtime._lock:
            self.runtime.controller.finish_session(self.now)
            await self.runtime._cycle()
        await self.hass.async_block_till_done()
        await self.time(30)
        phase = self.runtime.controller.light_after_run
        self.assertEqual(phase.ends_at, self.base + timedelta(seconds=36))
        self.assertAlmostEqual(self.light.brightness, 255*.64, delta=1)
        # Der laufende Lichtnachlauf besitzt sein Ziel als Controller-Snapshot;
        # ein Optionen-Reload darf ihn nicht auf den neuen Standard umstellen.
        await async_set_parameters(self.hass, self.entry, {
            "nominal_power_kw": 5, "session_light_brightness_percent": 20}, partial=True)
        await self.hass.async_block_till_done()
        self.runtime = self.entry.runtime_data
        self.runtime._clock = lambda: self.now
        self.assertEqual(self.runtime.controller.light_after_run, phase)
        await self.time(31)
        self.assertAlmostEqual(self.light.brightness, 255*.64, delta=1)
        await self.set_source("upper_temperature", 70)
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        self.assertIsNone(self.runtime.controller.light_after_run)
        # Der Neustart ersetzt den Lichtnachlauf, blendet aber vom sichtbaren
        # 64-%-Wert in seine neue temperaturabhängige Vorgabe über.
        self.assertAlmostEqual(self.light.brightness, 255*.64, delta=1)
        await self.time(37)
        self.assertTrue(self.light.is_on)
        self.assertLess(self.light.brightness, 255*.64)
        self.assertGreater(self.light.brightness, 255*.05)

    async def test_failed_session_light_commands_are_archived_and_retried(self):
        await self.runtime.set_operation(True)
        await self.time(30)
        self.assertTrue(self.light.is_on)
        self.light.fail_commands = True
        await self.runtime.set_operation(False)
        await self.time(31)  # The first distinct fade value requires a command.
        self.assertEqual(self.runtime.device.faults["session_light"], "service_unavailable")
        calls = len(self.light.calls)
        await self.time(32)
        self.assertEqual(len(self.light.calls), calls + 1)
        await self.time(180)
        self.assertEqual(self.runtime.device.faults["session_light"], "service_unavailable")
        off_calls = len([call for call in self.light.calls if call[0] == "off"])
        await self.time(781)
        self.assertEqual(len([call for call in self.light.calls if call[0] == "off"]), off_calls + 1)
        self.assertFalse(self.heater.is_on)
        await self.runtime.archive.flush()
        stored = self.runtime.archive.read(self.runtime.controller.light_after_run.session_id)
        failed = [r["payload"] for r in stored["records"] if r["kind"] == "light_command"
                  and r["payload"]["service_error"]]
        self.assertGreaterEqual(len(failed), 4)

    async def test_manual_light_after_expiry_supersedes_off_without_flicker(self):
        await self.runtime.set_operation(True)
        await self.runtime.set_operation(False)
        await self.time(150)
        self.now = self.base + timedelta(seconds=750)
        calls = len(self.light.calls)
        await self.runtime.set_light_override(37)
        await self.hass.async_block_till_done()
        self.assertEqual([call[0] for call in self.light.calls[calls:]], ["on"])
        self.assertAlmostEqual(self.light.brightness, 255*.37, delta=1)
        self.assertEqual(self.runtime.device.light_output.last_automatic_brightness, 0)
        await self.runtime.set_light_override(None)
        self.assertFalse(self.light.is_on)

    async def test_automatic_light_service_echo_does_not_create_manual_override(self):
        await self.runtime.set_operation(True)
        await self.time(1)  # the first fade value still rounds to brightness 0
        self.assertFalse(self.light.is_on)
        self.assertIsNone(self.runtime.device.light_output.manual_brightness)
        await self.time(30)
        self.assertTrue(self.light.is_on)
        self.assertIsNone(self.runtime.device.light_output.manual_brightness)

    async def test_repeated_light_echoes_consume_each_sent_command(self):
        device = self.runtime.device
        for brightness in (39, 40, 39):
            device._expect_light_change(self.now, "turn_on", brightness)
        self.assertEqual(len(device._expected_light_changes), 3)

        for raw_brightness in (99, 102, 99):
            await self.set_light_externally(True, raw_brightness)
            self.assertIsNone(device.light_output.manual_brightness)

        await self.set_light_externally(True, 128)
        self.assertAlmostEqual(
            device.light_output.manual_brightness, 128 * 100 / 255
        )
        await self.set_light_externally(False)
        self.assertEqual(device.light_output.manual_brightness, 0)

    async def test_percent_step_light_echo_stays_automatic_after_fade(self):
        self.light.percent_steps = True
        self.hass.states.async_set("sun.sun", "above_horizon", {"elevation": 10})
        await self.set_source("upper_temperature", 70)
        await self.runtime.set_operation(True)
        await self.time(30)
        self.assertIsNone(self.runtime.device.light_output.manual_brightness)
        await self.set_source("upper_temperature", 70)
        calls = len(self.light.calls)
        await self.time(31)
        await self.time(32)
        self.assertEqual(len(self.light.calls), calls)

        await self.runtime.archive.flush()
        records = self.runtime.archive.read(self.runtime.session.session_id)["records"]
        brightnesses = [
            record["payload"]["brightness_pct"]
            for record in records
            if record["kind"] == "light_command"
            and record["payload"]["service"] == "turn_on"
        ]
        self.assertTrue(brightnesses)
        self.assertTrue(all(value == round(value) for value in brightnesses))

    async def test_external_light_selection_expires_but_unchanged_report_does_not_extend_it(self):
        await self.runtime.set_operation(True)
        await self.set_light_externally(True, 128)
        output = self.runtime.device.light_output
        self.assertAlmostEqual(output.manual_brightness, 128 * 100 / 255)
        ends_at = output.manual_ends_at
        self.light.async_write_ha_state()  # state_report, not another choice
        await self.hass.async_block_till_done()
        self.assertEqual(output.manual_ends_at, ends_at)
        await self.time(600)
        self.assertIsNone(output.manual_brightness)

    async def test_external_light_off_is_the_same_manual_selection(self):
        await self.runtime.set_operation(True)
        await self.time(30)
        await self.set_light_externally(False)
        output = self.runtime.device.light_output
        self.assertEqual(output.manual_brightness, 0)
        self.assertEqual(output.manual_ends_at, self.now + timedelta(minutes=10))

    async def test_external_light_selection_is_indefinite_in_manual_mode(self):
        from dataclasses import replace

        self.runtime.controller.set_control_mode("manual")
        self.runtime.configuration = replace(
            self.runtime.configuration, control_mode="manual"
        )
        await self.set_light_externally(True, 128)
        output = self.runtime.device.light_output
        self.assertAlmostEqual(output.manual_brightness, 128 * 100 / 255)
        self.assertIsNone(output.manual_ends_at)
        await self.time(600)
        self.assertAlmostEqual(output.manual_brightness, 128 * 100 / 255)

    async def test_delayed_automatic_echo_does_not_replace_external_dimmer_selection(self):
        self.light.defer_state_writes = True
        await self.runtime.set_operation(True)
        await self.time(30)
        automatic_brightness = self.light.brightness  # command sent, echo pending
        await self.set_light_externally(True, 128)
        output = self.runtime.device.light_output
        self.assertAlmostEqual(output.manual_brightness, 128 * 100 / 255)
        self.light._attr_brightness = automatic_brightness
        self.light.async_write_ha_state()  # delayed echo of the automatic command
        await self.hass.async_block_till_done()
        self.assertAlmostEqual(output.manual_brightness, 128 * 100 / 255)
        self.assertEqual(self.light.calls[-1], ("on", {"brightness": 128}))

    async def test_pending_light_command_is_not_repeated_and_external_choice_still_applies(self):
        self.light.defer_state_writes = True
        await self.runtime.set_light_override(37)
        calls = len(self.light.calls)
        await self.runtime.tick()
        await self.hass.async_block_till_done()
        self.assertEqual(len(self.light.calls), calls)

        await self.set_light_externally(True, 128)
        self.assertAlmostEqual(
            self.runtime.device.light_output.manual_brightness, 128 * 100 / 255
        )

    async def test_session_light_deadline_survives_options_change_but_not_restart(self):
        from custom_components.ha_sauna.settings import async_set_parameters
        await async_set_parameters(self.hass, self.entry, {
            "session_gap_minutes": .2, "session_light_brightness_percent": 64}, partial=True)
        await self.hass.async_block_till_done()
        self.runtime = self.entry.runtime_data
        self.base = self.now = datetime.now(UTC)
        self.runtime._clock = lambda: self.now
        await self.runtime.set_operation(True)
        async with self.runtime._lock:
            self.runtime.controller.finish_session(self.now)
            await self.runtime._cycle()
        await self.time(5)
        phase = self.runtime.controller.light_after_run
        await async_set_parameters(self.hass, self.entry, {"nominal_power_kw": 5}, partial=True)
        await self.hass.async_block_till_done()
        self.runtime = self.entry.runtime_data
        self.runtime._clock = lambda: self.now
        self.assertEqual(self.runtime.controller.light_after_run, phase)
        await self.hass.config_entries.async_reload(self.entry.entry_id)
        await self.hass.async_block_till_done()
        self.runtime = self.entry.runtime_data
        self.runtime._clock = lambda: self.now
        self.assertIsNone(self.runtime.controller.light_after_run)

    async def test_failed_old_light_off_blocks_reassignment_and_keeps_timer(self):
        await self.runtime.set_operation(True)
        async with self.runtime._lock:
            self.runtime.controller.finish_session(self.now)
            await self.runtime._cycle()
        await self.time(30)
        self.assertTrue(self.light.is_on)
        phase = self.runtime.controller.light_after_run
        self.light.fail_commands = True
        old_light = self.entry.options["bindings"]["light"]
        replacement = "light.replacement"
        self.hass.states.async_set(replacement, "off", {"supported_color_modes": ["brightness"]})
        self.hass.config_entries.async_update_entry(
            self.entry,
            options={
                **self.entry.options,
                "bindings": {**self.entry.options["bindings"], "light": replacement},
            },
        )
        await self.hass.async_block_till_done()
        self.assertIs(self.entry.runtime_data, self.runtime)
        self.assertEqual(self.entry.options["bindings"]["light"], old_light)
        self.assertEqual(self.runtime.controller.light_after_run, phase)
        self.assertFalse(self.runtime.reconfiguring)
        self.assertTrue(self.light.is_on)
        self.assertEqual(self.runtime.device.faults["session_light"], "service_unavailable")
        self.light.fail_commands = False
        await self.runtime.set_light_override(37)
        await self.hass.async_block_till_done()
        self.assertAlmostEqual(self.light.brightness, 255 * .37, delta=1)

    async def test_unconfirmed_old_light_off_blocks_reassignment_and_keeps_timer(self):
        await self.runtime.set_operation(True)
        async with self.runtime._lock:
            self.runtime.controller.finish_session(self.now)
            await self.runtime._cycle()
        await self.time(30)
        self.assertTrue(self.light.is_on)
        phase = self.runtime.controller.light_after_run
        self.light.defer_state_writes = True
        old_light = self.entry.options["bindings"]["light"]
        replacement = "light.replacement"
        self.hass.states.async_set(replacement, "off", {"supported_color_modes": ["brightness"]})
        self.hass.config_entries.async_update_entry(
            self.entry,
            options={
                **self.entry.options,
                "bindings": {**self.entry.options["bindings"], "light": replacement},
            },
        )
        await self.hass.async_block_till_done()
        self.assertIs(self.entry.runtime_data, self.runtime)
        self.assertEqual(self.entry.options["bindings"]["light"], old_light)
        self.assertEqual(self.runtime.controller.light_after_run, phase)
        self.assertFalse(self.runtime.reconfiguring)
        self.assertEqual(self.hass.states.get(old_light).state, "on")
        self.assertEqual(self.runtime.device.faults["session_light"], "feedback_missing")

    async def test_old_light_stays_off_while_reassignment_waits_for_reload(self):
        await self.runtime.set_operation(True)
        async with self.runtime._lock:
            self.runtime.controller.finish_session(self.now)
            await self.runtime._cycle()
        await self.time(30)
        self.assertTrue(self.light.is_on)
        old_runtime = self.runtime
        replacement = "light.replacement"
        self.hass.states.async_set(
            replacement, "off", {"supported_color_modes": ["brightness"]}
        )
        entered, release = asyncio.Event(), asyncio.Event()
        original_reload = self.hass.config_entries.async_reload

        async def paused_reload(entry_id):
            entered.set()
            await release.wait()
            return await original_reload(entry_id)

        with patch.object(
            self.hass.config_entries, "async_reload", side_effect=paused_reload
        ):
            self.hass.config_entries.async_update_entry(
                self.entry,
                options={
                    **self.entry.options,
                    "bindings": {
                        **self.entry.options["bindings"], "light": replacement,
                    },
                },
            )
            try:
                await asyncio.wait_for(entered.wait(), timeout=3)
                self.assertFalse(self.light.is_on)
                calls = len(self.light.calls)
                await old_runtime.tick()
                self.assertFalse(self.light.is_on)
                self.assertEqual(len(self.light.calls), calls)
            finally:
                release.set()
            await self.hass.async_block_till_done()

    async def test_additional_door_signal_updates_timeline_without_obsolete_cooling_wait(self):
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        for second in range(70):
            self.now=self.base+timedelta(seconds=second)
            temperature=50 if second<20 else 50-.02*min(20,second-20)+max(0,second-40)*.03
            for position in ("upper","lower"):
                await self.set_source(f"{position}_temperature",temperature)
                await self.set_source(f"{position}_humidity",30)
            await self.runtime.tick()
            await self.hass.async_block_till_done()
        from custom_components.ha_sauna.core.timeline import Kind
        doors=[e for e in self.runtime.session.timeline.processed if e.kind in (Kind.DOOR_OPEN,Kind.DOOR_CLOSE)]
        self.assertEqual([e.kind for e in doors],[Kind.DOOR_OPEN,Kind.DOOR_CLOSE])
        self.assertFalse(any(d.purpose == "person_opportunity" for d in self.runtime.session.deadlines))
        self.assertTrue(self.heater.is_on)

    async def test_temperature_entities_cannot_override_running_cooling(self):
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        from custom_components.ha_sauna.core.timeline import Event, Kind
        identity = self.runtime.session.session_id
        self.assertEqual(self.runtime.session.timeline.door, Door.CLOSED)
        for kind, second in ((Kind.INFUSION, 2),
                             (Kind.DOOR_OPEN, 3), (Kind.VENTILATION, 4)):
            self.now = self.base + timedelta(seconds=second)
            await self.runtime.receive(Event(f"cool:{second}", identity, kind, self.now, self.now))
            await self.hass.async_block_till_done()
        self.assertEqual(self.runtime.controller.phase,"nachlauf")
        self.assertFalse(self.heater.is_on)
        cycle=self.runtime.session.after_run
        deadlines=self.runtime.session.deadlines
        self.heater.calls.clear()
        await self.hass.services.async_call("climate","set_temperature",{"entity_id":self.climate,"temperature":95},blocking=True)
        entities=er.async_entries_for_config_entry(er.async_get(self.hass),self.entry.entry_id)
        end=next(e.entity_id for e in entities if e.unique_id.endswith("_final_temperature_c"))
        await self.hass.services.async_call("number","set_value",{"entity_id":end,"value":100},blocking=True)
        await self.hass.async_block_till_done()
        self.assertIs(self.entry.runtime_data,self.runtime)
        self.assertEqual(self.runtime.session.after_run,cycle)
        self.assertEqual(self.runtime.session.deadlines,deadlines)
        self.assertFalse(self.heater.is_on)
        self.assertNotIn(True,self.heater.calls)
        self.assertEqual(self.hass.states.get(self.climate).attributes["temperature"],95)
        self.assertEqual(float(self.hass.states.get(end).state),100)

    async def test_normal_idle_keeps_operation_and_ui_physical_input_share_control(self):
        await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": self.climate, "hvac_mode": "heat"}, blocking=True)
        await self.hass.async_block_till_done()
        self.assertEqual(self.hass.states.get(self.operation).state, "on")
        identity = self.runtime.session.session_id
        await self.time(10)
        await self.set_source("upper_temperature", 85)
        self.assertFalse(self.heater.is_on)
        self.assertTrue(self.runtime.session.operation_enabled)
        self.assertEqual(self.runtime.session.session_id, identity)
        self.assertEqual(self.hass.states.get(self.climate).attributes["hvac_action"], "idle")
        self.assertEqual(self.runtime.controller.mechanical_timer_status["pause_reason"], "contactor_off")
        await self.time(20)
        self.assertEqual(self.runtime.controller.mechanical_timer_status["remaining_seconds"], 14390)
        self.assertIsNone(self.runtime.controller.mechanical_timer_ends_at)
        await self.set_source("upper_temperature", 70)
        await self.time(25)
        self.assertEqual(self.runtime.controller.mechanical_timer_status["remaining_seconds"], 14385)
        await self.set_source("control_input", "on")
        await self.set_source("control_input", "off")
        self.assertFalse(self.runtime.session.operation_enabled)
        await self.hass.services.async_call("switch", "turn_on", {"entity_id": self.operation}, blocking=True)
        self.assertEqual(self.runtime.session.session_id, identity)

    async def test_successful_command_without_feedback_does_not_count_heating(self):
        self.heater.accept_commands = False
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        self.assertIn(True, self.heater.calls)
        self.assertFalse(self.heater.is_on)
        await self.time(2)
        self.assertNotIn("heater_feedback_mismatch", self.runtime.controller.protection)
        await self.time(7)
        self.assertEqual(self.runtime.session.heating.elapsed_seconds, 0)
        self.assertEqual(self.runtime.controller.mechanical_timer_status["remaining_seconds"], 14400)
        self.assertIsNone(self.runtime.controller.mechanical_timer_ends_at)
        self.assertIn("heater_feedback_mismatch", self.runtime.controller.protection)
        self.assertFalse(self.heater.is_on)
        await self.time(8)
        self.assertFalse(self.heater.is_on)
        self.assertTrue(self.runtime.session.operation_enabled)

    async def test_transient_sensor_failure_is_visible_without_latched_shutdown(self):
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        await self.set_source("upper_temperature", "unavailable")
        await self.time(1)
        self.assertIn("upper_temperature", self.runtime.device.faults)
        self.assertEqual(self.runtime.controller.protection, set())
        self.assertTrue(self.heater.is_on)
        self.assertEqual(tuple(p.value for p in self.runtime.detector.active_positions), ("lower",))
        await self.set_source("upper_temperature", 70)
        await self.time(2)
        self.assertNotIn("upper_temperature", self.runtime.device.faults)
        self.assertEqual(len(self.runtime.detector.active_positions), 2)

    async def test_upper_temperature_leads_and_lower_takes_over_only_after_upper_expires(self):
        self.now = self.base + timedelta(seconds=1)
        await self.set_source("lower_temperature", 70)
        await self.set_source("upper_temperature", 90)
        await self.runtime.set_operation(True)
        self.assertEqual(self.runtime.controller.temperature, 90)
        self.assertEqual(self.runtime.device.regulation_measurement(self.now).position.value, "upper")
        self.assertFalse(self.heater.is_on)

        await self.set_source("upper_temperature", "unavailable")
        self.now = self.base + timedelta(seconds=32)
        await self.set_source("lower_temperature", 70)
        self.assertEqual(self.runtime.controller.temperature, 70)
        self.assertEqual(self.runtime.device.regulation_measurement(self.now).position.value, "lower")
        self.assertIn("upper_temperature", self.runtime.device.faults)
        self.assertNotIn("regulation_temperature_unavailable", self.runtime.device.faults)
        self.assertFalse(self.runtime.controller.protection)
        self.assertTrue(self.heater.is_on)

        await self.set_source("upper_temperature", 90)
        self.assertEqual(self.runtime.controller.temperature, 90)
        self.assertEqual(self.runtime.device.regulation_measurement(self.now).position.value, "upper")
        self.assertNotIn("upper_temperature", self.runtime.device.faults)
        self.assertFalse(self.heater.is_on)

    async def prepare_temperature_reporting_gap(self, validity_seconds):
        from custom_components.ha_sauna.core.display import phase_timer
        options = {**self.entry.options, "parameters": {**self.entry.options["parameters"],
            "sensor_timeout_seconds": validity_seconds, "fault_confirmation_seconds": 120,
            "minimum_heating_minutes": 10, "heating_minutes": 90}}
        self.hass.config_entries.async_update_entry(self.entry, options=options)
        await self.hass.async_block_till_done()
        self.runtime = self.entry.runtime_data
        self.base = self.now = datetime.now(UTC)
        self.runtime._clock = lambda: self.now
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        self.now = self.base + timedelta(seconds=610)
        await self.set_source("upper_temperature", 70)
        self.assertEqual(phase_timer(self.runtime.controller, self.now)["kind"], "heating")
        self.heater.calls.clear()

    async def test_suitable_validity_preserves_completed_minimum_heating_across_reporting_gap(self):
        from custom_components.ha_sauna.core.display import phase_timer
        await self.prepare_temperature_reporting_gap(30)
        started_at = self.runtime.session.heating.intervals[0].started_at
        await self.time(616)
        self.assertEqual(self.runtime.controller.temperature, 70)
        self.assertNotIn("regulation_temperature_unavailable", self.runtime.device.faults)
        self.assertTrue(self.heater.is_on)
        self.assertFalse(self.runtime.controller.protection)
        self.now = self.base + timedelta(seconds=616.2)
        await self.set_source("upper_temperature", 71)
        self.assertNotIn("regulation_temperature_unavailable", self.runtime.device.faults)
        self.assertTrue(self.heater.is_on)
        self.assertFalse(self.heater.calls)
        self.assertEqual(len(self.runtime.session.heating.intervals), 1)
        self.assertEqual(self.runtime.session.heating.intervals[0].started_at, started_at)
        self.assertIsNone(self.runtime.session.heating.intervals[0].ended_at)
        self.assertEqual(phase_timer(self.runtime.controller, self.now)["kind"], "heating")
        self.assertAlmostEqual(self.runtime.session.heating.elapsed_seconds, 616.2, places=3)

    async def test_too_short_validity_causes_real_switching_and_new_minimum_heating(self):
        from custom_components.ha_sauna.core.display import phase_timer
        await self.prepare_temperature_reporting_gap(5)
        await self.time(616)
        self.assertIsNone(self.runtime.controller.temperature)
        self.assertFalse(self.heater.is_on)
        self.assertEqual(self.runtime.controller.last_decision.reason, "upper_temperature_unavailable")
        self.assertFalse(self.runtime.controller.protection)
        self.now = self.base + timedelta(seconds=616.2)
        await self.set_source("upper_temperature", 71)
        self.assertTrue(self.heater.is_on)
        self.assertEqual(self.heater.calls, [False, True])
        self.assertEqual(len(self.runtime.session.heating.intervals), 2)
        self.assertEqual(self.runtime.session.heating.intervals[-1].started_at, self.now)
        self.assertEqual(phase_timer(self.runtime.controller, self.now)["kind"], "minimum_heating")
        self.assertEqual(phase_timer(self.runtime.controller, self.now)["seconds"], 600)

    async def test_expired_temperature_stops_immediately_and_persistent_failure_latches(self):
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        await self.set_source("upper_temperature", "unavailable")
        await self.time(31)
        self.assertIsNone(self.runtime.controller.temperature)
        self.assertEqual(self.runtime.device.faults["regulation_temperature_unavailable"], "pending")
        self.assertFalse(self.heater.is_on)
        await self.time(35.9)
        self.assertFalse(self.heater.is_on)
        await self.time(36)
        self.assertFalse(self.heater.is_on)
        self.assertIn("regulation_temperature_unavailable", self.runtime.controller.protection)
        self.assertEqual(self.runtime.device.faults["regulation_temperature_unavailable"], "confirmed")
        self.assertTrue(self.runtime.session.operation_enabled)
        self.heater.calls.clear()
        await self.set_source("upper_temperature", 70)
        await self.time(37)
        self.assertFalse(self.heater.is_on)
        self.assertNotIn(True, self.heater.calls)
        self.assertEqual(len(self.runtime.session.heating.intervals), 1)

    async def test_queued_control_edges_cannot_backdate_a_late_service_failure(self):
        from homeassistant.exceptions import HomeAssistantError

        options = {
            **self.entry.options,
            "parameters": {
                **self.entry.options["parameters"],
                "sensor_timeout_seconds": 120,
                "feedback_timeout_seconds": 60,
            },
        }
        self.hass.config_entries.async_update_entry(self.entry, options=options)
        await self.hass.async_block_till_done()
        self.runtime = self.entry.runtime_data
        self.base = self.now = datetime.now(UTC)
        self.runtime._clock = lambda: self.now
        await self.set_source("control_input", "on")
        self.assertTrue(self.heater.is_on)
        adapter = self.runtime.device
        entered, release = asyncio.Event(), asyncio.Event()

        async def failing_off(**_kwargs):
            self.heater.calls.append(False)
            entered.set()
            await release.wait()
            raise HomeAssistantError("Synthetic delayed switch failure")

        async def queued_control(second, value):
            self.now = self.base + timedelta(seconds=second)
            self.hass.states.async_set("binary_sensor.operator", value)

            async def received():
                while not any(
                    event.data["entity_id"] == "binary_sensor.operator"
                    and event.data["new_state"].state == value
                    for _received_at, event in self.runtime._pending_device_inputs
                ):
                    await asyncio.sleep(0)

            await asyncio.wait_for(received(), 3)

        with patch.object(self.heater, "async_turn_off", side_effect=failing_off):
            try:
                self.now = self.base + timedelta(seconds=1)
                self.hass.states.async_set(
                    self.entry.options["bindings"]["upper_temperature"], "90",
                    self.hass.states.get(
                        self.entry.options["bindings"]["upper_temperature"]
                    ).attributes,
                )
                await asyncio.wait_for(entered.wait(), 3)
                await queued_control(2, "off")
                await queued_control(30, "on")
                self.now = self.base + timedelta(seconds=50)
            finally:
                release.set()
            await self.hass.async_block_till_done()
        self.assertTrue(self.runtime.session.operation_enabled)
        self.assertEqual(adapter.command_error_at, self.now)
        self.assertEqual(adapter.fault_since["heater_service_unavailable"], self.now)
        self.assertNotIn("heater_service_unavailable", self.runtime.controller.protection)
        await self.time(54.9)
        self.assertNotIn("heater_service_unavailable", self.runtime.controller.protection)
        await self.time(55)
        self.assertIn("heater_service_unavailable", self.runtime.controller.protection)
        self.assertEqual(adapter.command_error_at, self.base + timedelta(seconds=50))
        async with self.runtime._lock:
            with patch.object(self.heater, "async_turn_off", side_effect=failing_off):
                await adapter.send(False, self.now, force=True)
            self.assertEqual(adapter.command_error_at, self.base + timedelta(seconds=50))
            self.assertEqual(adapter.fault_since["heater_service_unavailable"],
                             self.base + timedelta(seconds=50))
            await adapter.send(False, self.now, force=True)
            self.assertFalse(adapter.command_error)
            self.assertIsNone(adapter.command_error_at)
            self.assertNotIn("heater_service_unavailable", adapter.fault_since)
            self.assertIn("heater_service_unavailable", self.runtime.controller.protection)
            self.now = self.base + timedelta(seconds=56)
            with patch.object(self.heater, "async_turn_off", side_effect=failing_off):
                await adapter.send(False, self.now, force=True)
            self.assertEqual(adapter.command_error_at, self.now)
            self.assertNotIn("heater_service_unavailable", adapter.fault_since)
        await self.hass.async_block_till_done()
        self.assertEqual(adapter.fault_since["heater_service_unavailable"], self.now)

    async def test_expired_temperature_cannot_restart_idle_heater_after_target_change(self):
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        self.now = self.base + timedelta(seconds=1)
        await self.set_source("upper_temperature", 85)
        self.assertFalse(self.heater.is_on)
        await self.time(32)
        self.assertIsNone(self.runtime.controller.temperature)
        self.assertEqual(self.runtime.device.faults["regulation_temperature_unavailable"], "pending")
        self.heater.calls.clear()
        await self.hass.services.async_call("climate", "set_temperature", {
            "entity_id": self.climate, "temperature": 95}, blocking=True)
        await self.hass.async_block_till_done()
        self.assertEqual(self.runtime.controller.target_temperature, 95)
        self.assertFalse(self.heater.is_on)
        self.assertNotIn(True, self.heater.calls)
        self.assertEqual(self.runtime.controller.last_decision.reason, "upper_temperature_unavailable")
        self.now = self.base + timedelta(seconds=33)
        await self.set_source("upper_temperature", 70)
        self.assertTrue(self.heater.is_on)
        self.assertEqual(len(self.runtime.session.heating.intervals), 2)

    async def test_mechanical_timer_expiry_is_informative_and_heating_feedback_is_separate(self):
        # Configure before starting, via the real options listener and reload.
        options = {**self.entry.options, "parameters": {**self.entry.options["parameters"],
            "mechanical_timer_minutes": 1, "mechanical_timer_warning_minutes": .25,
            "sensor_timeout_seconds": 120}}
        self.hass.config_entries.async_update_entry(self.entry, options=options)
        await self.hass.async_block_till_done()
        self.runtime = self.entry.runtime_data
        self.base = self.now = datetime.now(UTC)
        self.runtime._clock = lambda: self.now
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        await self.time(10)
        self.heater.powered = False
        await self.set_source("heater_feedback", "off")
        await self.time(20)
        self.assertEqual(self.runtime.controller.mechanical_timer_status["remaining_seconds"], 40)
        self.assertEqual(self.runtime.session.heating.elapsed_seconds, 10)
        self.heater.powered = True
        await self.set_source("heater_feedback", "on")
        await self.time(45)
        self.assertIn((self.runtime.session.session_id, "warning"), self.runtime.device.notified)
        # Ablauf der Schätzung allein schaltet nichts und verändert keine Diagnose.
        await self.time(60)
        self.assertTrue(self.heater.is_on)
        self.assertTrue(self.runtime.controller.feedback)
        self.assertEqual(self.runtime.controller.protection, set())
        self.now = self.base + timedelta(seconds=61)
        self.heater.powered = False
        self.hass.states.async_set("binary_sensor.actual_heating", "off")
        await self.hass.async_block_till_done()
        elapsed = self.runtime.session.heating.elapsed_seconds
        self.assertTrue(self.heater.is_on)  # Relay command is not physical heating.
        await self.time(70)
        self.assertEqual(self.runtime.session.heating.elapsed_seconds, elapsed)
        self.assertFalse(self.runtime.controller.feedback)
        # Schütz hat den Befehl ausgeführt. Fehlende Heizleistung bei angezogenem
        # Schütz pausiert den Zähler, löst aber keinen technischen Abbruch aus.
        self.assertEqual(self.runtime.controller.protection, set())
        self.assertTrue(self.heater.is_on)
        self.assertEqual(self.runtime.device.faults["heater_no_power"], "measured")
        self.assertEqual(self.runtime.device.notified, {(self.runtime.session.session_id, "warning"),
            (self.runtime.session.session_id, "expired")})
        import asyncio
        await self.runtime.archive.flush()
        data = await asyncio.to_thread(self.runtime.archive.read, self.runtime.session.session_id, limit=10000)
        self.assertEqual([r["payload"]["kind"] for r in data["records"] if r["kind"] == "notice"],
                         ["mechanical_timer_warning", "mechanical_timer_expired"])

    async def configure_feedback(self, *, power_sensor=False):
        bindings = dict(self.entry.options["bindings"])
        bindings.pop("heater_feedback", None)
        if power_sensor:
            bindings["heater_power"] = "sensor.oven_power"
            self.hass.states.async_set("sensor.oven_power", "0", {
                "device_class": "power", "unit_of_measurement": "kW"})
        self.hass.config_entries.async_update_entry(self.entry, options={
            "bindings": bindings, "parameters": {**self.entry.options["parameters"],
                "power_heating_threshold_w": 100, "sensor_timeout_seconds": 120}})
        await self.hass.async_block_till_done()
        self.runtime = self.entry.runtime_data
        self.base = self.now = datetime.now(UTC)
        self.runtime._clock = lambda: self.now

    async def prepare_gang_after_run(self):
        options = {**self.entry.options, "parameters": {**self.entry.options["parameters"],
            "heating_minutes": 1, "heating_reduction_minutes": .25,
            "sensor_timeout_seconds": 180}}
        self.hass.config_entries.async_update_entry(self.entry, options=options)
        await self.hass.async_block_till_done()
        self.runtime = self.entry.runtime_data
        self.base = self.now = datetime.now(UTC)
        self.runtime._clock = lambda: self.now
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        from custom_components.ha_sauna.core.timeline import Event, Kind
        # Valid measurement sources establish the documented initial CLOSED
        # assumption. A synthetic close here would be a duplicate door edge.
        self.assertEqual(self.runtime.session.timeline.door, Door.CLOSED)
        for second, kind in ((2, Kind.INFUSION),
                             (61, Kind.DOOR_OPEN), (62, Kind.VENTILATION)):
            self.now = self.base + timedelta(seconds=second)
            await self.runtime.receive(Event(f"manual-test:{second}", self.runtime.session.session_id,
                kind, self.now, self.now))
            await self.hass.async_block_till_done()
        self.assertEqual(self.runtime.controller.phase, "nachlauf")

    async def test_manual_expiry_returns_to_running_after_run_curve(self):
        self.hass.config_entries.async_update_entry(self.entry, options={
            **self.entry.options,
            "parameters": {**self.entry.options["parameters"], "manual_override_minutes": .1},
        })
        await self.hass.async_block_till_done()
        await self.prepare_gang_after_run()
        # A warm, valid input puts the return target above the phase's dim
        # level, so the later part of this actual cooling curve rises.
        await self.set_source("upper_temperature", 85)
        session_id = self.runtime.session.session_id
        phase = self.runtime.session.after_run
        ends_at = phase.ends_at
        dim_percent = self.runtime.configuration.parameters.values["after_run_brightness_percent"]
        await self.time(72)
        before = self.light.brightness
        await self.runtime.set_light_override(80)
        await self.hass.async_block_till_done()
        self.assertAlmostEqual(self.light.brightness, 255 * .8, delta=1)
        await self.time(78)
        self.assertIsNone(self.runtime.device.light_output.manual_brightness)
        self.assertEqual(self.runtime.session.after_run.phase_id, phase.phase_id)
        self.assertEqual(self.runtime.session.after_run.started_at, phase.started_at)
        self.assertEqual(self.runtime.session.after_run.ends_at, ends_at)
        self.assertEqual(self.runtime.controller.phase, "nachlauf")
        self.assertTrue(self.light.is_on)
        self.assertEqual(self.hass.states.get(self.light.entity_id).state, "on")
        self.assertGreaterEqual(self.light.brightness, round(255 * dim_percent / 100))
        self.assertLess(self.light.brightness, before)
        self.assertLess(self.light.brightness, 255 * self.runtime.device.normal_light_brightness() / 100)
        resumed = self.light.brightness
        # Cooling duration comes from the Controller's factual calculation;
        # sample its rise before that canonical end rather than assume t85.
        self.now = ends_at - timedelta(seconds=1)
        await self.runtime.tick()
        await self.hass.async_block_till_done()
        self.assertGreater(self.light.brightness, resumed)
        self.assertEqual(self.runtime.controller.phase, "nachlauf")
        self.assertEqual(self.runtime.session.after_run.phase_id, phase.phase_id)
        self.assertEqual(self.runtime.session.after_run.started_at, phase.started_at)
        self.assertEqual(self.runtime.session.after_run.ends_at, ends_at)
        await self.runtime.archive.flush()
        commands = [
            record["payload"] for record in self.runtime.archive.read(session_id)["records"]
            if record["kind"] == "light_command"
            and record["payload"]["brightness_pct"] == 80
        ]
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0]["phase"], "nachlauf")
        self.assertEqual(commands[0]["ends_at"], ends_at.isoformat())

    async def test_missing_off_feedback_keeps_cooling_light_in_its_phase(self):
        await self.prepare_gang_after_run()
        await self.time(72)
        before = self.light.brightness
        phase = self.runtime.session.after_run
        self.assertIsNotNone(phase.ends_at)
        self.heater.accept_commands = False
        heater = self.entry.options["bindings"]["heater"]
        self.hass.states.async_set(heater, "unknown")
        await self.hass.async_block_till_done()
        await self.time(73)
        self.assertIsNone(self.runtime.session.after_run.ends_at)
        self.assertIsNone(self.runtime.session.after_run.paused_at)
        self.assertLessEqual(self.light.brightness, before + 2)
        self.heater.accept_commands = True
        self.hass.states.async_set(heater, "off")
        await self.hass.async_block_till_done()
        self.assertIsNotNone(self.runtime.session.after_run.ends_at)

    async def test_manual_phase_end_returns_to_regulation_without_a_cooling_cycle(self):
        await self.prepare_gang_after_run()
        identity = self.runtime.session.session_id
        self.assertFalse(self.heater.is_on)
        after_run_start = self.light.brightness
        self.assertTrue(self.light.is_on)
        await self.time(72)
        self.assertGreater(self.light.brightness, 255*.15)
        self.assertLess(self.light.brightness, after_run_start)
        await self.runtime.finish_phase("after_run", self.runtime.session.after_run.phase_id)
        await self.hass.async_block_till_done()
        self.assertTrue(self.heater.is_on)
        self.assertEqual(self.runtime.session.timeline.gang_count, 1)
        self.assertEqual(self.runtime.session.session_id, identity)
        self.assertEqual(self.runtime.controller.mechanical_timer_status["remaining_seconds"], 14400-62)
        import asyncio
        await self.runtime.archive.flush()
        archived = await asyncio.to_thread(self.runtime.archive.read, identity, limit=10000)
        actions = [r["payload"]["purpose"] for r in archived["records"] if r["kind"] == "manual_phase_end"]
        self.assertEqual(actions, ["after_run"])

    async def test_contactor_only_counts_without_invented_temperature_cutoff(self):
        await self.configure_feedback()
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        await self.time(60)
        self.assertTrue(self.heater.is_on)
        self.assertEqual(self.runtime.session.heating.elapsed_seconds, 60)
        self.assertAlmostEqual(self.runtime.session.energy.total_kwh, .075)
        self.assertEqual(self.runtime.session.energy.source, "estimated")
        self.assertEqual(self.runtime.device.heating_observation["source"], "contactor")
        self.assertTrue(self.runtime.device.heating_observation["estimated"])
        self.assertEqual(self.runtime.controller.inhibits, set())
        self.assertEqual(self.runtime.controller.protection, set())
        await self.runtime.set_operation(False)
        await self.hass.async_block_till_done()
        await self.time(90)
        self.assertEqual(self.runtime.session.heating.elapsed_seconds, 60)

    async def test_optional_power_measurement_controls_counter_not_contactor(self):
        await self.configure_feedback(power_sensor=True)
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        await self.time(5)
        self.assertTrue(self.heater.is_on)
        self.assertEqual(self.runtime.session.heating.elapsed_seconds, 0)
        await self.set_source("heater_power", 6)
        await self.time(15)
        self.assertEqual(self.runtime.session.heating.elapsed_seconds, 10)
        self.assertEqual(self.runtime.device.heating_observation["power_w"], 6000)
        self.assertEqual(self.runtime.device.heating_observation["source"], "power")
        await self.set_source("heater_power", 0.05)
        await self.time(30)
        self.assertEqual(self.runtime.session.heating.elapsed_seconds, 10)
        self.assertTrue(self.heater.is_on)
        self.assertEqual(self.runtime.controller.protection, set())
        await self.set_source("heater_power", "unavailable")
        await self.time(35)
        self.assertEqual(self.runtime.session.heating.elapsed_seconds, 15)
        self.assertIn("heater_power", self.runtime.device.faults)
        self.assertTrue(self.runtime.device.heating_observation["estimated"])
        await self.set_source("heater_power", 0)
        await self.time(45)
        self.assertEqual(self.runtime.session.heating.elapsed_seconds, 15)
        self.assertEqual(self.runtime.device.heating_observation["source"], "power")
        self.assertNotIn("heater_power", self.runtime.device.faults)
        self.assertAlmostEqual(self.runtime.session.energy.measured_kwh, .016875)
        self.assertAlmostEqual(self.runtime.session.energy.estimated_kwh, .00625)
        self.assertEqual(self.runtime.session.energy.source, "mixed")

    async def test_detached_binary_button_starts_then_toggles_heater_override(self):
        from dataclasses import replace
        self.runtime.configuration = replace(self.runtime.configuration, control_input_mode="button")
        await self.set_source("control_input", "on")
        identity = self.runtime.session.session_id
        self.assertTrue(self.runtime.session.operation_enabled)
        self.assertTrue(self.heater.is_on)
        await self.set_source("control_input", "off")
        self.assertTrue(self.runtime.session.operation_enabled)
        await self.set_source("control_input", "on")
        self.assertTrue(self.runtime.session.operation_enabled)
        self.assertIsNone(self.runtime.controller.heater_override)
        await self.set_source("control_input", "off")
        self.assertIsNotNone(self.runtime.controller.heater_override)
        self.assertFalse(self.heater.is_on)
        self.assertEqual(self.runtime.session.session_id, identity)

    async def test_shelly_event_button_ignores_press_release_and_recovery_duplicates(self):
        # Rebind through real HA options; the old listener must disappear.
        values={**self.entry.options["bindings"], "control_input":"event.detached_button"}
        self.hass.states.async_set("event.detached_button", "unknown", {"event_type":None})
        self.hass.config_entries.async_update_entry(self.entry, options={**self.entry.options,
            "bindings":values, "control_input_mode":"button"})
        await self.hass.async_block_till_done()
        self.runtime=self.entry.runtime_data
        self.runtime._clock=lambda:self.now
        self.now=self.runtime.device.input_started_at+timedelta(seconds=1)
        async def push(kind):
            self.now+=timedelta(milliseconds=100)
            self.hass.states.async_set("event.detached_button", self.now.isoformat(), {"event_type":kind})
            await self.hass.async_block_till_done()
        await push("btn_down")
        self.assertTrue(self.runtime.session.operation_enabled)
        await push("btn_up")
        self.assertTrue(self.runtime.session.operation_enabled)
        await push("single_push")
        self.assertTrue(self.runtime.session.operation_enabled)
        self.assertIsNone(self.runtime.controller.heater_override)
        saved=self.hass.states.get("event.detached_button")
        self.hass.states.async_set("event.detached_button", "unavailable")
        await self.hass.async_block_till_done()
        self.hass.states.async_set("event.detached_button", saved.state, saved.attributes)
        await self.hass.async_block_till_done()
        self.assertTrue(self.runtime.session.operation_enabled)
        # The old binary input no longer controls this instance.
        self.hass.states.async_set("binary_sensor.operator", "on")
        await self.hass.async_block_till_done()
        self.assertTrue(self.runtime.session.operation_enabled)
        await push("btn_down")
        await push("btn_up")
        await push("single_push")
        self.assertTrue(self.runtime.session.operation_enabled)
        self.assertIsNotNone(self.runtime.controller.heater_override)

    async def test_independent_event_longs_start_then_stop_the_same_runtime(self):
        values = {**self.entry.options["bindings"], "control_input": "event.detached_button"}
        self.hass.states.async_set("event.detached_button", "unknown", {"event_type": None})
        self.hass.config_entries.async_update_entry(
            self.entry,
            options={**self.entry.options, "bindings": values, "control_input_mode": "button"},
        )
        await self.hass.async_block_till_done()
        self.runtime = self.entry.runtime_data
        self.base = self.now = max(
            self.runtime._clock(),
            self.runtime.device.input_started_at + timedelta(seconds=1),
        )
        self.runtime._clock = lambda: self.now
        button = self.runtime._button

        async def push(kind, *, at=None):
            self.now += timedelta(seconds=1)
            self.hass.states.async_set(
                "event.detached_button", (at or self.now).isoformat(), {"event_type": kind}
            )
            await self.hass.async_block_till_done()

        await push("long_push")
        session_id = self.runtime.session.session_id
        self.assertTrue(self.runtime.controller.last_decision.heat)
        self.assertTrue(self.runtime.device.command)
        self.assertTrue(self.heater.is_on)
        first_event_at = self.now
        self.hass.states.async_set("event.detached_button", "unavailable")
        await self.hass.async_block_till_done()
        await push("long_push", at=first_event_at)
        self.assertEqual(self.runtime.session.session_id, session_id)
        self.assertTrue(self.heater.is_on)
        self.assertEqual(len(self.runtime.controller.completed_sessions), 0)

        await push("long_push")

        self.assertIs(self.runtime._button, button)
        self.assertIsNone(self.runtime.session)
        self.assertFalse(self.runtime.controller.last_decision.heat)
        self.assertFalse(self.runtime.device.command)
        self.assertFalse(self.heater.is_on)
        self.assertFalse(self.heater.calls[-1])
        self.assertEqual(len(self.runtime.controller.completed_sessions), 1)
        self.assertFalse(self.light.is_on)
        self.assertIsNone(self.runtime.controller.light_after_run)
        second_event_at = self.now
        self.hass.states.async_set("event.detached_button", "unavailable")
        await self.hass.async_block_till_done()
        await push("long_push", at=second_event_at)
        self.assertIsNone(self.runtime.session)
        self.assertFalse(self.heater.is_on)
        self.assertEqual(len(self.runtime.controller.completed_sessions), 1)

        await push("single_push")
        self.assertTrue(self.runtime.session.operation_enabled)
        self.assertTrue(self.heater.is_on)

    async def test_event_long_hold_acknowledges_then_release_starts_light_afterrun(self):
        values = {**self.entry.options["bindings"], "control_input": "event.detached_button"}
        self.hass.states.async_set("event.detached_button", "unknown", {"event_type": None})
        self.hass.config_entries.async_update_entry(
            self.entry,
            options={**self.entry.options, "bindings": values, "control_input_mode": "button"},
        )
        await self.hass.async_block_till_done()
        self.runtime = self.entry.runtime_data
        self.runtime._clock = lambda: self.now
        self.now = self.runtime.device.input_started_at + timedelta(seconds=1)

        async def push(kind, *, at=None):
            self.now += timedelta(milliseconds=100)
            self.hass.states.async_set(
                "event.detached_button",
                (at or self.now).isoformat(),
                {"event_type": kind},
            )
            await self.hass.async_block_till_done()

        await push("btn_down")
        session_id = self.runtime.session.session_id
        await push("btn_up")
        await push("single_push")
        # Erst die nächste Geste in der laufenden Sitzung darf sie beenden.
        await push("btn_down")
        await push("long_push")
        self.assertIsNone(self.runtime.session)
        self.assertFalse(self.light.is_on)
        self.assertEqual(self.light.calls[-1][0], "off")
        self.assertIsNone(self.runtime.controller.light_after_run)
        await push("btn_up")
        self.assertEqual(self.runtime.controller.light_after_run.session_id, session_id)
        self.now += timedelta(seconds=self.runtime.configuration.parameters.values["light_transition_seconds"])
        await self.runtime.tick()
        await self.hass.async_block_till_done()
        self.assertTrue(self.light.is_on)
        self.assertAlmostEqual(
            self.light.brightness,
            255 * self.runtime.configuration.parameters.values["session_light_brightness_percent"] / 100,
            delta=1,
        )
        stale = self.now - timedelta(seconds=1)
        await push("single_push", at=stale)
        self.assertIsNone(self.runtime.session)

    async def test_manual_mode_keeps_explicit_light_through_session_and_operation_off(self):
        from dataclasses import replace
        from custom_components.ha_sauna.core.timeline import Event, Kind

        self.runtime.controller.set_control_mode("manual")
        self.runtime.configuration = replace(self.runtime.configuration, control_mode="manual")
        await self.runtime.set_light_override(35)
        await self.runtime.set_operation(True)
        session_id = self.runtime.session.session_id
        self.assertEqual(self.runtime.session.timeline.door, Door.CLOSED)
        await self.runtime.receive(Event("manual-infusion", session_id, Kind.INFUSION, self.now, self.now))
        await self.runtime.set_operation(False)
        await self.hass.async_block_till_done()
        self.assertAlmostEqual(self.light.brightness, 255 * .35, delta=1)

    async def test_reassignment_waits_for_running_on_before_final_off(self):
        old_runtime = self.runtime
        adapter = old_runtime.device
        entered, release, handing_off = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original_on = self.light.async_turn_on
        original_finish = adapter.finish_session_light

        async def paused_on(**kwargs):
            entered.set()
            await release.wait()
            await original_on(**kwargs)

        async def finish(*args, **kwargs):
            handing_off.set()
            return await original_finish(*args, **kwargs)

        replacement = "light.replacement"
        self.hass.states.async_set(
            replacement, "off", {"supported_color_modes": ["brightness"]}
        )
        self.light.calls.clear()
        with (
            patch.object(self.light, "async_turn_on", side_effect=paused_on),
            patch.object(adapter, "finish_session_light", side_effect=finish),
        ):
            selecting = asyncio.create_task(old_runtime.set_light_override(80))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                self.hass.config_entries.async_update_entry(
                    self.entry,
                    options={
                        **self.entry.options,
                        "bindings": {
                            **self.entry.options["bindings"], "light": replacement,
                        },
                    },
                )
                await asyncio.wait_for(handing_off.wait(), 3)
            finally:
                release.set()
            await selecting
            await self.hass.async_block_till_done()
        self.assertTrue(old_runtime.closed)
        self.assertIsNot(self.entry.runtime_data, old_runtime)
        self.assertEqual([call[0] for call in self.light.calls], ["on", "off"])
        self.assertEqual(self.hass.states.get(self.light.entity_id).state, "off")
        self.runtime = self.entry.runtime_data
        self.runtime._clock = lambda: self.now

    async def test_cancelled_light_caller_keeps_service_until_handoff_can_finish(self):
        adapter = self.runtime.device
        entered, release = asyncio.Event(), asyncio.Event()
        original_on = self.light.async_turn_on

        async def paused_on(**kwargs):
            entered.set()
            await release.wait()
            await original_on(**kwargs)

        self.light.calls.clear()
        with patch.object(self.light, "async_turn_on", side_effect=paused_on):
            selecting = asyncio.create_task(self.runtime.set_light_override(80))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                selecting.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await selecting
                active_service = adapter._light_service_task
                self.assertFalse(active_service.done())
                self.assertFalse(await adapter.finish_session_light(self.now, None))
                self.assertIs(adapter._light_service_task, active_service)
                self.assertFalse(active_service.done())
                self.assertEqual(self.light.calls, [])
                adapter.restore_light_ownership()
            finally:
                release.set()
            self.assertTrue(await adapter.finish_session_light(self.now, None))
            await self.hass.async_block_till_done()
        self.assertEqual([call[0] for call in self.light.calls], ["on", "off"])
        self.assertFalse(self.light.is_on)

    async def _assert_cancelled_light_completion_is_archived(self, *, fails):
        import json
        import zipfile

        await self.runtime.set_operation(True)
        session_id = self.runtime.session.session_id
        entered, release = asyncio.Event(), asyncio.Event()
        original_on = self.light.async_turn_on
        adapter = self.runtime.device
        original_finished = adapter._light_service_finished
        failed_expectations_before_callback = []
        service = None

        async def paused_on(**kwargs):
            entered.set()
            await release.wait()
            await original_on(**kwargs)

        def finished(task, *args):
            if fails and task is service:
                failed_expectations_before_callback.append([
                    expected for expected in adapter._expected_light_changes
                    if expected.get("service_task") is task
                ])
            original_finished(task, *args)

        with (
            patch.object(self.light, "async_turn_on", side_effect=paused_on),
            patch.object(adapter, "_light_service_finished", side_effect=finished),
        ):
            selecting = asyncio.create_task(self.runtime.set_light_override(80))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                selecting.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await selecting
                service = self.runtime.device._light_service_task
                # End the originating session while the actual service remains
                # pending. Its completion must retain the original derivation.
                async with self.runtime._lock:
                    self.runtime.controller.finish_session(self.now)
                    self.runtime.device.set_light_override(None, at=self.now)
                    self.runtime.persist_completed_sessions()
                self.assertIsNone(self.runtime.session)
                self.light.fail_commands = fails
                self.now += timedelta(seconds=3)
            finally:
                release.set()
            if fails:
                from homeassistant.exceptions import HomeAssistantError
                with self.assertRaises(HomeAssistantError):
                    await service
            else:
                await service
            self.light.fail_commands = False
            await self.hass.async_block_till_done()
        if fails:
            self.assertEqual(failed_expectations_before_callback, [[]])
        await self.runtime.archive.flush()
        commands = [
            record for record in self.runtime.archive.read(session_id)["records"]
            if record["kind"] == "light_command"
            and record["payload"]["brightness_pct"] == 80
        ]
        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command["session_id"], session_id)
        self.assertEqual(command["payload"]["phase"], "aufheizen")
        self.assertEqual(command["payload"]["purpose"], "aufheizen")
        self.assertIsNone(command["payload"]["ends_at"])
        self.assertEqual(command["payload"]["planned_at"], self.base.isoformat())
        self.assertEqual(command["payload"]["sent_at"], self.base.isoformat())
        self.assertEqual(command["payload"]["completed_at"], self.now.isoformat())
        self.assertEqual(command["received_at"], self.now.isoformat())
        self.assertEqual(
            command["payload"]["service_error"], "HomeAssistantError" if fails else None
        )
        path = await self.runtime.archive.export()
        try:
            with zipfile.ZipFile(path) as archive:
                exported = [json.loads(line) for line in archive.read("records.jsonl").splitlines()]
            self.assertEqual(
                [record for record in exported if record["id"] == command["id"]], [command]
            )
        finally:
            path.unlink()

    async def test_cancelled_light_success_keeps_one_original_session_record(self):
        await self._assert_cancelled_light_completion_is_archived(fails=False)

    async def test_cancelled_light_failure_keeps_one_original_session_record(self):
        await self._assert_cancelled_light_completion_is_archived(fails=True)

    async def test_cancelled_actual_light_service_removes_echo_before_callback_and_archives(self):
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        session_id = self.runtime.session.session_id
        adapter = self.runtime.device
        original_finished = adapter._light_service_finished
        entered = asyncio.Event()
        expectations_before_callback = []
        service = None

        async def paused_call(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        def finished(task, *args):
            if task is service:
                expectations_before_callback.append([
                    expected for expected in adapter._expected_light_changes
                    if expected.get("service_task") is task
                ])
            original_finished(task, *args)

        self.light.calls.clear()
        with (
            patch.object(adapter, "light_call", side_effect=paused_call),
            patch.object(adapter, "_light_service_finished", side_effect=finished),
        ):
            selecting = asyncio.create_task(self.runtime.set_light_override(80))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                service = adapter._light_service_task
                selecting.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await selecting
                self.assertTrue(any(expected.get("service_task") is service
                                    for expected in adapter._expected_light_changes))
                self.now += timedelta(seconds=3)
                service.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await service
                await self.hass.async_block_till_done()
            finally:
                for task in (selecting, service):
                    if task is not None and not task.done():
                        task.cancel()
                await asyncio.gather(*(task for task in (selecting, service) if task is not None),
                                     return_exceptions=True)
        self.assertEqual(expectations_before_callback, [[]])
        self.assertEqual(self.light.calls, [])
        self.assertEqual(adapter.faults["operation_light"], "service_unavailable")
        await self.runtime.archive.flush()
        commands = [record for record in self.runtime.archive.read(session_id)["records"]
                    if record["kind"] == "light_command"
                    and record["payload"]["brightness_pct"] == 80]
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0]["session_id"], session_id)
        self.assertEqual(commands[0]["payload"]["service_error"], "CancelledError")
        self.assertEqual(commands[0]["payload"]["completed_at"], self.now.isoformat())
        self.assertEqual(commands[0]["received_at"], self.now.isoformat())

    async def _assert_session_light_deadline_is_archived(self, caller):
        import json
        import zipfile

        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        session_id = self.runtime.session.session_id
        self.now = self.base + timedelta(seconds=1)
        await self.runtime.set_operation(False)
        await self.hass.async_block_till_done()
        phase = self.runtime.controller.light_after_run
        ends_at = phase.ends_at.isoformat()
        entered, release = asyncio.Event(), asyncio.Event()
        original_on = self.light.async_turn_on

        async def paused_on(**kwargs):
            # Only the explicit 80-%-command owns this barrier. Automatic
            # phase-ramp commands keep their regular service/feedback path.
            if kwargs.get("brightness") == 204:
                entered.set()
                await release.wait()
            await original_on(**kwargs)

        self.now = planned_at = self.base + timedelta(seconds=2)
        with patch.object(self.light, "async_turn_on", side_effect=paused_on):
            selecting = asyncio.create_task(self.runtime.set_light_override(80))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                service = self.runtime.device._light_service_task
                self.assertEqual(self.runtime.device.light_output.manual_brightness, 80)
                if caller == "cancel":
                    selecting.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await selecting
                elif caller == "timeout":
                    await selecting
                    self.assertEqual(self.runtime.device.faults["session_light"], "service_unavailable")
                self.assertFalse(service.done())
                if caller != "normal":
                    # The original deadline belongs to the already planned
                    # task even if its Controller phase is removed meanwhile.
                    async with self.runtime._lock:
                        self.runtime.controller.finish_session(self.now, light_after_run=False)
                        self.runtime.persist_completed_sessions()
                    self.assertIsNone(self.runtime.session)
                    self.assertIsNone(self.runtime.controller.light_after_run)
                self.now = self.base + timedelta(seconds=3)
            finally:
                release.set()
            await service
            if caller == "normal":
                await selecting
            await self.hass.async_block_till_done()
        self.assertEqual(
            len([call for call in self.light.calls
                 if call[0] == "on" and call[1].get("brightness") == 204]),
            1,
        )
        if caller == "normal":
            self.assertTrue(self.light.is_on)
            self.assertAlmostEqual(self.light.brightness, 255 * .8, delta=1)
        else:
            self.assertFalse(self.light.is_on)
        self.assertNotIn("session_light", self.runtime.device.faults)
        await self.runtime.archive.flush()
        commands = [
            record for record in self.runtime.archive.read(session_id)["records"]
            if record["kind"] == "light_command"
            and record["payload"]["brightness_pct"] == 80
        ]
        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command["session_id"], session_id)
        self.assertEqual(command["payload"]["phase"], "session_light")
        self.assertEqual(command["payload"]["purpose"], "session_end")
        self.assertEqual(command["payload"]["ends_at"], ends_at)
        self.assertEqual(command["payload"]["planned_at"], planned_at.isoformat())
        self.assertEqual(command["payload"]["sent_at"], planned_at.isoformat())
        self.assertEqual(command["payload"]["completed_at"], self.now.isoformat())
        self.assertEqual(command["received_at"], self.now.isoformat())
        self.assertIsNone(command["payload"]["service_error"])
        path = await self.runtime.archive.export()
        try:
            with zipfile.ZipFile(path) as archive:
                exported = [json.loads(line) for line in archive.read("records.jsonl").splitlines()]
            self.assertEqual(
                [record for record in exported if record["id"] == command["id"]], [command]
            )
        finally:
            path.unlink()

    async def test_normal_session_light_completion_archives_original_deadline(self):
        await self._assert_session_light_deadline_is_archived("normal")

    async def test_cancelled_session_light_caller_archives_original_deadline(self):
        await self._assert_session_light_deadline_is_archived("cancel")

    async def test_timed_out_session_light_caller_archives_original_deadline(self):
        await self._assert_session_light_deadline_is_archived("timeout")

    async def test_light_archive_failure_does_not_change_actual_service_result(self):
        original_append = self.runtime.archive.append

        def append(kind, at, payload, session_id=None):
            if kind == "light_command":
                raise RuntimeError("Synthetic archive append failure")
            return original_append(kind, at, payload, session_id)

        with patch.object(self.runtime.archive, "append", side_effect=append):
            await self.runtime.set_light_override(80)
            self.assertEqual(self.light.brightness, 204)
            self.assertNotIn("operation_light", self.runtime.device.faults)
            self.assertEqual(self.runtime.device.faults["archive"], "Synthetic archive append failure")
            self.light.fail_commands = True
            await self.runtime.set_light_override(20)
            self.assertEqual(self.light.brightness, 204)
            self.assertEqual(self.runtime.device.faults["operation_light"], "service_unavailable")
            self.assertEqual(self.runtime.device.faults["archive"], "Synthetic archive append failure")
        self.light.fail_commands = False

    async def _assert_same_light_reload_waits_for_actual_service(self, *, serial):
        from homeassistant.config_entries import ConfigEntryState

        from custom_components.ha_sauna import _restore_failed_options
        from custom_components.ha_sauna.runtime import SaunaRuntime

        for cancel_caller in (False, True):
            with self.subTest(cancel_caller=cancel_caller):
                old = self.runtime
                options = dict(self.entry.options)
                entered, release, options_finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
                platform_slot = asyncio.Semaphore(1)
                original_on = self.light.async_turn_on
                original_reload = self.hass.config_entries.async_reload

                async def send_on(kwargs):
                    if not entered.is_set():
                        entered.set()
                        await release.wait()
                    await original_on(**kwargs)

                async def paused_on(**kwargs):
                    if serial:
                        async with platform_slot:
                            await send_on(kwargs)
                    else:
                        await send_on(kwargs)

                async def restore_options(*args, **kwargs):
                    try:
                        return await _restore_failed_options(*args, **kwargs)
                    finally:
                        # The options listener awaits rollback after HA reload
                        # returns False; assert only at that real completion.
                        options_finished.set()

                self.light.calls.clear()
                with (
                    patch.object(self.light, "async_turn_on", side_effect=paused_on),
                    patch("custom_components.ha_sauna._restore_failed_options", side_effect=restore_options),
                    patch.object(self.hass.config_entries, "async_reload", wraps=original_reload) as reload_entry,
                ):
                    selecting = asyncio.create_task(old.set_light_override(80))
                    try:
                        await asyncio.wait_for(entered.wait(), 3)
                        if cancel_caller:
                            selecting.cancel()
                            with self.assertRaises(asyncio.CancelledError):
                                await selecting
                        else:
                            await selecting  # The bounded caller wait times out.
                        service = old.device._light_service_task
                        self.hass.config_entries.async_update_entry(
                            self.entry, options={
                                **options,
                                "parameters": {
                                    **options["parameters"],
                                    "nominal_power_kw": options["parameters"]["nominal_power_kw"] + 1,
                                },
                            },
                        )
                        await asyncio.wait_for(options_finished.wait(), 8)
                        self.assertIs(self.entry.runtime_data, old)
                        self.assertFalse(old.closed)
                        self.assertFalse(old.archive.closed)
                        self.assertEqual(self.entry.state, ConfigEntryState.LOADED)
                        self.assertEqual(dict(self.entry.options), options)
                        self.assertTrue(old.device._light_owned)
                        self.assertFalse(service.done())
                        reload_entry.assert_not_called()
                    finally:
                        release.set()
                    await service
                    await self.hass.async_block_till_done()
                with patch(
                    "custom_components.ha_sauna.runtime.SaunaRuntime",
                    side_effect=lambda configuration: SaunaRuntime(configuration, clock=lambda: self.now),
                ):
                    self.hass.config_entries.async_update_entry(
                        self.entry, options={
                            **options,
                            "parameters": {
                                **options["parameters"],
                                "nominal_power_kw": options["parameters"]["nominal_power_kw"] + 1,
                            },
                        },
                    )
                    await self.hass.async_block_till_done()
                self.runtime = self.entry.runtime_data
                self.assertIsNot(self.runtime, old)
                self.assertEqual(self.entry.state, ConfigEntryState.LOADED)
                self.assertEqual(
                    self.runtime.configuration.parameters.values["nominal_power_kw"],
                    options["parameters"]["nominal_power_kw"] + 1,
                )
                await self.runtime.set_light_override(20)
                await self.hass.async_block_till_done()
                self.assertEqual(self.light.brightness, 51)
                self.assertEqual(self.runtime.device.light_output.manual_brightness, 20)
                self.assertEqual(
                    [kwargs["brightness"] for kind, kwargs in self.light.calls if kind == "on"],
                    [204, 51],
                )
                await self.set_light_externally(True, 128)
                self.assertAlmostEqual(
                    self.runtime.device.light_output.manual_brightness, 128 * 100 / 255
                )

    async def test_same_light_parallel_reload_rejects_pending_old_service(self):
        await self._assert_same_light_reload_waits_for_actual_service(serial=False)

    async def test_same_light_serial_reload_rejects_pending_old_service(self):
        await self._assert_same_light_reload_waits_for_actual_service(serial=True)

    async def test_same_light_reload_can_finish_old_service_before_new_selection(self):
        from custom_components.ha_sauna.runtime import SaunaRuntime

        old = self.runtime
        entered, release, handing_off = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original_on = self.light.async_turn_on
        original_prepare = old.device.prepare_light_handoff

        async def paused_on(**kwargs):
            entered.set()
            await release.wait()
            await original_on(**kwargs)

        async def prepare(**kwargs):
            handing_off.set()
            return await original_prepare(**kwargs)

        self.light.calls.clear()
        with (
            patch.object(self.light, "async_turn_on", side_effect=paused_on),
            patch.object(old.device, "prepare_light_handoff", side_effect=prepare),
            patch(
                "custom_components.ha_sauna.runtime.SaunaRuntime",
                side_effect=lambda configuration: SaunaRuntime(configuration, clock=lambda: self.now),
            ),
        ):
            selecting = asyncio.create_task(old.set_light_override(80))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                selecting.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await selecting
                options = self.entry.options
                self.hass.config_entries.async_update_entry(
                    self.entry, options={
                        **options,
                        "parameters": {
                            **options["parameters"],
                            "nominal_power_kw": options["parameters"]["nominal_power_kw"] + 1,
                        },
                    },
                )
                await asyncio.wait_for(handing_off.wait(), 3)
                self.assertIs(self.entry.runtime_data, old)
            finally:
                release.set()
            await self.hass.async_block_till_done()
        self.runtime = self.entry.runtime_data
        self.assertIsNot(self.runtime, old)
        self.assertTrue(old.closed)
        self.assertIsNone(self.runtime.device.light_output.manual_brightness)
        await self.runtime.set_light_override(20)
        await self.hass.async_block_till_done()
        self.assertEqual(self.light.brightness, 51)
        self.assertEqual(self.runtime.device.light_output.manual_brightness, 20)
        self.assertEqual(
            [kwargs["brightness"] for kind, kwargs in self.light.calls if kind == "on"],
            [204, 51],
        )

    async def test_waiting_reassignment_cannot_start_service_after_runtime_close(self):
        await self.runtime.set_light_override(80)
        await self.hass.async_block_till_done()
        adapter = self.runtime.device
        service = adapter._light_service_task
        preparing = asyncio.Event()
        original_prepare = adapter.prepare_light_handoff

        async def prepare():
            preparing.set()
            return await original_prepare()

        self.light.calls.clear()
        await adapter._light_output_lock.acquire()
        with patch.object(adapter, "prepare_light_handoff", side_effect=prepare):
            closing = asyncio.create_task(self.runtime.close())
            try:
                await asyncio.wait_for(preparing.wait(), 3)
                # Queue the original reassignment behind the close preflight.
                finishing = asyncio.create_task(adapter.finish_session_light(self.now, None))
                await asyncio.sleep(0)
            finally:
                adapter._light_output_lock.release()
            await closing
            self.assertFalse(await finishing)
        self.assertTrue(self.runtime.closed)
        self.assertTrue(self.runtime.archive.closed)
        self.assertIs(adapter._light_service_task, service)
        self.assertEqual(self.light.calls, [])

    async def test_close_wait_failure_keeps_archive_open_and_heater_off_then_retries(self):
        await self.runtime.set_operation(True)
        self.assertTrue(self.heater.is_on)
        session_id = self.runtime.session.session_id
        entered, release = asyncio.Event(), asyncio.Event()
        original_on = self.light.async_turn_on

        async def paused_on(**kwargs):
            entered.set()
            await release.wait()
            await original_on(**kwargs)

        with patch.object(self.light, "async_turn_on", side_effect=paused_on):
            selecting = asyncio.create_task(self.runtime.set_light_override(80))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                selecting.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await selecting
                service = self.runtime.device._light_service_task
                with self.assertRaises(RuntimeError):
                    await self.runtime.close()
                self.assertFalse(self.heater.is_on)
                self.assertFalse(self.runtime.closed)
                self.assertFalse(self.runtime.archive.closed)
            finally:
                release.set()
            await service
            await self.hass.async_block_till_done()
        await self.runtime.close()
        self.assertTrue(self.runtime.closed)
        self.assertTrue(self.runtime.archive.closed)
        commands = [
            record for record in self.runtime.archive.read(session_id)["records"]
            if record["kind"] == "light_command"
            and record["payload"]["brightness_pct"] == 80
        ]
        self.assertEqual(len(commands), 1)
        self.assertIsNone(commands[0]["payload"]["service_error"])

    async def test_binary_button_gap_then_off_cannot_create_a_long_hold(self):
        from dataclasses import replace

        self.runtime.configuration = replace(
            self.runtime.configuration, control_input_mode="button"
        )
        await self.runtime.set_operation(True)
        identity = self.runtime.session.session_id
        for second, value in ((1, "on"), (1.2, "unavailable"), (1.4, "off")):
            self.now = self.base + timedelta(seconds=second)
            await self.set_source("control_input", value)
        await self.time(3)
        self.assertEqual(self.runtime.session.session_id, identity)
        self.assertTrue(self.runtime.session.operation_enabled)
        self.assertIsNone(self.runtime.controller.heater_override)
        self.assertIsNone(self.runtime.controller.light_after_run)
        self.assertIsNone(self.runtime.device._button_hold_session_id)

    async def test_late_service_echo_after_cancel_keeps_automatic_light_and_external_choice(self):
        await self.runtime.set_operation(True)
        adapter = self.runtime.device
        entered, release = asyncio.Event(), asyncio.Event()
        original_on = self.light.async_turn_on

        async def paused_on(**kwargs):
            entered.set()
            await release.wait()
            await original_on(**kwargs)

        with patch.object(self.light, "async_turn_on", side_effect=paused_on):
            selecting = asyncio.create_task(self.runtime.set_light_override(80))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                selecting.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await selecting
                active_service = adapter._light_service_task
                self.assertFalse(await adapter.finish_session_light(self.now, None))
                adapter.restore_light_ownership()
                # Record AUTO while the previous actual service remains pending.
                async with self.runtime._lock:
                    self.runtime._set_light_override(None, self.now)
                self.now += timedelta(seconds=3)
            finally:
                release.set()
            await active_service
            await self.hass.async_block_till_done()
        self.assertIsNone(adapter.light_output.manual_brightness)
        await self.set_light_externally(True, 204)
        self.assertAlmostEqual(adapter.light_output.manual_brightness, 80)

    async def test_completed_old_light_echo_before_callback_keeps_new_session_automatic(self):
        from custom_components.ha_sauna.settings import async_set_parameters

        await async_set_parameters(self.hass, self.entry, {
            "session_gap_minutes": 1, "feedback_timeout_seconds": .025,
            "light_transition_seconds": 0,
        }, partial=True)
        await self.hass.async_block_till_done()
        self.runtime = self.entry.runtime_data
        self.base = self.now = self.runtime._clock()
        self.runtime._clock = lambda: self.now
        for position in ("upper", "lower"):
            await self.set_source(f"{position}_temperature", 70)
            await self.set_source(f"{position}_humidity", 30)
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        old_session = self.runtime.session.session_id
        self.now = self.base + timedelta(seconds=1)
        await self.runtime.set_operation(False)
        await self.hass.async_block_till_done()
        original_ends_at = self.runtime.controller.light_after_run.ends_at
        self.assertEqual(original_ends_at, self.base + timedelta(seconds=61))

        adapter = self.runtime.device
        original_call = adapter.light_call
        original_finished = adapter._light_service_finished
        original_discard = adapter._discard_expired_light_expectations
        entered, release = asyncio.Event(), asyncio.Event()
        old_service, waiting_tick = None, None
        callback_seen, release_runtime_lock = False, False
        completion_before_callback = []

        async def paused_call(service, data, **kwargs):
            nonlocal release_runtime_lock
            old_output = service == "turn_on" and data.get("brightness_pct") == 80
            if old_output:
                entered.set()
                await release.wait()
            await original_call(service, data, **kwargs)
            if old_output and release_runtime_lock:
                # Wake an already waiting original cycle before this transport
                # task returns and schedules its done callback. HA still owns
                # the actual service, entity feedback and context propagation.
                release_runtime_lock = False
                self.runtime._lock.release()

        def finished(task, *args):
            nonlocal callback_seen
            if task is old_service:
                callback_seen = True
            original_finished(task, *args)

        def discard(now):
            if old_service is not None and old_service.done() and not callback_seen:
                completion_before_callback.extend(
                    expected["completed_at"]
                    for expected in adapter._expected_light_changes
                    if expected.get("service_task") is old_service
                )
            original_discard(now)

        self.light.calls.clear()
        self.now = planned_at = self.base + timedelta(seconds=2)
        with (
            patch.object(adapter, "light_call", side_effect=paused_call),
            patch.object(adapter, "_light_service_finished", side_effect=finished),
            patch.object(adapter, "_discard_expired_light_expectations", side_effect=discard),
        ):
            selecting = asyncio.create_task(self.runtime.set_light_override(80))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                old_service = adapter._light_service_task
                selecting.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await selecting
                self.assertFalse(old_service.done())
                self.now = self.base + timedelta(seconds=3)
                token = next(deadline.token for deadline in self.runtime.session.deadlines
                             if deadline.purpose == "session_gap")
                await self.runtime.finish_session_gap(token)
                self.assertIsNone(self.runtime.session)
                self.now = self.base + timedelta(seconds=4)
                await self.runtime.set_operation(True)
                new_session = self.runtime.session.session_id
                self.assertNotEqual(new_session, old_session)
                self.assertIsNone(self.runtime.controller.light_after_run)
                await self.hass.async_block_till_done()

                self.now = completed_at = self.base + timedelta(seconds=7)
                await self.runtime._lock.acquire()
                release_runtime_lock = True
                waiting_tick = asyncio.create_task(self.runtime.tick())
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                self.assertFalse(waiting_tick.done())
                release.set()
                await old_service
                await waiting_tick
                await self.hass.async_block_till_done()
            finally:
                release.set()
                if release_runtime_lock:
                    release_runtime_lock = False
                    self.runtime._lock.release()
                await asyncio.gather(
                    *(task for task in (selecting, old_service, waiting_tick) if task is not None),
                    return_exceptions=True,
                )

        self.assertTrue(callback_seen)
        # These values are observed at real task.done/callback-pending edges;
        # no task state, completion timestamp or callback result is replaced.
        self.assertIn(completed_at, completion_before_callback)
        self.assertIsNone(adapter.light_output.manual_brightness)
        self.assertEqual(self.runtime.session.session_id, new_session)
        self.assertEqual(
            [(kind, kwargs.get("brightness")) for kind, kwargs in self.light.calls],
            [("on", 204), ("on", 54)],
        )
        self.assertEqual(self.light.brightness, 54)
        await self.runtime.archive.flush()
        old_commands = [record for record in self.runtime.archive.read(old_session)["records"]
                        if record["kind"] == "light_command"
                        and record["payload"]["brightness_pct"] == 80]
        self.assertEqual(len(old_commands), 1)
        command = old_commands[0]
        self.assertEqual(command["session_id"], old_session)
        self.assertEqual(command["payload"], {
            "planned_at": planned_at.isoformat(), "purpose": "session_end",
            "phase": "session_light", "service": "turn_on", "brightness_pct": 80,
            "ends_at": original_ends_at.isoformat(), "sent_at": planned_at.isoformat(),
            "completed_at": completed_at.isoformat(), "service_error": None,
        })
        self.assertEqual(command["received_at"], completed_at.isoformat())
        new_commands = [record["payload"]
                        for record in self.runtime.archive.read(new_session)["records"]
                        if record["kind"] == "light_command"]
        self.assertEqual([command["brightness_pct"] for command in new_commands], [21])
        self.now = self.base + timedelta(seconds=9)
        await self.set_light_externally(True, 204)
        self.assertEqual(adapter.light_output.manual_brightness, 80)

    async def test_pending_on_cannot_complete_due_off_from_current_off_feedback(self):
        from types import SimpleNamespace

        adapter = self.runtime.device
        entered, release = asyncio.Event(), asyncio.Event()
        original_on = self.light.async_turn_on

        async def paused_on(**kwargs):
            entered.set()
            await release.wait()
            await original_on(**kwargs)

        self.light.calls.clear()
        with patch.object(self.light, "async_turn_on", side_effect=paused_on):
            selecting = asyncio.create_task(self.runtime.set_light_override(80))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                selecting.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await selecting
                self.assertFalse(self.light.is_on)
                async with self.runtime._lock:
                    self.runtime._set_light_override(None, self.now)
                    self.runtime.controller.light_after_run = SimpleNamespace(
                        session_id="ended", started_at=self.now, ends_at=self.now
                    )
                finishing = asyncio.create_task(adapter._finish_expired_session_light(self.now))
                await asyncio.sleep(0)
                self.assertIsNone(adapter._light_session_off_completed_key)
                self.assertFalse(finishing.done())
            finally:
                release.set()
            self.assertTrue(await finishing)
            await self.hass.async_block_till_done()
        self.assertEqual([kind for kind, _kwargs in self.light.calls], ["on", "off"])
        self.assertFalse(self.light.is_on)

    async def test_binary_hold_gap_and_manual_choice_release_to_configured_afterrun(self):
        from dataclasses import replace
        import json
        import zipfile

        self.runtime.configuration = replace(
            self.runtime.configuration, control_input_mode="button"
        )
        await self.runtime.set_operation(True)
        identity = self.runtime.session.session_id
        self.now = self.base + timedelta(seconds=1)
        await self.set_source("control_input", "on")
        threshold = self.runtime.configuration.parameters.values["button_hold_seconds"]
        await self.time(1 + threshold)
        held_at = self.now
        self.assertIsNone(self.runtime.session)
        self.assertFalse(self.light.is_on)
        self.assertIsNone(self.runtime.controller.light_after_run)
        await self.runtime.set_light_override(80)
        self.assertFalse(self.light.is_on)
        self.now += timedelta(seconds=.2)
        await self.set_source("control_input", "unknown")
        self.assertIsNone(self.runtime.controller.light_after_run)
        self.now += timedelta(seconds=.2)
        await self.set_source("control_input", "off")
        phase = self.runtime.controller.light_after_run
        self.assertEqual(phase.session_id, identity)
        self.assertEqual(phase.started_at, self.now)
        self.assertIsNone(self.runtime.device.light_output.manual_brightness)
        self.now += timedelta(
            seconds=self.runtime.configuration.parameters.values["light_transition_seconds"]
        )
        await self.runtime.tick()
        await self.hass.async_block_till_done()
        self.assertAlmostEqual(self.light.brightness, 255 * .5, delta=1)
        self.now += timedelta(seconds=1)
        planned_at = self.now
        await self.runtime.set_light_override(80)
        await self.hass.async_block_till_done()
        self.assertAlmostEqual(self.light.brightness, 255 * .8, delta=1)
        self.now += timedelta(seconds=1)
        self.assertLess(self.now, phase.ends_at)
        await self.runtime.close()
        await self.hass.async_block_till_done()
        stored = self.runtime.archive.read(identity)
        self.assertEqual(stored["session"]["ended_at"], held_at.isoformat())
        commands = [
            record for record in stored["records"]
            if record["kind"] == "light_command"
            and record["payload"]["brightness_pct"] == 80
        ]
        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command["session_id"], identity)
        self.assertEqual(command["payload"]["phase"], "session_light")
        self.assertEqual(command["payload"]["purpose"], "session_end")
        self.assertEqual(command["payload"]["ends_at"], phase.ends_at.isoformat())
        self.assertEqual(command["payload"]["planned_at"], planned_at.isoformat())
        self.assertEqual(command["payload"]["sent_at"], planned_at.isoformat())
        self.assertEqual(command["payload"]["completed_at"], planned_at.isoformat())
        self.assertIsNone(command["payload"]["service_error"])
        path = await self.runtime.archive.export()
        try:
            with zipfile.ZipFile(path) as archive:
                exported = [json.loads(line) for line in archive.read("records.jsonl").splitlines()]
            self.assertEqual(
                [record for record in exported if record["id"] == command["id"]], [command]
            )
        finally:
            path.unlink()
