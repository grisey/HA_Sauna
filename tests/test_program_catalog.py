"""Named temperature program catalog without Home Assistant imports."""

import unittest

from custom_components.ha_sauna.core.program_catalog import (
    DEFAULT_PROGRAMS,
    NamedTemperatureProgram,
    load_programs,
    migrate_legacy_programs,
    program_options,
)


class ProgramCatalogTests(unittest.TestCase):
    def test_defaults_have_stable_names_and_hold_their_end_target(self):
        self.assertEqual(
            [(program.id, program.name) for program in DEFAULT_PROGRAMS],
            [
                ("genusszeit", "Genusszeit"),
                ("gipfelstuermer", "Gipfelstürmer"),
                ("ewigkeit", "Ewigkeit"),
                ("liegewiese", "Liegewiese"),
                ("hoehenwanderung", "Höhenwanderung"),
                ("schnellstarter", "Schnellstarter"),
            ],
        )
        program = DEFAULT_PROGRAMS[1].temperature_program(minimum_c=60, maximum_c=100)
        self.assertEqual(program.target(0), 84)
        self.assertEqual(program.target(3), 100)
        self.assertEqual(program.target(9), 100)

    def test_loading_rejects_invalid_data_and_duplicate_ids(self):
        with self.assertRaises(ValueError):
            NamedTemperatureProgram("id", "", 80, 90, 3)
        with self.assertRaises(ValueError):
            NamedTemperatureProgram("id", "Name", 80, 90, 3.0)
        with self.assertRaises(ValueError):
            load_programs(
                [DEFAULT_PROGRAMS[0].as_dict(), DEFAULT_PROGRAMS[0].as_dict()],
                minimum_c=60,
                maximum_c=100,
            )

    def test_manual_steps_are_canonical_and_reject_conflicting_metadata(self):
        program = NamedTemperatureProgram(
            "manuell", "Manuell", temperature_steps=(80, 86, 90)
        )
        self.assertEqual(
            (program.start_c, program.end_c, program.distribution_gangs), (80, 90, 3)
        )
        self.assertEqual(
            program.temperature_program(minimum_c=60, maximum_c=100).target(8), 90
        )
        loaded = load_programs(
            [{"id": "manuell", "name": "Manuell", "temperature_steps": [80, 86, 90]}],
            minimum_c=60,
            maximum_c=100,
        )
        self.assertEqual(loaded, (program,))
        with self.assertRaises(ValueError):
            NamedTemperatureProgram("manuell", "Manuell", 80, 90, 4, (80, 86, 90))
        with self.assertRaises(ValueError):
            load_programs(
                [{**DEFAULT_PROGRAMS[0].as_dict(), "start_c": 59}],
                minimum_c=60,
                maximum_c=100,
            )
        with self.assertRaises(ValueError):
            load_programs(
                [{**DEFAULT_PROGRAMS[0].as_dict(), "id": "current"}],
                minimum_c=60,
                maximum_c=100,
            )

    def test_options_are_small_and_json_safe(self):
        self.assertEqual(
            program_options(DEFAULT_PROGRAMS, minimum_c=60, maximum_c=100)[0],
            {"id": "genusszeit", "name": "Genusszeit"},
        )

    def test_legacy_profiles_only_survive_when_changed_or_referenced(self):
        self.assertEqual(
            [
                program.id
                for program in migrate_legacy_programs({}, minimum_c=60, maximum_c=100)
            ],
            [program.id for program in DEFAULT_PROGRAMS],
        )
        migrated = migrate_legacy_programs(
            {"parameters": {"program_1_end_c": 98}, "button_program": "program_2"},
            minimum_c=60,
            maximum_c=100,
        )
        self.assertEqual(
            [program.id for program in migrated][-2:], ["program_1", "program_2"]
        )
        self.assertEqual(migrated[-2].as_dict()["name"], "Bisheriges Programm 1")
        self.assertEqual(migrated[-2].end_c, 98)
        self.assertEqual(migrated[-1].as_dict()["name"], "Bisheriges Programm 2")
        self.assertEqual((migrated[-1].start_c, migrated[-1].end_c), (70, 90))
