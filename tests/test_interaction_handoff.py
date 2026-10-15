"""Complete interaction snapshots leave existing gesture and reply owners intact."""

import unittest
from dataclasses import FrozenInstanceError, fields, replace

from custom_components.ha_sauna.core.button import (
    START_HOLD,
    START_RELEASE,
    ButtonGestures,
)
from custom_components.ha_sauna.core.interaction_handoff import (
    ButtonGestureSnapshot,
    DeviceReceiptSnapshot,
    NotificationEnvelopeSnapshot,
    ReplyAssociationSnapshot,
    ReplyCorrelatorSnapshot,
    ReplyEnvelopeSnapshot,
    TransportHandoffSnapshot,
)
from custom_components.ha_sauna.core.model_handoff import (
    FrozenMap,
    HandoffContext,
    ModelHandoff,
)
from custom_components.ha_sauna.core.notification_exchange import (
    DeviceReceipt,
    ReplyCorrelator,
    ReplyDisposition,
    TransportHandoff,
)
from test_button import THRESHOLD, at
from test_foundation import T0
import test_notification_exchange as exchange_fixtures


class ButtonGestureSnapshotTests(unittest.TestCase):
    def test_capture_is_complete_and_does_not_consume_the_next_gesture_action(
        self,
    ) -> None:
        owner = ButtonGestures(None, "long")
        reference = ButtonGestures(None, "long")
        for button in (owner, reference):
            self.assertIsNone(button.handle("press", False, at(0)))
            self.assertEqual(button.handle("long", False, at(2)), START_HOLD)

        before = ButtonGestureSnapshot.capture(owner)
        self.assertEqual(before, ButtonGestureSnapshot.capture(owner))
        self.assertEqual(
            {definition.name for definition in fields(before)},
            {name.removeprefix("_") for name in vars(owner)},
        )
        self.assertTrue(before.start_hold_sent)
        self.assertTrue(before.long_seen)
        self.assertIsNone(before.hold_threshold)
        self.assertEqual(owner.handle("release", True, at(3)), START_RELEASE)
        self.assertEqual(reference.handle("release", True, at(3)), START_RELEASE)
        self.assertEqual(
            ButtonGestureSnapshot.capture(owner),
            ButtonGestureSnapshot.capture(reference),
        )
        self.assertTrue(before.start_hold_sent)
        self.assertTrue(before.long_seen)

        after = ButtonGestureSnapshot.capture(owner)
        context = HandoffContext("entry", "button-observation", T0)
        handoff = ModelHandoff(context, before, after)
        self.assertIs(handoff.before, before)
        self.assertIs(handoff.after, after)
        with self.assertRaises(FrozenInstanceError):
            before.pressed_at = None

    def test_binary_hold_configuration_and_unknown_native_context_are_preserved(
        self,
    ) -> None:
        owner = ButtonGestures(THRESHOLD, "double")
        initial = ButtonGestureSnapshot.capture(owner)
        self.assertEqual(initial.hold_threshold, THRESHOLD)
        self.assertEqual(initial.session_gesture, "double")
        self.assertIsNone(initial.native_started_while_off)
        owner.handle("on", True, at(0))
        pressed = ButtonGestureSnapshot.capture(owner)

        self.assertEqual(pressed.pressed_at, at(0))
        self.assertTrue(pressed.binary_press)
        self.assertTrue(pressed.gesture_active)
        self.assertIsNone(initial.pressed_at)
        handoff = ModelHandoff(
            HandoffContext("entry", "binary-button-observation", T0),
            initial,
            pressed,
        )
        self.assertEqual(handoff.after.hold_threshold, THRESHOLD)


class ReplyCorrelatorSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        # Reuse the existing exchange fixture without inheriting its test cases.
        exchange_fixtures.NotificationExchangeTests.setUp(self)
        self.context = HandoffContext("entry", "reply-observation", T0)

    def test_complete_exchange_observations_keep_both_payloads_without_aliases(
        self,
    ) -> None:
        notification_payload = {"opaque": [{"notification": 1}]}
        reply_payload = {"opaque": [{"reply": 2}]}
        notification = replace(self.notification, payload=notification_payload)
        reply = replace(self.reply, payload=reply_payload)
        owner = ReplyCorrelator()
        owner.register(notification)
        association = owner.receive(reply)
        handoff = TransportHandoff(notification, False, "adapter observation")
        receipt = DeviceReceipt(notification, "device observation")

        reply_snapshot = ReplyEnvelopeSnapshot.capture(reply)
        handoff_snapshot = TransportHandoffSnapshot.capture(handoff)
        receipt_snapshot = DeviceReceiptSnapshot.capture(receipt)
        association_snapshot = ReplyAssociationSnapshot.capture(association)
        observations = (
            (reply, reply_snapshot),
            (handoff, handoff_snapshot),
            (receipt, receipt_snapshot),
            (association, association_snapshot),
        )
        for original, snapshot in observations:
            with self.subTest(model=type(snapshot).__name__):
                self.assertEqual(
                    {definition.name for definition in fields(snapshot)},
                    {definition.name for definition in fields(original)},
                )
                self.assertIs(
                    ModelHandoff(self.context, None, snapshot).after, snapshot
                )

        self.assertEqual(reply_snapshot.reply_id, reply.reply_id)
        self.assertEqual(reply_snapshot.message_id, reply.message_id)
        self.assertEqual(reply_snapshot.correlation_id, reply.correlation_id)
        self.assertEqual(reply_snapshot.entry_id, reply.entry_id)
        self.assertEqual(reply_snapshot.session_id, reply.session_id)
        self.assertIs(reply_snapshot.source, reply.source)
        self.assertIs(reply_snapshot.actor, reply.actor)
        self.assertFalse(handoff_snapshot.accepted)
        self.assertEqual(handoff_snapshot.evidence, handoff.evidence)
        self.assertEqual(receipt_snapshot.evidence, receipt.evidence)
        self.assertEqual(association_snapshot.disposition, ReplyDisposition.ACCEPTED)
        self.assertEqual(association_snapshot.reply, reply_snapshot)

        notification_payload["opaque"][0]["notification"] = 10
        reply_payload["opaque"][0]["reply"] = 20
        for snapshot in (
            handoff_snapshot.notification,
            receipt_snapshot.notification,
            association_snapshot.notification,
        ):
            self.assertEqual(snapshot.payload["opaque"][0]["notification"], 1)
        self.assertEqual(reply_snapshot.payload["opaque"][0]["reply"], 2)
        self.assertEqual(association_snapshot.reply.payload["opaque"][0]["reply"], 2)
        self.assertIs(association.notification, notification)
        self.assertIs(association.reply, reply)
        self.assertEqual(
            owner.receive(reply).disposition,
            ReplyDisposition.DUPLICATE,
        )

    def test_association_capture_preserves_every_existing_disposition(self) -> None:
        unknown = self.correlator.receive(replace(self.reply, correlation_id="unknown"))
        mismatch = self.correlator.receive(replace(self.reply, message_id="other"))
        accepted = self.correlator.receive(self.reply)
        duplicate = self.correlator.receive(self.reply)

        for association, expected in (
            (unknown, ReplyDisposition.UNKNOWN),
            (mismatch, ReplyDisposition.MESSAGE_MISMATCH),
            (accepted, ReplyDisposition.ACCEPTED),
            (duplicate, ReplyDisposition.DUPLICATE),
        ):
            with self.subTest(disposition=expected):
                snapshot = ReplyAssociationSnapshot.capture(association)
                self.assertEqual(snapshot.disposition, expected)
                self.assertEqual(snapshot.reply.reply_id, association.reply.reply_id)
                self.assertEqual(
                    snapshot.reply.message_id, association.reply.message_id
                )
                if association.notification is None:
                    self.assertIsNone(snapshot.notification)
                else:
                    self.assertEqual(
                        snapshot.notification,
                        NotificationEnvelopeSnapshot.capture(association.notification),
                    )
                self.assertIs(
                    ModelHandoff(self.context, None, snapshot).after, snapshot
                )

        self.assertEqual(
            self.correlator.receive(
                replace(self.reply, reply_id="next-reply")
            ).disposition,
            ReplyDisposition.ACCEPTED,
        )

    def test_snapshot_preserves_every_registration_payload_and_reply_identity(
        self,
    ) -> None:
        self.assertEqual(
            self.correlator.receive(self.reply).disposition,
            ReplyDisposition.ACCEPTED,
        )
        payload = {
            "opaque": {"arbitrary_key": [1, {"nested": "original"}]},
            "uninterpreted": ["original"],
            "actor_context": self.reply.actor,
        }
        additional = replace(
            self.notification,
            message_id="additional-message",
            correlation_id="additional-correlation",
            session_id=None,
            payload=payload,
        )
        self.correlator.register(additional)
        snapshot = ReplyCorrelatorSnapshot.capture(self.correlator)

        self.assertEqual(snapshot, ReplyCorrelatorSnapshot.capture(self.correlator))
        self.assertEqual(len(snapshot.registrations), 2)
        registered = {
            item.notification.message_id: item for item in snapshot.registrations
        }
        original = registered[self.notification.message_id]
        other = registered[additional.message_id]
        self.assertEqual(original.reply_ids, frozenset({self.reply.reply_id}))
        self.assertEqual(other.reply_ids, frozenset())
        self.assertEqual(
            {definition.name for definition in fields(other.notification)},
            {definition.name for definition in fields(additional)},
        )
        self.assertEqual(other.notification.message_id, additional.message_id)
        self.assertEqual(other.notification.correlation_id, additional.correlation_id)
        self.assertEqual(other.notification.entry_id, additional.entry_id)
        self.assertIs(other.notification.target, additional.target)
        self.assertIs(other.notification.payload["actor_context"], self.reply.actor)
        self.assertIsNone(other.notification.session_id)
        self.assertIsInstance(other.notification.payload, FrozenMap)
        self.assertEqual(
            other.notification.payload["opaque"]["arbitrary_key"][1]["nested"],
            "original",
        )

        payload["opaque"]["arbitrary_key"][1]["nested"] = "changed later"
        payload["uninterpreted"].append("another-value")
        self.assertEqual(
            other.notification.payload["opaque"]["arbitrary_key"][1]["nested"],
            "original",
        )
        self.assertEqual(
            other.notification.payload["uninterpreted"],
            ("original",),
        )
        self.assertIs(ModelHandoff(self.context, None, snapshot).after, snapshot)

        self.assertEqual(
            self.correlator.receive(self.reply).disposition,
            ReplyDisposition.DUPLICATE,
        )
        self.assertEqual(
            self.correlator.receive(
                replace(self.reply, reply_id="new-reply")
            ).disposition,
            ReplyDisposition.ACCEPTED,
        )
        self.assertEqual(original.reply_ids, frozenset({self.reply.reply_id}))
        self.assertTrue(self.correlator.forget(additional))
        self.assertEqual(len(snapshot.registrations), 2)

    def test_registration_order_does_not_change_the_snapshot(self) -> None:
        another = replace(
            self.notification,
            message_id="second-message",
            correlation_id="second-correlation",
            session_id=None,
        )
        first = ReplyCorrelator()
        second = ReplyCorrelator()
        first.register(self.notification)
        first.register(another)
        second.register(another)
        second.register(self.notification)

        self.assertEqual(
            ReplyCorrelatorSnapshot.capture(first),
            ReplyCorrelatorSnapshot.capture(second),
        )

    def test_global_correlator_snapshot_keeps_all_entry_and_session_scopes(
        self,
    ) -> None:
        other_entry = replace(
            self.notification,
            entry_id="other-entry",
            session_id=None,
        )
        self.correlator.register(other_entry)
        snapshot = ReplyCorrelatorSnapshot.capture(self.correlator)
        handoff = ModelHandoff(
            HandoffContext(None, "global-reply-observation", T0),
            None,
            snapshot,
        )

        self.assertIs(handoff.after, snapshot)
        self.assertEqual(
            {
                (item.notification.entry_id, item.notification.session_id)
                for item in snapshot.registrations
            },
            {("entry", "session"), ("other-entry", None)},
        )

    def test_live_opaque_objects_keep_the_transport_contract_but_are_not_snapshots(
        self,
    ) -> None:
        opaque = object()
        notification = replace(self.notification, payload=opaque)
        owner = ReplyCorrelator()
        owner.register(notification)

        with self.assertRaises(TypeError):
            NotificationEnvelopeSnapshot.capture(notification)
        with self.assertRaises(TypeError):
            ReplyCorrelatorSnapshot.capture(owner)

        self.assertIs(notification.payload, opaque)
        result = owner.receive(self.reply)
        self.assertEqual(result.disposition, ReplyDisposition.ACCEPTED)
        self.assertIs(result.notification, notification)
        self.assertIs(result.notification.payload, opaque)

        reply = replace(self.reply, payload=opaque)
        association = owner.receive(reply)
        with self.assertRaises(TypeError):
            ReplyEnvelopeSnapshot.capture(reply)
        with self.assertRaises(TypeError):
            ReplyAssociationSnapshot.capture(association)
        self.assertIs(association.reply.payload, opaque)


if __name__ == "__main__":
    unittest.main()
