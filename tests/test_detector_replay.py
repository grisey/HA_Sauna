"""Chronology, bound originals and observational neutrality of detector replays."""

from collections import deque
from copy import deepcopy
from datetime import datetime, timedelta
import gzip
import importlib.util
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "detector_replay_under_test", ROOT / "tests/analysis/detector_replay.py",
)
REPLAY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPLAY)


class DetectorReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.modules, _ = REPLAY.load_owners()
        settings = json.loads((ROOT / "tests/settings.json").read_bytes())
        cls.origin = datetime.fromisoformat(settings["fixtures"]["session"]["origin"])
        cls.parameters = cls.modules["parameters"].Parameters({}).as_dict()

    def test_bound_measurements_cover_sections_once_and_keep_real_gaps(self):
        loaded = REPLAY.load_inputs(REPLAY.DEFAULT_MANIFEST)
        existing = json.loads((ROOT / "tests/replay_manifest.json").read_bytes())
        self.assertFalse(set(loaded["sessions"]) & {e["id"] for e in existing["sessions"]})
        for identity, session in loaded["sessions"].items():
            with self.subTest(recording=identity):
                fixture = loaded["fixtures"][identity]
                self.assertEqual(fixture["kind"], "measurement_recording")
                self.assertFalse(fixture["actions"])
                self.assertFalse(fixture["expectations"])
                self.assertEqual(fixture["configuration"], {"parameter_source": "current_catalog"})
                points = [p for series in session["original_series"].values() for p in series]
                self.assertTrue(all(p["origin"] == "samples" for p in points))
                self.assertEqual(len(points), fixture["samples_count"])
                self.assertEqual(len({p["source_index"] for p in points}), len(points))
                previous = None
                selected_indices = []
                for section in fixture["recording_sections"]:
                    selected = [p for p in points
                                if section["first_sample_s"] <= p["t"] <= section["last_sample_s"]]
                    self.assertEqual(len(selected), section["samples_count"])
                    selected_indices.extend(p["source_index"] for p in selected)
                    if previous:
                        self.assertAlmostEqual(
                            section["start_s"] - previous["archived_end_s"],
                            section["gap_from_previous_end_s"], places=6,
                        )
                        self.assertFalse(any(previous["last_sample_s"] < p["t"]
                                             < section["first_sample_s"] for p in points))
                    previous = section
                self.assertEqual(sorted(selected_indices), sorted(p["source_index"] for p in points))
                self.assertGreaterEqual(session["duration_s"], max(p["t"] for p in points))

    def test_fixture_binding_rejects_changed_count_and_hash(self):
        entry = json.loads(REPLAY.DEFAULT_MANIFEST.read_bytes())["sessions"][0]
        for key, value in (("samples_count", entry["samples_count"] - 1),
                           ("samples_sha256", "invalid")):
            with self.subTest(field=key), self.assertRaises(ValueError):
                REPLAY.reader.read_session({**entry, key: value})

    def test_selection_rejects_partial_existing_time_group_and_unknown_ids(self):
        with self.assertRaisesRegex(ValueError, "complete time group"):
            REPLAY.load_inputs(ROOT / "tests/replay_manifest.json", "R02")
        with self.assertRaisesRegex(ValueError, "Unknown recordings"):
            REPLAY.load_inputs(REPLAY.DEFAULT_MANIFEST, "not-a-recording")

    def test_blank_selection_means_all_and_internal_empty_id_is_invalid(self):
        with patch.object(REPLAY.reader, "read_session", side_effect=RuntimeError("reached reader")):
            with self.assertRaisesRegex(RuntimeError, "reached reader"):
                REPLAY.load_inputs(REPLAY.DEFAULT_MANIFEST, "")
        with self.assertRaisesRegex(ValueError, "nonempty"):
            REPLAY.load_inputs(REPLAY.DEFAULT_MANIFEST, "R05,,R06")

    def test_group_preserves_one_instance_ties_gap_and_final_fractional_input(self):
        instances = []
        grid = self.parameters["grid_seconds"]

        class ClockDetector:
            def __init__(self, parameters, origin, observer=None):
                self.grid_seconds = parameters.as_dict()["grid_seconds"]
                self.origin, self.index = origin, -1
                self.open, self.rejected = False, 0
                self.pending, self.latest = deque(), {}
                self.accepted, self.consumed, self.ticks = [], [], []
                instances.append(self)

            def accept(self, measurement):
                self.pending.append(measurement)
                self.accepted.append(measurement)
                return True

            def advance(self, at, **kwargs):
                final = math.floor((at - self.origin).total_seconds() / self.grid_seconds)
                while self.index < final:
                    self.index += 1
                    stamp = self.origin + timedelta(seconds=self.index * self.grid_seconds)
                    while self.pending and self.pending[0].received_at <= stamp:
                        item = self.pending.popleft()
                        self.consumed.append((stamp, item))
                        self.latest[item.position, item.quantity] = item
                    self.ticks.append((stamp, len(self.consumed)))
                return []

        role = REPLAY.reader.ROLES[0]
        def recording(identity, times):
            return {"id": identity, "duration_s": times[-1], "provenance": {}, "original_series": {
                role: [{"origin": "samples", "source_index": i, "t": t, "value": i}
                       for i, t in enumerate(times)]}}
        # Values/times here exercise exact boundaries and order, not event criteria.
        sessions = {"A": recording("A", [grid, grid, grid * 1.5]),
                    "B": recording("B", [0, grid * 0.5])}
        # Dictionary/channel order deliberately opposes original receipt order.
        first = sessions["A"]["original_series"][role].pop(0)
        sessions["A"]["original_series"][REPLAY.reader.ROLES[1]] = [first]
        group = {"id": "A-B", "origin_recording": "A", "source_sha256": [],
                 "recordings": [{"id": "A", "origin_offset_s": 0},
                                {"id": "B", "origin_offset_s": grid * 4,
                                 "previous_recording": "A", "gap_from_previous_end_s": grid * 2.5}]}
        manifest = {"sessions": [{"id": k} for k in sessions], "time_groups": [group]}
        axes = REPLAY.axis_owner.recording_time_axes(
            manifest, durations_s={k: v["duration_s"] for k, v in sessions.items()},
        )
        modules = {**self.modules, "detector": SimpleNamespace(Detector=ClockDetector)}
        with TemporaryDirectory() as folder:
            result = REPLAY.replay_group(group, sessions, axes, modules, self.origin,
                                         folder, diagnostics=False)
        self.assertEqual(len(instances), 1)
        detector = instances[0]
        self.assertEqual([m.value for m in detector.accepted], [0, 1, 2, 0, 1])
        self.assertEqual(len(detector.consumed), len(detector.accepted))
        self.assertFalse(detector.pending)
        self.assertEqual(detector.ticks[1][1], 2)
        self.assertEqual(detector.ticks[2][1], 3)
        self.assertEqual(detector.ticks[3][1], 3)
        self.assertEqual(result["original_rows"], 5)
        self.assertTrue(all(item.received_at <= stamp for stamp, item in detector.consumed))

    def test_diagnostic_serializer_preserves_sets_and_timestamp_keys(self):
        stamp = self.origin + timedelta(seconds=self.parameters["grid_seconds"])
        result = REPLAY.relative({"interrupted": {"source_gap"},
                                  "pending": {stamp: {"value": 1}}}, self.origin)
        encoded = json.dumps(result, allow_nan=False, sort_keys=True)
        self.assertIn("source_gap", encoded)
        self.assertIn('"value": 1', encoded)

    def test_observed_prefix_iterator_keeps_both_prefixes_and_early_exit(self):
        completed = []
        prefixes = REPLAY._ObservedPrefixes(("first", "second", "unused"),
            lambda value: value, lambda value, state, **meta: completed.append((value, state)))
        for value in prefixes:
            if value == "second":
                break
        prefixes.finish()
        self.assertEqual(completed, [("first", "first"), ("second", "second")])

    def test_observation_preserves_real_detector_outputs_on_existing_door_curve(self):
        manifest = json.loads((ROOT / "tests/replay_manifest.json").read_bytes())
        refs = json.loads((ROOT / "tests/analysis/proxy_calibration_references.json").read_bytes())
        reference = next(e for e in refs["event_references"] if e["kind"] == "door_open")
        identity = reference["recording"]
        entry = next(e for e in manifest["sessions"] if e["id"] == identity)
        session = REPLAY.reader.read_session(entry)
        window = self.parameters["curve_window_seconds"]
        start = max(0, reference["allowed_onset_s"][0] - window)
        end = reference["allowed_onset_s"][1] + window
        sliced = deepcopy(session)
        sliced["duration_s"] = end - start
        sliced["original_series"] = {
            role: [{**p, "t": round(p["t"] - start, 6)} for p in points
                   if p["origin"] == "samples" and start <= p["t"] <= end]
            for role, points in session["original_series"].items()
        }
        group = {"id": identity, "origin_recording": identity, "source_sha256": [],
                 "recordings": [{"id": identity, "origin_offset_s": 0}]}
        axes = REPLAY.axis_owner.recording_time_axes({"sessions": [{"id": identity}],
                                                    "time_groups": [group]})
        with TemporaryDirectory() as folder:
            plain = REPLAY.replay_group(group, {identity: sliced}, axes, self.modules,
                                       self.origin, Path(folder) / "plain", diagnostics=False)
            observed = REPLAY.replay_group(group, {identity: sliced}, axes, self.modules,
                                          self.origin, Path(folder) / "observed")
            rows = [json.loads(line) for line in gzip.decompress(
                (Path(folder) / "observed" / observed["diagnostics_file"]).read_bytes(),
            ).splitlines()]
        self.assertEqual(observed["detections"], plain["detections"])
        self.assertEqual(observed["final_owner_state"], plain["final_owner_state"])
        self.assertEqual(observed["rejected"], plain["rejected"])
        prefixes = [r for r in rows if r["type"] == "original_prefix"]
        self.assertTrue(prefixes)
        self.assertTrue(any(r["owner"] == "_original_door_evidence"
                            and r["before"] != r["after"] for r in prefixes),
                        "Parity must cover an actual door-candidate transition")
        self.assertTrue(any(a["raster_at"] == b["raster_at"]
                            for a, b in zip(prefixes, prefixes[1:])))


if __name__ == "__main__":
    unittest.main()
