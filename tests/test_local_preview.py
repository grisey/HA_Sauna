"""The local review transport must preserve actual control/role contracts."""
import unittest
from datetime import timedelta

from tools.local_preview import Preview


class LocalPreviewTests(unittest.TestCase):
    def setUp(self):
        self.preview = Preview()

    def test_user_state_removes_diagnostics_and_retains_control_permissions(self):
        self.preview.action("/simulate", {"action":"role", "value":"user"})
        state = self.preview.state()
        self.assertFalse(state["permissions"]["admin"])
        self.assertTrue(state["permissions"]["heater"])
        self.assertNotIn("bindings", state["configuration"])
        self.assertNotIn("presence", state)
        self.assertNotIn("decision", state)
        self.assertNotIn("sensor_timeout_seconds", state["configuration"]["parameters"])
        value = not self.preview.c.contactor
        self.preview.action("/preview/heater", {"value":value})
        self.assertIs(self.preview.c.heater_override, value)
        self.preview.action("/simulate", {"action":"role", "value":"admin"})
        self.assertIn("presence", self.preview.state())

    def test_user_light_presets_and_percentages_keep_their_meaning(self):
        self.preview.admin = False
        for value, expected in ((False, 0), (True, self.preview.c.parameters.values["session_light_brightness_percent"]), ("normal", 35), (None, 35), (55, 55)):
            self.preview.action("/preview/light", {"value":value})
            self.assertEqual(self.preview.light, expected)
        with self.assertRaises(ValueError):
            self.preview.action("/preview/light", {"value":101})

    def test_matching_light_observation_does_not_create_or_change_override(self):
        self.assertEqual(self.preview.light, 35)
        self.assertIsNone(self.preview.light_manual)
        for value in (True, 35):
            self.preview.action("/preview/light", {"value":value})
            self.assertIsNone(self.preview.light_manual)
        self.preview.action("/preview/light", {"value":55})
        for value in (True, 55):
            self.preview.action("/preview/light", {"value":value})
            self.assertEqual(self.preview.light_manual, 55)
            self.assertEqual(self.preview.light, 55)
        self.preview.action("/preview/light", {"value":None})
        self.assertIsNone(self.preview.light_manual)
        self.assertEqual(self.preview.light, 35)

    def test_user_can_control_manual_heater_through_real_controller(self):
        self.preview.admin = False
        self.preview.reset("manuell")
        self.assertTrue(self.preview.state()["permissions"]["heater"])
        self.preview.action("/preview/heater", {"value":True})
        self.assertTrue(self.preview.c.last_decision.heat)
        self.preview.action("/preview/heater", {"value":False})
        self.assertFalse(self.preview.c.last_decision.heat)

    def test_manual_mode_enters_with_both_selections_off_and_does_not_restore_them(self):
        self.preview.action("/preview/control", {"enabled":False})
        token = next(d.token for d in self.preview.c.session.deadlines
                     if d.purpose == "session_gap")
        self.preview.action("/preview/finish-session", {"token":token})
        self.preview.action("/preview/control-mode", {"mode":"manual"})
        controls = self.preview.state()["manual_controls"]
        self.assertIs(controls["heater"]["manual"], False)
        self.assertEqual(controls["light"]["manual"], 0)
        self.assertEqual(self.preview.light, 0)
        self.preview.action("/preview/control-mode", {"mode":"automatic"})
        self.assertIsNone(self.preview.state()["manual_controls"]["heater"]["manual"])
        self.assertIsNone(self.preview.state()["manual_controls"]["light"]["manual"])
        self.preview.action("/preview/control-mode", {"mode":"manual"})
        self.preview.action("/preview/control", {"enabled":True})
        self.preview.action("/preview/heater", {"value":True})
        self.preview.action("/preview/light", {"value":True})
        with self.assertRaisesRegex(ValueError, "laufende Session"):
            self.preview.action("/preview/control-mode", {"mode":"automatic"})
        self.assertIs(self.preview.state()["manual_controls"]["heater"]["manual"], True)
        self.assertGreater(self.preview.state()["manual_controls"]["light"]["manual"], 0)

    def test_explicit_manual_entry_for_both_roles_without_extra_sessions(self):
        for admin in (False, True):
            with self.subTest(admin=admin):
                self.preview.reset("archiv")
                self.preview.admin = admin
                self.assertIsNone(self.preview.c.session)
                self.preview.action("/preview/light", {"value":60})
                self.assertEqual(self.preview.c.control_mode, "automatic")
                self.assertIsNone(self.preview.c.session)
                self.preview.action("/preview/control-mode", {"mode":"manual"})
                self.preview.action("/preview/light", {"value":60})
                self.assertEqual(self.preview.c.control_mode, "manual")
                self.assertIsNone(self.preview.c.session)
                self.assertEqual(self.preview.light, 60)
                self.preview.action("/preview/heater", {"value":True})
                self.assertIsNone(self.preview.c.session)
                self.assertFalse(self.preview.state()["operation_enabled"])
                self.assertTrue(self.preview.c.last_decision.heat)
                self.preview.action("/preview/heater", {"value":False})
                self.assertIsNone(self.preview.c.session)
                self.assertFalse(self.preview.c.last_decision.heat)
                self.preview.action("/preview/control", {"enabled":True})
                session_id = self.preview.c.session.session_id
                self.preview.action("/preview/heater", {"value":True})
                self.assertEqual(self.preview.c.session.session_id, session_id)
                self.preview.action("/preview/control", {"enabled":False})
                self.preview.action("/preview/light", {"value":40})
                self.assertEqual(self.preview.c.session.session_id, session_id)
                self.assertFalse(self.preview.c.session.operation_enabled)

    def test_manual_scenario_starts_with_both_explicit_off_selections(self):
        self.preview.reset("manuell")
        controls = self.preview.state()["manual_controls"]
        self.assertIs(controls["heater"]["manual"], False)
        self.assertEqual(controls["light"]["manual"], 0)

    def test_local_control_and_target_use_actual_backend(self):
        self.preview.action("/preview/temperature", {"target_temperature_c":85})
        self.assertEqual(self.preview.c.target_temperature, 85)
        self.preview.action("/preview/control", {"enabled":False})
        self.assertFalse(self.preview.c.session.operation_enabled)
        self.preview.action("/preview/control", {"enabled":True})
        self.assertTrue(self.preview.c.session.operation_enabled)

    def test_user_can_finish_only_current_cooling_and_cannot_replay(self):
        self.preview.admin = False
        phase = self.preview.c.session.after_run
        self.assertIsNotNone(phase)
        session_id = self.preview.c.session.session_id
        token = self.preview.state()["session"]["after_run"]["phase_id"]
        self.assertIn("presence:direct:", phase.phase_id)
        self.assertNotEqual(token, phase.phase_id)
        for body in (
            [], {}, {"purpose": "session_gap", "token": phase.phase_id},
            {"purpose": "confirmation", "token": phase.phase_id},
            {"purpose": "after_run", "token": []},
            {"purpose": "after_run", "token": ""},
            {"purpose": "after_run", "token": phase.phase_id, "extra": True},
            {"purpose": "after_run", "token": "stale"},
        ):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.preview.action("/preview/finish_phase", body)
            self.assertEqual(self.preview.c.session.after_run.phase_id, phase.phase_id)
        body = {"purpose": "after_run", "token": token}
        self.preview.action("/preview/finish_phase", body)
        self.assertIsNone(self.preview.c.session.after_run)
        self.assertEqual(self.preview.c.session.session_id, session_id)
        self.assertTrue(self.preview.c.session.operation_enabled)
        self.assertEqual(self.preview.c.session.after_run_history[-1].ends_at, self.preview.now)
        with self.assertRaises(ValueError):
            self.preview.action("/preview/finish_phase", body)
        self.assertEqual(len(self.preview.c.session.after_run_history), 1)
        for action in ("door_open", "on", "door_close", "door_open", "off", "door_close"):
            self.preview.action("/simulate", {"action": action})
        next_phase = self.preview.c.session.after_run
        self.assertIsNotNone(next_phase)
        self.assertNotEqual(next_phase.phase_id, phase.phase_id)
        with self.assertRaises(ValueError):
            self.preview.action("/preview/finish_phase", body)
        self.assertEqual(self.preview.c.session.after_run.phase_id, next_phase.phase_id)
        next_token = self.preview.state()["session"]["after_run"]["phase_id"]
        self.assertNotEqual(next_token, token)
        self.preview.action("/preview/finish_phase", {
            "purpose": "after_run", "token": next_token,
        })
        self.assertIsNone(self.preview.c.session.after_run)
        self.assertEqual(len(self.preview.c.session.after_run_history), 2)

    def test_completed_session_keeps_history_after_restart_and_for_user(self):
        session_id = self.preview.c.session.session_id
        self.preview.action("/preview/control", {"enabled":False})
        token = next(d.token for d in self.preview.c.session.deadlines
                     if d.purpose == "session_gap")
        self.preview.action("/preview/finish-session", {"token":token})
        self.assertIsNone(self.preview.c.session)
        self.assertEqual(self.preview.state()["last_session"]["timeline"]["session_id"], session_id)
        self.assertEqual(self.preview.archive()[0]["session_id"], session_id)
        completed = self.preview.archive(session_id)
        self.assertIsNotNone(completed["session"]["ended_at"])
        self.assertTrue(completed["records"])
        self.assertTrue(completed["phase_projection"]["intervals"])
        for action in ("door_open", "door_close", "infusion"):
            with self.assertRaisesRegex(ValueError, "Ohne laufende Sitzung"):
                self.preview.action("/simulate", {"action":action})
        self.preview.action("/preview/control", {"enabled":True})
        self.assertNotEqual(self.preview.c.session.session_id, session_id)
        continued = self.preview.archive(session_id)
        self.assertEqual(continued["session"], completed["session"])
        self.assertEqual(continued["phase_projection"], completed["phase_projection"])
        self.assertEqual(continued["records"][:len(completed["records"])], completed["records"])
        self.assertTrue(all(record["kind"] == "measurement"
                            for record in continued["records"][len(completed["records"]):]))
        self.assertFalse(continued["measurement_window"]["complete"])
        from custom_components.ha_sauna.core.history import HISTORY_CONTEXT_SECONDS
        self.preview.step(HISTORY_CONTEXT_SECONDS)
        final = self.preview.archive(session_id)
        self.assertTrue(final["measurement_window"]["complete"])
        self.preview.step(60)
        self.assertEqual(self.preview.archive(session_id), final)
        self.assertEqual(len(self.preview.archive()), 2)
        self.preview.admin = False
        public = self.preview.archive(session_id)
        self.assertNotIn("configuration", public["session"])
        self.assertTrue(public["records"])
        self.assertTrue(all(r["kind"] in ("measurement", "source_snapshot", "phase")
                            for r in public["records"]))

    def test_minute_step_and_scenario_state_match_preview_controls(self):
        before = self.preview.now
        self.preview.action("/simulate", {"action":"step"})
        self.assertEqual(self.preview.now - before, timedelta(minutes=1))
        self.preview.action("/simulate", {"action":"scenario:manuell"})
        self.assertEqual(self.preview.state()["preview_scenario"], "manuell")

    def test_program_choices_use_existing_runtime_settings_without_session_reset(self):
        self.preview.admin = False
        session_id = self.preview.c.session.session_id
        program = self.preview.runtime.configuration.temperature_programs[0]
        saved = self.preview.action("/preview/program", {"profile":program.id})
        self.assertEqual(saved["selected_program_id"], program.id)
        self.assertEqual(self.preview.c.target_temperature, program.start_c)
        self.preview.action("/preview/program", {"temperature_steps":[80, 85, 90]})
        self.assertEqual(self.preview.c.session.temperature_program_steps, (80, 85, 90))
        self.preview.action("/preview/program", {
            "target_temperature_c":82, "final_temperature_c":88, "temperature_gangs":3,
        })
        self.assertEqual(self.preview.c.session.temperature_program_mode, "progressive")
        self.assertEqual(self.preview.c.target_temperature, 82)
        self.assertIsNone(self.preview.c.session.temperature_program_steps)
        self.preview.action("/preview/temperature", {"final_temperature_c":89})
        self.assertEqual(self.preview.c.session.temperature_program_mode, "progressive")
        self.preview.action("/preview/temperature", {"target_temperature_c":86})
        self.assertEqual(self.preview.c.target_temperature, 86)
        self.assertEqual(self.preview.c.session.temperature_program_mode, "constant")
        self.assertIsNone(self.preview.runtime.configuration.selected_program_id)
        self.assertEqual(self.preview.c.session.session_id, session_id)
