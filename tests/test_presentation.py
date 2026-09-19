"""Gemeinsame Begriffe, gültige Protokollstufen und störungsarme Logausgabe."""
import json
import logging
from pathlib import Path
import unittest
from custom_components.ha_sauna.core.parameters import DEFINITIONS
from custom_components.ha_sauna.log import SaunaLog
from custom_components.ha_sauna.presentation import configuration_message, fault_message


class PresentationTests(unittest.TestCase):
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
        handler=Capture();log.logger.addHandler(handler);log.logger.propagate=False
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
        with self.assertRaises(ValueError): log.set_level("invalid")
