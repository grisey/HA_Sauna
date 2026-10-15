"""Read-only observation forms over existing model transitions."""

import unittest
from dataclasses import FrozenInstanceError, replace

from custom_components.ha_sauna.core.control_handoff import (
    ControllerOutput,
    ConfigurationOutput,
    ConsumerEventOutput,
    PresenceOutput,
    PresenceSourceSelectionSnapshot,
)
from custom_components.ha_sauna.core.consumer_events import gang_changes
from custom_components.ha_sauna.core.configuration_model import ConfigurationModel
from custom_components.ha_sauna.core.model_handoff import (
    HandoffContext, ModelHandoff, freeze_data,
)
from custom_components.ha_sauna.core.contracts import ConsumerEvent
from custom_components.ha_sauna.core.notification_recipients import ActorRef
from custom_components.ha_sauna.core.presence import (
    PresenceProjection,
    ProxyPresenceSource,
    binary_presence,
)
from custom_components.ha_sauna.core.timeline import Kind
from test_cooling import at, controller
from custom_components.ha_sauna.runtime import Configuration
from test_foundation import bindings, event, parameters


class ControlHandoffTests(unittest.TestCase):
    def test_source_selection_observation_does_not_choose_or_backdate_a_source(self):
        initial = PresenceSourceSelectionSnapshot(
            "ha_presence", "ha_presence", None, None, None, None,
        )
        selected = PresenceSourceSelectionSnapshot(
            "ha_presence", "proxy", False, at(1), at(3), at(4),
        )
        handoff = ModelHandoff(HandoffContext("entry", "selection", at(5)), initial, selected)
        self.assertIsNone(handoff.before.available)
        self.assertEqual(handoff.after.configured_source, "ha_presence")
        self.assertEqual(handoff.after.effective_source, "proxy")
        self.assertEqual(handoff.after.availability_since, at(1))
        self.assertEqual(handoff.after.last_changed_at, at(3))
        self.assertEqual(handoff.after.last_processed_at, at(4))

    def test_configuration_copies_entire_existing_options_and_capability_model(self):
        configuration = Configuration(bindings(), parameters())
        options = configuration.as_options()
        model = ConfigurationModel.from_configuration(configuration)
        output = ConfigurationOutput(freeze_data(options), model)
        self.assertEqual(output.options, freeze_data(configuration.as_options()))
        self.assertEqual(set(output.options), set(options))
        self.assertIs(output.model, model)
        saved = output.options
        options.clear()
        self.assertEqual(output.options, saved)
        self.assertTrue(output.options)
        with self.assertRaises(FrozenInstanceError):
            output.model = model
        for change in (
            {"options": configuration.as_options()},
            {"options": ActorRef(None, "input")},
            {"model": ActorRef(None, "input")},
        ):
            with self.subTest(change=change), self.assertRaises(TypeError):
                replace(output, **change)

    def test_capture_reuses_owned_models_and_existing_derived_values(self):
        owner = controller()
        owner.report_contactor(True, at(1))
        owner.report_heating(True, at(1))
        owner.report_power(2400, at(20), at(2))
        owner.report_fallback_heating(False, at(2))
        snapshot = ControllerOutput.capture(owner)
        self.assertIs(snapshot.session, owner.session)
        self.assertIs(snapshot.last_decision, owner.last_decision)
        self.assertIs(snapshot.automatic_decision, owner.automatic_decision)
        self.assertEqual(
            snapshot.parameters, tuple(sorted(owner.parameters.as_dict().items()))
        )
        for name in (
            "phase", "target_temperature", "next_gang_temperature",
            "thermostat_target", "thermostat_restart_temperature",
            "regulation_inputs", "regulation_causes",
            "heater_override_ends_at", "control_mode_change_allowed",
        ):
            with self.subTest(name=name):
                self.assertEqual(getattr(snapshot, name), getattr(owner, name))
        self.assertEqual(snapshot.power_w, 2400)
        self.assertEqual(snapshot.power_valid_until, at(20))
        self.assertEqual(snapshot.fallback_heating, False)
        self.assertEqual(snapshot.last_at, owner._last_at)
        with owner.recognition_temperature_raster(72, at(10)):
            raster = ControllerOutput.capture(owner)
        self.assertEqual(raster.recognition_temperature_raster, (72, at(10)))
        self.assertIsNone(snapshot.recognition_temperature_raster)

    def test_capture_does_not_mutate_owner_or_follow_later_mutations(self):
        owner = controller()
        before = ControllerOutput.capture(owner)
        decisions = tuple(owner.decisions)
        events = tuple(owner.consumer_events)
        second = ControllerOutput.capture(owner)
        self.assertEqual(before, second)
        self.assertEqual(tuple(owner.decisions), decisions)
        self.assertEqual(tuple(owner.consumer_events), events)
        owner.protection.add("synthetic")
        owner.inhibits.add("synthetic")
        owner.set_temperature(75, at(1), valid_until=at(5))
        after = ControllerOutput.capture(owner)
        self.assertFalse(before.protection)
        self.assertFalse(before.inhibits)
        self.assertEqual(after.protection, {"synthetic"})
        self.assertEqual(after.temperature_valid_until, at(5))
        self.assertNotEqual(before.session, after.session)
        with self.assertRaises(FrozenInstanceError):
            before.phase = "changed"

    def test_completed_session_retains_terminal_output(self):
        owner = controller()
        active = ControllerOutput.capture(owner)
        owner.finish_session(at(1))
        final = ControllerOutput.capture(owner)
        self.assertIsNone(final.session)
        self.assertEqual(len(final.completed_sessions), 1)
        terminal = final.completed_sessions[0]
        self.assertEqual(terminal.session_id, active.session.session_id)
        self.assertEqual(terminal.ended_at, at(1))
        self.assertEqual(terminal.deadlines, ())
        self.assertIsNone(active.session.ended_at)
        self.assertIs(final.light_after_run, owner.light_after_run)

    def test_override_binding_and_door_state_are_observed(self):
        owner = controller()
        owner.set_heater_override(False, at(1))
        snapshot = ControllerOutput.capture(owner)
        self.assertEqual(snapshot.override_binding, owner._override_snapshot)
        self.assertIsNotNone(snapshot.override_binding)
        self.assertEqual(snapshot.heater_override, False)
        self.assertIs(snapshot.manual_thermostat, owner._manual_thermostat)
        owner.process(event("opening", Kind.DOOR_OPEN, 2))
        owner.process(event("closure", Kind.DOOR_CLOSE, 3))
        door = ControllerOutput.capture(owner)
        self.assertIs(door.door_request, owner.door_request)
        self.assertEqual(door.door_request_pending, owner._door_request_pending)
        self.assertNotEqual(snapshot.door_request, door.door_request)

    def test_presence_capture_copies_all_views_without_accepting_reports(self):
        owner = PresenceProjection()
        proxy = ProxyPresenceSource.present(event("person", Kind.PERSON_STRONG, 1))
        direct = binary_presence("binary_sensor.synthetic", "on", at(2), at(2))
        owner.accept(proxy)
        owner.accept(direct)
        before = PresenceOutput.capture(owner)
        self.assertEqual(before.active_source, owner.active_source)
        self.assertIs(before.current, proxy)
        self.assertEqual(before.external, ((direct.source, direct),))
        self.assertEqual(before.reports, tuple(sorted(owner.reports.items())))
        self.assertEqual(before.observations, owner.observations)
        self.assertEqual(PresenceOutput.capture(owner), before)
        owner.accept(binary_presence("binary_sensor.synthetic", "off", at(3), at(3)))
        after = PresenceOutput.capture(owner)
        self.assertIsNone(before.observations[0].ended_at)
        self.assertEqual(after.observations[0].ended_at, at(3))
        self.assertEqual(len(before.reports), 2)
        self.assertEqual(len(after.reports), 3)

    def test_factories_require_their_model_owner(self):
        with self.assertRaises(TypeError):
            ControllerOutput.capture(PresenceProjection())
        with self.assertRaises(TypeError):
            PresenceOutput.capture(controller())

    def test_output_fields_reject_immutable_values_of_the_wrong_type(self):
        snapshot = ControllerOutput.capture(controller())
        for change in (
            {"regulation_inputs": ActorRef(None, "input")},
            {"door_request": snapshot.session},
            {"completed_sessions": (snapshot.last_decision,)},
            {"parameters": (("wrong", "value"),)},
        ):
            with self.subTest(change=change), self.assertRaises(TypeError):
                replace(snapshot, **change)
        presence = PresenceOutput.capture(PresenceProjection())
        for change in (
            {"current": ActorRef(None, "input")},
            {"reports": (("wrong", snapshot.session),)},
            {"observations": (snapshot.session,)},
        ):
            with self.subTest(change=change), self.assertRaises(TypeError):
                replace(presence, **change)

    def test_gang_event_contains_actual_owner_and_recognition_evidence(self):
        owner = controller()
        owner.process(event("open", Kind.DOOR_OPEN, 1))
        owner.process(event("close", Kind.DOOR_CLOSE, 2))
        owner.process(event("person", Kind.PERSON_STRONG, 3))
        owner.process(event("infusion", Kind.INFUSION, 4))
        before = owner.session
        owner.process(event("exit-open", Kind.DOOR_OPEN, 70))
        owner.process(event("vent", Kind.VENTILATION, 71))
        after = owner.session
        events = gang_changes(before, after, at(71))
        ended = next(item for item in events if item.kind == "gang_ended")
        output = ConsumerEventOutput(ended, after)
        self.assertIs(output.event, ended)
        self.assertIs(output.gang, after.timeline.completed[-1])
        self.assertEqual(output.gang.recognition_event_id, "person")
        self.assertEqual(output.event.source_ref, output.gang.end_event_id)
        owner.finish_session(at(72))
        self.assertIs(output.session, after)
        self.assertEqual(output.gang.recognition_event_id, "person")
        for event_change, session in (
            ({}, None), ({"session_id": "other"}, after),
            ({"gang_id": "missing"}, after),
        ):
            with self.subTest(event_change=event_change), self.assertRaises(ValueError):
                ConsumerEventOutput(replace(ended, **event_change), session)

    def test_presence_event_has_complete_matching_source_with_optional_session(self):
        report = binary_presence("binary_sensor.synthetic", "on", at(1), at(1))
        value = ConsumerEvent(
            report.report_id, "occupancy", None, report.effective_at,
            report.received_at, report.source_ref, presence=report,
        )
        output = ConsumerEventOutput(value)
        self.assertIs(output.event.presence, report)
        self.assertIsNone(output.gang)
        session = controller().session
        scoped = ConsumerEventOutput(replace(value, session_id=session.session_id), session)
        self.assertIs(scoped.session, session)
        for change in (
            {"presence": None}, {"source_ref": "different"},
            {"event_id": "different"}, {"received_at": at(2)},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                ConsumerEventOutput(replace(value, **change))
        with self.assertRaises(ValueError):
            ConsumerEventOutput(value, session)
        with self.assertRaises(TypeError):
            ConsumerEventOutput(ActorRef(None, "input"))
