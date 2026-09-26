from datetime import timedelta
import unittest

from custom_components.ha_sauna.core.contracts import ReadinessPause
from custom_components.ha_sauna.core.heater_overrides import (
    DoorRequestState, cooling_duration, update_door_request,
)
from custom_components.ha_sauna.core.parameters import Parameters
from test_foundation import T0, parameters


class DoorRequestTests(unittest.TestCase):
    def update(self, state=None, seconds=0, **kwargs):
        args = dict(now=T0 + timedelta(seconds=seconds), session_id="s1",
                    enabled=True, heating=False, cooling=False)
        args.update(kwargs)
        return update_door_request(state or DoorRequestState(), **args)

    def opened(self, **kwargs):
        return self.update(door_open=True, event_id="open-1", **kwargs)[0]

    def test_unset_delay_keeps_close_trigger_and_has_no_open_timeout(self):
        state = self.opened()
        self.assertIsNone(state.deadline)
        state, pulse = self.update(state, seconds=1000)
        self.assertFalse(pulse)
        state, pulse = self.update(state, seconds=1001, door_open=False,
                                   event_id="close-1")
        self.assertTrue(pulse)
        _, pulse = self.update(state, seconds=1002, door_open=False,
                               event_id="close-1")
        self.assertFalse(pulse)

    def test_early_close_consumes_timer_and_duplicate_open_does_not_extend(self):
        state = self.opened(open_delay_seconds=60)
        state, pulse = self.update(state, seconds=20, door_open=True,
                                   event_id="open-2", open_delay_seconds=60)
        self.assertEqual(state.deadline, T0 + timedelta(seconds=60))
        state, pulse = self.update(state, seconds=30, door_open=False,
                                   event_id="close-1")
        self.assertTrue(pulse)
        _, pulse = self.update(state, seconds=60, timer_token="open-1")
        self.assertFalse(pulse)

    def test_open_timeout_consumes_later_close(self):
        state = self.opened(open_delay_seconds=60)
        state, pulse = self.update(state, seconds=59)
        self.assertFalse(pulse)
        state, pulse = self.update(state, seconds=60, timer_token="open-1")
        self.assertTrue(pulse)
        _, pulse = self.update(state, seconds=61, door_open=False,
                               event_id="close-1")
        self.assertFalse(pulse)

    def test_stale_timer_cannot_trigger_new_cycle(self):
        state = self.opened(open_delay_seconds=60)
        state, _ = self.update(state, seconds=20, door_open=False, event_id="close-1")
        state, _ = self.update(state, seconds=30, door_open=True,
                               event_id="open-2", open_delay_seconds=5)
        state, pulse = self.update(state, seconds=60, timer_token="open-1")
        self.assertFalse(pulse)
        _, pulse = self.update(state, seconds=60, timer_token="open-2")
        self.assertTrue(pulse)

    def test_cooling_off_and_session_switch_consume_without_replay(self):
        for block in ({"cooling": True}, {"enabled": False},
                      {"session_id": "s2"}):
            with self.subTest(block=block):
                state = self.opened(open_delay_seconds=60)
                state, pulse = self.update(state, seconds=10, **block)
                self.assertFalse(pulse)
                session = block.get("session_id", "s1")
                state, pulse = self.update(state, seconds=60, session_id=session)
                self.assertFalse(pulse)
                _, pulse = self.update(state, seconds=61, session_id=session,
                                       door_open=False, event_id="close-1")
                self.assertFalse(pulse)

    def test_opening_during_cooling_or_off_is_not_queued(self):
        for block in ({"cooling": True}, {"enabled": False}):
            state = self.opened(open_delay_seconds=60, **block)
            _, pulse = self.update(state, seconds=20, door_open=False, event_id="close-1")
            self.assertFalse(pulse)

    def test_replayed_open_event_cannot_arm_another_cycle(self):
        state = self.opened()
        state, _ = self.update(state, door_open=False, event_id="close-1")
        state, _ = self.update(state, door_open=True, event_id="open-1")
        _, pulse = self.update(state, door_open=False, event_id="close-2")
        self.assertFalse(pulse)

    def test_duplicate_open_during_early_heat_preserves_later_close(self):
        state = self.opened(open_delay_seconds=60)
        state, pulse = self.update(state, seconds=10, heating=True,
                                   door_open=True, event_id="open-1")
        self.assertFalse(pulse)
        state, pulse = self.update(state, seconds=20, door_open=False, event_id="close-1")
        self.assertTrue(pulse)
        _, pulse = self.update(state, seconds=21, door_open=False, event_id="close-1")
        self.assertFalse(pulse)

    def test_heating_before_deadline_does_not_consume_a_future_request(self):
        state = self.opened(open_delay_seconds=60, heating=True)
        state, pulse = self.update(state, seconds=10, heating=True)
        self.assertFalse(pulse)
        state, pulse = self.update(state, seconds=60)
        self.assertTrue(pulse)
        _, pulse = self.update(state, seconds=61, door_open=False, event_id="close-1")
        self.assertFalse(pulse)

    def test_duplicate_open_at_deadline_consumes_request_satisfied_by_heating(self):
        state = self.opened(open_delay_seconds=60)
        state, pulse = self.update(state, seconds=60, heating=True,
                                   door_open=True, event_id="open-1")
        self.assertFalse(pulse)
        _, pulse = self.update(state, seconds=61, door_open=False, event_id="close-1")
        self.assertFalse(pulse)

    def test_cooling_duration_retains_saved_value_regardless_of_pause_history(self):
        configured = Parameters({**parameters().as_dict(), "after_run_minutes": 7})
        pauses = (ReadinessPause(T0, T0 + timedelta(minutes=30)),)
        self.assertEqual(cooling_duration(configured, pauses), 420)
