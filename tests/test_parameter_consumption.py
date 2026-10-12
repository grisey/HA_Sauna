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
        key = "history_live_poll_ms"
        choice = preferences[key]["default"] + preferences[key]["step"]
        configuration = Configuration(
            bindings(), Parameters({}), appearance={"preferences": {key: choice}}
        )
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
        self.assertEqual(Configuration.from_options(saved), configuration)
        saved["appearance"]["preferences"][key] = preferences[key]["default"]
        self.assertEqual(configuration.appearance["preferences"][key], choice)
        self.assertEqual(snapshot["appearance"]["preferences"][key], choice)

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
