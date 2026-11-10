"""E2a: retired settings are discarded on load; archived configurations stay readable."""

import tempfile
import unittest
from copy import deepcopy
from datetime import timedelta
from functools import partial
from pathlib import Path

from test_foundation import T0, bindings

from custom_components.ha_sauna.archive import Archive, plain
from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.defaults import section
from custom_components.ha_sauna.core.parameters import (
    BY_KEY,
    LEGACY_PARAMETER_KEYS,
    Parameters,
)
from custom_components.ha_sauna.runtime import Configuration

# K1: values of the former curve recognition without any consumer.
RETIRED = frozenset({
    "curve_huber_delta", "curve_max_candidates", "curve_max_points",
    "curve_min_side_samples", "curve_original_background_residual",
    "curve_original_background_slope", "curve_original_continuation_contrast",
    "curve_original_gap_humidity_lower_seconds", "curve_original_gap_humidity_upper_seconds",
    "curve_original_gap_temperature_lower_seconds",
    "curve_original_gap_temperature_upper_seconds", "curve_original_kink_factor",
    "curve_original_kink_history_windows", "curve_original_new_infusion_contrast",
    "curve_original_noise_humidity_lower", "curve_original_noise_humidity_upper",
    "curve_original_noise_temperature_lower", "curve_original_noise_temperature_upper",
    "curve_original_open_contrast", "curve_original_samples", "curve_original_significance",
    "curve_robust_iterations", "curve_window_seconds", "humidity_change_absolute_tolerance",
    "median_seconds",
})
# K5: fixed zoom and pan behaviour of the history.
FIXED_PREFERENCES = (
    "history_wheel_zoom_coefficient", "history_keyboard_pan_fraction",
    "history_zoom_factor", "history_max_zoom",
)


def options(parameters=None, **extra):
    return {"bindings": bindings().as_dict(), "parameters": parameters or {}, **extra}


class RetiredSettingsTests(unittest.IsolatedAsyncioTestCase):
    def test_retired_curve_values_are_accepted_and_discarded(self):
        self.assertEqual(len(RETIRED), 25)
        self.assertTrue(RETIRED <= LEGACY_PARAMETER_KEYS)
        self.assertTrue(RETIRED.isdisjoint(BY_KEY))
        self.assertTrue(RETIRED.isdisjoint(
            spec["key"] for spec in section("legacy_parameters")))
        saved = {**dict.fromkeys(RETIRED, 1.5), "after_run_minutes": 7}
        parameters = Parameters(saved)
        self.assertEqual(parameters.values["after_run_minutes"], 7)
        self.assertTrue(RETIRED.isdisjoint(parameters.as_dict()))
        self.assertTrue(RETIRED.isdisjoint(parameters.as_options()))
        self.assertEqual(parameters, Parameters({"after_run_minutes": 7}))
        for historical in (False, True):
            with self.subTest(historical=historical):
                configuration = Configuration.from_options(
                    options(saved), historical=historical
                )
                for serialized in (configuration.as_options(), configuration.as_snapshot()):
                    self.assertTrue(RETIRED.isdisjoint(serialized["parameters"]))
                    self.assertEqual(serialized["parameters"]["after_run_minutes"], 7)

    def test_catalog_fields_without_consumer_are_gone(self):
        for name in ("parameters", "legacy_parameters"):
            for spec in section(name):
                with self.subTest(key=spec["key"]):
                    self.assertNotIn("group", spec)
                    self.assertNotIn("expert", spec)

    def test_fixed_history_preferences_ignore_saved_values_and_standards(self):
        catalog = section("appearance")["preferences"]
        saved = {key: catalog[key]["default"] * 2 for key in FIXED_PREFERENCES}
        standards = {f"appearance.preferences.{key}": value for key, value in saved.items()}
        configuration = Configuration.from_options(options(
            appearance={"preferences": saved}, field_standards=standards,
        ))
        for key in FIXED_PREFERENCES:
            with self.subTest(key=key):
                self.assertEqual(catalog[key]["configuration_surface"], "catalog")
                self.assertEqual(configuration.appearance["preferences"][key],
                                 catalog[key]["default"])
                self.assertNotIn(key, configuration.as_options()["appearance"].get(
                    "preferences", {}))
                self.assertNotIn(f"appearance.preferences.{key}",
                                 configuration.field_standards)
                self.assertNotIn(f"appearance.preferences.{key}",
                                 Configuration.standard_field_groups())

    async def test_archived_configuration_with_retired_values_stays_readable(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        archive = Archive(Path(directory.name) / "archive.sqlite", "entry")
        await archive.start()
        self.addAsyncCleanup(archive.close)
        current = Configuration.from_options(options()).as_snapshot()
        # Snapshot as written before E2a: the retired values were part of it.
        earlier = deepcopy(current)
        earlier["parameters"].update(dict.fromkeys(RETIRED, 2.0))
        for identity, configuration in (("before", earlier), ("after", current)):
            controller = Controller(Parameters({}))
            controller.begin_session(identity, T0)
            snapshot = plain(controller.session)
            snapshot["timeline"]["session_id"] = identity
            snapshot["configuration"] = configuration
            archive.append("session", T0 + timedelta(seconds=1), snapshot, identity)
        await archive.flush()
        read = partial(archive.read, now=T0 + timedelta(hours=1))
        stored = read("before")["session"]["configuration"]
        self.assertEqual(stored, earlier)
        loaded = Configuration.from_options(stored, historical=True)
        self.assertEqual(loaded.as_snapshot(), current)
        self.assertEqual(read("after")["session"]["configuration"], current)


if __name__ == "__main__":
    unittest.main()
