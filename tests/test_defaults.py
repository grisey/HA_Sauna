"""Factory catalog changes stay distinct from saved settings and migrations."""

import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from custom_components.ha_sauna.core.defaults import (
    load_catalog,
    instance_default,
    section,
    validate_catalog,
)
from custom_components.ha_sauna.core.parameters import BY_KEY, EDITABLE_DEFINITIONS, Parameters


class DefaultsTests(unittest.TestCase):
    def test_hand_edited_catalog_fails_at_loader_before_consumers(self):
        mutations = {
            "missing_cache_budget": lambda data: data["runtime"].pop(
                "archive_projection_cache_entries"
            ),
            "missing_ui_step": lambda data: data["frontend"].pop("temperature_step_c"),
            "missing_precision": lambda data: data["appearance"]["precision"].pop(
                "energy"
            ),
            "missing_scale": lambda data: data["appearance"]["scales"].pop("humidity"),
            "missing_instrument": lambda data: data["appearance"]["instruments"].pop("light"),
            "invalid_instrument_default": lambda data: data["appearance"]["instruments"][
                "default"
            ].update(default="inherit"),
            "invalid_instrument_options": lambda data: data["appearance"]["instruments"][
                "temperature"
            ].update(options=["round", "linear"]),
            "missing_scale_limit": lambda data: data["appearance"]["scales"][
                "humidity"
            ].pop("maximum"),
            "nan_limit": lambda data: data["appearance"]["scales"]["humidity"].update(
                maximum=float("nan")
            ),
            "infinite_limit": lambda data: data["appearance"]["scales"][
                "temperature"
            ].update(minimum=float("-inf")),
            "bool_limit": lambda data: data["appearance"]["scales"]["humidity"].update(
                minimum=False
            ),
            "overflowing_scale_span": lambda data: data["appearance"]["scales"][
                "temperature"
            ]["default"].update(minimum=-1e308, maximum=1e308),
            "bool_version": lambda data: data.update(schema_version=True),
            "float_version": lambda data: data.update(schema_version=1.0),
            "bool_cache_budget": lambda data: data["runtime"].update(
                archive_projection_cache_entries=True
            ),
            "float_program_count": lambda data: data["programs"][0].update(
                distribution_gangs=3.0
            ),
            "missing_parameter": lambda data: data["parameters"].pop(0),
            "missing_color_role": lambda data: data["appearance"]["colors"].pop(0),
            "unsupported_setup": lambda data: data["setup"].update(log_level="ERROR"),
            "null_parameter_default": lambda data: next(
                spec for spec in data["parameters"]
                if spec["key"] == "session_gap_minutes"
            ).update(default=None),
            "null_color_default": lambda data: data["appearance"]["colors"][0].update(default=None),
            "invalid_parameter_reference": lambda data: data["instance"].update(
                button_temperature_c={"parameter": "final_temperature_c"}
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "defaults.json"
            for name, mutate in mutations.items():
                with self.subTest(case=name):
                    data = load_catalog()
                    mutate(data)
                    path.write_text(json.dumps(data), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_catalog(path)

    def test_valid_custom_bounds_and_supported_setup_choices_remain_editable(self):
        data = load_catalog()
        data["appearance"]["scales"]["humidity"].update(minimum=5, maximum=80)
        data["appearance"]["scales"]["humidity"]["default"].update(
            minimum=5, maximum=70
        )
        data["runtime"]["archive_projection_cache_entries"] = 7
        data["setup"] = {"program_mode": "constant", "control_input_mode": "switch"}
        self.assertIs(validate_catalog(data), data)

    def test_detection_group_scope_requires_known_sources_and_complete_hints(self):
        for metadata in (
            {"presence_sources": ["combined"]},
            {"presence_sources": []},
            {"presence_sources": ["proxy", "proxy"]},
            {"presence_sources": "proxy"},
            {"descriptions": {"proxy": "Hinweis"}},
            {"descriptions": {"proxy": "Hinweis", "ha_presence": ""}},
        ):
            with self.subTest(metadata=metadata):
                data = load_catalog()
                data["frontend"]["settings_subgroups"][0].update(metadata)
                with self.assertRaises(ValueError):
                    validate_catalog(data)

    def test_catalog_rejects_invalid_defaults_before_consumer_imports(self):
        mutations = (
            lambda data: data["parameters"].append(deepcopy(data["parameters"][0])),
            lambda data: data["parameters"][0].update(default=-1),
            lambda data: data["parameters"][0].update(step=0),
            lambda data: data["parameters"][0].update(settings_group="missing"),
            lambda data: data["programs"][0].update(start_c=101),
            lambda data: data["appearance"]["scales"]["temperature"]["default"].update(
                minimum=200
            ),
            lambda data: data["runtime"].update(archive_projection_cache_entries=0),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                data = load_catalog()
                mutate(data)
                with self.assertRaises(ValueError):
                    validate_catalog(data)

    def test_catalog_views_are_detached_and_every_setting_has_group_metadata(self):
        catalog = section("parameters")
        catalog[0]["default"] = -1
        self.assertGreater(section("parameters")[0]["default"], 0)
        group_ids = {group["id"] for group in section("frontend")["settings_groups"]}
        self.assertTrue(
            all(d.settings_group in group_ids for d in EDITABLE_DEFINITIONS)
        )
        subgroup_ids = {
            group["id"] for group in section("frontend")["settings_subgroups"]
        }
        self.assertTrue(
            all(
                d.settings_subgroup in subgroup_ids
                for d in EDITABLE_DEFINITIONS
                if d.settings_group != "programs"
            )
        )
        self.assertEqual(
            [d.key for d in EDITABLE_DEFINITIONS],
            [d["key"] for d in sorted(section("parameters"), key=lambda d: d["order"])],
        )
        door = BY_KEY["door_open_drop_c"]
        self.assertTrue(door.expert)
        self.assertEqual(door.settings_group, "sensors")
        self.assertEqual(door.step, "any")
        self.assertNotIn(
            "Manuelle Lichtänderungen",
            BY_KEY["operation_brightness_percent"].description,
        )

    def test_button_temperature_default_is_independent_of_current_parameters(self):
        expected = section("instance")["button_temperature_c"]
        for target in (72, 87):
            self.assertEqual(
                instance_default("button_temperature_c", parameters={"target_temperature_c": target}),
                expected,
            )
        self.assertEqual(instance_default("button_temperature_c"), expected)
        self.assertNotIn("selected_program_id", section("instance"))
        self.assertNotIn("temperature_steps", section("instance"))

    def test_explicit_catalog_parameter_reference_uses_current_parameters(self):
        catalog = load_catalog()
        catalog["instance"]["button_temperature_c"] = {"parameter": "target_temperature_c"}
        validate_catalog(catalog)
        with patch("custom_components.ha_sauna.core.defaults._CATALOG", catalog):
            self.assertEqual(
                instance_default("button_temperature_c", parameters={"target_temperature_c": 87}),
                87,
            )
            self.assertEqual(instance_default("button_temperature_c"), BY_KEY["target_temperature_c"].default)

    def test_button_temperature_catalog_default_obeys_parameter_bounds(self):
        minimum = BY_KEY["sauna_min_temperature_c"].default
        maximum = BY_KEY["target_temperature_c"].maximum
        for value in (minimum, maximum):
            catalog = load_catalog()
            catalog["instance"]["button_temperature_c"] = value
            self.assertIs(validate_catalog(catalog), catalog)
        for value in (minimum - .1, maximum + .1, True, None, float("nan")):
            with self.subTest(value=value):
                catalog = load_catalog()
                catalog["instance"]["button_temperature_c"] = value
                with self.assertRaises(ValueError):
                    validate_catalog(catalog)

    def test_obsolete_timer_parameters_are_accepted_but_not_retained(self):
        for value in (None, 0, 12, "obsolete"):
            with self.subTest(value=value):
                parameters = Parameters({
                    "mechanical_timer_minutes": value,
                    "mechanical_timer_warning_minutes": value,
                })
                self.assertEqual(parameters.as_dict(), Parameters({}).as_dict())
        self.assertNotIn("mechanical_timer_minutes", BY_KEY)
        self.assertNotIn("mechanical_timer_warning_minutes", BY_KEY)

    def test_changed_factory_values_preserve_saved_choices_and_legacy_modes(self):
        # A fresh interpreter models the documented reload boundary and avoids
        # mutating definitions already imported by unrelated test modules.
        code = """
import json
from custom_components.ha_sauna.core import defaults
catalog = defaults.load_catalog()
for spec in catalog["parameters"]:
    if spec["key"] == "session_gap_minutes":
        spec["default"] = 21
catalog["instance"]["log_level"] = "DEBUG"
catalog["programs"][0]["start_c"] = 81
catalog["appearance"]["scales"]["temperature"]["default"]["minimum"] = 35
defaults._CATALOG = defaults.validate_catalog(catalog)
from custom_components.ha_sauna.bindings import ROLES
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.runtime import Configuration
bindings = {role.key: f"{role.domains[0]}.test_{role.key}" for role in ROLES if not role.optional or role.device_class in {"temperature", "humidity"}}
legacy = Configuration.from_options({"bindings": bindings, "parameters": {"target_temperature_c": 80}})
saved = legacy.as_options()
saved["parameters"]["session_gap_minutes"] = 17
saved["log_level"] = "ERROR"
saved["temperature_programs"][0]["start_c"] = 82
saved["appearance"]["scales"]["temperature"]["minimum"] = 42
loaded = Configuration.from_options(saved)
assert legacy.program_mode == loaded.program_mode == "constant"
assert legacy.parameters.values["session_gap_minutes"] == 21
assert loaded.parameters.values["session_gap_minutes"] == 17
assert legacy.log_level == "DEBUG" and loaded.log_level == "ERROR"
assert legacy.temperature_programs[0].start_c == 81
assert loaded.temperature_programs[0].start_c == 82
assert legacy.appearance["scales"]["temperature"]["minimum"] == 35
assert loaded.appearance["scales"]["temperature"]["minimum"] == 42
assert not any(program.id == "program_1" for program in legacy.temperature_programs)
assert defaults.instance_default("program_mode", setup=True) == catalog["setup"]["program_mode"]
derived = Configuration.from_options({"bindings": bindings, "parameters": {"target_temperature_c": 87}})
assert derived.button_temperature_c == defaults.instance_default("button_temperature_c")
explicit = derived.as_options()
explicit["button_temperature_c"] = 83
assert Configuration.from_options(explicit).button_temperature_c == 83
explicit["button_temperature_c"] = None
assert Configuration.from_options(explicit).button_temperature_c == defaults.instance_default("button_temperature_c")
assert derived.selected_program_id is None and derived.temperature_steps is None
print(json.dumps({"new_gap": 21, "saved_gap": 17, "legacy_mode": loaded.program_mode}))
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["legacy_mode"], "constant")
