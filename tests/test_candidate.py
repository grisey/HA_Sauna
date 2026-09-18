"""The accepted candidate stays unchanged; raw records remain local."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
PROVENANCE = json.loads((ROOT / "candidate/provenienz.json").read_text(encoding="utf-8"))


class CandidateIntegrityTests(unittest.TestCase):
    def test_frozen_files_match(self):
        for name, digest in PROVENANCE["candidate_files_sha256"].items():
            with self.subTest(file=name):
                self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), digest)

    def test_reference_modes_are_complete(self):
        self.assertEqual(set(PROVENANCE["modes"]), {"beide", "nur_k3", "nur_k6"})
        for mode, result in PROVENANCE["modes"].items():
            with self.subTest(mode=mode):
                self.assertEqual(result["matched_openings"], 15)
                self.assertEqual(result["detected_closures"], 15)
                self.assertEqual(result["person_background_positive_checks"], 0)
                self.assertEqual(result["infusion_background_positive_seconds"], 0)
                self.assertEqual(len(result["gangs"]), 4)
                self.assertTrue(all(g["lead_to_infusion_s"] > 0 for g in result["gangs"]))


@unittest.skipUnless(os.environ.get("SAUNA_RECORDER_ARCHIVE"), "privater Recorderexport nicht angegeben")
class RecorderReplayTests(unittest.TestCase):
    def test_full_replay_matches_original(self):
        source = Path(os.environ["SAUNA_RECORDER_ARCHIVE"])
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), PROVENANCE["source_sha256"])
        spec = importlib.util.spec_from_file_location("sauna_candidate", ROOT / "candidate/replay.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        parameters = json.loads((ROOT / "candidate/parameter.json").read_text(encoding="utf-8"))
        result = module.main(source, parameters, tests=True)
        # A caller may keep the same archive under a different local filename.
        result["source"] = PROVENANCE["source_archive"]
        encoded = (json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
        self.assertEqual(hashlib.sha256(encoded).hexdigest(), PROVENANCE["full_replay_result_sha256"])


if __name__ == "__main__":
    unittest.main()
