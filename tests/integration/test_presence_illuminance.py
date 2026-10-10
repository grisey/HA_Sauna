"""Illuminance keeps original HA reports in the session archive only."""
import asyncio
import json
import sqlite3
import unittest
import zipfile
from contextlib import closing
from pathlib import Path

from harness import create_sauna, retain_session, start_hass


class PresenceIlluminanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass, self.temp = await start_hass()
        self.source = "sensor.presence_illuminance"
        self.attrs = {"device_class": "illuminance", "unit_of_measurement": "lx"}
        self.hass.states.async_set(self.source, "12.75", self.attrs)
        self.entry = await create_sauna(
            self.hass, binding_overrides={"presence_illuminance": self.source},
            parameter_overrides={"sensor_timeout_seconds": 60},
        )
        self.runtime = self.entry.runtime_data

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self.temp.cleanup()

    async def readings(self):
        await self.runtime.archive.flush()
        with closing(sqlite3.connect(self.runtime.archive.path)) as db:
            return [(session, json.loads(payload)) for session, payload in db.execute(
                "SELECT session_id,payload FROM records WHERE kind='illuminance' ORDER BY id"
            )]

    async def test_session_snapshot_reports_and_export_preserve_original_values(self):
        old = self.hass.states.get(self.source)
        await self.runtime.set_operation(True)
        retain_session(self.runtime)
        session_id = self.runtime.session.session_id
        first = (await self.readings())[0]
        self.assertEqual(first[0], session_id)
        self.assertEqual(first[1]["state"], old.state)
        self.assertTrue(first[1]["session_start_snapshot"])
        for key in ("last_changed", "last_updated", "last_reported"):
            self.assertEqual(first[1][key], getattr(old, key).isoformat())
        self.runtime.persist()
        self.assertEqual(len(await self.readings()), 1)
        originals = []
        for value in ("0", "123.456", "unknown", "unavailable", "123.456"):
            self.hass.states.async_set(self.source, value, self.attrs, force_update=True)
            originals.append(self.hass.states.get(self.source))
            await self.hass.async_block_till_done()
        records = await self.readings()
        self.assertEqual(len(records), len(originals) + 1)
        for (identity, payload), state in zip(records[1:], originals, strict=True):
            self.assertEqual(identity, session_id)
            self.assertEqual(payload["source"], self.source)
            self.assertEqual(payload["state"], state.state)
            self.assertEqual(payload["attributes"], dict(state.attributes))
            self.assertEqual(payload["last_reported"], state.last_reported.isoformat())
            self.assertFalse(payload["session_start_snapshot"])
        exported = await self.runtime.archive.export()
        try:
            with zipfile.ZipFile(exported) as archive:
                reports = [json.loads(line) for line in archive.read("records.jsonl").splitlines()]
                saved = [record["payload"] for record in reports if record["kind"] == "illuminance"]
                self.assertEqual(saved, [payload for _, payload in records])
        finally:
            Path(exported).unlink()

    async def test_physical_start_before_queued_report_preserves_start_snapshot(self):
        now = self.runtime._clock()
        self.runtime._clock = lambda: now
        old = self.hass.states.get(self.source)
        control = self.runtime.device.bindings["control_input"]

        async def both_received():
            while len(self.runtime._pending_device_inputs) < 2:
                await asyncio.sleep(0)

        # Queue actual HA state events behind the runtime lock so both are
        # consumed in one FIFO drain before the cycle persists its state.
        async with self.runtime._lock:
            self.hass.states.async_set(
                control, now.isoformat(),
                {**self.hass.states.get(control).attributes, "event_type": "single_push"},
            )
            self.hass.states.async_set(self.source, "37.125", self.attrs)
            reported = self.hass.states.get(self.source)
            await asyncio.wait_for(both_received(), 3)
            self.assertEqual(
                [event.data["entity_id"]
                 for _, event in self.runtime._pending_device_inputs],
                [control, self.source],
            )
        await self.hass.async_block_till_done()
        self.assertTrue(self.runtime.session.operation_enabled)
        retain_session(self.runtime)
        records = await self.readings()
        self.assertEqual(len(records), 2)
        for (session_id, payload), state, snapshot in zip(
            records, (old, reported), (True, False), strict=True,
        ):
            self.assertEqual(session_id, self.runtime.session.session_id)
            self.assertEqual(payload["source"], self.source)
            self.assertEqual(payload["state"], state.state)
            self.assertEqual(payload["attributes"], dict(state.attributes))
            self.assertEqual(payload["session_start_snapshot"], snapshot)
            for key in ("last_changed", "last_updated", "last_reported"):
                self.assertEqual(payload[key], getattr(state, key).isoformat())

    async def test_no_session_no_recording_and_no_light_control_input(self):
        now = self.runtime._clock()
        self.runtime._clock = lambda: now
        device = self.runtime.device
        before = (self.runtime.configuration.as_options(), dict(device.measurements),
                  device.light_observation, self.runtime.controller.control_mode)
        for value in ("0", "65000", "unknown", "unavailable"):
            self.hass.states.async_set(self.source, value, self.attrs)
            await self.hass.async_block_till_done()
        self.assertIsNone(self.runtime.session)
        self.assertEqual(await self.readings(), [])
        after = (self.runtime.configuration.as_options(), dict(device.measurements),
                 device.light_observation, self.runtime.controller.control_mode)
        self.assertEqual(after, before)
        self.assertNotIn("presence_illuminance", device.faults)
        self.assertNotIn("presence_illuminance", device.measurements)
