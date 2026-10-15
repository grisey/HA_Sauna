"""Catalogue transport and non-default consumer boundaries, without session replay."""

from copy import deepcopy
from datetime import timedelta
import json
import math
from pathlib import Path
import unittest

from custom_components.ha_sauna.appearance import (
    default_appearance,
    validate_appearance,
)
from custom_components.ha_sauna.core.defaults import (
    load_catalog,
    preference_values,
    section,
    validate_catalog,
)
from custom_components.ha_sauna.core.detector import Detector, robust_slope
from custom_components.ha_sauna.core.light import normal_brightness
from custom_components.ha_sauna.core.parameters import (
    BY_KEY,
    ParameterError,
    Parameters,
)
from custom_components.ha_sauna.core.warmup import HeatingProgress
from custom_components.ha_sauna.runtime import Configuration
from sessions.fixture_configuration import catalog_configuration
from test_foundation import T0, bindings
from test_settings import setting


class ParameterConsumptionTests(unittest.TestCase):
    def test_session_binding_tracks_effective_values_without_presentation_metadata(
        self,
    ):
        original = load_catalog()
        _, _, digest = catalog_configuration(original)
        presentation = deepcopy(original)
        presentation["appearance"]["preferences"]["history_live_poll_ms"][
            "default"
        ] += presentation["appearance"]["preferences"]["history_live_poll_ms"]["step"]
        presentation["parameters"][0]["description"] += " Presentation text."
        self.assertEqual(catalog_configuration(presentation)[2], digest)
        parameter = deepcopy(original)
        spec = next(
            item
            for item in parameter["parameters"]
            if item["key"] == "after_run_minutes"
        )
        spec["default"] += spec["number_step"]
        self.assertNotEqual(catalog_configuration(parameter)[2], digest)
        program = deepcopy(original)
        program["programs"][0]["start_c"] += BY_KEY["target_temperature_c"].number_step
        self.assertNotEqual(catalog_configuration(program)[2], digest)

    def test_each_preference_consumes_its_exclusive_default_and_configuration_surface(
        self,
    ):
        catalog = load_catalog()
        defaults = default_appearance()
        for key, spec in catalog["appearance"]["preferences"].items():
            with self.subTest(key=key):
                self.assertIn(spec["configuration_surface"], {"catalog", "panel"})
                self.assertEqual(preference_values({})[key], spec["default"])
                candidates = (
                    spec["default"] + spec["step"],
                    spec["default"] - spec["step"],
                )
                changed = None
                for candidate in candidates:
                    if not spec["minimum"] <= candidate <= spec["maximum"]:
                        continue
                    edited_catalog = deepcopy(catalog)
                    edited_catalog["appearance"]["preferences"][key]["default"] = (
                        candidate
                    )
                    try:
                        changed = preference_values({}, catalog=edited_catalog)
                    except ValueError:
                        continue
                    break
                self.assertIsNotNone(changed, f"No distinct valid default for {key}")
                self.assertEqual(changed[key], candidate)
                if spec["configuration_surface"] == "catalog":
                    # Previously saved technical values cannot override the current
                    # catalogue. A new write must report the ownership violation.
                    self.assertEqual(
                        preference_values({key: candidate})[key], spec["default"]
                    )
                    with self.assertRaises(ValueError):
                        validate_appearance(
                            {"preferences": {key: candidate}},
                            reject_catalog_overrides=True,
                        )
                else:
                    saved = validate_appearance({"preferences": {key: candidate}})
                    self.assertEqual(saved["preferences"][key], candidate)
                    self.assertEqual(validate_appearance(saved), saved)
                for invalid in (
                    True,
                    None,
                    float("nan"),
                    float("inf"),
                    spec["maximum"] + spec["step"],
                ):
                    with self.assertRaises(ValueError):
                        validate_appearance(
                            {"preferences": {key: invalid}},
                            reject_catalog_overrides=True,
                        )
        self.assertEqual(default_appearance(), defaults)

    def test_every_standard_declares_one_configuration_surface(self):
        catalog = load_catalog()
        for section_name in ("parameters", "legacy_parameters"):
            for spec in catalog[section_name]:
                with self.subTest(section=section_name, key=spec["key"]):
                    self.assertIn(
                        spec["configuration_surface"],
                        {"catalog", "integration", "panel"},
                    )
                    invalid = deepcopy(catalog)
                    next(
                        item
                        for item in invalid[section_name]
                        if item["key"] == spec["key"]
                    ).pop("configuration_surface")
                    with self.assertRaises(ValueError):
                        validate_catalog(invalid)
        parameters = Parameters({})
        expected = {
            item["key"]
            for item in catalog["parameters"]
            if item["configuration_surface"] != "catalog"
        }
        self.assertEqual(set(parameters.as_options()), expected)
        self.assertTrue(
            {item["key"] for item in catalog["parameters"]} <= set(parameters.as_dict())
        )

    def test_options_store_choices_and_snapshot_preserves_every_effective_value(self):
        catalog = load_catalog()
        preferences = catalog["appearance"]["preferences"]
        key, spec = next(
            (name, spec)
            for name, spec in preferences.items()
            if spec["configuration_surface"] == "panel"
            and spec["integer"]
            and spec["minimum"] <= spec["default"] - spec["step"]
            and spec["default"] + spec["step"] <= spec["maximum"]
        )
        factory = spec["default"]
        choice = factory + spec["step"]
        standard = factory - spec["step"]
        field = f"appearance.preferences.{key}"
        appearance = {"preferences": {key: choice}}
        standards = {field: standard}
        configuration = Configuration(
            bindings(),
            Parameters({}),
            appearance=appearance,
            field_standards=standards,
        )
        appearance["preferences"][key] = factory
        standards[field] = factory
        self.assertEqual(
            tuple(
                configuration.field_value(field, source=source)
                for source in ("active", "standard", "factory")
            ),
            (choice, standard, factory),
        )
        self.assertEqual(len({choice, standard, factory}), 3)
        with self.assertRaises(TypeError):
            configuration.field_standards[field] = factory
        saved = configuration.as_options()
        snapshot = configuration.as_snapshot()
        self.assertEqual(
            set(saved["parameters"]),
            {
                spec["key"]
                for spec in catalog["parameters"]
                if spec["configuration_surface"] != "catalog"
            },
        )
        self.assertEqual(snapshot["parameters"], configuration.parameters.as_dict())
        self.assertEqual(
            set(saved["appearance"]["preferences"]),
            {
                name
                for name, spec in preferences.items()
                if spec["configuration_surface"] == "panel"
            },
        )
        self.assertEqual(set(snapshot["appearance"]["preferences"]), set(preferences))
        self.assertEqual(snapshot["appearance"], configuration.appearance)
        self.assertEqual(saved["appearance"]["preferences"][key], choice)
        self.assertEqual(saved["field_standards"], {field: standard})
        self.assertEqual(snapshot["field_standards"], {field: standard})
        restored = Configuration.from_options(saved)
        historical = Configuration.from_options(snapshot, historical=True)
        self.assertEqual(restored, configuration)
        self.assertEqual(historical, configuration)
        saved["appearance"]["preferences"][key] = factory
        saved["field_standards"][field] = factory
        self.assertEqual(snapshot["appearance"]["preferences"][key], choice)
        self.assertEqual(snapshot["field_standards"][field], standard)
        snapshot["appearance"]["preferences"][key] = standard
        snapshot["field_standards"][field] = choice
        self.assertEqual(saved["appearance"]["preferences"][key], factory)
        self.assertEqual(saved["field_standards"][field], factory)
        updated = configuration.with_field_standard(field, choice)
        cleared = configuration.without_field_standards([field])
        self.assertEqual(updated.field_value(field, source="standard"), choice)
        self.assertEqual(cleared.field_value(field, source="standard"), factory)
        self.assertEqual(dict(cleared.field_standards), {})
        for copied in (configuration, restored, historical, updated, cleared):
            self.assertEqual(copied.appearance["preferences"][key], choice)
        for copied in (configuration, restored, historical):
            self.assertEqual(dict(copied.field_standards), {field: standard})

    def test_nullable_electricity_price_keeps_active_standard_and_factory_distinct(
        self,
    ):
        catalog = load_catalog()
        key = "electricity_price_eur_per_kwh"
        definition = BY_KEY[key]
        field = f"parameters.{key}"
        self.assertEqual(
            (
                definition.unit,
                definition.default,
                definition.optional,
                definition.step,
                definition.configuration_surface,
                definition.settings_group,
                definition.settings_subgroup,
            ),
            (
                "€/kWh",
                None,
                True,
                "any",
                "integration",
                "operation",
                "heater_accounting",
            ),
        )
        self.assertEqual(Configuration.standard_field_groups()[field], "operation")
        self.assertIn(field, Configuration.standard_fields(group="operation"))
        self.assertIn(field, Configuration.standard_fields())
        active = definition.number_step * 1.5
        standard = definition.number_step * 2.5
        self.assertTrue(0 < active < standard <= definition.maximum)
        self.assertNotEqual(active, round(active, 2))
        expected_parameters, _, _ = catalog_configuration(catalog)
        expected_parameters.pop(key)
        cases = (
            ("old_options", {}, {}, None, None),
            ("explicit_empty", {key: None}, {field: standard}, None, standard),
            ("zero", {key: 0}, {field: standard}, 0, standard),
            ("positive", {key: active}, {field: standard}, active, standard),
            ("empty_standard", {key: active}, {field: None}, active, None),
            ("zero_standard", {key: active}, {field: 0}, active, 0),
        )
        for case, parameters, standards, expected_active, expected_standard in cases:
            with self.subTest(case=case):
                options = {
                    "bindings": bindings().as_dict(),
                    "parameters": parameters,
                    "field_standards": standards,
                }
                configuration = Configuration.from_options(options)
                self.assertEqual(
                    {
                        name: configuration.parameters.values[name]
                        for name in expected_parameters
                    },
                    expected_parameters,
                )
                self.assertEqual(
                    tuple(
                        configuration.field_value(field, source=source)
                        for source in ("active", "standard", "factory")
                    ),
                    (expected_active, expected_standard, None),
                )
                for values in (
                    configuration.parameters.as_dict(),
                    configuration.parameters.as_options(),
                ):
                    self.assertIn(key, values)
                    self.assertEqual(values[key], expected_active)
                saved = configuration.as_options()
                snapshot = configuration.as_snapshot()
                for serialized in (saved, snapshot):
                    self.assertIn(key, serialized["parameters"])
                    self.assertEqual(serialized["parameters"][key], expected_active)
                    self.assertEqual(serialized["field_standards"], standards)
                for restored in (
                    Configuration.from_options(json.loads(json.dumps(saved))),
                    Configuration.from_options(
                        json.loads(json.dumps(snapshot)), historical=True
                    ),
                    Configuration.from_options(options, historical=True),
                ):
                    self.assertEqual(restored, configuration)
                emptied = configuration.with_field_values({field: None})
                self.assertIsNone(emptied.field_value(field))
                self.assertEqual(
                    emptied.field_value(field, source="standard"), expected_standard
                )
                self.assertEqual(dict(emptied.field_standards), standards)
                factory = configuration.with_field_values(
                    {field: configuration.field_value(field, source="factory")}
                ).without_field_standards([field])
                for source in ("active", "standard", "factory"):
                    self.assertIsNone(factory.field_value(field, source=source))
                self.assertNotIn(field, factory.field_standards)
                self.assertIsNone(factory.as_options()["parameters"][key])
                self.assertIsNone(factory.as_snapshot()["parameters"][key])
                self.assertEqual(dict(configuration.field_standards), standards)
                self.assertEqual(configuration.field_value(field), expected_active)

    def test_loader_keeps_intrinsic_standards_and_discards_obsolete_entries(self):
        catalog = load_catalog()
        definition = BY_KEY["nominal_power_kw"]
        active = definition.default + definition.number_step
        self.assertLessEqual(active, definition.maximum)
        configuration = Configuration(bindings(), Parameters({definition.key: active}))
        scale = catalog["appearance"]["scales"]["humidity"]
        scale_field = "appearance.scales.humidity.minimum"
        standard = configuration.appearance["scales"]["humidity"]["maximum"]
        self.assertGreaterEqual(standard, scale["minimum"])
        self.assertLessEqual(standard, scale["maximum"])
        catalog_key, catalog_spec = next(
            (name, spec)
            for name, spec in catalog["appearance"]["preferences"].items()
            if spec["configuration_surface"] == "catalog"
        )
        options = configuration.as_options()
        options["field_standards"] = {
            scale_field: standard,
            f"parameters.{definition.key}": definition.maximum + definition.number_step,
            "unknown.field": standard,
            f"appearance.preferences.{catalog_key}": catalog_spec["default"],
        }
        loaded = Configuration.from_options(options)
        self.assertEqual(dict(loaded.field_standards), {scale_field: standard})
        self.assertEqual(
            loaded.parameters.as_dict(), configuration.parameters.as_dict()
        )
        self.assertEqual(loaded.appearance, configuration.appearance)
        self.assertEqual(loaded.field_value(scale_field, source="standard"), standard)
        self.assertEqual(
            loaded.field_value(f"parameters.{definition.key}", source="standard"),
            definition.default,
        )
        self.assertEqual(loaded.parameters.values[definition.key], active)
        with self.assertRaises(ValueError):
            loaded.with_field_values({scale_field: standard})

    def test_twilight_uses_both_nondefault_endpoints(self):
        values = Parameters({}).as_dict()
        shift = BY_KEY["light_night_elevation_degrees"].number_step
        lower = values["light_night_elevation_degrees"] + shift
        upper = values["light_day_elevation_degrees"] + shift
        configured = Parameters(
            {
                "light_night_elevation_degrees": lower,
                "light_day_elevation_degrees": upper,
            }
        )
        self.assertEqual(
            normal_brightness(lower, configured), values["night_brightness_percent"]
        )
        self.assertEqual(
            normal_brightness(upper, configured), values["operation_brightness_percent"]
        )
        self.assertAlmostEqual(
            normal_brightness((lower + upper) / 2, configured),
            (
                values["night_brightness_percent"]
                + values["operation_brightness_percent"]
            )
            / 2,
        )
        with self.assertRaises(ParameterError):
            Parameters(
                {
                    "light_night_elevation_degrees": upper,
                    "light_day_elevation_degrees": lower,
                }
            )

    def test_warmup_evidence_consumes_configured_minimum_points_and_span(self):
        values = Parameters({}).as_dict()
        count = (
            values["warmup_minimum_points"]
            + BY_KEY["warmup_minimum_points"].number_step
        )
        ratio = (
            values["warmup_minimum_span_ratio"]
            + BY_KEY["warmup_minimum_span_ratio"].number_step
        )
        configured = Parameters(
            {"warmup_minimum_points": count, "warmup_minimum_span_ratio": ratio}
        )
        window = configured.seconds("warmup_estimation_minutes")
        progress = HeatingProgress(window, parameters=configured)
        for index in range(count - 1):
            self.assertIsNone(
                progress.accept(
                    T0 + timedelta(seconds=index * window * ratio / count),
                    values["light_reference_temperature_c"],
                )
            )
        self.assertIsNone(
            progress.accept(
                T0 + timedelta(seconds=window * ratio / 2),
                values["light_reference_temperature_c"],
            )
        )
        evidence = progress.accept(
            T0 + timedelta(seconds=window * ratio),
            values["light_reference_temperature_c"],
        )
        self.assertTrue(evidence["no_rise"])
        self.assertEqual(evidence["started_at"], T0)

    def test_detector_grid_controls_sample_clock_and_slope_units(self):
        values = Parameters({}).as_dict()
        grid = values["grid_seconds"] + BY_KEY["grid_seconds"].number_step
        # All dependent windows are explicit catalogue relations. This input
        # adjustment preserves validity; the clock/slope oracle is independent.
        values["grid_seconds"] = grid
        for spec in section("parameters"):
            for divisor in spec.get("multiple_of_parameters", []):
                values[spec["key"]] = (
                    math.ceil(values[spec["key"]] / values[divisor]) * values[divisor]
                )
        parameters = Parameters(values)
        detector = Detector(parameters, T0)
        sampled = []
        detector.advance(
            T0 + timedelta(seconds=grid * 2.5),
            enabled=False,
            before_sample=lambda at: sampled.append(at),
        )
        self.assertEqual(
            sampled, [T0 + timedelta(seconds=grid * index) for index in range(3)]
        )
        self.assertAlmostEqual(robust_slope([0, grid, grid * 2], step=grid), 60)

    def test_test_infrastructure_declares_positive_process_limits_once(self):
        self.assertTrue(
            all(value > 0 for value in setting("runner.timeouts_s").values())
        )
        self.assertTrue(
            all(value > 0 for value in setting("runner.job_timeouts_minutes").values())
        )
        # Neither loader contains a second copy of the executable defaults.
        settings = json.loads(Path(__file__).with_name("settings.json").read_text())
        self.assertEqual(setting("fixtures.history"), settings["fixtures"]["history"])
