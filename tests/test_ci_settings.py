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
    CORE_ROOT,
    ROOT,
    outputs,
    run_core,
    run_integration,
    select_core_tests,
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
CORE_DEFAULT_CASE = (
    "test_ci_settings.CISettingsTests."
    "test_unfiltered_dispatch_contains_every_check_and_manifest_case"
)
CORE_FILTERED_CASE = (
    "test_ci_settings.CISettingsTests."
    "test_filtered_dispatch_keeps_requested_jobs_and_bound_session_cases"
)


class CISettingsTests(unittest.TestCase):
    def configured_outputs(
        self, checks="", cases="", integration_tests="", core_tests=""
    ):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "outputs"
            with patch.dict(
                os.environ,
                {
                    "GITHUB_OUTPUT": str(path),
                    "SAUNA_TEST_CHECKS": checks,
                    "SAUNA_SESSION_CASES": cases,
                    "SAUNA_INTEGRATION_TESTS": integration_tests,
                    "SAUNA_CORE_TESTS": core_tests,
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
        self.assertEqual(configured["core_tests"], [])
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

    def test_core_selection_accepts_only_complete_source_test_ids(self):
        configured = self.configured_outputs(
            "core", core_tests=f" {CORE_FILTERED_CASE}, {CORE_DEFAULT_CASE} "
        )
        self.assertEqual(configured["checks"], ["core"])
        self.assertEqual(
            configured["core_tests"], [CORE_DEFAULT_CASE, CORE_FILTERED_CASE]
        )
        self.assertEqual(select_core_tests("  "), [])

    def test_invalid_test_selection_cannot_expand_or_skip_the_run(self):
        for job, other_job, argument, known in (
            ("ha", "core", "integration_tests", LIFECYCLE_CASE),
            ("core", "ha", "core_tests", CORE_DEFAULT_CASE),
        ):
            class_id = known.rsplit(".", 1)[0]
            for checks, names in (
                (job, class_id),
                (job, f"{class_id}.test_missing"),
                (job, f"{known},{known}"),
                (job, f"{known},"),
                (job, ","),
                (job, f"{known}; echo unexpected"),
                (other_job, known),
            ):
                with self.subTest(selector=argument, checks=checks, names=names):
                    with self.assertRaises(ValueError):
                        self.configured_outputs(checks, **{argument: names})

    def test_test_selection_reads_sync_and_async_source_without_imports(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "test_probe.py"
            source.write_text(
                "raise RuntimeError('must not be imported')\n"
                "class ProbeTests:\n"
                "    def test_sync(self):\n"
                "        pass\n"
                "    async def test_async(self):\n"
                "        pass\n",
                encoding="utf-8",
            )
            expected = [
                "test_probe.ProbeTests.test_sync",
                "test_probe.ProbeTests.test_async",
            ]
            for root, selector in (
                ("CORE_ROOT", select_core_tests),
                ("INTEGRATION_ROOT", select_integration_tests),
            ):
                with (
                    self.subTest(root=root),
                    patch(f"ci_settings.{root}", Path(directory)),
                ):
                    self.assertEqual(
                        selector(f" {expected[1]}, {expected[0]} "), expected
                    )

    def test_selected_core_run_loads_only_named_tests_and_requires_success(self):
        def failed_case():
            raise AssertionError("selected test failed")

        def skipped_case():
            raise unittest.SkipTest("required test skipped")

        for status, cases, expected_exit in (
            ("success", [unittest.FunctionTestCase(lambda: None)], 0),
            ("failure", [unittest.FunctionTestCase(failed_case)], 1),
            ("empty", [], 1),
            ("skipped", [unittest.FunctionTestCase(skipped_case)], 1),
        ):
            with (
                self.subTest(status=status),
                patch.dict(
                    os.environ,
                    {
                        "SAUNA_CORE_TESTS": (
                            f" {CORE_FILTERED_CASE}, {CORE_DEFAULT_CASE} "
                        )
                    },
                ),
                patch.object(sys, "path", list(sys.path)),
                patch(
                    "ci_settings.unittest.defaultTestLoader.loadTestsFromNames",
                    return_value=unittest.TestSuite(cases),
                ) as named_loader,
                patch("ci_settings.unittest.defaultTestLoader.discover") as discovery,
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as completed,
            ):
                run_core()
            named_loader.assert_called_once_with(
                [CORE_DEFAULT_CASE, CORE_FILTERED_CASE]
            )
            discovery.assert_not_called()
            self.assertEqual(completed.exception.code, expected_exit)

    def test_unfiltered_core_run_discovers_all_tests(self):
        for raw in ("", "  "):
            with (
                self.subTest(raw=raw),
                patch.dict(os.environ, {"SAUNA_CORE_TESTS": raw}),
                patch.object(sys, "path", list(sys.path)),
                patch(
                    "ci_settings.unittest.defaultTestLoader.loadTestsFromNames"
                ) as named_loader,
                patch(
                    "ci_settings.unittest.defaultTestLoader.discover",
                    return_value=unittest.TestSuite(
                        [unittest.FunctionTestCase(lambda: None)]
                    ),
                ) as discovery,
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as completed,
            ):
                run_core()
            discovery.assert_called_once_with(str(CORE_ROOT))
            named_loader.assert_not_called()
            self.assertEqual(completed.exception.code, 0)

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
