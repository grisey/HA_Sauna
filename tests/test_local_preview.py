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
        self.preview.reset("bereit")
        value = not self.preview.c.contactor
        self.preview.action("/preview/heater", {"value":value})
        self.assertIs(self.preview.c.heater_override, value)
        self.preview.action("/simulate", {"action":"role", "value":"admin"})
        self.assertIn("presence", self.preview.state())

    def test_display_parameter_save_is_visible_without_a_ha_options_listener(self):
        self.preview.reset("archiv")
        before = self.preview.runtime.configuration.as_options()
        current = before["parameters"]["preset_step_c"]
        changed = current + 1
        self.preview.action("/preview/parameters", {"preset_step_c": changed})
        after = self.preview.state()["configuration"]
        before["parameters"]["preset_step_c"] = changed
        self.assertEqual(after, before)
        self.assertFalse(self.preview.runtime.reconfiguring)
        self.preview.admin = False
        with self.assertRaises(ValueError):
            self.preview.action("/preview/parameters", {"preset_step_c": current})

    def test_cooling_exposes_the_same_reason_as_rejected_heater_action(self):
        for admin in (True, False):
            with self.subTest(admin=admin):
                self.preview.admin = admin
                reason = self.preview.state()["manual_controls"]["heater"]["blocked_on_reason"]
                self.assertTrue(reason)
                with self.assertRaises(ValueError) as rejected:
                    self.preview.action("/preview/heater", {"value":True})
                self.assertEqual(str(rejected.exception), reason)

    def test_user_light_presets_and_percentages_keep_their_meaning(self):
        self.preview.admin = False
        self.preview.reset("bereit")
        normal = round(self.preview.normal_light())
        for value, expected in (
            (False, 0),
            (True, self.preview.c.parameters.values["session_light_brightness_percent"]),
            ("normal", normal), (55, 55),
        ):
            self.preview.action("/preview/light", {"value":value})
            self.assertEqual(self.preview.light, expected)
        self.preview.action("/preview/light", {"value":None})
        self.assertIsNone(self.preview.light_manual)
        self.preview.step(self.preview.c.parameters.values["light_transition_seconds"] + 1)
        self.assertEqual(self.preview.light, normal)
        with self.assertRaises(ValueError):
            self.preview.action("/preview/light", {"value":101})

    def test_matching_light_observation_does_not_create_or_change_override(self):
        self.preview.reset("bereit")
        actual = self.preview.light
        self.assertIsNone(self.preview.light_manual)
        for value in (True, actual):
            self.preview.action("/preview/light", {"value":value})
            self.assertIsNone(self.preview.light_manual)
        self.preview.action("/preview/light", {"value":55})
        for value in (True, 55):
            self.preview.action("/preview/light", {"value":value})
            self.assertEqual(self.preview.light_manual, 55)
            self.assertEqual(self.preview.light, 55)
        self.preview.action("/preview/light", {"value":None})
        self.assertIsNone(self.preview.light_manual)
        self.preview.step(self.preview.c.parameters.values["light_transition_seconds"] + 1)
        self.assertEqual(self.preview.light, round(self.preview.normal_light()))

    def test_heater_feedback_follows_upper_threshold_unless_explicitly_fixed(self):
        self.preview.reset("bereit")
        self.preview.action("/simulate", {"action":"feedback", "value":True})
        self.preview.action("/simulate", {
            "action":"temperature", "value":self.preview.c.thermostat_target,
        })
        self.assertFalse(self.preview.c.last_decision.heat)
        self.assertTrue(self.preview.state()["manual_controls"]["heater"]["observation"]["on"])
        for admin in (True, False):
            self.preview.admin = admin
            self.assertEqual(self.preview.state()["preview_feedback"],
                             {"following":False, "on":True})
        self.preview.action("/simulate", {"action":"feedback", "value":"follow"})
        self.assertEqual(self.preview.state()["preview_feedback"],
                         {"following":True, "on":False})
        self.assertFalse(self.preview.state()["manual_controls"]["heater"]["observation"]["on"])
        self.preview.step(self.preview.c.parameters.seconds("thermostat_cooldown_minutes") + 1,
                          temperature=self.preview.c.thermostat_restart_temperature)
        self.assertTrue(self.preview.c.contactor)
        self.preview.step(self.preview.c.parameters.seconds("minimum_heating_minutes") + 1,
                          temperature=self.preview.c.thermostat_target)
        self.assertFalse(self.preview.c.contactor)

    def test_light_follows_temperature_and_expires_manual_override(self):
        self.preview.reset("aufheizen")
        initial = self.preview.light
        self.preview.step(
            self.preview.c.parameters.seconds("minimum_heating_minutes"),
            temperature=self.preview.c.target_temperature,
        )
        self.assertGreater(self.preview.light, initial)
        self.preview.action("/preview/light", {"value":55})
        state = self.preview.state()["manual_controls"]["light"]
        self.assertIsNotNone(state["override_ends_at"])
        self.assertEqual(state["manual_feedback_target"], 55)
        self.preview.step(self.preview.c.parameters.seconds("manual_override_minutes") + 1)
        self.assertIsNone(self.preview.light_manual)
        self.assertIsNone(self.preview.state()["manual_controls"]["light"]["manual_feedback_target"])
        self.preview.step(self.preview.c.parameters.values["light_transition_seconds"] + 1)
        self.assertEqual(self.preview.light, round(self.preview.normal_light()))

    def test_light_phase_change_releases_manual_selection(self):
        self.preview.reset("aufheizen")
        self.preview.action("/preview/light", {"value":55})
        self.assertEqual(self.preview.light_manual, 55)
        self.preview.action("/simulate", {
            "action":"temperature", "value":self.preview.c.target_temperature,
        })
        self.assertEqual(self.preview.c.phase, "bereit")
        self.assertIsNone(self.preview.light_manual)
        self.preview.step(self.preview.c.parameters.values["light_transition_seconds"] + 1)
        self.assertEqual(self.preview.light, round(self.preview.normal_light()))

    def test_light_manual_mode_keeps_selection_and_session_light_finishes(self):
        self.preview.reset("manuell")
        self.preview.action("/preview/light", {"value":55})
        self.preview.step(self.preview.c.parameters.seconds("manual_override_minutes") + 1)
        self.assertEqual(self.preview.light_manual, 55)
        self.assertEqual(self.preview.light, 55)
        self.preview.reset("bereit")
        self.preview.c.finish_session(self.preview.now, light_after_run=True)
        self.preview.sample()
        self.preview.step((self.preview.c.light_after_run.ends_at - self.preview.now).total_seconds())
        self.assertEqual(self.preview.light, 0)

    def test_user_can_control_manual_heater_through_real_controller(self):
        self.preview.admin = False
        self.preview.reset("manuell")
        self.assertTrue(self.preview.state()["permissions"]["heater"])
        self.preview.action("/preview/heater", {"value":True})
        self.assertTrue(self.preview.c.last_decision.heat)
        self.preview.action("/preview/heater", {"value":False})
        self.assertFalse(self.preview.c.last_decision.heat)

    def test_user_sees_manual_enablement_when_thermostat_needs_no_heat(self):
        self.preview.admin = False
        self.preview.reset("manuell")
        temperature = (self.preview.c.target_temperature
                       + self.preview.c.parameters.values["readiness_offset_c"])
        self.preview.action("/simulate", {"action":"temperature", "value":temperature})
        self.preview.action("/preview/heater", {"value":True})
        state = self.preview.state()
        self.assertNotIn("decision", state)
        heater = state["manual_controls"]["heater"]
        self.assertIs(heater["manual"], True)
        self.assertIs(heater["commanded"], False)
        self.assertIs(heater["observation"]["on"], False)

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
        self.preview.action("/preview/heater", {"value":True})
        self.preview.action("/preview/light", {"value":True})
        self.assertIsNone(self.preview.c.session)
        with self.assertRaises(ValueError):
            self.preview.action("/preview/control", {"enabled":True})
        self.preview.action("/preview/control-mode", {"mode":"automatic"})
        self.assertIsNone(self.preview.state()["manual_controls"]["heater"]["manual"])
        self.assertIsNone(self.preview.state()["manual_controls"]["light"]["manual"])

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
                with self.assertRaises(ValueError):
                    self.preview.action("/preview/control", {"enabled":True})
                self.assertIsNone(self.preview.c.session)
                self.preview.action("/preview/control-mode", {"mode":"automatic"})
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
        self.assertIsNone(self.preview.c.session)
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

    def test_preview_button_short_uses_real_gestures_without_starting_a_session(self):
        self.preview.reset("manuell")
        for expected in (True, False):
            self.preview.action("/simulate", {"action":"button_short"})
            self.assertEqual(self.preview.c.last_decision.heat, expected)
            self.assertIsNone(self.preview.c.session)
            self.assertEqual(self.preview.c.control_mode, "manual")
        self.preview.reset("archiv")
        self.preview.action("/simulate", {"action":"button_short"})
        self.assertEqual(self.preview.c.control_mode, "manual")
        self.assertIsNone(self.preview.c.session)
        self.assertTrue(self.preview.c.last_decision.heat)

    def test_native_multiple_press_selection_starts_and_ends_without_short_start(self):
        for gesture in ("double", "triple"):
            with self.subTest(gesture=gesture):
                self.preview.reset("manuell")
                self.preview.admin = False
                self.assertIn(gesture, self.preview.state()["button_session_gestures"])
                saved = self.preview.action("/preview/button-gesture", {"gesture":gesture})
                self.assertEqual(saved["button_session_gesture"], gesture)
                self.assertEqual(self.preview.entry.options["button_session_gesture"], gesture)
                self.preview.action("/simulate", {"action":"button_short"})
                self.assertIsNone(self.preview.c.session)
                self.preview.action("/simulate", {"action":f"button_{gesture}"})
                self.assertIsNotNone(self.preview.c.session)
                self.assertEqual(self.preview.c.control_mode, "automatic")
                with self.assertRaises(ValueError):
                    self.preview.action("/preview/button-gesture", {"gesture":"long"})
                self.preview.action("/simulate", {"action":f"button_{gesture}"})
                self.assertIsNone(self.preview.c.session)
        for gesture in ("short", "unknown"):
            with self.assertRaises(ValueError):
                self.preview.action("/preview/button-gesture", {"gesture":gesture})

    def test_preview_button_hold_uses_configured_threshold_and_release_feedback(self):
        self.preview.reset("manuell")
        self.preview.action("/preview/button-gesture", {"gesture":"long"})
        self.assertEqual(self.preview.runtime.configuration.control_input_mode, "button")
        before = self.preview.now
        seconds = self.preview.c.parameters.values["button_hold_seconds"]
        bright = self.preview.c.parameters.values["session_light_brightness_percent"]
        self.preview.action("/simulate", {"action":"button_hold"})
        state = self.preview.state()
        identity = self.preview.c.session.session_id
        self.assertEqual(self.preview.now - before, timedelta(seconds=seconds))
        self.assertEqual(self.preview.c.control_mode, "automatic")
        self.assertTrue(state["preview_button"]["pressed"])
        self.assertTrue(state["preview_button"]["start_hold"])
        self.assertEqual(state["manual_controls"]["light"]["observation"]["brightness_percent"], bright)
        with self.assertRaises(ValueError):
            self.preview.action("/simulate", {"action":"button_short"})
        self.preview.action("/simulate", {"action":"button_release"})
        self.assertFalse(self.preview.state()["preview_button"]["pressed"])
        self.assertFalse(self.preview.runtime.button_start_hold_active)
        self.assertEqual(self.preview.c.session.session_id, identity)
        self.preview.action("/simulate", {"action":"button_short"})
        self.assertEqual(self.preview.c.session.session_id, identity)
        self.preview.action("/simulate", {"action":"button_hold"})
        self.assertIsNone(self.preview.c.session)
        self.assertEqual(self.preview.state()["manual_controls"]["light"]["observation"]["brightness_percent"], 0)
        self.preview.action("/simulate", {"action":"button_release"})
        self.assertIsNotNone(self.preview.c.light_after_run)
        self.assertEqual(self.preview.state()["manual_controls"]["light"]["observation"]["brightness_percent"], 0)
        self.preview.step(self.preview.c.parameters.values["light_transition_seconds"] + 1)
        self.assertEqual(self.preview.state()["manual_controls"]["light"]["observation"]["brightness_percent"], bright)

    def test_minute_step_and_scenario_state_match_preview_controls(self):
        before = self.preview.now
        self.preview.action("/simulate", {"action":"step"})
        self.assertEqual(self.preview.now - before, timedelta(minutes=1))
        self.preview.action("/simulate", {"action":"scenario:manuell"})
        self.assertEqual(self.preview.state()["preview_scenario"], "manuell")

    def test_scenario_reset_invalidates_the_previous_archive_without_creating_sessions(self):
        previous = self.preview.state()
        session_id = previous["session"]["timeline"]["session_id"]
        self.assertIsNotNone(self.preview.archive(session_id))
        self.preview.action("/simulate", {"action":"scenario:manuell"})
        current = self.preview.state()
        self.assertGreater(current["archive_revision"], previous["archive_revision"])
        self.assertIsNone(current["session"])
        self.assertIsNone(current["last_session"])
        self.assertEqual(self.preview.archive(), [])
        self.assertIsNone(self.preview.archive(session_id))
        self.preview.action("/preview/heater", {"value":True})
        self.assertEqual(self.preview.state()["archive_revision"], current["archive_revision"])
        self.assertEqual(self.preview.archive(), [])

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
        standard = self.preview.runtime.configuration.parameters.values["standard_temperature_c"]
        saved = self.preview.action("/preview/program", {"profile": "constant"})
        self.assertEqual(saved["parameters"]["target_temperature_c"], standard)
        self.assertEqual(self.preview.c.target_temperature, standard)
        self.assertEqual(self.preview.entry.options["parameters"]["target_temperature_c"], standard)
        self.preview.action("/preview/temperature", {"target_temperature_c":86})
        self.assertEqual(self.preview.c.target_temperature, 86)
        self.preview.step(1)
        self.assertEqual(self.preview.c.target_temperature, 86)
        self.assertEqual(self.preview.c.session.temperature_program_mode, "constant")
        self.assertIsNone(self.preview.runtime.configuration.selected_program_id)
        self.assertEqual(self.preview.c.session.session_id, session_id)
