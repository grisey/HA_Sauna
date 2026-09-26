"""Finalizing an interrupted session stays independent of Home Assistant."""

import unittest
from datetime import timedelta

from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.timeline import Kind
from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime
from test_foundation import T0, bindings, event, parameters


def at(seconds):
    return T0 + timedelta(seconds=seconds)


def gap(controller):
    return next(
        deadline
        for deadline in controller.session.deadlines
        if deadline.purpose == "session_gap"
    )


class FinishSessionTests(unittest.TestCase):
    def controller(self, *, manual=False):
        return Controller(
            Parameters(parameters().as_dict()),
            control_mode="manual" if manual else "automatic",
        )

    def paused(self, controller, session_id="s"):
        controller.set_temperature(60, T0)
        controller.set_operation(True, T0, session_id=session_id)
        controller.set_operation(False, at(1))
        return gap(controller)

    def test_finishes_now_archives_and_next_start_has_a_new_id(self):
        controller = self.controller()
        token = self.paused(controller)

        controller.finish_session_gap(token.token, at(2))

        self.assertIsNone(controller.session)
        self.assertEqual(controller.completed_sessions[-1].session_id, "s")
        self.assertEqual(controller.completed_sessions[-1].ended_at, at(2))
        self.assertEqual(controller.light_after_run.ends_at, at(2))
        controller.set_operation(True, at(3), session_id="new")
        self.assertEqual(controller.session.session_id, "new")

    def test_accounts_real_time_until_the_manual_finish(self):
        controller = self.controller()
        controller.set_temperature(60, T0)
        controller.set_operation(True, T0, session_id="s")
        controller.report_heating(True, T0)
        controller.set_operation(False, at(1))
        token = gap(controller)

        controller.finish_session_gap(token.token, at(10))

        completed = controller.completed_sessions[-1]
        self.assertEqual(completed.ended_at, at(10))
        self.assertEqual(completed.heating.accounted_at, at(10))
        self.assertGreaterEqual(completed.heating.elapsed_seconds, 10)
        self.assertEqual(completed.energy.accounted_at, at(10))

    def test_due_gap_uses_the_regular_completion_once(self):
        for manual, offset in ((False, 0), (False, 1), (True, 0), (True, 1)):
            with self.subTest(manual=manual, offset=offset):
                controller = self.controller(manual=manual)
                token = self.paused(controller)
                due_at = token.due_at

                controller.finish_session_gap(token.token, due_at + timedelta(seconds=offset))

                self.assertIsNone(controller.session)
                self.assertEqual(len(controller.completed_sessions), 1)
                self.assertEqual(controller.completed_sessions[-1].ended_at, due_at)
                self.assertEqual(controller.light_after_run.ends_at, due_at)

    def test_confirmed_and_unconfirmed_gangs_are_not_counted_twice(self):
        controller = self.controller()
        controller.set_temperature(60, T0)
        controller.set_operation(True, T0, session_id="s")
        controller.process(event("close", Kind.DOOR_CLOSE, 1))
        controller.process(event("person", Kind.PERSON_STRONG, 2))
        controller.process(event("infusion", Kind.INFUSION, 3))
        controller.set_operation(False, at(4))
        token = gap(controller)

        controller.finish_session_gap(token.token, at(5))

        self.assertEqual(controller.completed_sessions[-1].timeline.gang_count, 1)
        with self.assertRaises(ValueError):
            controller.finish_session_gap(token.token, at(6))

        unconfirmed = self.controller()
        unconfirmed.set_temperature(60, T0)
        unconfirmed.set_operation(True, T0, session_id="unconfirmed")
        unconfirmed.process(event("close", Kind.DOOR_CLOSE, 1, "unconfirmed"))
        unconfirmed.process(event("person", Kind.PERSON_STRONG, 2, "unconfirmed"))
        unconfirmed.set_operation(False, at(3))
        unconfirmed.finish_session_gap(gap(unconfirmed).token, at(4))
        self.assertEqual(unconfirmed.completed_sessions[-1].timeline.gang_count, 0)

    def test_old_token_cannot_finish_a_resumed_or_reinterrupted_session(self):
        controller = self.controller()
        old = self.paused(controller)
        controller.set_operation(True, at(2))
        with self.assertRaises(ValueError):
            controller.finish_session_gap(old.token, at(3))
        controller.set_operation(False, at(4))
        current = gap(controller)
        with self.assertRaises(ValueError):
            controller.finish_session_gap(old.token, at(5))
        controller.finish_session_gap(current.token, at(5))
        self.assertIsNone(controller.session)

    def test_manual_and_protected_pauses_finish_without_starting_heat(self):
        controller = self.controller(manual=True)
        controller.protection.add("confirmed_controller_failure")
        token = self.paused(controller)

        controller.finish_session_gap(token.token, at(2))

        self.assertIn("confirmed_controller_failure", controller.protection)
        self.assertFalse(controller.last_decision.heat)
        self.assertIsNone(controller.session)
        self.assertEqual(controller.light_after_run.ends_at, at(2))


class RuntimeFinishSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_cycles_after_a_valid_finish(self):
        now = T0
        runtime = SaunaRuntime(
            Configuration(bindings(), parameters()), clock=lambda: now
        )
        runtime.controller.set_temperature(60, now)
        runtime.controller.set_operation(True, now, session_id="s")
        runtime.controller.set_operation(False, now + timedelta(seconds=1))
        token = gap(runtime.controller).token
        cycles = 0

        async def cycle(*, sample=False):
            nonlocal cycles
            cycles += 1

        runtime._cycle = cycle
        now = now + timedelta(seconds=2)
        await runtime.finish_session_gap(token)

        self.assertEqual(cycles, 1)
        self.assertIsNone(runtime.session)
