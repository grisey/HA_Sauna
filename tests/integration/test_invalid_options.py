"""Real HA ConfigEntry handling for malformed external option writes."""

import unittest
from datetime import UTC, datetime

from harness import create_sauna, start_hass

from custom_components.ha_sauna.core.timeline import Event, Kind
from custom_components.ha_sauna.runtime import Configuration


class InvalidExternalOptionsIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass, self.temp = await start_hass()
        self.entry = await create_sauna(self.hass)

    async def asyncTearDown(self):
        await self.hass.async_block_till_done()
        await self.hass.async_stop(force=True)
        self.temp.cleanup()

    async def test_invalid_external_options_restore_active_runtime_and_saved_entry(
        self,
    ):
        runtime = self.entry.runtime_data
        now = datetime.now(UTC)
        runtime._clock = lambda: now
        await runtime.set_operation(True)
        session_id = runtime.session.session_id
        await runtime.receive(
            Event("door-closed", session_id, Kind.DOOR_CLOSE, now, now)
        )
        await runtime.receive(Event("gang", session_id, Kind.INFUSION, now, now))
        await runtime.set_heater_override(False)
        runtime.controller.report_heating(True, now)

        expected_options = dict(self.entry.options)
        expected_session = runtime.session
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
                self.hass.config_entries.async_update_entry(
                    self.entry, options={**expected_options, **changed}
                )
                await self.hass.async_block_till_done()
                self.assertEqual(dict(self.entry.options), expected_options)
                self.assertIs(self.entry.runtime_data, runtime)
                self.assertEqual(runtime.session, expected_session)
                self.assertFalse(runtime.reconfiguring)

        self.hass.config_entries.async_update_entry(
            self.entry,
            options={
                **expected_options,
                "parameters": {
                    **expected_options["parameters"],
                    "target_temperature_c": 81,
                },
            },
        )
        await self.hass.async_block_till_done()
        self.assertIs(self.entry.runtime_data, runtime)
        self.assertEqual(
            runtime.configuration.parameters.values["target_temperature_c"], 81
        )
        self.assertFalse(runtime.reconfiguring)

        await runtime.set_operation(False)
        self.assertFalse(runtime.session.operation_enabled)
        await self.hass.config_entries.async_reload(self.entry.entry_id)
        await self.hass.async_block_till_done()
        reloaded = self.entry.runtime_data
        self.assertEqual(
            Configuration.from_options(self.entry.options), reloaded.configuration
        )
        await reloaded.set_operation(True)
        self.assertTrue(reloaded.session.operation_enabled)


if __name__ == "__main__":
    unittest.main()
