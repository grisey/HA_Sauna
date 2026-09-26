"""External option writes must not leave an active runtime unreloadable."""

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from custom_components.ha_sauna import async_options_updated
from custom_components.ha_sauna.bindings import ROLES, Bindings
from custom_components.ha_sauna.core.parameters import Parameters
from custom_components.ha_sauna.core.timeline import Event, Kind
from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime


def configuration():
    bindings = {
        role.key: f"{role.domains[0]}.invalid_options_{role.key}"
        for role in ROLES
        if not role.optional
    }
    return Configuration(
        Bindings(bindings),
        Parameters({"after_run_minutes": 8, "oven_cooling_max_minutes": 15}),
    )


class _ConfigEntries:
    def __init__(self):
        self.updates = 0

    def async_update_entry(self, entry, *, options):
        self.updates += 1
        entry.options = options

    async def async_reload(self, entry_id):
        Configuration.from_options(self.entry.options)


class InvalidExternalOptionsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = datetime(2030, 1, 1, tzinfo=UTC)
        self.configuration = configuration()
        self.runtime = SaunaRuntime(self.configuration, clock=lambda: self.now)
        self.entry = SimpleNamespace(
            entry_id="invalid-options",
            runtime_data=self.runtime,
            options=self.configuration.as_options(),
        )
        config_entries = _ConfigEntries()
        config_entries.entry = self.entry
        self.hass = SimpleNamespace(config_entries=config_entries)
        await self.runtime.set_operation(True)
        session_id = self.runtime.session.session_id
        await self.runtime.receive(
            Event("door-closed", session_id, Kind.DOOR_CLOSE, self.now, self.now)
        )
        await self.runtime.receive(
            Event("gang", session_id, Kind.INFUSION, self.now, self.now)
        )
        await self.runtime.set_heater_override(False)

    async def asyncTearDown(self):
        await self.runtime.close()

    async def test_invalid_active_options_restore_everything_and_allow_a_later_live_change(
        self,
    ):
        expected_options = self.configuration.as_options()
        expected_runtime = self.runtime
        expected_session = self.runtime.session
        invalid_payloads = (
            {"parameters": {**expected_options["parameters"], "unknown_key": 1}},
            {
                "parameters": {
                    **expected_options["parameters"],
                    "after_run_minutes": 20,
                    "oven_cooling_max_minutes": 15,
                }
            },
            {"parameters": 1},
        )

        for changed in invalid_payloads:
            with self.subTest(changed=changed):
                self.entry.options = {**expected_options, **changed}
                await async_options_updated(self.hass, self.entry)
                self.assertEqual(self.entry.options, expected_options)
                self.assertIs(self.entry.runtime_data, expected_runtime)
                self.assertEqual(self.runtime.session, expected_session)
                self.assertFalse(self.runtime.reconfiguring)

        self.entry.options = {
            **expected_options,
            "parameters": {
                **expected_options["parameters"],
                "target_temperature_c": 81,
            },
        }
        await async_options_updated(self.hass, self.entry)
        self.assertIs(self.entry.runtime_data, expected_runtime)
        self.assertEqual(
            self.runtime.configuration.parameters.values["target_temperature_c"], 81
        )
        self.assertFalse(self.runtime.reconfiguring)

    async def test_invalid_options_without_runtime_remain_a_regular_reload_error(self):
        options = self.configuration.as_options()
        self.entry.runtime_data = None
        self.entry.options = {
            **options,
            "parameters": {**options["parameters"], "unknown_key": 1},
        }
        with self.assertRaisesRegex(ValueError, "unknown_parameter"):
            await async_options_updated(self.hass, self.entry)
        self.assertEqual(self.entry.options["parameters"]["unknown_key"], 1)


if __name__ == "__main__":
    unittest.main()
