"""Required, bound acceptance fixtures. Linux plus HA, no private input switch."""

import asyncio
import filecmp
import gzip
import io
import json
import os
import platform
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from custom_components.ha_sauna.core.controller import Controller
from custom_components.ha_sauna.core.input_handoff import InputTiming, TemperatureInput

from runner import ORIGIN, TRACE_CASES, SessionRun, load_manifest


def _write_timeline(path, snapshots):
    with path.open("wb") as output:
        with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline="\n") as stream:
                for row in snapshots:
                    stream.write(json.dumps(row, ensure_ascii=False,
                                            sort_keys=True, allow_nan=False) + "\n")


class CompleteSessionTests(unittest.TestCase):
    def test_consumer_error_dominates_evidence_error(self):
        self.assertEqual(platform.system(), "Linux", "Session tests run on Linux")
        fixture = next(case for case in load_manifest("scenario_manifest.json")
                       if case["base_id"] == "G01")
        value = TemperatureInput(None, None, InputTiming(None, None, None, ORIGIN))
        for fails in (True, False):
            with self.subTest(consumer_fails=fails):
                consumer_error = ValueError("consumer failure") if fails else None
                evidence_error = RuntimeError("evidence failure")
                with patch.object(Controller, "consume_temperature_input",
                                  side_effect=consumer_error) as original:
                    run = SessionRun(fixture)
                    self.addCleanup(run._temporary.cleanup)
                    self.assertIsNotNone(run.producer_controller_trace)
                    with patch.object(run, "_record_controller_input",
                                      side_effect=evidence_error):
                        expected = consumer_error if fails else evidence_error
                        with self.assertRaises(type(expected)) as raised:
                            run.runtime.controller.consume_temperature_input(value)
                    self.assertIs(raised.exception, expected)
                    original.assert_called_once_with(value)
                    if fails:
                        self.assertIn("RuntimeError: evidence failure",
                                      "\n".join(consumer_error.__notes__))

    def test_derived_sessions(self):
        self._exercise("scenario_manifest.json")

    def test_measured_sessions(self):
        self._exercise("replay_manifest.json")

    def _exercise(self, manifest):
        self.assertEqual(platform.system(), "Linux", "Session tests run on Linux")
        cases = load_manifest(manifest)
        selected = os.environ.get("SAUNA_SESSION_CASE")
        if selected:
            wanted = set(selected.split(","))
            known = cases + load_manifest("replay_manifest.json" if manifest == "scenario_manifest.json"
                                          else "scenario_manifest.json")
            ids = {c["id"] for c in known} | {c["base_id"] for c in known}
            self.assertFalse(wanted - ids, f"Unknown selected session: {wanted - ids}")
            cases = [case for case in cases if case["id"] in wanted
                     or case.get("base_id") in wanted]
            if not cases:
                return  # The other manifest owns the explicitly selected case.
        evidence = os.environ.get("SAUNA_SESSION_EVIDENCE")
        for case in cases:
            with self.subTest(session=case["id"]):
                print(f"\nSESSION {case['id']} BEGIN", flush=True)
                run = SessionRun(case)
                results, failure = [], None
                try:
                    expects_trace = case.get("base_id", case["id"]) in TRACE_CASES
                    if expects_trace:
                        self.assertIsNotNone(run.producer_controller_trace,
                                             f"Missing trace for bound fixture {case['id']}")
                    asyncio.run(run.run())
                    if expects_trace:
                        trace = run.producer_controller_trace
                        self.assertGreater(trace["call_counts"]["total"], 0)
                        self.assertGreater(trace["call_counts"]["recorded"], 0)
                        self.assertTrue(trace["rows"])
                        self.assertTrue(trace["scope"]["runtime_consumer_connected"])
                    results = run.evaluate()
                except Exception as error:
                    failure = f"{type(error).__name__}: {error}"
                    raise
                finally:
                    if evidence:
                        folder = Path(evidence)
                        folder.mkdir(parents=True, exist_ok=True)
                        (folder / f"{case['id']}.json").write_text(json.dumps({
                            "id": case["id"], "evaluations": results, "error": failure,
                            "actions": run.action_results, "calls": run.calls,
                            "notifications": run.notifications,
                            "archive_session_count": getattr(run, "archive_session_count", None),
                            "archived_sessions": getattr(run, "archived_sessions", []),
                            "archived_preheat_roles": getattr(run, "archived_preheat_roles", []),
                            "archived_preheat_comparison": getattr(run, "archived_preheat_comparison", None),
                            "recording_history_errors": run.recording_history_errors,
                            "input_transitions": run.input_transitions,
                            "completed_sessions": getattr(run, "final_sessions", []),
                            "consumer_events": getattr(run, "consumer_events", []),
                            "detections": getattr(run, "detections", []),
                            "historical_output_comparison": run.output_comparison(),
                            **({"producer_controller_trace": run.producer_controller_trace}
                               if run.producer_controller_trace is not None else {}),
                        }, ensure_ascii=False, indent=2, sort_keys=True,
                            allow_nan=False) + "\n", encoding="utf-8")
                        timeline = folder / f"{case['id']}.timeline.jsonl.gz"
                        _write_timeline(timeline, run.snapshots)
                        if failure is None and case.get("base_id", case["id"]) == "G00":
                            self.assertTrue(run.snapshots)
                            with TemporaryDirectory() as temporary:
                                repeated = Path(temporary) / "different-name.jsonl.gz"
                                _write_timeline(repeated, run.snapshots)
                                identical = filecmp.cmp(timeline, repeated, shallow=False)
                                self.assertTrue(identical)
                                headers, mtimes, filename_flags = [], [], []
                                for written in (timeline, repeated):
                                    with written.open("rb") as source:
                                        header = source.read(10)
                                    self.assertEqual(header[:3], b"\x1f\x8b\x08")
                                    self.assertEqual(header[3] & 8, 0)
                                    self.assertEqual(header[4:8], b"\x00" * 4)
                                    headers.append(header.hex())
                                    mtimes.append(int.from_bytes(header[4:8], "little"))
                                    filename_flags.append(bool(header[3] & 8))
                                    with gzip.open(written, "rt", encoding="utf-8") as source:
                                        for expected, line in zip(run.snapshots, source, strict=True):
                                            self.assertEqual(json.loads(line), expected)
                                observation = {
                                    "case": case["id"], "compressed_bytes_equal": identical,
                                    "gzip_headers_hex": headers, "mtime": mtimes,
                                    "filename_header_present": filename_flags,
                                    "original_rows_equal_in_both_outputs": len(run.snapshots),
                                }
                            (folder / f"{case['id']}.timeline-serialization.json").write_text(
                                json.dumps(observation, indent=2, sort_keys=True,
                                           allow_nan=False) + "\n", encoding="utf-8")
                    print(f"SESSION {case['id']} END ({failure or 'evaluated'})", flush=True)
                failures = [r for r in results if not r["passed"]]
                self.assertFalse(failures, json.dumps(failures, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    unittest.main()
