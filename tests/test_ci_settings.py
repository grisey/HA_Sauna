"""Dispatch selectors preserve complete defaults and bound manifest IDs."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

from ci_settings import (
    CHECKS,
    ROOT,
    outputs,
    run_integration,
    select_integration_tests,
)


LIFECYCLE_CASE = (
    "test_lifecycle.LifecycleTests."
    "test_reload_removes_only_this_instances_parameter_numbers"
)
APPEARANCE_CASE = (
    "test_panel_api.PanelAPITests."
    "test_appearance_updates_live_without_reloading_or_changing_control"
)


class CISettingsTests(unittest.TestCase):
    def configured_outputs(self, checks="", cases="", integration_tests=""):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "outputs"
            with patch.dict(
                os.environ,
                {
                    "GITHUB_OUTPUT": str(path),
                    "SAUNA_TEST_CHECKS": checks,
                    "SAUNA_SESSION_CASES": cases,
                    "SAUNA_INTEGRATION_TESTS": integration_tests,
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
        self.assertEqual(configured["integration_tests"], [])
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

    def test_integration_selection_accepts_only_complete_source_test_ids(self):
        configured = self.configured_outputs(
            "ha", integration_tests=f" {APPEARANCE_CASE}, {LIFECYCLE_CASE} "
        )
        self.assertEqual(configured["checks"], ["ha"])
        self.assertEqual(
            configured["integration_tests"], [LIFECYCLE_CASE, APPEARANCE_CASE]
        )
        self.assertEqual(select_integration_tests("  "), [])

    def test_invalid_integration_selection_cannot_expand_or_skip_the_run(self):
        for checks, names in (
            ("ha", "test_lifecycle.LifecycleTests"),
            ("ha", "test_lifecycle.LifecycleTests.test_missing"),
            ("ha", f"{LIFECYCLE_CASE},{LIFECYCLE_CASE}"),
            ("ha", f"{LIFECYCLE_CASE},"),
            ("ha", ","),
            ("ha", f"{LIFECYCLE_CASE}; echo unexpected"),
            ("core", LIFECYCLE_CASE),
        ):
            with self.subTest(checks=checks, names=names):
                with self.assertRaises(ValueError):
                    self.configured_outputs(checks, integration_tests=names)

    def test_integration_selection_reads_source_without_import_side_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "test_probe.py"
            source.write_text(
                "raise RuntimeError('must not be imported')\n"
                "class ProbeTests:\n"
                "    async def test_known(self):\n"
                "        pass\n",
                encoding="utf-8",
            )
            with patch("ci_settings.INTEGRATION_ROOT", Path(directory)):
                self.assertEqual(
                    select_integration_tests("test_probe.ProbeTests.test_known"),
                    ["test_probe.ProbeTests.test_known"],
                )

    def test_required_integration_run_fails_for_empty_or_skipped_suites(self):
        def skipped_case():
            raise unittest.SkipTest("required test skipped")

        for cases, expected_exit in (
            ([unittest.FunctionTestCase(lambda: None)], 0),
            ([unittest.FunctionTestCase(skipped_case)], 1),
            ([], 1),
        ):
            with (
                self.subTest(expected_exit=expected_exit, cases=len(cases)),
                patch.dict(
                    os.environ,
                    {"HA_TEST_REQUIRED": "1", "SAUNA_INTEGRATION_TESTS": ""},
                ),
                patch.object(sys, "path", list(sys.path)),
                patch(
                    "ci_settings.unittest.defaultTestLoader.discover",
                    return_value=unittest.TestSuite(cases),
                ),
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as completed,
            ):
                run_integration()
            self.assertEqual(completed.exception.code, expected_exit)
