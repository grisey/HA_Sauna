"""Echter HA-Pfad: Entity -> Listener -> Detektor/Kern -> Service -> Aktorfeedback."""
from datetime import UTC, datetime, timedelta
import unittest
from homeassistant.components.switch import SwitchEntity
from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
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

    async def async_turn_on(self, **kwargs):
        self.calls.append(("on", kwargs))
        if self.fail_commands:
            from homeassistant.exceptions import HomeAssistantError
            raise HomeAssistantError("Synthetic light failure")
        self._attr_is_on = True
        self._attr_brightness = kwargs.get("brightness", self._attr_brightness)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self.calls.append(("off", kwargs))
        if self.fail_commands:
            from homeassistant.exceptions import HomeAssistantError
            raise HomeAssistantError("Synthetic light failure")
        self._attr_is_on = False
        self.async_write_ha_state()


class DevicePathTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass, self.temp = await start_hass(with_recorder=getattr(self, "with_recorder", False))
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

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self.assertFalse(self.heater.calls[-1])
        self.temp.cleanup()

    async def set_source(self, role, value):
        entity = self.entry.options["bindings"][role]
        old = self.hass.states.get(entity)
        self.hass.states.async_set(entity, str(value), old.attributes if old else {})
        await self.hass.async_block_till_done()

    async def time(self, seconds):
        self.now = self.base + timedelta(seconds=seconds)
        await self.runtime.tick()
        await self.hass.async_block_till_done()

    async def test_full_measured_gang_and_cooling_chain_through_ha(self):
        self.assertTrue(self.heater.calls)
        self.assertFalse(any(self.heater.calls))
        self.assertIsNone(self.runtime.session)
        self.assertFalse(self.light.is_on)
        await self.set_source("control_input", "on")
        self.assertTrue(self.light.is_on)
        self.assertAlmostEqual(self.light.brightness, 255*.35, delta=1)
        normal_brightness = self.light.brightness
        session_id = self.runtime.session.session_id
        provisional = None
        confirmed = None
        for second in range(401):
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
            for position in ("upper", "lower"):
                await self.set_source(f"{position}_temperature", temperature)
                await self.set_source(f"{position}_humidity", humidity)
            await self.runtime.tick()
            await self.hass.async_block_till_done()
            gang = self.runtime.session.timeline.active
            if gang and not gang.infusion_events and provisional is None:
                provisional = gang
            if gang and gang.infusion_events and confirmed is None:
                confirmed = gang
                self.assertEqual(gang.gang_id, provisional.gang_id)
                self.assertEqual(gang.started_at, provisional.started_at)
                self.assertTrue(self.heater.is_on)
        self.assertIsNotNone(provisional)
        self.assertIsNotNone(confirmed)
        self.assertEqual(self.runtime.session.timeline.gang_count, 1)
        self.assertEqual(self.runtime.session.session_id, session_id)
        self.assertFalse(self.heater.is_on)
        self.assertIsNotNone(self.runtime.session.cooling)
        self.assertLess(self.light.brightness, 20)
        end = self.runtime.session.cooling.ends_at
        self.assertEqual(self.runtime.session.cooling.credited_seconds, 30)
        self.now = end
        await self.runtime.tick()
        await self.hass.async_block_till_done()
        self.assertIsNone(self.runtime.session.cooling)
        self.assertEqual(self.light.brightness, normal_brightness)
        self.assertTrue(self.light.is_on)
        self.assertTrue(self.heater.is_on)
        self.assertEqual(self.runtime.session.heating.elapsed_seconds, 0)
        await self.runtime.archive.flush()
        import asyncio
        archived = await asyncio.to_thread(self.runtime.archive.read, session_id, limit=10000)
        originals = [r for r in archived["records"] if r["kind"] == "measurement"]
        self.assertGreaterEqual(len(originals), 4 * 401)
        self.assertTrue(any(r["kind"] == "command" and r["payload"]["heat"] for r in archived["records"]))
        self.assertTrue(any(r["kind"] == "detection" for r in archived["records"]))
        from custom_components.ha_sauna.core.timeline import Kind
        persons = [e for e in self.runtime.session.timeline.processed
                   if e.kind in (Kind.PERSON_STRONG, Kind.PERSON_WEAK)]
        self.assertEqual([e.event_id for e in persons], [provisional.recognition_event_id])
        self.assertGreater(self.runtime.session.heating.intervals[0].ended_at.timestamp() - self.base.timestamp(), 240)

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

    async def test_light_start_after_run_cooling_and_restore_use_real_light_service(self):
        from custom_components.ha_sauna.core.timeline import Event, Kind
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        self.assertAlmostEqual(self.light.brightness, 255*.35, delta=1)
        # Manuelle Helligkeit vor dem Nachlauf muss später zurückkehren.
        await self.hass.services.async_call("light", "turn_on", {"entity_id":self.light.entity_id,"brightness":160}, blocking=True)
        await self.hass.async_block_till_done()
        session_id = self.runtime.session.session_id
        async def signal(kind, seconds):
            self.now=self.base+timedelta(seconds=seconds)
            await self.runtime.receive(Event(str(seconds),session_id,kind,self.now,self.now))
            await self.hass.async_block_till_done()
        await signal(Kind.DOOR_CLOSE,1)
        await signal(Kind.INFUSION,2)
        self.now=self.base+timedelta(seconds=241)
        await self.set_source("upper_temperature",70)
        await self.runtime.tick()
        await signal(Kind.DOOR_OPEN,242)
        await signal(Kind.VENTILATION,243)
        self.assertFalse(self.heater.is_on)
        self.assertAlmostEqual(self.light.brightness,255*.15,delta=1)
        await self.time(273)
        self.assertEqual(self.runtime.controller.phase,"zwangskühlung")
        self.assertAlmostEqual(self.light.brightness,255*.05,delta=1)
        await self.time(274)
        self.assertAlmostEqual(self.light.brightness,255*.05,delta=1)
        self.now=self.base+timedelta(seconds=303)
        await self.set_source("upper_temperature",70)
        await self.runtime.tick()
        await self.hass.async_block_till_done()
        self.assertEqual(self.light.brightness,160)
        brightness=[call[1].get("brightness") for call in self.light.calls if call[0]=="on"]
        self.assertEqual(len(brightness),5)  # Start, manuell, Nachlauf, Kühlung, zurück.

    async def test_light_failure_is_reported_and_does_not_disable_heating(self):
        self.light.fail_commands=True
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        self.assertIn("operation_light",self.runtime.device.faults)
        self.assertTrue(self.heater.is_on)
        self.assertFalse(self.light.is_on)
        await self.time(1)
        self.assertEqual(len(self.light.calls),1)
        await self.runtime.set_operation(False)
        self.light.fail_commands=False
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        self.assertNotIn("operation_light",self.runtime.device.faults)
        self.assertTrue(self.light.is_on)

    async def test_session_light_defaults_reach_real_light_service_and_switch_off(self):
        from custom_components.ha_sauna.core.display import phase_timer
        await self.runtime.set_operation(True)
        session_id = self.runtime.session.session_id
        await self.runtime.set_operation(False)
        await self.hass.async_block_till_done()
        self.heater.calls.clear()
        await self.time(149)
        self.assertIsNotNone(self.runtime.session)
        self.assertAlmostEqual(self.light.brightness, 255*.35, delta=1)
        await self.time(150)
        self.assertIsNone(self.runtime.session)
        self.assertAlmostEqual(self.light.brightness, 255*.5, delta=1)
        self.assertEqual(phase_timer(self.runtime.controller, self.now)["seconds"], 600)
        calls = len(self.light.calls)
        await self.time(749)
        self.assertEqual(len(self.light.calls), calls)
        self.assertTrue(self.light.is_on)
        await self.time(750)
        self.assertFalse(self.light.is_on)
        self.assertIsNone(phase_timer(self.runtime.controller, self.now))
        await self.time(751)
        self.assertEqual(len(self.light.calls), calls+1)
        self.assertNotIn(True, self.heater.calls)
        await self.runtime.archive.flush()
        stored = self.runtime.archive.read(session_id)
        commands = [r["payload"] for r in stored["records"] if r["kind"] == "light_command" and r["payload"]["purpose"] == "session_end"]
        self.assertEqual([c["service"] for c in commands], ["turn_on", "turn_off"])

    async def test_custom_session_light_survives_options_reload_and_new_start_cancels_it(self):
        from custom_components.ha_sauna.settings import async_set_parameters
        await async_set_parameters(self.hass, self.entry, {
            "session_light_minutes": .2, "session_light_brightness_percent": 64}, partial=True)
        await self.hass.async_block_till_done()
        self.runtime = self.entry.runtime_data
        self.base = self.now = datetime.now(UTC)
        self.runtime._clock = lambda: self.now
        await self.runtime.set_operation(True)
        await self.runtime.set_operation(False)
        await self.hass.async_block_till_done()
        await self.time(150)
        phase = self.runtime.controller.light_after_run
        self.assertEqual(phase.ends_at, self.base+timedelta(seconds=162))
        self.assertAlmostEqual(self.light.brightness, 255*.64, delta=1)
        await async_set_parameters(self.hass, self.entry, {"nominal_power_kw": 5}, partial=True)
        await self.hass.async_block_till_done()
        self.runtime = self.entry.runtime_data
        self.runtime._clock = lambda: self.now
        self.assertEqual(self.runtime.controller.light_after_run, phase)
        await self.time(151)
        self.assertAlmostEqual(self.light.brightness, 255*.64, delta=1)
        await self.set_source("upper_temperature", 70)
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        self.assertIsNone(self.runtime.controller.light_after_run)
        self.assertAlmostEqual(self.light.brightness, 255*.35, delta=1)
        await self.time(163)
        self.assertTrue(self.light.is_on)
        self.assertAlmostEqual(self.light.brightness, 255*.35, delta=1)

    async def test_failed_session_light_commands_are_visible_and_not_repeated_each_tick(self):
        await self.runtime.set_operation(True)
        await self.runtime.set_operation(False)
        await self.hass.async_block_till_done()
        self.light.fail_commands = True
        await self.time(150)
        self.assertEqual(self.runtime.device.faults["session_light"], "turn_on_failed")
        calls = len(self.light.calls)
        await self.time(151)
        self.assertEqual(len(self.light.calls), calls)
        await self.time(750)
        self.assertEqual(self.runtime.device.faults["session_light"], "turn_off_failed")
        await self.time(751)
        self.assertEqual(len(self.light.calls), calls+1)
        self.assertFalse(self.heater.is_on)

    async def test_additional_door_signal_updates_timeline_and_cooling_wait(self):
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
        self.assertEqual(self.runtime.controller.cooling_wait_until,doors[-1].effective_at+timedelta(minutes=4))
        self.assertTrue(self.heater.is_on)

    async def test_temperature_entities_cannot_override_running_cooling(self):
        await self.runtime.set_operation(True)
        await self.hass.async_block_till_done()
        self.now=self.base+timedelta(seconds=240)
        await self.set_source("upper_temperature",70)
        await self.runtime.tick()
        await self.hass.async_block_till_done()
        self.assertEqual(self.runtime.controller.phase,"zwangskühlung")
        self.assertFalse(self.heater.is_on)
        cycle=self.runtime.session.cooling
        deadlines=self.runtime.session.deadlines
        self.heater.calls.clear()
        await self.hass.services.async_call("climate","set_temperature",{"entity_id":self.climate,"temperature":95},blocking=True)
        entities=er.async_entries_for_config_entry(er.async_get(self.hass),self.entry.entry_id)
        end=next(e.entity_id for e in entities if e.unique_id.endswith("_final_temperature_c"))
        await self.hass.services.async_call("number","set_value",{"entity_id":end,"value":100},blocking=True)
        await self.hass.async_block_till_done()
        self.assertIs(self.entry.runtime_data,self.runtime)
        self.assertEqual(self.runtime.session.cooling,cycle)
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
        for second, kind in ((1, Kind.DOOR_CLOSE), (2, Kind.INFUSION),
                             (61, Kind.DOOR_OPEN), (62, Kind.VENTILATION)):
            self.now = self.base + timedelta(seconds=second)
            await self.runtime.receive(Event(f"manual-test:{second}", self.runtime.session.session_id,
                kind, self.now, self.now))
            await self.hass.async_block_till_done()
        self.assertEqual(self.runtime.controller.phase, "nachlauf")

    async def test_manual_phase_end_preserves_cooling_light_and_heater_sequence(self):
        await self.prepare_gang_after_run()
        identity = self.runtime.session.session_id
        self.assertFalse(self.heater.is_on)
        self.assertAlmostEqual(self.light.brightness, 255*.15, delta=1)
        await self.time(72)
        await self.runtime.finish_phase("after_run", self.runtime.session.after_run.phase_id)
        await self.hass.async_block_till_done()
        self.assertEqual(self.runtime.controller.phase, "zwangskühlung")
        self.assertEqual(self.runtime.session.cooling.credited_seconds, 10)
        self.assertFalse(self.heater.is_on)
        self.assertAlmostEqual(self.light.brightness, 255*.05, delta=1)
        await self.time(82)
        await self.runtime.finish_phase("forced_cooling", self.runtime.session.cooling.cycle_id)
        await self.hass.async_block_till_done()
        self.assertTrue(self.heater.is_on)
        self.assertAlmostEqual(self.light.brightness, 255*.35, delta=1)
        self.assertEqual(self.runtime.session.timeline.gang_count, 1)
        self.assertEqual(self.runtime.session.session_id, identity)
        self.assertEqual(self.runtime.controller.heating_limit_seconds, 45)
        self.assertEqual(self.runtime.controller.mechanical_timer_status["remaining_seconds"], 14400-62)
        import asyncio
        await self.runtime.archive.flush()
        archived = await asyncio.to_thread(self.runtime.archive.read, identity, limit=10000)
        actions = [r["payload"]["purpose"] for r in archived["records"] if r["kind"] == "manual_phase_end"]
        self.assertEqual(actions, ["after_run", "forced_cooling"])

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

    async def test_detached_binary_button_toggles_operation_on_press_not_release(self):
        from dataclasses import replace
        self.runtime.configuration = replace(self.runtime.configuration, control_input_mode="button")
        await self.set_source("control_input", "on")
        identity = self.runtime.session.session_id
        self.assertTrue(self.runtime.session.operation_enabled)
        self.assertTrue(self.heater.is_on)
        await self.set_source("control_input", "off")
        self.assertTrue(self.runtime.session.operation_enabled)
        await self.set_source("control_input", "on")
        self.assertFalse(self.runtime.session.operation_enabled)
        self.assertFalse(self.heater.is_on)
        await self.set_source("control_input", "off")
        self.assertFalse(self.runtime.session.operation_enabled)
        self.assertEqual(self.runtime.session.session_id, identity)

    async def test_shelly_event_button_ignores_press_release_and_recovery_duplicates(self):
        from dataclasses import replace
        from custom_components.ha_sauna.bindings import Bindings
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
        self.assertIsNone(self.runtime.session)
        await push("btn_up")
        self.assertIsNone(self.runtime.session)
        await push("single_push")
        self.assertTrue(self.runtime.session.operation_enabled)
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
        self.assertFalse(self.runtime.session.operation_enabled)
