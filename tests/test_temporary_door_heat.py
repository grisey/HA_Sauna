import unittest

from custom_components.ha_sauna.core.temporary_door_heat import (
    TemporaryDoorHeatState,
    advance,
)


class TemporaryDoorHeatTests(unittest.TestCase):
    def transition(self, state, event_id, door_open, **kwargs):
        return advance(
            state,
            event_id=event_id,
            door_open=door_open,
            enabled=True,
            **kwargs,
        )

    def test_eligible_open_then_close_emits_exactly_one_request(self):
        opened = self.transition(TemporaryDoorHeatState(), "open-1", True)
        self.assertFalse(opened.request)
        closed = self.transition(opened.state, "close-1", False)
        self.assertTrue(closed.request)
        self.assertFalse(closed.state.eligible_open)
        self.assertFalse(self.transition(closed.state, "close-2", False).request)

    def test_duplicate_events_cannot_rearm_or_repeat_request(self):
        opened = self.transition(TemporaryDoorHeatState(), "open-1", True)
        duplicate_open = self.transition(opened.state, "open-1", True)
        self.assertEqual(duplicate_open.state, opened.state)
        closed = self.transition(duplicate_open.state, "close-1", False)
        self.assertTrue(closed.request)
        duplicate_close = self.transition(closed.state, "close-1", False)
        self.assertFalse(duplicate_close.request)
        self.assertEqual(duplicate_close.state, closed.state)

    def test_close_without_an_eligible_open_is_inert(self):
        result = self.transition(TemporaryDoorHeatState(), "close-1", False)
        self.assertFalse(result.request)
        self.assertFalse(result.state.door_open)

    def test_blocked_open_invalidates_the_cycle_until_its_close(self):
        opened = self.transition(TemporaryDoorHeatState(), "open-1", True, gang_active=True)
        self.assertTrue(opened.state.invalidated)
        closed = self.transition(opened.state, "close-1", False)
        self.assertFalse(closed.request)
        next_open = self.transition(closed.state, "open-2", True)
        self.assertTrue(self.transition(next_open.state, "close-2", False).request)

    def test_blocked_close_invalidates_an_previously_eligible_cycle(self):
        opened = self.transition(TemporaryDoorHeatState(), "open-1", True)
        closed = self.transition(opened.state, "close-1", False, cooling=True)
        self.assertFalse(closed.request)

    def test_disabled_open_cannot_request_and_anonymous_events_are_inert(self):
        disabled = advance(
            TemporaryDoorHeatState(),
            event_id="open-1",
            door_open=True,
            enabled=False,
        )
        self.assertFalse(advance(
            disabled.state,
            event_id="close-1",
            door_open=False,
            enabled=True,
        ).request)
        self.assertFalse(self.transition(TemporaryDoorHeatState(), None, True).request)
