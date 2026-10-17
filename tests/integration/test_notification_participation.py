"""Successful session controls add their observed user; no-op/rejection does not."""

import json
import unittest
from dataclasses import replace
from datetime import timedelta

from homeassistant.auth.const import GROUP_ID_USER
from homeassistant.core import Context
from homeassistant.helpers import entity_registry as er

import test_device_path as device_path
from custom_components.ha_sauna.core.notification_recipients import (
    ActorRef,
    CapabilityEvidence,
    RecipientAssignment,
    RecipientCapability,
    RecipientRef,
    RecipientResolution,
    ResolvedRecipient,
    TargetIdentity,
)
from custom_components.ha_sauna.settings import async_set_program


class NotificationParticipationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = device_path.DevicePathTests.asyncSetUp

    async def prepare_participants(self, *, start_operation=True):
        self.users = [
            await self.hass.auth.async_create_user(
                f"Synthetic participant {index}",
                group_ids=[GROUP_ID_USER],
            )
            for index in range(2)
        ]
        references = [
            RecipientRef("entity", f"notify.synthetic_{i}", "home_assistant")
            for i in range(2)
        ]
        self.targets = [
            TargetIdentity("home_assistant", ref.reference_id) for ref in references
        ]
        self.runtime.configuration = replace(
            self.runtime.configuration,
            notification_assignments=tuple(
                RecipientAssignment(user.id, frozenset((reference,)))
                for user, reference in zip(self.users, references, strict=True)
            ),
        )
        self.runtime.notification_resolutions = tuple(
            RecipientResolution(
                reference,
                ResolvedRecipient(
                    reference,
                    target,
                    RecipientCapability(send=CapabilityEvidence("test", "send")),
                ),
            )
            for reference, target in zip(references, self.targets, strict=True)
        )
        if not start_operation:
            return
        await self.hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": self.operation},
            blocking=True,
            context=Context(user_id=self.users[0].id),
        )
        self.assertEqual(
            self.runtime.notification_recipients.targets, frozenset((self.targets[0],))
        )

    def assert_both(self):
        self.assertEqual(
            self.runtime.notification_recipients.targets, frozenset(self.targets)
        )

    def light_participation_snapshot(self):
        output = self.runtime.device.light_output
        recipients = self.runtime.notification_recipients
        return {
            "override": {
                "brightness": output.manual_brightness,
                "ends_at": (
                    output.manual_ends_at.isoformat()
                    if output.manual_ends_at is not None
                    else None
                ),
            },
            "targets": (
                sorted(target.endpoint for target in recipients.targets)
                if recipients is not None
                else []
            ),
        }

    async def select_external_light(self, brightness, context):
        """Observe the bound HA entity's actual service-generated state event."""
        observed = []

        async def changed(event):
            new = event.data.get("new_state")
            if (
                event.data.get("entity_id") == self.light.entity_id
                and new is not None
                and new.context.id == context.id
            ):
                observed.append(event)

        unsubscribe = self.hass.bus.async_listen("state_changed", changed)
        try:
            await self.hass.services.async_call(
                "light",
                "turn_on",
                {"entity_id": self.light.entity_id, "brightness": brightness},
                blocking=True,
                context=context,
            )
            await self.hass.async_block_till_done()
        finally:
            unsubscribe()
        self.assertEqual(len(observed), 1)
        new = observed[0].data["new_state"]
        self.assertEqual(new.attributes["brightness"], brightness)
        self.assertEqual(new.context.user_id, context.user_id)
        return new

    def print_light_evidence(self, **observed):
        print(
            "NOTIFICATION_EVIDENCE "
            + json.dumps({"selector": self.id(), **observed}, sort_keys=True)
        )

    async def test_external_light_preserves_authenticated_actor_after_normalization(
        self,
    ):
        await self.prepare_participants()
        session_id = self.runtime.session.session_id
        before = self.light_participation_snapshot()
        self.assertIsNone(before["override"]["brightness"])

        unknown = await self.select_external_light(64, Context())
        unknown_after = self.light_participation_snapshot()
        self.assertAlmostEqual(unknown_after["override"]["brightness"], 64 * 100 / 255)
        self.assertEqual(unknown_after["targets"], before["targets"])
        self.assertIsNone(unknown.context.user_id)

        selected = await self.select_external_light(
            94, Context(user_id=self.users[1].id)
        )
        after = self.light_participation_snapshot()
        self.assertAlmostEqual(after["override"]["brightness"], 94 * 100 / 255)
        self.assertEqual(
            self.runtime.device.light_output.manual_ends_at,
            self.now
            + timedelta(
                seconds=self.runtime.configuration.parameters.seconds(
                    "manual_override_minutes"
                )
            ),
        )
        self.assertEqual(self.runtime.session.session_id, session_id)
        self.assert_both()
        self.print_light_evidence(
            input_user_id=selected.context.user_id,
            raw_brightness=selected.attributes["brightness"],
            before=unknown_after,
            after=after,
            unknown={
                "input_user_id": unknown.context.user_id,
                "before": before,
                "after": unknown_after,
            },
            same_session=self.runtime.session.session_id == session_id,
        )

    async def test_external_light_repeated_noop_and_unavailable_do_not_add_user(self):
        await self.prepare_participants()
        await self.select_external_light(94, Context())
        before = self.light_participation_snapshot()
        recipients = self.runtime.notification_recipients
        context = Context(user_id=self.users[1].id)
        guards = {}

        # An identical HA service result is a report, not a fresh light choice.
        await self.hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": self.light.entity_id, "brightness": 94},
            blocking=True,
            context=context,
        )
        await self.hass.async_block_till_done()
        self.assertEqual(self.light_participation_snapshot(), before)
        self.assertIs(self.runtime.notification_recipients, recipients)
        guards["repeated_report"] = self.light_participation_snapshot()

        # HA can report changed attributes without changing the normalized choice.
        state = self.hass.states.get(self.light.entity_id)
        self.hass.states.async_set(
            self.light.entity_id,
            state.state,
            {**state.attributes, "synthetic_report": "changed"},
            context=context,
        )
        self.assertEqual(
            self.hass.states.get(self.light.entity_id).context.user_id,
            self.users[1].id,
        )
        await self.hass.async_block_till_done()
        self.assertEqual(self.light_participation_snapshot(), before)
        self.assertIs(self.runtime.notification_recipients, recipients)
        guards["same_normalized_selection"] = self.light_participation_snapshot()

        self.hass.states.async_set(self.light.entity_id, "unavailable", context=context)
        self.assertEqual(
            self.hass.states.get(self.light.entity_id).context.user_id,
            self.users[1].id,
        )
        await self.hass.async_block_till_done()
        self.assertEqual(self.light_participation_snapshot(), before)
        self.assertIs(self.runtime.notification_recipients, recipients)
        guards["unavailable_state"] = self.light_participation_snapshot()
        self.print_light_evidence(
            input_user_id=context.user_id, before=before, unchanged_guards=guards
        )

    async def test_external_light_matching_service_echo_does_not_add_user(self):
        await self.prepare_participants()
        self.light.defer_state_writes = True
        await self.runtime.set_light_override(
            37, actor=ActorRef(self.users[0].id, "ha_context")
        )
        await self.hass.async_block_till_done()
        expected = [
            item
            for item in self.runtime.device._expected_light_changes
            if item["signature"] == ("on", 94)
        ]
        self.assertEqual(len(expected), 1)
        before = self.light_participation_snapshot()
        recipients = self.runtime.notification_recipients

        # The existing admission rule suppresses the retained command's matching
        # value inside its feedback window, including a user-context state.
        self.light.defer_state_writes = False
        selected = await self.select_external_light(
            94, Context(user_id=self.users[1].id)
        )
        after = self.light_participation_snapshot()
        self.assertNotIn(expected[0], self.runtime.device._expected_light_changes)
        self.assertEqual(after, before)
        self.assertIs(self.runtime.notification_recipients, recipients)
        self.print_light_evidence(
            input_user_id=selected.context.user_id,
            raw_brightness=selected.attributes["brightness"],
            before=before,
            after=after,
            unchanged_guards={"matching_service_echo": after},
            retained_expectation_consumed=True,
        )

    async def test_external_light_without_session_does_not_create_recipients(self):
        await self.prepare_participants(start_operation=False)
        before = self.light_participation_snapshot()
        selected = await self.select_external_light(
            94, Context(user_id=self.users[1].id)
        )
        after = self.light_participation_snapshot()
        self.assertAlmostEqual(after["override"]["brightness"], 94 * 100 / 255)
        self.assertIsNone(self.runtime.session)
        self.assertIsNone(self.runtime.notification_recipients)
        self.print_light_evidence(
            input_user_id=selected.context.user_id,
            before=before,
            after=after,
            session_active=False,
        )

    async def test_second_user_temperature_entity(self):
        await self.prepare_participants()
        registry = er.async_get(self.hass)
        entity = registry.async_get_entity_id(
            "number", "ha_sauna", f"{self.entry.entry_id}_target_temperature_c"
        )
        target = (
            self.runtime.configuration.parameters.values["target_temperature_c"] + 1
        )
        await self.hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": entity, "value": target},
            blocking=True,
            context=Context(user_id=self.users[1].id),
        )
        self.assert_both()

    async def test_second_user_program(self):
        await self.prepare_participants()
        await async_set_program(
            self.hass,
            self.entry,
            "genusszeit",
            actor=ActorRef(self.users[1].id, "ha_authenticated_request"),
        )
        self.assert_both()

    async def test_second_user_heater(self):
        await self.prepare_participants()
        await self.runtime.set_heater_override(
            False, actor=ActorRef(self.users[1].id, "ha_context")
        )
        self.assert_both()

    async def test_second_user_light(self):
        await self.prepare_participants()
        await self.runtime.set_light_override(
            37, actor=ActorRef(self.users[1].id, "ha_authenticated_request")
        )
        self.assert_both()

    async def test_duplicate_and_rejected_controls_do_not_add_second_user(self):
        await self.prepare_participants()
        actor = ActorRef(self.users[1].id, "ha_context")
        await self.runtime.set_operation(True, actor=actor)
        with self.assertRaises(ValueError):
            await self.runtime.set_light_override(101, actor=actor)
        self.assertEqual(
            self.runtime.notification_recipients.targets, frozenset((self.targets[0],))
        )
