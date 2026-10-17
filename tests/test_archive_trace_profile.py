"""Full detector traces are opt-in; live and semantic evidence stay identical."""

import json
import tempfile
import unittest
import zipfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from custom_components.ha_sauna.archive import Archive, encoded, plain
from custom_components.ha_sauna.core.models import Position, Quantity
from custom_components.ha_sauna.core.timeline import Kind
from custom_components.ha_sauna.runtime import Configuration, SaunaRuntime
from test_detector import measurement
from test_foundation import T0, bindings, event, parameters


class ArchiveTraceProfileTests(unittest.IsolatedAsyncioTestCase):
    def test_archive_profile_is_explicit_and_strict(self):
        path = Path("unused-profile.sqlite")
        self.assertEqual(Archive(path, "entry").trace_profile, "normal")
        for profile in ("normal", "analysis"):
            self.assertEqual(
                Archive(path, "entry", trace_profile=profile).trace_profile, profile,
            )
        for profile in (None, True, "", "debug", "ANALYSIS"):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                Archive(path, "entry", trace_profile=profile)

    async def test_default_skips_only_full_traces_with_identical_live_and_native_evidence(self):
        async def capture(path, *, profile=None):
            clock = [T0]
            runtime = SaunaRuntime(Configuration(bindings(), parameters()), lambda: clock[0])
            options = {} if profile is None else {"trace_profile": profile}
            await runtime.start_archive(path, "entry", **options)
            self.assertEqual(runtime.archive.trace_profile, profile or "normal")
            try:
                # Stable control identities make every semantic payload comparable.
                identities = (UUID(int=index) for index in range(1, 1000))
                with patch("custom_components.ha_sauna.core.controller.uuid4", side_effect=identities):
                    runtime.controller.set_temperature(90, T0)
                    runtime.controller.begin_session("profile", T0)
                    runtime.controller.report_contactor(True, T0)
                    runtime.controller.report_heating(True, T0)
                    runtime._sync_detector()
                    live = []
                    observed = runtime.detector.observer

                    def observe(trace):
                        live.append(plain(trace))
                        observed(trace)

                    runtime.detector.observer = observe
                    # Existing joint-fall fixture used by the native input batch test.
                    for second in range(39):
                        clock[0] = T0 + timedelta(seconds=second)
                        fall = max(0, second - 15)
                        temperature, humidity = 90 - .08 * fall, 20 - .06 * fall
                        runtime.controller.set_temperature(temperature, clock[0])
                        for position in (Position.UPPER, Position.LOWER):
                            for quantity, value in (
                                (Quantity.TEMPERATURE, temperature),
                                (Quantity.HUMIDITY, humidity),
                            ):
                                original = measurement(position, quantity, value, second, measured=clock[0])
                                runtime.detector.accept(original)
                                runtime.archive.append("measurement", clock[0], original, "profile")
                        runtime._deliver_detection(clock[0])
                        runtime.persist()
                    self.assertIn(Kind.DOOR_OPEN, [item.kind for item in runtime.session.timeline.processed])
                    self.assertTrue(live)
                    self.assertEqual(plain(runtime.detector.diagnostic), live[-1])
                    # Existing confirmed-gang/cooling sequence also exercises its
                    # native assignments, confirmation, phases and heat decisions.
                    for second, kind in (
                        (40, Kind.DOOR_CLOSE), (41, Kind.PERSON_STRONG),
                        (42, Kind.INFUSION), (70, Kind.DOOR_OPEN), (71, Kind.VENTILATION),
                    ):
                        clock[0] = T0 + timedelta(seconds=second)
                        runtime._process_event(event(kind.value, kind, second, session="profile"))
                        runtime.persist()
                    runtime.controller.report_contactor(False, clock[0])
                    runtime.controller.report_heating(False, clock[0])
                    self.assertEqual(runtime.session.timeline.gang_count, 1)
                    self.assertIsNotNone(runtime.session.after_run)
                    runtime.device = SimpleNamespace(faults={"heater_service_unavailable": "confirmed"})
                    runtime.persist()
                    state = plain(runtime.session)
                await runtime.archive.flush()
                stored = runtime.archive.read("profile", limit=10000, now=clock[0])
                self.assertIsNone(stored["next_after"])
                records = stored["records"]
                traces = [item["payload"] for item in records if item["kind"] == "detector_trace"]
                self.assertEqual(traces, live if profile == "analysis" else [])
                kinds = {item["kind"] for item in records}
                self.assertTrue({"measurement", "detection", "consumer_event", "session",
                                 "phase", "decision", "diagnostic"} <= kinds)
                self.assertEqual(sum(item["kind"] == "measurement" for item in records), 39 * 4)
                self.assertTrue(any(item["kind"] == "diagnostic" and item["payload"] == {
                    "faults": {"heater_service_unavailable": "confirmed"},
                } for item in records))
                self.assertNotIn("trace_profile", stored["session"]["configuration"])
                semantic = [(item["kind"], item["received_at"], item["payload"])
                            for item in records if item["kind"] != "detector_trace"]
                return live, plain(runtime.session.timeline.processed), state, semantic
            finally:
                await runtime.archive.close()

        with tempfile.TemporaryDirectory() as directory:
            normal = await capture(Path(directory) / "normal.sqlite")
            analysis = await capture(Path(directory) / "analysis.sqlite", profile="analysis")
        self.assertEqual(normal, analysis)

    async def test_explicit_historical_trace_survives_normal_reopen_and_export(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite"
            archive = Archive(path, "entry")
            await archive.start()
            runtime = SaunaRuntime(Configuration(bindings(), parameters()), lambda: T0)
            runtime.controller.begin_session("historical", T0)
            trace = {"at": T0, "signals": [Kind.DOOR_OPEN],
                     "metrics": {"upper": {"door_temperature_slope": -.123456789012345}}}
            expected = json.loads(encoded(trace))
            try:
                archive.save_session(runtime.session, T0, runtime.configuration.as_snapshot())
                # Explicit imported records remain supported independently of
                # the runtime's policy for newly generated full diagnostics.
                archive.append("detector_trace", T0, trace, "historical")
                await archive.flush()
            finally:
                await archive.close()
            archive = Archive(path, "entry")
            await archive.start()
            try:
                stored = archive.read("historical", now=T0)
                self.assertEqual([item["payload"] for item in stored["records"]
                                  if item["kind"] == "detector_trace"], [expected])
                exported = await archive.export()
                try:
                    with zipfile.ZipFile(exported) as bundle:
                        rows = [json.loads(line) for line in bundle.read("records.jsonl").splitlines()]
                    self.assertEqual([item["payload"] for item in rows
                                      if item["kind"] == "detector_trace"], [expected])
                finally:
                    exported.unlink()
            finally:
                await archive.close()

    async def test_analysis_encoding_failure_is_logged_and_keeps_fault_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = SaunaRuntime(Configuration(bindings(), parameters()), lambda: T0)
            await runtime.start_archive(Path(directory) / "invalid.sqlite", "entry", trace_profile="analysis")
            try:
                runtime.controller.begin_session("invalid", T0)
                runtime._sync_detector()
                with patch.object(runtime.log, "error") as error:
                    runtime.detector.observer({"at": T0, "value": float("nan")})
                error.assert_called_once()
                self.assertEqual(error.call_args.args[0], "detector_trace_invalid")
                runtime.device = SimpleNamespace(faults={"heater_service_unavailable": "confirmed"})
                runtime.persist()
                await runtime.archive.flush()
                stored = runtime.archive.read("invalid", now=T0)
                self.assertFalse(any(item["kind"] == "detector_trace" for item in stored["records"]))
                self.assertTrue(any(item["kind"] == "diagnostic" and item["payload"] == {
                    "faults": {"heater_service_unavailable": "confirmed"},
                } for item in stored["records"]))
            finally:
                await runtime.archive.close()
