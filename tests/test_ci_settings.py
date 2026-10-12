"""Dispatch selectors preserve complete defaults and bound manifest IDs."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ci_settings import CHECKS, ROOT, outputs


class CISettingsTests(unittest.TestCase):
    def configured_outputs(self, checks="", cases=""):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "outputs"
            with patch.dict(
                os.environ,
                {
                    "GITHUB_OUTPUT": str(path),
                    "SAUNA_TEST_CHECKS": checks,
                    "SAUNA_SESSION_CASES": cases,
                },
            ):
                outputs()
            return {
                key: json.loads(value)
                for line in path.read_text(encoding="utf-8").splitlines()
                for key, value in (line.split("=", 1),)
            }

    def test_unfiltered_dispatch_contains_every_check_and_manifest_case(self):
        configured = self.configured_outputs()
        self.assertEqual(configured["checks"], list(CHECKS))
        scenarios = json.loads((ROOT / "tests/scenario_manifest.json").read_text())
        replays = json.loads((ROOT / "tests/replay_manifest.json").read_text())
        self.assertEqual(
            configured["cases"],
            [case["id"] for case in scenarios["scenarios"] + replays["sessions"]],
        )

    def test_filtered_dispatch_keeps_requested_jobs_and_bound_session_cases(self):
        configured = self.configured_outputs("sessions, core", " R03,G02-proxy ")
        self.assertEqual(configured["checks"], ["core", "sessions"])
        self.assertEqual(configured["cases"], ["G02-proxy", "R03"])

    def test_invalid_dispatch_cannot_silently_skip_requested_work(self):
        for checks, cases in (
            ("unknown", ""),
            ("core,", ""),
            ("core,core", ""),
            ("sessions", "missing"),
            ("sessions", "R03,R03"),
            ("ha", "R03"),
        ):
            with self.subTest(checks=checks, cases=cases):
                with self.assertRaises(ValueError):
                    self.configured_outputs(checks, cases)
