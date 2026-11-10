"""E2: functional settings order, editing places and the three value levels."""

import unittest

from test_foundation import bindings

from custom_components.ha_sauna.core import notification_rules
from custom_components.ha_sauna.core.defaults import section
from custom_components.ha_sauna.core.parameters import (
    BY_KEY,
    EDITABLE_DEFINITIONS,
    LIVE_TEMPERATURE_KEYS,
)
from custom_components.ha_sauna.presentation import public_state
from custom_components.ha_sauna.runtime import Configuration


class SettingsOrderTests(unittest.TestCase):
    def test_groups_follow_function_with_technical_values_last_and_collapsed(self):
        groups = section("frontend")["settings_groups"]
        self.assertEqual(
            [group["id"] for group in groups],
            ["programs", "temperature", "light", "installation", "control",
             "monitoring", "recognition", "notifications", "appearance",
             "personal", "maintenance", "technical"],
        )
        technical = groups[-1]
        self.assertTrue(technical["collapsed"])
        self.assertEqual(
            [group["id"] for group in groups if group.get("collapsed")], ["technical"]
        )
        catalog_only = {d.key for d in EDITABLE_DEFINITIONS
                        if d.configuration_surface == "catalog"}
        self.assertEqual(
            {d.key for d in EDITABLE_DEFINITIONS if d.settings_group == "technical"},
            catalog_only | {"warmup_estimation_minutes", "history_context_seconds"},
        )
        # Placeholder for notification settings: no own keys in this package.
        self.assertFalse(
            [d.key for d in EDITABLE_DEFINITIONS if d.settings_group == "notifications"]
        )

    def test_regularly_used_and_installation_values_have_functional_places(self):
        places = {
            "preset_start_c": ("programs", "temperature_choices"),
            "standard_temperature_c": ("temperature", "temperature_defaults"),
            "button_hold_seconds": ("temperature", "session_controls"),
            "operation_brightness_percent": ("light", "light_curve"),
            "nominal_power_kw": ("installation", "heater_accounting"),
            "electricity_price_eur_per_kwh": ("installation", "heater_accounting"),
            "cabin_volume_m3": ("installation", "heater_accounting"),
            "thermostat_cooldown_minutes": ("control", "temperature_control"),
            "minimum_heating_minutes": ("control", "temperature_control"),
            "after_run_minutes": ("control", "oven_cooling"),
            "session_gap_minutes": ("control", "session_resume"),
            "sensor_timeout_seconds": ("monitoring", "signals"),
            "fault_confirmation_seconds": ("monitoring", "heater_feedback"),
            "presence_fallback_delay_seconds": ("recognition", "presence_direct"),
            "confirmation_minutes": ("recognition", "presence_evaluation"),
        }
        for key, place in places.items():
            with self.subTest(key=key):
                definition = BY_KEY[key]
                self.assertEqual(
                    (definition.settings_group, definition.settings_subgroup), place
                )

    def test_live_temperature_choice_stays_outside_standards_scopes(self):
        groups = Configuration.standard_field_groups()
        for key in LIVE_TEMPERATURE_KEYS:
            self.assertNotIn(f"parameters.{key}", groups)
        for key in ("preset_start_c", "preset_step_c", "preset_count"):
            self.assertEqual(groups[f"parameters.{key}"], "programs")
        self.assertEqual(
            set(Configuration.standard_fields(group="programs")),
            {"parameters.preset_start_c", "parameters.preset_step_c",
             "parameters.preset_count"},
        )

    def test_field_states_compare_active_value_with_standard_or_factory(self):
        options = {
            "bindings": bindings().as_dict(),
            "parameters": {"minimum_heating_minutes": 12, "thermostat_cooldown_minutes": 7},
            "field_standards": {
                "parameters.minimum_heating_minutes": 12,
                "parameters.after_run_minutes": 6,
            },
        }
        states = Configuration.from_options(options).field_states()
        self.assertEqual(set(states), set(Configuration.standard_field_groups()))
        self.assertEqual(
            states["parameters.minimum_heating_minutes"],
            {"reference": "standard", "reference_value": 12, "differs": False},
        )
        self.assertEqual(
            states["parameters.after_run_minutes"],
            {"reference": "standard", "reference_value": 6,
             "differs": BY_KEY["after_run_minutes"].default != 6},
        )
        self.assertEqual(
            states["parameters.thermostat_cooldown_minutes"],
            {"reference": "factory",
             "reference_value": BY_KEY["thermostat_cooldown_minutes"].default,
             "differs": True},
        )
        self.assertEqual(
            states["parameters.electricity_price_eur_per_kwh"],
            {"reference": "factory", "reference_value": None, "differs": False},
        )
        self.assertFalse(states["appearance.instruments.default"]["differs"])

    def test_notification_settings_compare_with_factory_values_only(self):
        # BN settings have no saved standard: factory value and active value.
        rules = notification_rules
        self.assertEqual(rules.factory_deviations(rules.NotificationSettings()), {})
        changed = rules.NotificationSettings.from_dict({
            "occasions": {"ready": False, "safety": True},
            "timer_runtime_minutes": 180,
            "overtemperature_c": rules.VALUES["overtemperature_c"][2],
        })
        self.assertEqual(
            rules.factory_deviations(changed),
            {
                "timer_runtime_minutes": rules.VALUES["timer_runtime_minutes"][2],
                "occasions": {"ready": True},
            },
        )

    def test_field_states_are_administrator_data(self):
        state = {
            "configuration": {"parameters": {}},
            "parameters": [],
            "measurements": [],
            "field_states": {"parameters.nominal_power_kw": {"differs": True}},
        }
        self.assertNotIn("field_states", public_state(state))


if __name__ == "__main__":
    unittest.main()
