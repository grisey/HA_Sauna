"""Light completion cannot change the recording deadline or its identity."""

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
    def controller(self):
        return Controller(Parameters(parameters().as_dict()))

    def paused(self, controller, session_id="s"):
        controller.set_temperature(60, T0)
        controller.set_operation(True, T0, session_id=session_id)
        controller.set_operation(False, at(1))
        return gap(controller)

    def test_light_finishes_now_and_resume_keeps_recording_identity(self):
        controller = self.controller()
        token = self.paused(controller)

        controller.finish_session_gap(token.token, at(2))

        self.assertEqual(controller.session.session_id, "s")
        self.assertIn(token, controller.session.deadlines)
        self.assertEqual(controller.completed_sessions, ())
        self.assertEqual(controller.light_after_run.ends_at, at(2))
        controller.set_operation(True, at(3), session_id="new")
        self.assertEqual(controller.session.session_id, "s")
        self.assertIsNone(controller.light_after_run)

    def test_off_decision_keeps_origin_across_immediate_new_session(self):
        controller = self.controller()
        controller.set_temperature(60, T0)
        controller.set_operation(True, T0, session_id="old")
        controller.finish_session(at(1))
        closing = controller.last_decision
        self.assertFalse(closing.heat)
        self.assertEqual(closing.session_id, "old")
        controller.set_operation(True, at(2), session_id="new")
        self.assertEqual(controller.session.session_id, "new")
        self.assertIn(closing, controller.decisions)
        self.assertEqual(closing.session_id, "old")

    def test_light_completion_accounts_time_without_finalizing_the_recording(self):
        controller = self.controller()
        controller.set_temperature(60, T0)
        controller.set_operation(True, T0, session_id="s")
        controller.report_heating(True, T0)
        controller.set_operation(False, at(1))
        token = gap(controller)

        controller.finish_session_gap(token.token, at(10))

        self.assertEqual(controller.completed_sessions, ())
        self.assertIsNone(controller.session.ended_at)
        self.assertEqual(controller.session.heating.accounted_at, at(10))
        self.assertGreaterEqual(controller.session.heating.elapsed_seconds, 10)
        self.assertEqual(controller.session.energy.accounted_at, at(10))
        self.assertIn(token, controller.session.deadlines)

    def test_due_gap_uses_the_regular_completion_once(self):
        for offset in (0, 1):
            with self.subTest(offset=offset):
                controller = self.controller()
                token = self.paused(controller)
                due_at = token.due_at

                controller.finish_session_gap(token.token, due_at + timedelta(seconds=offset))

                self.assertIsNone(controller.session)
                self.assertEqual(len(controller.completed_sessions), 1)
                self.assertEqual(controller.completed_sessions[-1].ended_at, due_at)
                self.assertEqual(controller.light_after_run.ends_at, due_at)

    def test_resume_boundary_is_identical_across_control_modes(self):
        for first_mode in ("manual", "automatic"):
            for resumed_mode in ("manual", "automatic"):
                for microseconds in (-1, 0, 1):
                    with self.subTest(first=first_mode, resumed=resumed_mode,
                                      offset_us=microseconds):
                        controller = Controller(parameters(), control_mode=first_mode)
                        controller.set_temperature(60, T0)
                        if first_mode == "manual":
                            controller.set_heater_override(True, T0)
                            identity = controller.session.session_id
                            controller.set_heater_override(False, at(1))
                        else:
                            controller.set_operation(True, T0, session_id="original")
                            identity = controller.session.session_id
                            controller.set_operation(False, at(1))
                        deadline = gap(controller)
                        resume_at = deadline.due_at + timedelta(microseconds=microseconds)
                        controller.advance(resume_at, evaluate=False)
                        controller.set_control_mode(resumed_mode, resume_at)
                        if resumed_mode == "manual":
                            controller.set_heater_override(True, resume_at)
                        else:
                            controller.set_operation(True, resume_at, session_id="new")
                        if microseconds < 0:
                            self.assertEqual(controller.session.session_id, identity)
                            self.assertEqual(controller.session.timeline.session_started_at, T0)
                            self.assertEqual(controller.completed_sessions, ())
                        else:
                            self.assertNotEqual(controller.session.session_id, identity)
                            self.assertEqual(controller.session.timeline.session_started_at, resume_at)
                            self.assertEqual(len(controller.completed_sessions), 1)
                            self.assertEqual(controller.completed_sessions[0].ended_at,
                                             deadline.due_at)
                        self.assertTrue(controller.session.operation_enabled)
                        self.assertFalse(any(d.purpose == "session_gap"
                                             for d in controller.session.deadlines))

    def test_confirmed_and_unconfirmed_gangs_are_not_counted_twice(self):
        controller = self.controller()
        controller.set_temperature(60, T0)
        controller.set_operation(True, T0, session_id="s")
        controller.process(event("entry-open", Kind.DOOR_OPEN, 0))
        controller.process(event("close", Kind.DOOR_CLOSE, 1))
        controller.process(event("person", Kind.PERSON_STRONG, 2))
        controller.process(event("infusion", Kind.INFUSION, 3))
        controller.set_operation(False, at(4))
        token = gap(controller)

        controller.finish_session_gap(token.token, at(5))

        self.assertEqual(controller.session.timeline.gang_count, 1)
        controller.finish_session_gap(token.token, at(6))
        controller.advance(token.due_at)
        self.assertEqual(controller.completed_sessions[-1].timeline.gang_count, 1)
        self.assertEqual(controller.completed_sessions[-1].ended_at, token.due_at)

        unconfirmed = self.controller()
        unconfirmed.set_temperature(60, T0)
        unconfirmed.set_operation(True, T0, session_id="unconfirmed")
        unconfirmed.process(event("entry-open", Kind.DOOR_OPEN, 0, "unconfirmed"))
        unconfirmed.process(event("close", Kind.DOOR_CLOSE, 1, "unconfirmed"))
        unconfirmed.process(event("person", Kind.PERSON_STRONG, 2, "unconfirmed"))
        unconfirmed.set_operation(False, at(3))
        deadline = gap(unconfirmed)
        unconfirmed.finish_session_gap(deadline.token, at(4))
        unconfirmed.advance(deadline.due_at)
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
        self.assertIn(current, controller.session.deadlines)
        self.assertFalse(controller.session.operation_enabled)

    def test_protected_pauses_finish_without_starting_heat(self):
        controller = self.controller()
        controller.protection.add("confirmed_controller_failure")
        token = self.paused(controller)

        controller.finish_session_gap(token.token, at(2))

        self.assertIn("confirmed_controller_failure", controller.protection)
        self.assertFalse(controller.last_decision.heat)
        self.assertFalse(controller.session.operation_enabled)
        self.assertIn(token, controller.session.deadlines)
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
        self.assertEqual(runtime.session.session_id, "s")
        self.assertFalse(runtime.session.operation_enabled)
        self.assertEqual(gap(runtime.controller).token, token)
