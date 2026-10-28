"""Bound, checkout-local evidence; a missing input must fail the mandatory suite."""

import gzip
import hashlib
import json
import math
import unittest
from itertools import pairwise
from pathlib import Path

from sessions.fixture_configuration import bound_parameters

ROOT = Path(__file__).resolve().parent
CLIMATE = {"upper_temperature", "upper_humidity", "lower_temperature", "lower_humidity"}
FORBIDDEN_INPUTS = {"door_open", "door_close", "ventilation", "infusion", "gang_started", "gang_ended"}


def bound(path, digest):
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != digest:
        raise AssertionError(f"Unbound or changed fixture: {path}")
    return payload


class RequiredSessionFixtureTests(unittest.TestCase):
    def test_every_scenario_and_replay_has_bound_values_and_independent_evaluations(self):
        for name in ("scenario_manifest.json", "replay_manifest.json"):
            manifest = json.loads((ROOT / name).read_text())
            if "configuration_file" in manifest:
                self.assertTrue(bound_parameters())
            for file_key, hash_key in (("source_curves", "source_curves_sha256"),
                                       ("configuration_file", "configuration_sha256"),
                               ("construction_file", "construction_sha256")):
                if file_key in manifest:
                    source = ROOT / manifest[file_key]
                    self.assertTrue(source.resolve().is_relative_to(ROOT.resolve()))
                    bound(source, manifest[hash_key])
            entries = manifest.get("scenarios", manifest.get("replays", manifest.get("sessions")))
            self.assertTrue(entries, name)
            self.assertEqual(len(entries), len({entry["id"] for entry in entries}))
            for entry in entries:
                with self.subTest(manifest=name, case=entry["id"]):
                    path = ROOT / entry.get("path", entry.get("file"))
                    self.assertTrue(path.resolve().is_relative_to(ROOT.resolve()))
                    case = json.loads(bound(path, entry["sha256"]))
                    if case.get("kind") == "derived_whole_session":
                        self.assertNotIn("parameters", case["configuration"])
                        self.assertEqual(case["configuration"]["parameter_source"],
                                         "configuration.json")
                    if case.get("kind") == "real_session":
                        output_path = path.parent / case["observed_outputs_file"]
                        self.assertTrue(output_path.resolve().is_relative_to(ROOT.resolve()))
                        outputs = bound(output_path, case["observed_outputs_sha256"])
                        self.assertEqual(len(gzip.decompress(outputs).splitlines()),
                                         case["observed_outputs_count"])
                        self.assertEqual(case["feedback_mode"], "simulated_acknowledgement")
                    self.assertEqual(case["id"], entry["id"])
                    self.assertGreater(case["duration_s"], 0)
                    self.assertTrue(case["actions"])
                    self.assertTrue(case["expectations"])
                    checks = case["expectations"]
                    self.assertEqual(len(checks), len({check["id"] for check in checks}))
                    for check in checks:
                        self.assertTrue(check["reason"].strip(), check["id"])
                        self.assertTrue(check["values"], check["id"])
                        times = check.get("between_s", [check.get("at_s")])
                        self.assertTrue(all(t is not None and 0 <= t <= case["duration_s"] for t in times), check)
                        if "input_transition" in check:
                            self.assertIn("between_s", check)
                            self.assertEqual(len(check["between_s"]), 2)
                            trigger = check["input_transition"]
                            self.assertEqual(set(trigger), {"counter_increases", "before"})
                            self.assertTrue(trigger["counter_increases"])
                            self.assertTrue(trigger["before"])
                            self.assertNotIn("eventually", check)
                    for action in case["actions"]:
                        self.assertNotIn(action["name"], FORBIDDEN_INPUTS)
                        self.assertTrue(0 <= action["t"] <= case["duration_s"])
                    sample_name = case.get("samples_file", case.get("measurement_file", case.get("measurements_file")))
                    sample_hash = case.get("samples_sha256", case.get("measurement_sha256", case.get("measurements_sha256")))
                    self.assertIsNotNone(sample_hash)
                    sample_path = path.parent / sample_name
                    self.assertTrue(sample_path.resolve().is_relative_to(ROOT.resolve()))
                    payload = bound(sample_path, sample_hash)
                    if sample_path.suffix == ".gz":
                        samples = [json.loads(line) for line in gzip.decompress(payload).splitlines()]
                    elif sample_path.suffix == ".json":
                        curve = json.loads(payload)
                        self.assertEqual(set(curve["roles"]), CLIMATE)
                        times = [row[0] for row in curve["rows"]]
                        self.assertTrue(all(a < b for a, b in pairwise(times)))
                        samples = [{"t": row[0], "role": role, "value": row[i]}
                                   for row in curve["rows"] for i, role in enumerate(curve["roles"], 1)]
                    else:
                        samples = [json.loads(line) for line in payload.splitlines()]
                    self.assertTrue(samples)
                    self.assertEqual({s["role"] for s in samples} & CLIMATE, CLIMATE)
                    for sample in samples:
                        self.assertNotIn(sample["role"], FORBIDDEN_INPUTS)
                        self.assertTrue(0 <= sample["t"] <= case["duration_s"], sample)
                        if sample["role"] in CLIMATE and isinstance(sample["value"], (int, float)):
                            self.assertTrue(math.isfinite(sample["value"]))
                            if sample["role"].endswith("humidity"):
                                self.assertTrue(0 <= sample["value"] <= 100, sample)

    def test_replay_selection_is_three_or_four_complete_recordings(self):
        manifest = json.loads((ROOT / "replay_manifest.json").read_text())
        entries = manifest.get("replays", manifest.get("sessions"))
        self.assertIn(len(entries), (3, 4))


if __name__ == "__main__":
    unittest.main()
