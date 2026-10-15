"""Selected inputs reach the existing control, accounting and session owners."""

import unittest

import test_cooling

from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.input_handoff import (
    ContactorFeedbackInput,
    ControlModeInput,
    HeaterOverrideInput,
    HeatingFeedbackInput,
    InputTiming,
    OperationInput,
    PowerFeedbackInput,
    TemperatureInput,
)
from custom_components.ha_sauna.core.presence import binary_presence
from custom_components.ha_sauna.core.timeline import Kind
from test_cooling import at, controller
from test_foundation import event, selected_occupancy


def timing(second):
    return InputTiming(at(0), at(1), None, at(second))


class ControllerInputTests(unittest.TestCase):
    def test_temperature_uses_evaluation_for_readiness_and_unknown_stops_heat(self):
        owner = controller()
        owner.consume_temperature_input(TemperatureInput(80, at(30), timing(10)))
        self.assertEqual(owner.session.ready_at, at(10))
        self.assertEqual(owner.last_decision.at, at(10))
        owner.consume_temperature_input(TemperatureInput(
            None, None, InputTiming(None, None, None, at(11)),
        ))
        self.assertFalse(owner.last_decision.heat)
        self.assertEqual(owner.last_decision.reason, "upper_temperature_unavailable")

    def test_feedback_and_power_keep_causal_intervals_and_expiry(self):
        owner = controller()
        owner.consume_heating_feedback_input(HeatingFeedbackInput(True, timing(10)))
        owner.report_fallback_heating(False, at(10))
        owner.consume_power_feedback_input(PowerFeedbackInput(1500, at(20), timing(10)))
        owner.advance(at(25))
        interval = owner.session.heating.intervals[0]
        self.assertEqual((interval.started_at, interval.ended_at), (at(10), at(20)))
        self.assertEqual(owner.session.heating.elapsed_seconds, 10)
        self.assertEqual(owner.session.energy.measured_seconds, 10)
        self.assertAlmostEqual(owner.session.energy.measured_kwh, 1500 * 10 / 3600000)
        owner.consume_heating_feedback_input(HeatingFeedbackInput(None, timing(26)))
        owner.advance(at(27))
        self.assertIsNone(owner.session.heating.off_since)
        self.assertEqual(owner.session.heating.elapsed_seconds, 10)

    def test_cooling_waits_for_actual_off_at_evaluation_boundary(self):
        owner = test_cooling.CoolingTests().cooling_after_confirmed_heat(off_feedback=False)
        self.assertTrue(owner.session.after_run.pending_start)
        owner.consume_contactor_feedback_input(ContactorFeedbackInput(None, timing(72)))
        self.assertTrue(owner.session.after_run.pending_start)
        owner.consume_contactor_feedback_input(ContactorFeedbackInput(False, timing(80)))
        self.assertFalse(owner.session.after_run.pending_start)
        self.assertEqual(owner.session.after_run.started_at, at(80))

    def test_operation_mode_and_override_share_recording_and_return_owner_results(self):
        owner = Controller(controller().parameters)
        owner.consume_temperature_input(TemperatureInput(70, None, timing(1)))
        session = owner.consume_operation_input(OperationInput(True, "selected", timing(2)))
        self.assertIs(session, owner.session)
        self.assertEqual(session.session_id, "selected")
        owner.consume_operation_input(OperationInput(False, None, timing(3)))
        self.assertFalse(owner.session.operation_enabled)
        self.assertIsNone(owner.consume_control_mode_input(ControlModeInput("manual", timing(4))))
        decision = owner.consume_heater_override_input(HeaterOverrideInput(True, timing(5)))
        self.assertIs(decision, owner.last_decision)
        self.assertTrue(decision.heat)
        self.assertEqual(owner.session.session_id, "selected")
        self.assertTrue(owner.session.operation_enabled)
        owner.consume_heater_override_input(HeaterOverrideInput(False, timing(6)))
        self.assertFalse(owner.last_decision.heat)
        self.assertFalse(owner.session.operation_enabled)

    def test_event_qualification_retains_originals_and_infusion_annotation(self):
        owner = controller()
        owner.process(event("open", Kind.DOOR_OPEN, 1))
        owner.process(event("close", Kind.DOOR_CLOSE, 2))
        for second, kind in ((3, Kind.PERSON_STRONG), (4, Kind.INFUSION)):
            original = event(f"unqualified-{second}", kind, second)
            self.assertFalse(owner.recognition_allowed(kind, person_evidence_qualified=False))
            self.assertFalse(owner.recognition_allowed_at(
                kind, at(second), person_evidence_qualified=False,
            ))
            result = owner.process(original, person_evidence_qualified=False)
            self.assertEqual(result.reason, "person_evidence_unqualified")
            self.assertIs(owner.session.timeline.processed[-1], original)
            self.assertIsNone(owner.session.timeline.active)
            self.assertEqual(owner._last_at, at(second))
        owner.consume_occupancy_input(selected_occupancy(binary_presence(
            "binary_sensor.selected", "on", at(1), at(5),
        ), at(5)))
        self.assertIsNotNone(owner.session.timeline.active)
        self.assertTrue(owner.recognition_allowed(Kind.INFUSION, person_evidence_qualified=False))
        original = event("infusion", Kind.INFUSION, 6)
        owner.process(original, person_evidence_qualified=False)
        self.assertIs(owner.session.timeline.active.infusion_events[-1], original)
        self.assertEqual(owner.session.timeline.active.started_at, at(2))
