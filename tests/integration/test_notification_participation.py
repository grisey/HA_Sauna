"""Successful session controls add their observed user; no-op/rejection does not."""

import unittest
from dataclasses import replace

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

    async def prepare_participants(self):
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
