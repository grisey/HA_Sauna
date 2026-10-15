"""Prepared source-free control inputs preserve domain values and causal time."""

import unittest
from dataclasses import FrozenInstanceError
from datetime import timedelta

from custom_components.ha_sauna.core.contracts import ControlInputs
from custom_components.ha_sauna.core.input_handoff import (
    ContactorFeedbackInput,
    ControlModeInput,
    DoorInput,
    HeaterOverrideInput,
    HeatingFeedbackInput,
    InfusionInput,
    InputTiming,
    OccupancyInput,
    OperationInput,
    PowerFeedbackInput,
    TemperatureInput,
    VentilationInput,
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
        occupancy = OccupancyInput("unknown", False, self.timing)
        door = DoorInput(Door.UNKNOWN, (), self.timing)

        for value in (temperature, heating, contactor, power, occupancy, door):
            with self.subTest(value=type(value).__name__):
                handoff = ModelHandoff(self.context, None, value)
                self.assertIs(handoff.after, value)
                self.assertEqual(value.timing.effective_at, T0)
                self.assertEqual(
                    value.timing.received_at, T0 + timedelta(seconds=20)
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
        self.assertNotEqual(occupancy, OccupancyInput("absent", True, self.timing))

    def test_validity_is_not_replaced_by_a_later_handoff_observation(self) -> None:
        valid_until = T0 + timedelta(seconds=22)
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

    def test_ventilation_preserves_the_owner_findings_without_adding_an_end(self) -> None:
        event = Event(
            event_id="ventilation-1",
            session_id="session",
            kind=Kind.VENTILATION,
            effective_at=T0,
            detected_at=T0 + timedelta(seconds=5),
            booking_at=None,
        )
        value = VentilationInput((event,), self.timing)

        self.assertIs(ModelHandoff(self.context, None, value).after, value)
        self.assertEqual(value.occurrences, (event,))
        self.assertEqual(value.occurrences[0].booking_at, event.detected_at)
        self.assertEqual(VentilationInput((), self.timing).occurrences, ())

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
        before = DoorInput(Door.CLOSED, (), self.timing)
        after = DoorInput(Door.CLOSED, (opened, closed), self.timing)

        handoff = ModelHandoff(self.context, before, after)
        self.assertEqual(handoff.before.state, handoff.after.state)
        self.assertEqual(handoff.after.transitions, (opened, closed))
        self.assertEqual(handoff.after.transitions[0].booking_at, opened.detected_at)
        self.assertEqual(
            handoff.after.transitions[1].booking_at,
            T0 + timedelta(seconds=10),
        )

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
        with self.assertRaisesRegex(ValueError, "VENTILATION"):
            VentilationInput((evidence,), self.timing)
        with self.assertRaisesRegex(ValueError, "DOOR_OPEN or DOOR_CLOSE"):
            DoorInput(Door.UNKNOWN, (evidence,), self.timing)

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
