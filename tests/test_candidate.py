"""Integrity of the frozen historical reference, not a product-behaviour oracle.

Current measured acceptance lives in tests/sessions and has required bundled
inputs. The historical archive CLI is retained in candidate/ unchanged.
"""
import hashlib
import json
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


if __name__ == "__main__":
    unittest.main()
