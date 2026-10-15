"""Explicit model consumption using existing core fixtures; no runtime stream."""

import unittest
from dataclasses import dataclass, fields, replace
from datetime import timedelta

from custom_components.ha_sauna.core.contracts import ConsumerEvent
from custom_components.ha_sauna.core.control_handoff import ControllerOutput
from custom_components.ha_sauna.core.model_handoff import (
    FrozenMap,
    HandoffContext,
    ModelHandoff,
    freeze_data,
)
from custom_components.ha_sauna.core.models import Session
from custom_components.ha_sauna.core.notification_recipients import ActorRef, SessionRecipients
from test_cooling import at, controller
from test_foundation import T0


@dataclass(frozen=True)
class ExtendedObservation:
    session: Session
    new_detail: tuple[str, ...]


@dataclass(frozen=True)
class RecipientAggregate:
    sessions: tuple[SessionRecipients, ...]


class ModelHandoffTests(unittest.TestCase):
    def setUp(self):
        self.context = HandoffContext("entry", "observation", T0)

    def test_consumer_uses_complete_owner_output_after_owner_has_advanced(self):
        owner = controller()
        before = ControllerOutput.capture(owner)
        owner.finish_session(at(1))
        after = ControllerOutput.capture(owner)
        handoff = ModelHandoff(self.context, before, after)
        # A subsequent owner change cannot replace the terminal observation.
        owner.set_operation(True, at(2), session_id="next")
        received = []

        class Consumer:
            def consume(self, observation: ModelHandoff[ControllerOutput]) -> None:
                received.append((
                    observation.context.entry_id,
                    observation.before.session.session_id,
                    observation.after.session,
                    observation.after.completed_sessions[-1].ended_at,
                ))

        Consumer().consume(handoff)
        self.assertEqual(received, [("entry", "s", None, at(1))])
        self.assertEqual(owner.session.session_id, "next")

    def test_existing_nested_model_values_are_not_rebuilt_or_field_filtered(self):
        snapshot = ControllerOutput.capture(controller())
        for definition in fields(snapshot):
            value = getattr(snapshot, definition.name)
            if value is not None and hasattr(value, "__dataclass_fields__"):
                with self.subTest(model=type(value).__name__):
                    handoff = ModelHandoff(self.context, None, value)
                    self.assertIs(handoff.after, value)
                    self.assertIs(handoff.model_type, type(value))
        extended = ExtendedObservation(snapshot.session, ("retained", "whole"))
        handoff = ModelHandoff(self.context, None, extended)
        self.assertIs(handoff.after, extended)
        self.assertEqual(handoff.after.new_detail, ("retained", "whole"))

    def test_repeated_evaluation_and_absence_have_no_implicit_filter(self):
        state = controller().session
        initial = ModelHandoff(self.context, None, state)
        repeated = ModelHandoff(replace(self.context, handoff_id="again"), state, state)
        removed = ModelHandoff(replace(self.context, handoff_id="absent"), state, None)
        self.assertIs(initial.after, repeated.after)
        self.assertIs(removed.before, state)
        self.assertIsNone(removed.after)
        self.assertIs(removed.model_type, Session)
        with self.assertRaises(ValueError):
            ModelHandoff(self.context, None, None)
        with self.assertRaises(TypeError):
            ModelHandoff(self.context, state, state.timeline)

    def test_session_scope_cannot_conflict_with_owned_nested_identity(self):
        state = controller().session
        context = replace(self.context, session_id="s")
        self.assertIs(ModelHandoff(context, None, state).after, state)
        with self.assertRaises(ValueError):
            ModelHandoff(replace(context, session_id="other"), None, state)
        with self.assertRaises(ValueError):
            ModelHandoff(context, state, Session.create("other", T0))
        # An aggregate across sessions has no single-session claim.
        ModelHandoff(self.context, state, Session.create("other", T0))
        decision = controller().last_decision
        with self.assertRaises(ValueError):
            ModelHandoff(context, None, replace(decision, session_id="other"))

    def test_context_preserves_unknown_actor_and_original_model_times(self):
        state = Session.create("s", T0)
        context = HandoffContext(
            "entry", "delivery", T0 + timedelta(seconds=10),
            ActorRef(None, "explicit-source"), "s",
        )
        result = ModelHandoff(context, None, state)
        self.assertIsNone(result.context.actor.user_id)
        self.assertEqual(result.after.started_at, T0)
        self.assertEqual(result.context.observed_at, T0 + timedelta(seconds=10))
        for change in ({"entry_id": ""}, {"session_id": ""}, {"handoff_id": " "}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(context, **change)
        with self.assertRaises(TypeError):
            replace(context, actor="guessed-user")

    def test_entry_scope_is_checked_with_and_without_session_scope(self):
        state = SessionRecipients("entry", "s")
        for session_id in (None, "s"):
            context = replace(self.context, session_id=session_id)
            self.assertIs(ModelHandoff(context, None, state).after, state)
            with self.assertRaises(ValueError):
                ModelHandoff(context, None, replace(state, entry_id="other"))

    def test_global_aggregate_preserves_entry_identities_without_a_common_claim(self):
        state = RecipientAggregate((
            SessionRecipients("entry", "first"),
            SessionRecipients("other", "second"),
        ))
        context = HandoffContext(None, "global", T0)
        handoff = ModelHandoff(context, state, state)
        self.assertEqual(tuple(item.entry_id for item in handoff.after.sessions),
                         ("entry", "other"))
        with self.assertRaises(ValueError):
            ModelHandoff(replace(context, entry_id="entry"), None, state)
        with self.assertRaises(ValueError):
            ModelHandoff(replace(context, session_id="first"), None, state)

    def test_complete_diagnostic_copy_keeps_unanticipated_fields(self):
        source = {"new-field": {"values": [1, 2]}, "when": T0, "missing": None}
        frozen = freeze_data(source)
        source["new-field"]["values"].append(3)
        self.assertIsInstance(frozen, FrozenMap)
        self.assertEqual(frozen["new-field"]["values"], (1, 2))
        self.assertEqual(frozen["when"], T0)
        self.assertIn("missing", frozen)
        with self.assertRaises(TypeError):
            frozen["when"] = at(1)
        with self.assertRaises(TypeError):
            ModelHandoff(self.context, None, frozen)

    def test_incomplete_reference_events_need_their_closed_owner_form(self):
        reference = ConsumerEvent("e", "gang_ended", "s", T0, T0, "source", "gang")
        with self.assertRaises(TypeError):
            ModelHandoff(self.context, None, reference)

    def test_immutable_container_does_not_hide_wrong_declared_model_type(self):
        state = controller().session
        wrong = ExtendedObservation(ActorRef(None, "source"), ("detail",))
        with self.assertRaises(TypeError):
            ModelHandoff(self.context, None, wrong)
        with self.assertRaises(TypeError):
            ModelHandoff(self.context, None, ExtendedObservation(state, ["mutable"]))

    def test_mutable_live_cyclic_and_nonfinite_data_are_rejected(self):
        @dataclass
        class MutableObservation:
            value: int

        with self.assertRaises(TypeError):
            ModelHandoff(self.context, None, MutableObservation(1))
        with self.assertRaises(TypeError):
            freeze_data({"live": object()})
        cycle = []
        cycle.append(cycle)
        with self.assertRaises(ValueError):
            freeze_data(cycle)
        with self.assertRaises(ValueError):
            freeze_data({"invalid": float("nan")})
        with self.assertRaises(ValueError):
            FrozenMap((("same", 1), ("same", 2)))
        with self.assertRaises(TypeError):
            freeze_data({1: "not a field name"})

    def test_direct_map_construction_retains_the_immutable_boundary(self):
        with self.assertRaises(TypeError):
            FrozenMap((("nested", ([],)),))
        with self.assertRaises(ValueError):
            FrozenMap((("nested", (float("inf"),)),))
        original = {"nested": {"items": [{"value": 1}, {"value": 2}]}}
        copied = freeze_data(original)
        original["nested"]["items"][0]["value"] = 99
        self.assertEqual(copied["nested"]["items"][0]["value"], 1)
        self.assertEqual(copied["nested"]["items"][1]["value"], 2)
