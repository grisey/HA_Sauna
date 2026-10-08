"""Gemeinsame Begriffe, gültige Protokollstufen und störungsarme Logausgabe."""
import json
import logging
from pathlib import Path
import unittest
from copy import deepcopy

from test_cooling import at, controller
from test_foundation import event

from custom_components.ha_sauna.archive import plain
from custom_components.ha_sauna.core.parameters import DEFINITIONS
from custom_components.ha_sauna.core.presence import binary_presence
from custom_components.ha_sauna.core.timeline import Kind
from custom_components.ha_sauna.log import SaunaLog
from custom_components.ha_sauna.presentation import (
    configuration_message, fault_message, public_phase_projection,
    public_session, public_state,
)


class PresentationTests(unittest.TestCase):
    def test_direct_presence_public_ids_preserve_live_archive_and_phase_references(self):
        sauna = controller()
        sauna.presence_source = "ha_presence"
        sauna.presence_entity = "binary_sensor.private_presence"
        sauna.process(event("entry-open", Kind.DOOR_OPEN, 1))
        sauna.observe_direct_presence(binary_presence(
            sauna.presence_entity, "on", at(2), at(2),
        ), at(2))
        sauna.process(event("entry-close", Kind.DOOR_CLOSE, 3))
        active = plain(sauna.session)
        public_active = public_session(active)
        raw_gang = active["timeline"]["active"]
        public_gang = public_active["timeline"]["active"]
        self.assertIn(sauna.presence_entity, raw_gang["gang_id"])
        self.assertNotEqual(public_gang["gang_id"], raw_gang["gang_id"])
        recognition = next(
            item for item in public_active["timeline"]["processed"]
            if item["kind"] == Kind.PRESENCE_CONFIRMED
        )
        self.assertEqual(public_gang["recognition_event_id"], recognition["event_id"])
        self.assertEqual(public_gang["start_source_event_id"], "entry-close")
        self.assertEqual(public_gang["session_id"], sauna.session.session_id)
        self.assertEqual(public_gang["started_at"], raw_gang["started_at"])
        self.assertEqual(public_gang["recognition_kind"], raw_gang["recognition_kind"])

        sauna.process(event("exit-open", Kind.DOOR_OPEN, 5))
        sauna.observe_direct_presence(binary_presence(
            sauna.presence_entity, "off", at(6), at(6),
        ), at(6))
        sauna.process(event("exit-close", Kind.DOOR_CLOSE, 7))
        sauna.report_contactor(False, at(8))
        raw = plain(sauna.session)
        raw["configuration"] = {"bindings": {"presence": sauna.presence_entity}}
        projection = plain(sauna.phase_projection(at(9)))
        before = deepcopy(raw)
        public = public_session(raw)
        completed = public["timeline"]["completed"][0]
        self.assertEqual(completed["gang_id"], public_gang["gang_id"])
        self.assertEqual(completed["recognition_event_id"], recognition["event_id"])
        end = next(item for item in public["timeline"]["processed"]
                   if item["kind"] == Kind.PRESENCE_ENDED)
        self.assertEqual(completed["end_event_id"], end["event_id"])
        self.assertNotEqual(end["event_id"], recognition["event_id"])
        self.assertEqual(public["after_run"]["phase_id"], completed["gang_id"])
        deadline = next(item for item in public["deadlines"]
                        if item["purpose"] == "after_run")
        self.assertEqual(deadline["token"], completed["gang_id"])
        public_projection = public_phase_projection(projection)
        self.assertEqual({item["source_id"] for item in public_projection["intervals"]
                          if item["source_id"]}, {completed["gang_id"]})
        self.assertNotIn("configuration", public)
        self.assertNotIn(sauna.presence_entity, json.dumps([public, public_projection]))
        self.assertEqual(public_session(public), public)
        self.assertEqual(raw, before)

        state = public_state({
            "configuration": raw["configuration"], "parameters": [], "measurements": [],
            "presence": {"entity_id": sauna.presence_entity},
            "session": active, "last_session": raw, "phase_projection": projection,
        })
        self.assertEqual(state["session"], public_active)
        self.assertEqual(state["last_session"], public)
        self.assertEqual(state["phase_projection"], public_projection)
        self.assertNotIn(sauna.presence_entity, json.dumps(state))

    def test_public_session_keeps_session_gap_control_token_and_empty_state(self):
        sauna = controller()
        sauna.set_operation(False, at(1))
        session = plain(sauna.session)
        self.assertEqual(public_session(session), session)
        self.assertIsNone(public_session(None))
        self.assertIsNone(public_phase_projection(None))

    def test_all_settings_have_the_same_labels_and_help_in_both_ha_forms(self):
        root=Path(__file__).resolve().parents[1]/"custom_components/ha_sauna"
        strings=json.loads((root/"strings.json").read_text())
        self.assertEqual(strings,json.loads((root/"translations/de.json").read_text()))
        for section in ("config","options"):
            fields=strings[section]["step"]["parameters"]
            for d in DEFINITIONS:
                self.assertEqual(fields["data"][d.key], d.label)
                self.assertEqual(fields["data_description"][d.key], d.description)
                self.assertTrue(d.description and d.group)
        self.assertNotIn("Grundgerüst", json.dumps(strings))
        self.assertNotIn("Testsession", json.dumps(strings))

    def test_configuration_and_measurement_errors_are_distinct_german_messages(self):
        text=configuration_message(["sensor_timeout_seconds","feedback_timeout_seconds"])
        self.assertIn("Höchstalter eines Messwerts",text)
        self.assertNotIn("_seconds",text)
        self.assertIn("veraltet",fault_message("upper_temperature","measurement_stale"))
        self.assertIn("noch nicht eingestellt",fault_message("upper_temperature","validity_unconfigured"))

    def test_standard_logging_levels_filter_and_changed_states_are_not_repeated(self):
        log=SaunaLog("unit-test")
        records=[]
        class Capture(logging.Handler):
            def emit(self,record): records.append(record)
        handler=Capture()
        log.logger.addHandler(handler)
        log.logger.propagate=False
        self.addCleanup(log.logger.removeHandler,handler)
        log.debug("measurement","Messwert: %s",42)
        log.change("phase","aus",logging.INFO,"Betriebszustand: %s","aus")
        log.change("phase","aus",logging.INFO,"Betriebszustand: %s","aus")
        self.assertEqual([r.levelname for r in records],["INFO"])
        self.assertEqual(records[0].sauna_event,"phase")
        log.set_level("ERROR")
        log.info("operation","Bedienung")
        log.logger.error("Fehler")
        log.set_level("DEBUG")
        log.debug("measurement","Messwert: %s",42)
        self.assertEqual([r.levelname for r in records],["INFO","ERROR","DEBUG"])
        with self.assertRaises(ValueError):
            log.set_level("invalid")
