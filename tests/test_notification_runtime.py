"""Mapped participants follow the controller session, including its OFF gap."""

import asyncio
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import Mock

from test_button_runtime import bindings

from custom_components.ha_sauna.bindings import Bindings
from custom_components.ha_sauna.core.notification_recipients import (
    ActorRef, CapabilityEvidence, RecipientAssignment, RecipientCapability,
    RecipientRef, RecipientResolution, ResolvedRecipient, TargetIdentity,
)
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime


class NotificationRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 16, 12, tzinfo=UTC)
        self.reference = RecipientRef("entity", "notify.synthetic", "home_assistant")
        self.target = TargetIdentity("home_assistant", "notify.synthetic")
        self.configuration = Configuration(
            Bindings(bindings()), Parameters({}), notification_assignments=(
                RecipientAssignment("synthetic-user", frozenset((self.reference,))),
            ),
        )
        self.runtime = SaunaRuntime(self.configuration, lambda: self.now)
        self.runtime.notification_entry_id = "synthetic-entry"
        self.runtime.notification_transport = Mock()
        self.runtime.notification_resolutions = (RecipientResolution(
            self.reference, ResolvedRecipient(
                self.reference, self.target,
                RecipientCapability(send=CapabilityEvidence("test", "send-only")),
            ),
        ),)

    def test_mapping_roundtrip_and_archive_privacy(self):
        options = self.configuration.as_options()
        self.assertEqual(Configuration.from_options(options).notification_assignments,
                         self.configuration.notification_assignments)
        self.assertNotIn("notification_assignments", self.configuration.as_snapshot())
        self.assertEqual(Configuration.from_options(
            replace(self.configuration, notification_assignments=()).as_options()
        ).notification_assignments, ())

    def test_start_accumulates_send_only_target_and_off_gap_retains_it(self):
        asyncio.run(self.runtime.set_operation(
            True, actor=ActorRef("synthetic-user", "ha_context"),
        ))
        recipients = self.runtime.notification_recipients
        self.assertEqual(recipients.targets, frozenset((self.target,)))
        asyncio.run(self.runtime.set_operation(False))
        self.assertEqual(self.runtime.notification_recipients, recipients)
        self.runtime.notification_transport.forget_session.assert_not_called()

    def test_unknown_actor_does_not_guess_participant(self):
        asyncio.run(self.runtime.set_operation(True, actor=ActorRef(None, "ha_context")))
        self.assertFalse(self.runtime.notification_recipients.targets)

    def test_completion_and_unload_clear_transient_participants(self):
        asyncio.run(self.runtime.set_operation(
            True, actor=ActorRef("synthetic-user", "ha_context"),
        ))
        session_id = self.runtime.session.session_id
        self.runtime.controller.finish_session(self.now)
        self.runtime.notify()
        self.assertIsNone(self.runtime.notification_recipients)
        self.runtime.notification_transport.forget_session.assert_called_once_with(session_id)
        asyncio.run(self.runtime.set_operation(True))
        asyncio.run(self.runtime.close())
        self.assertIsNone(self.runtime._notification_recipients)

    def test_second_user_effective_off_accumulates_but_duplicate_on_does_not(self):
        second = RecipientRef("entity", "notify.second", "home_assistant")
        second_target = TargetIdentity("home_assistant", "notify.second")
        self.runtime.configuration = replace(self.configuration, notification_assignments=(
            *self.configuration.notification_assignments,
            RecipientAssignment("second", frozenset((second,))),
        ))
        self.runtime.notification_resolutions += (RecipientResolution(second, ResolvedRecipient(
            second, second_target, RecipientCapability(send=CapabilityEvidence("test", "send")),
        )),)
        asyncio.run(self.runtime.set_operation(True, actor=ActorRef("synthetic-user", "test")))
        asyncio.run(self.runtime.set_operation(True, actor=ActorRef("second", "test")))
        self.assertEqual(self.runtime.notification_recipients.targets, frozenset((self.target,)))
        asyncio.run(self.runtime.set_operation(False, actor=ActorRef("second", "test")))
        self.assertEqual(self.runtime.notification_recipients.targets,
                         frozenset((self.target, second_target)))
