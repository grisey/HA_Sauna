"""Die führende Mindesttemperatur gilt für neue Gänge und ihre Türanker."""
import asyncio
import unittest

from test_cooling import at, controller
from test_foundation import T0, event
from test_presence_regressions import START, _runtime

from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.presence import (
    ProxyPresenceSource,
    binary_presence,
)
from custom_components.ha_sauna.core.timeline import Door, Event, Kind


class GangTemperatureTests(unittest.TestCase):
    def test_all_automatic_start_routes_require_current_valid_minimum(self):
        for temperature in (25, 59.9, 60, None, float("nan"), float("inf"), True):
            for kind in (Kind.PERSON_STRONG, Kind.PERSON_WEAK, Kind.INFUSION):
                for proxy in ((False, True) if kind != Kind.INFUSION else (False,)):
                    with self.subTest(temperature=temperature, kind=kind, proxy=proxy):
                        c = Controller(controller().parameters)
                        c.set_temperature(temperature, T0)
                        c.set_operation(True, T0, session_id="s")
                        c.process(event("entry-open", Kind.DOOR_OPEN, 1))
                        c.process(event("close", Kind.DOOR_CLOSE, 1))
                        signal = event("signal", kind, 2)
                        admitted = temperature == 60
                        self.assertEqual(c.recognition_allowed(kind), admitted)
                        result = (
                            c.process_presence(ProxyPresenceSource.present(signal), signal)
                            if proxy else c.process(signal)
                        )
                        self.assertEqual(result.changed, admitted)
                        self.assertEqual(c.session.timeline.active is not None, admitted)
                        if not admitted:
                            self.assertFalse(c.regulation_inputs.gang_heat_demand)
                            self.assertEqual(c.session.timeline.gang_count, 0)

    def test_cold_close_cannot_become_a_backdated_warm_start(self):
        for kind in (Kind.PERSON_STRONG, Kind.PERSON_WEAK, Kind.INFUSION):
            with self.subTest(kind=kind):
                c = controller()
                c.set_temperature(25, at(0))
                c.process(event("entry-open", Kind.DOOR_OPEN, 1))
                c.process(event("close", Kind.DOOR_CLOSE, 1))
                self.assertEqual(c.session.timeline.door, Door.CLOSED)
                self.assertIsNone(c.session.timeline.anchor)
                c.set_temperature(60, at(9))
                result = c.process(event("signal", kind, 10))
                self.assertEqual(result.reason, "entry_context_missing")
                self.assertIsNone(c.session.timeline.active)

    def test_delayed_close_uses_temperature_at_its_effective_time(self):
        for temperature in (25, None):
            with self.subTest(temperature=temperature):
                c = controller()
                c.set_temperature(temperature, at(1))
                c.set_temperature(60, at(9))
                c.process(Event("entry-open", "s", Kind.DOOR_OPEN, at(1), at(10)))
                c.process(Event("close", "s", Kind.DOOR_CLOSE, at(2), at(10)))
                self.assertIsNone(c.session.timeline.anchor)
                result = c.process(event("infusion", Kind.INFUSION, 11))
                self.assertEqual(result.reason, "entry_context_missing")
                self.assertIsNone(c.session.timeline.active)

    def test_warm_close_keeps_its_observed_start_after_history_pruning(self):
        c = controller()
        c.process(event("entry-open", Kind.DOOR_OPEN, 1))
        c.process(event("close", Kind.DOOR_CLOSE, 1))
        c.discard_recognition_context_before(at(5))
        c.set_temperature(62, at(9))
        c.process(event("weak", Kind.PERSON_WEAK, 10))
        self.assertEqual(c.session.timeline.active.started_at, at(1))
        self.assertEqual(len(c._gang_temperature_gates), 1)

    def test_old_cold_signal_cannot_start_when_delivery_is_warm(self):
        c = controller()
        c.set_temperature(25, at(1))
        c.process(event("entry-open", Kind.DOOR_OPEN, 2))
        c.process(event("close", Kind.DOOR_CLOSE, 2))
        c.set_temperature(60, at(9))
        self.assertFalse(c.recognition_allowed_at(Kind.INFUSION, at(5)))
        self.assertFalse(c.recognition_allowed_at(Kind.INFUSION, at(9)))
        result = c.process(Event("old", "s", Kind.INFUSION, at(5), at(10)),
                           recognition_at=at(5))
        self.assertEqual(result.reason, "temperature_below_minimum")
        self.assertIsNone(c.session.timeline.active)

    def test_expired_selected_temperature_cannot_start_or_preserve_an_unused_anchor(self):
        c = controller()
        c.set_temperature(70, at(0), valid_until=at(5))
        c.process(event("entry-open", Kind.DOOR_OPEN, 1))
        c.process(event("close", Kind.DOOR_CLOSE, 1))
        c.advance(at(5))
        self.assertTrue(c.recognition_allowed(Kind.INFUSION))
        c.advance(at(6))
        self.assertFalse(c.recognition_allowed(Kind.INFUSION))
        result = c.process(event("expired", Kind.INFUSION, 6))
        self.assertEqual(result.reason, "temperature_unavailable")
        c.set_temperature(70, at(9), valid_until=at(20))
        self.assertIsNone(c.session.timeline.anchor)
        self.assertFalse(c.recognition_allowed_at(Kind.INFUSION, at(7)))
        result = c.process(event("fresh", Kind.INFUSION, 10))
        self.assertEqual(result.reason, "entry_context_missing")
        self.assertIsNone(c.session.timeline.active)
        c.process(event("fresh-open", Kind.DOOR_OPEN, 11))
        c.process(event("fresh-close", Kind.DOOR_CLOSE, 12))
        c.process(event("fresh-entry", Kind.INFUSION, 13))
        self.assertEqual(c.session.timeline.active.started_at, at(12))

    def test_expired_delivery_cannot_book_an_old_fresh_start(self):
        c = controller()
        c.set_temperature(70, at(0), valid_until=at(5))
        c.process(event("entry-open", Kind.DOOR_OPEN, 1))
        c.process(event("close", Kind.DOOR_CLOSE, 1))
        result = c.process(Event("late", "s", Kind.INFUSION, at(2), at(10), at(3)),
                           recognition_at=at(2))
        self.assertEqual(result.reason, "temperature_unavailable")
        self.assertIsNone(c.session.timeline.active)

    def test_delayed_close_from_expired_or_cold_stretch_cannot_reappear(self):
        for interruption in ("expiry", "cold"):
            with self.subTest(interruption=interruption):
                c = controller()
                c.set_temperature(70, at(0), valid_until=at(5))
                if interruption == "cold":
                    c.set_temperature(25, at(4), valid_until=at(8))
                c.set_temperature(70, at(9), valid_until=at(20))
                c.process(Event("entry-open", "s", Kind.DOOR_OPEN, at(0), at(10)))
                c.process(Event("close", "s", Kind.DOOR_CLOSE, at(1), at(10)))
                self.assertIsNone(c.session.timeline.anchor)

    def test_continuous_fresh_measurements_keep_the_same_temperature_interval(self):
        c = controller()
        c.set_temperature(70, at(0), valid_until=at(5))
        c.process(event("entry-open", Kind.DOOR_OPEN, 1))
        c.process(event("close", Kind.DOOR_CLOSE, 1))
        c.set_temperature(71, at(5), valid_until=at(10))
        self.assertEqual(len(c._gang_temperature_gates), 1)
        c.process(event("water", Kind.INFUSION, 8))
        self.assertEqual(c.session.timeline.active.started_at, at(1))

    def test_temperature_drop_does_not_block_confirmation_followup_or_ventilation(self):
        for temperature in (25, None):
            with self.subTest(temperature=temperature):
                c = controller()
                c.process(event("entry-open", Kind.DOOR_OPEN, 1))
                c.process(event("close", Kind.DOOR_CLOSE, 1))
                c.process(event("person", Kind.PERSON_STRONG, 2))
                gang_id = c.session.timeline.active.gang_id
                c.set_temperature(temperature, at(3))
                self.assertTrue(c.recognition_allowed_at(Kind.INFUSION, at(3)))
                c.process(event("water", Kind.INFUSION, 4), recognition_at=at(4))
                c.process(event("water2", Kind.INFUSION, 5), recognition_at=at(5))
                self.assertEqual(c.session.timeline.active.gang_id, gang_id)
                self.assertEqual(len(c.session.timeline.active.infusion_events), 2)
                c.process(event("open", Kind.DOOR_OPEN, 6))
                c.process(event("vent", Kind.VENTILATION, 7))
                self.assertIsNone(c.session.timeline.active)
                self.assertEqual(c.session.timeline.gang_count, 1)
                self.assertIsNotNone(c.session.after_run)

    def test_direct_presence_remains_observational_at_any_temperature(self):
        async def exercise(temperature):
            runtime = _runtime([START])
            runtime.controller.set_temperature(temperature, START)
            runtime.controller.set_operation(True, START, session_id="direct")
            decisions = len(runtime.controller.decisions)
            report = binary_presence("binary_sensor.presence", "on", START, START)
            await runtime.accept_presence(report)
            self.assertIsNone(runtime.session.timeline.active)
            self.assertEqual(len(runtime.controller.decisions), decisions)
            self.assertEqual(runtime.presence.external[report.source], report)

        for temperature in (25, 60):
            with self.subTest(temperature=temperature):
                asyncio.run(exercise(temperature))
