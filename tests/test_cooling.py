"""Zusammenhängende Regelketten; Zeit und reale Rückmeldung separat steuerbar."""
from datetime import timedelta
import unittest

from custom_components.ha_sauna.archive import plain
from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.defaults import instance_default
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.timeline import Event, Kind
from test_foundation import T0, event, parameters


def at(seconds):
    return T0 + timedelta(seconds=seconds)


def controller(
    *, presence_source=instance_default("presence_source"), presence_entity=None, **overrides,
):
    # The target is an explicit selected program input; timing policy is unchanged.
    values = {**parameters().as_dict(), "target_temperature_c": 80, **overrides}
    result = Controller(
        Parameters(values), presence_source=presence_source, presence_entity=presence_entity,
    )
    result.set_temperature(70, T0)
    result.set_operation(True, T0, session_id="s")
    return result


class CoolingTests(unittest.TestCase):
    def cooling_after_confirmed_heat(self, *, off_feedback=True):
        c = controller()
        c.report_contactor(True, at(0))
        c.report_heating(True, at(0))
        self.start(c)
        c.process(event("open", Kind.DOOR_OPEN, 70))
        c.process(event("vent", Kind.VENTILATION, 71))
        if off_feedback:
            c.report_contactor(False, at(71))
            c.report_heating(False, at(71))
        return c

    def start(self, c):
        c.process(event("entry-open", Kind.DOOR_OPEN, 0))
        c.process(event("close", Kind.DOOR_CLOSE, 1))
        c.process(event("person", Kind.PERSON_STRONG, 2))
        c.process(event("infusion", Kind.INFUSION, 3))

    def finish(self, c):
        c.process(event("open", Kind.DOOR_OPEN, 70))
        c.process(event("vent", Kind.VENTILATION, 71))
        c.report_contactor(False, at(71))
        c.report_heating(False, at(71))

    def test_provisional_retraction_has_no_after_run_or_count(self):
        c = controller()
        c.process(event("entry-open", Kind.DOOR_OPEN, 0))
        c.process(event("close", Kind.DOOR_CLOSE, 1))
        # Satisfy door heat before isolating the provisional gang's retraction.
        c.report_heating(True, at(1))
        self.assertFalse(c.regulation_inputs.temporary_door_heat)
        c.process(event("person", Kind.PERSON_STRONG, 2))
        c.set_temperature(90, at(3))
        self.assertTrue(c.last_decision.heat)
        c.advance(at(1 + c.parameters.seconds("confirmation_minutes")))
        self.assertIsNone(c.session.after_run)
        self.assertEqual(c.session.timeline.completed, ())
        self.assertEqual(c.session.timeline.gang_count, 0)
        self.assertEqual(c.session.ready_at, at(3))
        self.assertEqual(c.phase, "bereit")
        self.assertFalse(c.last_decision.heat)

    def test_readiness_comparison_includes_the_configured_target_boundary(self):
        c = controller()
        target = c.target_temperature
        c.set_temperature(target - 1, at(1))
        self.assertIsNone(c.session.ready_at)
        c.set_temperature(target, at(2))
        self.assertEqual(c.session.ready_at, at(2))

    def test_confirmed_gang_clears_readiness(self):
        c = controller()
        c.set_temperature(80, at(1))
        self.assertIsNotNone(c.session.ready_at)
        c.process(event("entry-open", Kind.DOOR_OPEN, 1))
        c.process(event("close", Kind.DOOR_CLOSE, 2))
        c.process(event("person", Kind.PERSON_STRONG, 3))
        self.assertIsNotNone(c.session.ready_at)  # Vorläufige Erkennung rollt zurück.
        c.process(event("infusion", Kind.INFUSION, 4))
        self.assertIsNone(c.session.ready_at)
        c.set_temperature(90, at(5))
        self.assertIsNone(c.session.ready_at)

    def test_actual_feedback_separate_from_demand_and_local_reset(self):
        c = controller()
        self.assertTrue(c.last_decision.heat)
        c.advance(at(100))
        self.assertEqual(c.session.heating.elapsed_seconds, 0)
        c.report_heating(True, at(100))
        c.report_heating(False, at(120))
        deadline = 120 + c.parameters.seconds("heat_reset_minutes")
        c.advance(at(deadline - 1))
        self.assertEqual(c.session.heating.elapsed_seconds, 20)
        c.advance(at(deadline))
        self.assertEqual(c.session.heating.elapsed_seconds, 0)
        self.assertEqual(c.session.session_id, "s")
        self.assertEqual(c.session.cooling_history, ())

    def test_proxy_minimum_keeps_frozen_calculation_and_off_time_on_repeat(self):
        c = self.cooling_after_confirmed_heat()
        original = c.session.after_run
        minimum = original.cooling_calculation.base_minutes * 60
        self.assertGreater(original.duration_seconds, minimum)
        c.process(event("return-close", Kind.DOOR_CLOSE, 71 + minimum / 4))
        c.process(event("return-person", Kind.PERSON_STRONG, 71 + minimum / 3))
        first = c.session.after_run
        self.assertTrue(first.minimum_duration_requested)
        self.assertEqual(first.cooling_calculation, original.cooling_calculation)
        self.assertEqual(first.duration_seconds, original.duration_seconds)
        self.assertEqual((first.phase_id, first.started_at),
                         (original.phase_id, original.started_at))
        self.assertEqual(first.elapsed_seconds, minimum / 3)
        self.assertEqual(first.required_seconds, minimum)
        self.assertEqual(first.ends_at, original.started_at + timedelta(seconds=minimum))
        c.process(event("return-infusion", Kind.INFUSION, 71 + minimum / 2))
        repeated = c.session.after_run
        self.assertEqual(
            (repeated.phase_id, repeated.started_at, repeated.ends_at,
             repeated.duration_seconds, repeated.cooling_calculation),
            (first.phase_id, first.started_at, first.ends_at,
             first.duration_seconds, first.cooling_calculation),
        )
        self.assertEqual(repeated.elapsed_seconds, minimum / 2)
        self.assertEqual(repeated.remaining_seconds, minimum / 2)

    def test_completed_minimum_keeps_actual_elapsed_and_original_duration_in_archive(self):
        c = self.cooling_after_confirmed_heat()
        original = c.session.after_run
        minimum = original.cooling_calculation.base_minutes * 60
        elapsed = minimum + 1
        self.assertGreater(original.duration_seconds, elapsed)
        c.process(event("return-close", Kind.DOOR_CLOSE, 71 + minimum))
        c.process(event("return-person", Kind.PERSON_STRONG, 71 + elapsed))
        self.assertIsNone(c.session.after_run)
        completed = c.session.after_run_history[-1]
        self.assertEqual(completed.phase_id, original.phase_id)
        self.assertEqual(completed.started_at, original.started_at)
        self.assertEqual(completed.ends_at, at(71 + elapsed))
        self.assertEqual(completed.elapsed_seconds, elapsed)
        exported = plain(completed)
        self.assertEqual(exported["duration_seconds"], original.duration_seconds)
        self.assertEqual(exported["required_seconds"], minimum)
        self.assertEqual(exported["elapsed_seconds"], elapsed)
        self.assertEqual(exported["remaining_seconds"], 0)

    def test_proxy_minimum_does_not_credit_unknown_contactor_time(self):
        c = self.cooling_after_confirmed_heat()
        original = c.session.after_run
        minimum = original.cooling_calculation.base_minutes * 60
        known_off = minimum / 4
        c.process(event("return-close", Kind.DOOR_CLOSE, 72))
        c.report_contactor(None, at(71 + known_off))
        c.process(event("return-person", Kind.PERSON_STRONG, 71 + minimum / 2))
        c.advance(at(71 + minimum * 2))
        unknown = c.session.after_run
        self.assertEqual(unknown.phase_id, original.phase_id)
        self.assertTrue(unknown.minimum_duration_requested)
        self.assertIsNone(unknown.ends_at)
        self.assertEqual(unknown.elapsed_seconds, known_off)
        self.assertEqual(unknown.remaining_seconds, minimum - known_off)
        resumed_at = at(72 + minimum * 2)
        c.report_contactor(False, resumed_at)
        resumed = c.session.after_run
        self.assertEqual(resumed.ends_at,
                         resumed_at + timedelta(seconds=minimum - known_off))
        c.advance(resumed.ends_at)
        completed = c.session.after_run_history[-1]
        self.assertEqual(completed.elapsed_seconds, minimum)
        self.assertEqual(completed.active_intervals, (
            (original.started_at, at(71 + known_off)), (resumed_at, resumed.ends_at),
        ))

    def test_proxy_minimum_request_waits_for_actual_off_before_starting_its_clock(self):
        c = self.cooling_after_confirmed_heat(off_feedback=False)
        pending = c.session.after_run
        self.assertTrue(pending.pending_start)
        c.process(event("return-close", Kind.DOOR_CLOSE, 72))
        c.process(event("return-person", Kind.PERSON_STRONG, 73))
        requested = c.session.after_run
        self.assertTrue(requested.pending_start)
        self.assertTrue(requested.minimum_duration_requested)
        self.assertIsNone(requested.ends_at)
        self.assertEqual(requested.elapsed_seconds, 0)
        c.report_contactor(False, at(80))
        started = c.session.after_run
        minimum = started.cooling_calculation.base_minutes * 60
        self.assertEqual(started.phase_id, pending.phase_id)
        self.assertEqual(started.requested_at, pending.requested_at)
        self.assertEqual(started.started_at, at(80))
        self.assertFalse(started.pending_start)
        self.assertEqual(started.elapsed_seconds, 0)
        self.assertEqual(started.required_seconds, minimum)
        self.assertEqual(started.ends_at, at(80 + minimum))

    def test_cooling_signal_with_expired_entry_changes_only_its_clock(self):
        c = self.cooling_after_confirmed_heat()
        original = c.session.after_run
        original_gangs = c.session.timeline.completed
        c.process(event("return-close", Kind.DOOR_CLOSE, 72))
        c.report_contactor(None, at(73))
        c.process(Event("old-person", "s", Kind.PERSON_STRONG, at(69), at(74)),
                  recognition_at=at(69))
        self.assertFalse(c.session.after_run.minimum_duration_requested)
        received = 72 + c.parameters.seconds("confirmation_minutes") + 1
        c.advance(at(received))
        self.assertTrue(c.recognition_allowed(Kind.PERSON_WEAK))
        c.process(event("cooling-person", Kind.PERSON_WEAK, received))
        current = c.session.after_run
        self.assertTrue(current.minimum_duration_requested)
        self.assertEqual(current.phase_id, original.phase_id)
        self.assertEqual(current.started_at, original.started_at)
        self.assertEqual(current.elapsed_seconds, 2)
        self.assertIsNone(current.ends_at)
        self.assertIsNone(c.session.timeline.active)
        self.assertEqual(c.session.timeline.completed, original_gangs)

    def test_safety_survives_new_session_and_cannot_be_overridden_by_gang(self):
        c = controller()
        self.start(c)
        c.protection.add("confirmed_controller_failure")
        c.advance(at(4))
        self.assertFalse(c.last_decision.heat)
        c.set_operation(False, at(5))
        c.set_temperature(70, at(6))
        c.set_operation(True, at(5 + c.parameters.seconds("session_gap_minutes")), session_id="new")
        self.assertIn("confirmed_controller_failure", c.protection)
        self.assertFalse(c.last_decision.heat)

    def test_completed_sessions_allow_restart_with_or_without_counted_rounds(self):
        c = controller()
        c.report_contactor(True, at(0))
        c.set_operation(False, at(20))
        gap = c.parameters.seconds("session_gap_minutes")
        c.advance(at(20 + gap))
        self.assertIsNone(c.session)
        self.assertEqual(c.completed_sessions[-1].timeline.gang_count, 0)
        c.set_operation(True, at(40 + gap), session_id="s2")
        self.assertEqual(c.session.session_id, "s2")
        c.process(event("entry-open", Kind.DOOR_OPEN, 40 + gap, session="s2"))
        c.process(event("close", Kind.DOOR_CLOSE, 41 + gap, session="s2"))
        c.process(event("infusion", Kind.INFUSION, 42 + gap, session="s2"))
        c.set_operation(False, at(60 + gap))
        self.assertEqual(c.session.timeline.gang_count, 1)
        c.advance(at(60 + 2 * gap))
        self.assertIsNone(c.session)
        self.assertEqual(c.completed_sessions[-1].timeline.gang_count, 1)
        c.set_operation(True, at(80 + 2 * gap), session_id="s3")
        self.assertEqual(c.session.session_id, "s3")
        self.assertEqual(c.session.timeline.gang_count, 0)

    def test_program_cursor_follows_recording_and_exit_event_identity(self):
        c = controller(final_temperature_c=95)
        c.program_mode = "progressive"
        self.start(c)
        self.finish(c)
        before = (c.target_temperature, c.session.timeline.completed, c.session.after_run)
        self.assertFalse(c.process(event("vent", Kind.VENTILATION, 71)).changed)
        self.assertEqual(
            (c.target_temperature, c.session.timeline.completed, c.session.after_run), before,
        )
        identity = c.session.session_id
        target = c.target_temperature
        c.set_operation(False, at(72))
        c.set_operation(True, at(73), session_id="replacement")
        self.assertEqual((c.session.session_id, c.target_temperature), (identity, target))
        c.set_operation(False, at(74))
        ended_at = at(74) + timedelta(seconds=c.parameters.seconds("session_gap_minutes"))
        c.advance(ended_at)
        self.assertIsNone(c.session)
        c.set_operation(True, ended_at + timedelta(seconds=1), session_id="new")
        self.assertEqual(c.session.session_id, "new")
        self.assertEqual(c.session.timeline.gang_count, 0)
        self.assertEqual(c.target_temperature, c.parameters.values["target_temperature_c"])

    def test_retracted_gang_does_not_increase_target(self):
        c = controller(final_temperature_c=95)
        c.process(event("entry-open", Kind.DOOR_OPEN, 0))
        c.process(event("close", Kind.DOOR_CLOSE, 1))
        c.process(event("person", Kind.PERSON_STRONG, 2))
        c.process(event("open", Kind.DOOR_OPEN, 3))
        c.process(event("vent", Kind.VENTILATION, 4))
        self.assertEqual(c.session.timeline.gang_count, 0)
        self.assertEqual(c.target_temperature, 80)
