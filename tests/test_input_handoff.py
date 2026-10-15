"""Prepared source-free control inputs preserve domain values and causal time."""

import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta

from custom_components.ha_sauna.core.contracts import ControlInputs
from custom_components.ha_sauna.core.input_handoff import (
    ContactorFeedbackInput,
    ControlModeInput,
    DoorInput,
    DoorOperation,
    HeaterOverrideInput,
    HeatingFeedbackInput,
    InfusionInput,
    InputTiming,
    OccupancyInput,
    OperationInput,
    PowerFeedbackInput,
    TemperatureInput,
)
from custom_components.ha_sauna.core.model_handoff import HandoffContext, ModelHandoff
from custom_components.ha_sauna.core.observation_handoff import HeatingObservation
from custom_components.ha_sauna.core.timeline import Door, Event, Kind
from test_foundation import T0


class InputHandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.timing = InputTiming(
            effective_at=T0,
            received_at=T0 + timedelta(seconds=20),
            first_usable_at=T0 + timedelta(seconds=23),
            evaluated_at=T0 + timedelta(seconds=25),
        )
        self.context = HandoffContext(
            entry_id="entry",
            handoff_id="input-observation",
            observed_at=T0 + timedelta(seconds=30),
        )

    def test_different_separate_evidence_yields_the_same_selected_heating_input(self) -> None:
        relay = HeatingObservation("relay", True, None, True, False)
        power = HeatingObservation("power", True, 4500.0, None, False)
        selected_from_relay = HeatingFeedbackInput(True, self.timing)
        selected_from_power = HeatingFeedbackInput(True, self.timing)

        self.assertNotEqual(relay, power)
        self.assertEqual(selected_from_relay, selected_from_power)
        self.assertEqual(
            ModelHandoff(self.context, None, selected_from_relay).after,
            ModelHandoff(self.context, None, selected_from_power).after,
        )
        self.assertIs(ModelHandoff(self.context, None, relay).after, relay)
        self.assertIs(ModelHandoff(self.context, None, power).after, power)

    def test_unknown_values_keep_their_own_time_and_validity(self) -> None:
        temperature = TemperatureInput(None, None, self.timing)
        heating = HeatingFeedbackInput(None, self.timing)
        contactor = ContactorFeedbackInput(None, self.timing)
        power = PowerFeedbackInput(None, None, self.timing)
        occupancy = OccupancyInput(
            "unknown", False, False, None, self.timing
        )
        door = DoorInput(Door.UNKNOWN, (), (), self.timing)

        for value in (temperature, heating, contactor, power, occupancy, door):
            with self.subTest(value=type(value).__name__):
                handoff = ModelHandoff(self.context, None, value)
                self.assertIs(handoff.after, value)
                self.assertEqual(value.timing.effective_at, T0)
                self.assertEqual(
                    value.timing.received_at, T0 + timedelta(seconds=20)
                )
                self.assertEqual(
                    value.timing.first_usable_at, T0 + timedelta(seconds=23)
                )
                self.assertEqual(
                    value.timing.evaluated_at, T0 + timedelta(seconds=25)
                )
                self.assertNotEqual(
                    value.timing.evaluated_at, self.context.observed_at
                )

        self.assertIsNone(temperature.value)
        self.assertIsNone(temperature.valid_until)
        self.assertIsNone(heating.heating)
        self.assertIsNone(contactor.contactor)
        self.assertIsNone(power.power_w)
        self.assertIsNone(power.valid_until)
        self.assertNotEqual(heating, HeatingFeedbackInput(False, self.timing))
        self.assertNotEqual(contactor, ContactorFeedbackInput(False, self.timing))
        self.assertNotEqual(power, PowerFeedbackInput(0.0, None, self.timing))
        self.assertNotEqual(
            occupancy,
            OccupancyInput("absent", True, True, "report", self.timing),
        )

    def test_presence_qualification_does_not_contain_person_events(self) -> None:
        qualified = OccupancyInput(
            "present", True, True, "report-on", self.timing
        )
        absent = OccupancyInput(
            "absent", True, True, "report-off", self.timing
        )
        for value in (qualified, absent):
            with self.subTest(value=value):
                self.assertIs(ModelHandoff(self.context, None, value).after, value)
        self.assertTrue(qualified.qualified_presence)
        self.assertEqual(qualified.report_id, "report-on")
        self.assertTrue(absent.qualified_presence)
        self.assertEqual(absent.occupancy, "absent")

    def test_evidence_loss_is_unknown_and_can_leave_an_unchanged_admitted_state(self) -> None:
        admitted = OccupancyInput(
            "present", True, True, "report-on", self.timing
        )
        lost = OccupancyInput(
            "unknown", False, False, "report-unknown", self.timing
        )

        handoff = ModelHandoff(self.context, admitted, lost)
        self.assertEqual(handoff.after.occupancy, "unknown")
        self.assertFalse(handoff.after.qualified_presence)
        unchanged = ModelHandoff(self.context, admitted, admitted)
        self.assertIs(unchanged.before, unchanged.after)

    def test_validity_is_not_replaced_by_a_later_handoff_observation(self) -> None:
        valid_until = T0 + timedelta(seconds=26)
        value = TemperatureInput(85.5, valid_until, self.timing)
        power = PowerFeedbackInput(4500.0, valid_until, self.timing)

        self.assertEqual(ModelHandoff(self.context, None, value).after, value)
        self.assertEqual(ModelHandoff(self.context, None, power).after, power)
        self.assertEqual(value.valid_until, valid_until)
        self.assertEqual(power.valid_until, valid_until)

    def test_repeated_infusions_remain_distinct_with_original_event_times(self) -> None:
        first = Event(
            event_id="infusion-1",
            session_id="session",
            kind=Kind.INFUSION,
            effective_at=T0,
            detected_at=T0 + timedelta(seconds=5),
            booking_at=T0 + timedelta(seconds=3),
        )
        second = Event(
            event_id="infusion-2",
            session_id="session",
            kind=Kind.INFUSION,
            effective_at=T0 + timedelta(seconds=10),
            detected_at=T0 + timedelta(seconds=15),
            booking_at=None,
        )
        value = InfusionInput((first, second), self.timing)

        self.assertIs(ModelHandoff(self.context, None, value).after, value)
        self.assertEqual(value.occurrences, (first, second))
        self.assertIs(value.occurrences[0], first)
        self.assertEqual(value.occurrences[0].effective_at, T0)
        self.assertEqual(
            value.occurrences[0].detected_at, T0 + timedelta(seconds=5)
        )
        self.assertEqual(
            value.occurrences[0].booking_at, T0 + timedelta(seconds=3)
        )
        self.assertEqual(value.occurrences[1].booking_at, second.detected_at)
        self.assertNotEqual(first.event_id, second.event_id)
        self.assertEqual(
            value.timing.first_usable_at, T0 + timedelta(seconds=23)
        )
        self.assertNotEqual(value.timing.first_usable_at, first.detected_at)
        self.assertNotEqual(value.timing.first_usable_at, self.context.observed_at)

    def test_ventilation_stays_bound_to_the_existing_opening(self) -> None:
        opened = Event(
            "door-open-1", "session", Kind.DOOR_OPEN, T0, T0,
        )
        event = Event(
            event_id="ventilation-1",
            session_id="session",
            kind=Kind.VENTILATION,
            effective_at=T0 + timedelta(seconds=2),
            detected_at=T0 + timedelta(seconds=5),
            booking_at=None,
        )
        operation = DoorOperation(opened, None, event, True)
        value = DoorInput(
            Door.OPEN, (opened, event), (operation,), self.timing
        )

        self.assertIs(ModelHandoff(self.context, None, value).after, value)
        self.assertEqual(value.events, (opened, event))
        self.assertIs(value.operations[0].opening, opened)
        self.assertIs(value.operations[0].ventilation, event)
        self.assertEqual(value.operations[0].opening.event_id, "door-open-1")
        self.assertIsNone(value.operations[0].closing)
        unknown = DoorOperation(opened, None, None, False)
        unconfirmed = DoorOperation(opened, None, None, True)
        self.assertNotEqual(unknown, unconfirmed)
        self.assertIsNone(unknown.ventilation)
        self.assertIsNone(unconfirmed.ventilation)

    def test_door_returning_to_closed_keeps_both_transitions(self) -> None:
        opened = Event(
            event_id="door-open-1",
            session_id="session",
            kind=Kind.DOOR_OPEN,
            effective_at=T0,
            detected_at=T0 + timedelta(seconds=1),
            booking_at=None,
        )
        closed = Event(
            event_id="door-close-1",
            session_id="session",
            kind=Kind.DOOR_CLOSE,
            effective_at=T0 + timedelta(seconds=10),
            detected_at=T0 + timedelta(seconds=11),
            booking_at=T0 + timedelta(seconds=10),
        )
        operation = DoorOperation(opened, closed, None, True)
        before = DoorInput(Door.CLOSED, (), (), self.timing)
        after = DoorInput(Door.CLOSED, (opened, closed), (operation,), self.timing)

        handoff = ModelHandoff(self.context, before, after)
        self.assertEqual(handoff.before.state, handoff.after.state)
        self.assertEqual(handoff.after.events, (opened, closed))
        self.assertEqual(handoff.after.events[0].booking_at, opened.detected_at)
        self.assertEqual(
            handoff.after.events[1].booking_at,
            T0 + timedelta(seconds=10),
        )

    def test_equal_booking_times_keep_joint_door_and_ventilation_order(self) -> None:
        opened = Event("open", "session", Kind.DOOR_OPEN, T0, T0)
        at = T0 + timedelta(seconds=10)
        ventilation = Event("vent", "session", Kind.VENTILATION, at, at)
        closed = Event("close", "session", Kind.DOOR_CLOSE, at, at)
        operation = DoorOperation(opened, closed, ventilation, True)
        first = DoorInput(
            Door.CLOSED, (opened, ventilation, closed), (operation,), self.timing
        )
        second = replace(first, events=(opened, closed, ventilation))

        self.assertEqual(
            first.events[1].booking_at, first.events[2].booking_at
        )
        self.assertEqual(first.events, (opened, ventilation, closed))
        self.assertEqual(second.events, (opened, closed, ventilation))
        self.assertNotEqual(first, second)
        self.assertIs(ModelHandoff(self.context, first, second).after, second)

    def test_unmatched_closing_and_unknown_state_need_no_fabricated_opening(self) -> None:
        closed = Event("close", "session", Kind.DOOR_CLOSE, T0, T0)
        value = DoorInput(Door.CLOSED, (closed,), (), self.timing)

        self.assertIs(ModelHandoff(self.context, None, value).after, value)
        self.assertEqual(value.operations, ())
        self.assertEqual(value.events, (closed,))
        self.assertEqual(DoorInput(Door.UNKNOWN, (), (), self.timing).events, ())

    def test_presence_evidence_cannot_be_relabelled_as_a_domain_occurrence(self) -> None:
        evidence = Event(
            event_id="person-evidence",
            session_id="session",
            kind=Kind.PERSON_STRONG,
            effective_at=T0,
            detected_at=T0 + timedelta(seconds=5),
            booking_at=None,
        )

        with self.assertRaisesRegex(ValueError, "INFUSION"):
            InfusionInput((evidence,), self.timing)
        with self.assertRaisesRegex(ValueError, "DOOR_OPEN"):
            DoorInput(Door.UNKNOWN, (evidence,), (), self.timing)

    def test_operation_mode_override_and_rule_levels_stay_independent(self) -> None:
        operation = OperationInput(True, "session", self.timing)
        mode = ControlModeInput("manual", self.timing)
        override = HeaterOverrideInput(None, self.timing)
        inputs = ControlInputs(
            gang_heat_demand=True,
            temporary_door_heat=False,
            cooling=False,
        )

        for value in (operation, mode, override, inputs):
            with self.subTest(value=type(value).__name__):
                self.assertIs(ModelHandoff(self.context, None, value).after, value)

        self.assertTrue(operation.enabled)
        self.assertEqual(operation.session_id, "session")
        self.assertEqual(mode.mode, "manual")
        self.assertIsNone(override.heat)
        self.assertNotEqual(override, HeaterOverrideInput(False, self.timing))
        self.assertTrue(inputs.gang_heat_demand)
        self.assertFalse(inputs.temporary_door_heat)
        self.assertFalse(inputs.cooling)
        with self.assertRaises(FrozenInstanceError):
            operation.enabled = False


if __name__ == "__main__":
    unittest.main()
