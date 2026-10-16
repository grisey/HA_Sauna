"""Synthetic set/identity contracts; no Companion delivery observations."""

import unittest

from custom_components.ha_sauna.core.notification_recipients import (
    ActorRef,
    CapabilityEvidence,
    RecipientAssignment,
    RecipientCapability,
    RecipientRef,
    RecipientResolution,
    ResolvedRecipient,
    SessionRecipients,
    TargetIdentity,
    eligible_recipients,
    validate_assignment,
)


class NotificationRecipientsTests(unittest.TestCase):
    def setUp(self):
        self.evidence = CapabilityEvidence("synthetic adapter", "synthetic contract")
        self.capability = RecipientCapability(self.evidence, self.evidence)
        self.entity = RecipientRef("entity", "notify.synthetic", "synthetic")
        self.device = RecipientRef("device", "device-reference", "synthetic")
        self.other = RecipientRef("device", "other-reference", "synthetic")
        self.target = TargetIdentity("synthetic", "endpoint")
        self.other_target = TargetIdentity("synthetic", "other-endpoint")
        self.assignments = (
            RecipientAssignment("starter", frozenset({self.entity})),
            RecipientAssignment("participant", frozenset({self.device, self.other})),
        )
        self.resolutions = (
            self.resolve(self.entity, self.target),
            self.resolve(self.device, self.target),
            self.resolve(self.other, self.other_target),
        )

    def resolve(self, reference, target, capability=None):
        return RecipientResolution(
            reference,
            ResolvedRecipient(reference, target, capability or self.capability),
        )

    def test_explicit_actor_union_alias_deduplication_and_repetition(self):
        initial = SessionRecipients("instance", "session")
        first = initial.accumulate(
            ActorRef("starter", "authenticated input"),
            self.assignments,
            self.resolutions,
        )
        self.assertEqual(first.targets, {self.target})
        second = first.accumulate(
            ActorRef("participant", "another input"),
            self.assignments,
            self.resolutions,
        )
        self.assertEqual(second.targets, {self.target, self.other_target})
        self.assertEqual(
            second.accumulate(
                ActorRef("participant", "input"),
                self.assignments,
                self.resolutions,
            ),
            second,
        )
        self.assertEqual(initial.targets, frozenset())

    def test_unknown_or_unassigned_actor_has_no_inferred_recipients(self):
        state = SessionRecipients("instance", "session")
        for user_id in (None, "unassigned"):
            with self.subTest(user_id=user_id):
                self.assertEqual(
                    state.accumulate(
                        ActorRef(user_id, "physical input"),
                        self.assignments,
                        self.resolutions,
                    ),
                    state,
                )

    def test_send_capability_requires_evidence_and_resolution(self):
        assignment = (RecipientAssignment("user", {self.entity}),)
        for capability in (
            RecipientCapability(),
            RecipientCapability(reply=self.evidence),
        ):
            with self.subTest(capability=capability):
                result = SessionRecipients("instance", "session").accumulate(
                    ActorRef("user", "input"),
                    assignment,
                    (self.resolve(self.entity, self.target, capability),),
                )
                self.assertFalse(result.targets)
        result = SessionRecipients("instance", "session").accumulate(
            ActorRef("user", "input"),
            assignment,
            (RecipientResolution(self.entity),),
        )
        self.assertFalse(result.targets)

    def test_send_only_targets_can_be_selected_stored_and_accumulated(self):
        capability = RecipientCapability(send=self.evidence)
        resolutions = (self.resolve(self.entity, self.target, capability),)
        assignment = RecipientAssignment("user", {self.entity})
        self.assertTrue(capability.sendable)
        self.assertFalse(capability.interactive)
        self.assertEqual(eligible_recipients(resolutions), {self.entity})
        self.assertIs(validate_assignment(assignment, resolutions), assignment)
        result = SessionRecipients("instance", "session").accumulate(
            ActorRef("user", "input"), (assignment,), resolutions,
        )
        self.assertEqual(result.targets, {self.target})

    def test_sessions_instances_and_transport_namespaces_are_distinct(self):
        first = SessionRecipients("instance", "session", {self.target})
        self.assertNotEqual(first, SessionRecipients("instance", "other-session"))
        self.assertNotEqual(first, SessionRecipients("other-instance", "session"))
        self.assertNotEqual(self.target, TargetIdentity("other", "endpoint"))

    def test_caller_supplies_assignment_snapshot(self):
        state = SessionRecipients("instance", "session", {self.target})
        changed = (RecipientAssignment("starter", {self.other}),)
        result = state.accumulate(
            ActorRef("starter", "input"),
            changed,
            self.resolutions,
        )
        self.assertEqual(result.targets, {self.target, self.other_target})
        self.assertEqual(state.targets, {self.target})

    def test_invalid_or_conflicting_resolution_is_rejected(self):
        with self.assertRaises(ValueError):
            RecipientResolution(
                self.entity,
                ResolvedRecipient(self.device, self.target, self.capability),
            )
        with self.assertRaises(ValueError):
            ResolvedRecipient(
                self.entity,
                TargetIdentity("other", "endpoint"),
                self.capability,
            )
        with self.assertRaises(ValueError):
            SessionRecipients("instance", "session").accumulate(
                ActorRef("starter", "input"),
                self.assignments,
                (self.resolutions[0], RecipientResolution(self.entity)),
            )

    def test_empty_identity_and_empty_evidence_are_not_proof(self):
        for construct in (
            lambda: ActorRef("", "input"),
            lambda: ActorRef(None, ""),
            lambda: TargetIdentity("transport", ""),
            lambda: CapabilityEvidence("source", " "),
            lambda: RecipientCapability(send=True),
            lambda: RecipientRef("person", "reference", "transport"),
            lambda: RecipientAssignment("", frozenset()),
            lambda: SessionRecipients("instance", ""),
        ):
            with self.subTest(construct=construct), self.assertRaises(ValueError):
                construct()

    def test_selection_and_storage_share_capability_validation(self):
        valid = RecipientAssignment("user", {self.entity})
        self.assertEqual(
            eligible_recipients(self.resolutions),
            {
                self.entity,
                self.device,
                self.other,
            },
        )
        self.assertIs(validate_assignment(valid, self.resolutions), valid)
        for resolutions, reason in (
            ((), "No resolution supplied"),
            ((RecipientResolution(self.entity),), "unresolved"),
            (
                (self.resolve(self.entity, self.target, RecipientCapability()),),
                "Send capability evidence required",
            ),
            (
                (
                    self.resolve(
                        self.entity,
                        self.target,
                        RecipientCapability(reply=self.evidence),
                    ),
                ),
                "Send capability evidence required",
            ),
        ):
            with self.subTest(reason=reason):
                self.assertNotIn(self.entity, eligible_recipients(resolutions))
                with self.assertRaisesRegex(ValueError, reason):
                    validate_assignment(valid, resolutions)

    def test_mixed_assignment_is_rejected_without_partial_storage_result(self):
        assignment = RecipientAssignment("user", {self.entity, self.other})
        resolutions = (self.resolutions[0], RecipientResolution(self.other))
        self.assertEqual(eligible_recipients(resolutions), {self.entity})
        with self.assertRaisesRegex(ValueError, "unresolved"):
            validate_assignment(assignment, resolutions)
        self.assertEqual(assignment.recipients, {self.entity, self.other})

    def test_public_forms_reject_untyped_nested_values(self):
        state = SessionRecipients("instance", "session")
        for construct in (
            lambda: RecipientAssignment("user", {"arbitrary"}),
            lambda: SessionRecipients("instance", "session", {"arbitrary"}),
            lambda: ResolvedRecipient("ref", self.target, self.capability),
            lambda: ResolvedRecipient(self.entity, "target", self.capability),
            lambda: ResolvedRecipient(self.entity, self.target, True),
            lambda: RecipientResolution("ref"),
            lambda: RecipientResolution(self.entity, "recipient"),
            lambda: state.accumulate("actor", (), ()),
            lambda: state.accumulate(ActorRef(None, "input"), ("assignment",), ()),
            lambda: state.accumulate(ActorRef(None, "input"), (), ("resolution",)),
            lambda: eligible_recipients(("resolution",)),
            lambda: validate_assignment("assignment", ()),
        ):
            with self.subTest(construct=construct), self.assertRaises(ValueError):
                construct()
