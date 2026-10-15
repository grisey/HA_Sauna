"""Synthetic association checks; no transport or device functionality evidence."""

import unittest
from dataclasses import replace

from custom_components.ha_sauna.core.notification_exchange import (
    DeviceReceipt,
    NotificationEnvelope,
    ReplyAssociation,
    ReplyCorrelator,
    ReplyDisposition,
    ReplyEnvelope,
    TransportHandoff,
)
from custom_components.ha_sauna.core.notification_recipients import (
    ActorRef,
    TargetIdentity,
)


class NotificationExchangeTests(unittest.TestCase):
    def setUp(self):
        self.target = TargetIdentity("synthetic", "endpoint")
        self.notification = NotificationEnvelope(
            "message", "correlation", "entry", self.target, {"opaque": 1}, "session"
        )
        self.reply = ReplyEnvelope(
            "reply", "message", "correlation", "entry", self.target,
            ActorRef("reply-user", "authenticated-adapter"), {"opaque": 2}, "session"
        )
        self.correlator = ReplyCorrelator()
        self.correlator.register(self.notification)

    def test_association_preserves_independent_actor_and_payload(self):
        result = self.correlator.receive(self.reply)
        self.assertEqual(result.disposition, ReplyDisposition.ACCEPTED)
        self.assertIs(result.notification, self.notification)
        self.assertIs(result.reply.payload, self.reply.payload)
        self.assertEqual(result.reply.actor.user_id, "reply-user")

    def test_unknown_scopes_do_not_consume_reply_identity(self):
        changes = (
            {"correlation_id": "unknown"}, {"entry_id": "other"},
            {"session_id": "other"}, {"session_id": None},
            {"source": TargetIdentity("synthetic", "other")},
            {"source": TargetIdentity("other", "endpoint")},
        )
        for change in changes:
            with self.subTest(change=change):
                result = self.correlator.receive(replace(self.reply, **change))
                self.assertEqual(result.disposition, ReplyDisposition.UNKNOWN)
                self.assertIsNone(result.notification)
        self.assertEqual(
            self.correlator.receive(self.reply).disposition, ReplyDisposition.ACCEPTED
        )

    def test_wrong_message_is_rejected_before_duplicate_tracking(self):
        result = self.correlator.receive(replace(self.reply, message_id="other"))
        self.assertEqual(result.disposition, ReplyDisposition.MESSAGE_MISMATCH)
        self.assertIsNone(result.notification)
        self.assertEqual(
            self.correlator.receive(self.reply).disposition, ReplyDisposition.ACCEPTED
        )

    def test_duplicate_identity_and_distinct_replies(self):
        self.correlator.receive(self.reply)
        duplicate = replace(self.reply, payload={"changed": True})
        self.assertEqual(
            self.correlator.receive(duplicate).disposition, ReplyDisposition.DUPLICATE
        )
        self.assertEqual(
            self.correlator.receive(replace(self.reply, reply_id="another")).disposition,
            ReplyDisposition.ACCEPTED,
        )

    def test_same_ids_in_distinct_sessions_and_targets_are_independent(self):
        for changes in ({"session_id": "other"}, {"session_id": None}):
            notification = replace(self.notification, **changes)
            self.correlator.register(notification)
            self.assertEqual(
                self.correlator.receive(replace(self.reply, **changes)).disposition,
                ReplyDisposition.ACCEPTED,
            )
        target = TargetIdentity("synthetic", "second")
        self.correlator.register(replace(self.notification, target=target))
        self.assertEqual(
            self.correlator.receive(replace(self.reply, source=target)).disposition,
            ReplyDisposition.ACCEPTED,
        )
        self.assertEqual(
            self.correlator.receive(self.reply).disposition, ReplyDisposition.ACCEPTED
        )

    def test_registration_cannot_erase_duplicate_knowledge(self):
        self.correlator.receive(self.reply)
        with self.assertRaises(ValueError):
            self.correlator.register(self.notification)
        self.assertEqual(
            self.correlator.receive(self.reply).disposition, ReplyDisposition.DUPLICATE
        )

    def test_explicit_forgetting_checks_message_and_releases_association(self):
        with self.assertRaises(ValueError):
            self.correlator.forget(replace(self.notification, message_id="other"))
        self.assertTrue(self.correlator.forget(self.notification))
        self.assertFalse(self.correlator.forget(self.notification))
        self.assertEqual(
            self.correlator.receive(self.reply).disposition, ReplyDisposition.UNKNOWN
        )

    def test_handoff_receipt_and_reply_are_separate_observations(self):
        handoff = TransportHandoff(self.notification, True, "adapter acceptance")
        receipt = DeviceReceipt(self.notification, "observed receipt")
        self.assertTrue(handoff.accepted)
        self.assertNotIsInstance(handoff, DeviceReceipt)
        self.assertIs(receipt.notification, self.notification)
        self.assertEqual(
            self.correlator.receive(self.reply).disposition, ReplyDisposition.ACCEPTED
        )

    def test_empty_identity_or_observation_evidence_is_rejected(self):
        for change in ({"message_id": ""}, {"entry_id": " "}, {"session_id": ""}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(self.notification, **change)
        with self.assertRaises(ValueError):
            replace(self.reply, reply_id="")
        with self.assertRaises(ValueError):
            TransportHandoff(self.notification, True, "")
        with self.assertRaises(ValueError):
            DeviceReceipt(self.notification, "")

    def test_nested_exchange_references_require_the_declared_types(self):
        changes = (
            (self.notification, {"target": None}),
            (self.notification, {"target": "endpoint"}),
            (self.reply, {"source": None}),
            (self.reply, {"actor": None}),
            (self.reply, {"actor": "reply-user"}),
        )
        for envelope, change in changes:
            with self.subTest(change=change), self.assertRaises(TypeError):
                replace(envelope, **change)
        for value in (None, self.reply, "message"):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    TransportHandoff(value, True, "evidence")
                with self.assertRaises(TypeError):
                    DeviceReceipt(value, "evidence")
        for accepted in (None, 0, 1, "true"):
            with self.subTest(accepted=accepted), self.assertRaises(TypeError):
                TransportHandoff(self.notification, accepted, "evidence")

    def test_association_types_are_checked_without_interpreting_payloads(self):
        association = ReplyAssociation(
            ReplyDisposition.ACCEPTED, self.reply, self.notification
        )
        for change in (
            {"disposition": "accepted"}, {"reply": None},
            {"reply": self.notification}, {"notification": self.reply},
        ):
            with self.subTest(change=change), self.assertRaises(TypeError):
                replace(association, **change)
        opaque = object()
        self.assertIs(replace(self.notification, payload=opaque).payload, opaque)
        self.assertIsNone(replace(self.reply, payload=None).payload)

    def test_correlator_rejects_wrong_envelope_types_at_each_boundary(self):
        calls = (
            (self.correlator.register, self.reply),
            (self.correlator.receive, self.notification),
            (self.correlator.forget, self.reply),
        )
        for method, wrong_type in calls:
            for value in (None, wrong_type, object()):
                with self.subTest(method=method.__name__, value=value):
                    with self.assertRaises(TypeError):
                        method(value)
        self.assertEqual(
            self.correlator.receive(self.reply).disposition, ReplyDisposition.ACCEPTED
        )

    def test_successful_association_requires_complete_matching_notification(self):
        changes = (
            {"message_id": "other"}, {"correlation_id": "other"},
            {"entry_id": "other"}, {"session_id": "other"},
            {"session_id": None},
            {"target": TargetIdentity("synthetic", "other")},
            {"target": TargetIdentity("other", "endpoint")},
        )
        for disposition in (ReplyDisposition.ACCEPTED, ReplyDisposition.DUPLICATE):
            with self.subTest(disposition=disposition):
                association = ReplyAssociation(
                    disposition, self.reply, self.notification
                )
                with self.assertRaises(ValueError):
                    replace(association, notification=None)
                for change in changes:
                    with self.subTest(change=change), self.assertRaises(ValueError):
                        replace(
                            association,
                            notification=replace(self.notification, **change),
                        )
                unscoped_notification = replace(self.notification, session_id=None)
                unscoped_reply = replace(self.reply, session_id=None)
                self.assertIs(
                    ReplyAssociation(disposition, unscoped_reply,
                                     unscoped_notification).notification,
                    unscoped_notification,
                )

    def test_unsuccessful_association_cannot_claim_a_notification(self):
        for disposition in (
            ReplyDisposition.UNKNOWN, ReplyDisposition.MESSAGE_MISMATCH,
        ):
            association = ReplyAssociation(disposition, self.reply)
            self.assertIsNone(association.notification)
            for notification in (
                self.notification, replace(self.notification, message_id="other"),
            ):
                with (
                    self.subTest(disposition=disposition),
                    self.assertRaises(ValueError),
                ):
                    replace(association, notification=notification)
