"""Regression coverage for overflowing temperature-program values."""

import asyncio
import json
import unittest
from types import SimpleNamespace

from custom_components.ha_sauna import async_options_updated
from custom_components.ha_sauna.bindings import ROLES, Bindings
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.program_catalog import (
    NamedTemperatureProgram,
    load_programs,
)
from custom_components.ha_sauna.core.temperature_program import temperature_steps
from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime
from custom_components.ha_sauna.settings import (
    async_set_program_catalog,
    async_set_temperature_steps,
)


def _json_number(value):
    """Model the numeric JSON round-trip used by external option writers."""
    return json.loads(json.dumps(value))


def _bindings():
    return {
        role.key: f"{role.domains[0]}.temperature_overflow_{role.key}"
        for role in ROLES
        if not role.optional
    }


class _ConfigEntries:
    def __init__(self, entry):
        self.entry = entry
        self.reloads = 0

    def async_update_entry(self, entry, *, options):
        entry.options = options

    async def async_reload(self, entry_id):
        self.reloads += 1
        self.entry.runtime_data = SaunaRuntime(
            Configuration.from_options(self.entry.options)
        )


class TemperatureOverflowTests(unittest.TestCase):
    def test_common_validators_normalize_json_integer_overflow(self):
        huge = _json_number(10**400)
        for value in (huge, -huge):
            sign = "positive" if value > 0 else "negative"
            with (
                self.subTest(validator="temperature_steps", value=sign),
                self.assertRaisesRegex(ValueError, "finite temperature"),
            ):
                temperature_steps([value])
            for field in ("start_c", "end_c"):
                values = {"start_c": 80, "end_c": 90}
                values[field] = value
                with (
                    self.subTest(validator=field, value=sign),
                    self.assertRaisesRegex(ValueError, "endliche Zahl"),
                ):
                    NamedTemperatureProgram(
                        "overflow", "Overflow", values["start_c"], values["end_c"], 2
                    )

    def test_existing_type_finite_and_range_checks_remain_value_errors(self):
        for value in (True, float("nan"), float("inf"), float("-inf"), -1, 1000):
            with (
                self.subTest(validator="temperature_steps", value=repr(value)),
                self.assertRaises(ValueError),
            ):
                temperature_steps([value])
        for value in (True, float("nan"), float("inf"), float("-inf")):
            with (
                self.subTest(validator="catalog", value=repr(value)),
                self.assertRaises(ValueError),
            ):
                NamedTemperatureProgram("invalid", "Invalid", value, 90, 2)

    def test_settings_reject_overflow_without_changing_runtime_or_options(self):
        async def exercise():
            configuration = Configuration(Bindings(_bindings()), Parameters({}))
            runtime = SaunaRuntime(configuration)
            entry = SimpleNamespace(
                runtime_data=runtime, options=configuration.as_options()
            )
            hass = SimpleNamespace(config_entries=_ConfigEntries(entry))
            before_options = entry.options
            before_configuration = runtime.configuration
            huge = _json_number(10**400)

            with self.assertRaises(ValueError):
                await async_set_temperature_steps(hass, entry, [huge])
            catalog = [
                program.as_dict()
                for program in before_configuration.temperature_programs
            ]
            for field, value in (("start_c", huge), ("end_c", -huge)):
                changed = list(catalog)
                changed[0] = {**changed[0], field: value}
                with self.subTest(field=field), self.assertRaises(ValueError):
                    await async_set_program_catalog(hass, entry, changed)

            self.assertEqual(entry.options, before_options)
            self.assertEqual(runtime.configuration, before_configuration)
            await runtime.close()

        asyncio.run(exercise())

    def test_listener_restores_last_valid_options_without_a_reload(self):
        async def exercise():
            configuration = Configuration(Bindings(_bindings()), Parameters({}))
            runtime = SaunaRuntime(configuration)
            entry = SimpleNamespace(
                entry_id="temperature-overflow",
                runtime_data=runtime,
                options=configuration.as_options(),
            )
            config_entries = _ConfigEntries(entry)
            hass = SimpleNamespace(config_entries=config_entries)
            expected_options = entry.options
            huge = _json_number(10**400)
            entry.options = {**expected_options, "temperature_steps": [huge]}

            await async_options_updated(hass, entry)

            self.assertEqual(entry.options, expected_options)
            self.assertIs(entry.runtime_data, runtime)
            self.assertFalse(runtime.reconfiguring)
            self.assertEqual(config_entries.reloads, 0)

            entry.options = {
                **expected_options,
                "parameters": {
                    **expected_options["parameters"],
                    "target_temperature_c": 81,
                },
            }
            await async_options_updated(hass, entry)
            self.assertEqual(
                runtime.configuration.parameters.values["target_temperature_c"], 81
            )
            await config_entries.async_reload(entry.entry_id)
            await entry.runtime_data.set_operation(True)
            self.assertTrue(entry.runtime_data.session.operation_enabled)
            await entry.runtime_data.close()

        asyncio.run(exercise())

    def test_catalog_loading_normalizes_overflow(self):
        huge = _json_number(10**400)
        with self.assertRaises(ValueError):
            load_programs(
                [
                    {
                        "id": "overflow",
                        "name": "Overflow",
                        "start_c": 80,
                        "end_c": huge,
                        "distribution_gangs": 2,
                    }
                ],
                minimum_c=60,
                maximum_c=100,
            )


if __name__ == "__main__":
    unittest.main()
