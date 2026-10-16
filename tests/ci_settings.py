"""Expose the exclusive test catalogue to GitHub Actions and dependency setup."""

import argparse
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from test_settings import setting

ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = ROOT / "tests"
HA_ROOT = ROOT / "tests/ha"
INTEGRATION_ROOT = ROOT / "tests/integration"
CHECKS = ("core", "ha", "browser", "sessions")


def select_values(raw, available, label):
    """Validate a dispatch filter against its actual job or manifest IDs."""
    if not raw.strip():
        return list(available)
    selected = [value.strip() for value in raw.split(",")]
    unknown = set(selected) - set(available)
    if unknown:
        raise ValueError(f"Unknown {label}: {', '.join(sorted(unknown))}")
    if len(selected) != len(set(selected)):
        raise ValueError(f"Duplicate {label}")
    return [value for value in available if value in selected]


def select_test_methods(raw, test_root, label):
    """Validate complete test IDs from source without importing test fixtures."""
    if not raw.strip():
        return []
    available = [
        f"{path.stem}.{definition.name}.{method.name}"
        for path in sorted(test_root.glob("test_*.py"))
        for definition in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(definition, ast.ClassDef)
        for method in definition.body
        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
        and method.name.startswith("test_")
    ]
    return select_values(raw, available, label)


def select_core_tests(raw):
    return select_test_methods(raw, CORE_ROOT, "core tests")


def select_ha_tests(raw):
    return select_test_methods(raw, HA_ROOT, "HA adapter tests")


def select_integration_tests(raw):
    return select_test_methods(raw, INTEGRATION_ROOT, "integration tests")


def outputs():
    values = {"settings": setting("runner"), "browser": setting("browser")}
    derived = json.loads((ROOT / "tests/scenario_manifest.json").read_text())[
        "scenarios"
    ]
    replays = json.loads((ROOT / "tests/replay_manifest.json").read_text())["sessions"]
    values["checks"] = select_values(
        os.environ.get("SAUNA_TEST_CHECKS", ""), CHECKS, "checks"
    )
    core_filter = os.environ.get("SAUNA_CORE_TESTS", "")
    if core_filter.strip() and "core" not in values["checks"]:
        raise ValueError("core_tests requires core in checks")
    values["core_tests"] = select_core_tests(core_filter)
    cases_filter = os.environ.get("SAUNA_SESSION_CASES", "")
    if cases_filter.strip() and "sessions" not in values["checks"]:
        raise ValueError("session_cases requires sessions in checks")
    values["cases"] = select_values(
        cases_filter, [case["id"] for case in derived + replays], "session cases"
    )
    integration_filter = os.environ.get("SAUNA_INTEGRATION_TESTS", "")
    if integration_filter.strip() and "ha" not in values["checks"]:
        raise ValueError("integration_tests requires ha in checks")
    values["integration_tests"] = select_integration_tests(integration_filter)
    ha_filter = os.environ.get("SAUNA_HA_TESTS", "")
    if ha_filter.strip() and "ha" not in values["checks"]:
        raise ValueError("ha_tests requires ha in checks")
    values["ha_tests"] = select_ha_tests(ha_filter)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        for key, value in values.items():
            output.write(f"{key}={json.dumps(value, separators=(',', ':'))}\n")


def install_ha():
    # Reuse the complete shared environment used by the native HA/browser fixtures.
    subprocess.run(
        [
            sys.executable, "-m", "pip", "install", "-r",
            str(ROOT / "tests/browser/requirements.txt"),
        ],
        check=True,
    )


def run_core():
    names = select_core_tests(os.environ.get("SAUNA_CORE_TESTS", ""))
    # Named tests need the same module priority as core discovery.
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(CORE_ROOT))
    print("CORE_TESTS", json.dumps(names or ["all"]), flush=True)
    loader = unittest.defaultTestLoader
    suite = (
        loader.loadTestsFromNames(names) if names else loader.discover(str(CORE_ROOT))
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    successful = result.wasSuccessful() and result.testsRun > 0 and not result.skipped
    if result.skipped:
        print("Required core tests were skipped", flush=True)
    raise SystemExit(0 if successful else 1)


def run_ha():
    names = select_ha_tests(os.environ.get("SAUNA_HA_TESTS", ""))
    # Named tests need the same module priority as HA adapter discovery.
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(HA_ROOT))
    print("HA_TESTS", json.dumps(names or ["all"]), flush=True)
    loader = unittest.defaultTestLoader
    suite = (
        loader.loadTestsFromNames(names) if names else loader.discover(str(HA_ROOT))
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    successful = result.wasSuccessful() and result.testsRun > 0
    if os.environ.get("HA_TEST_REQUIRED") and result.skipped:
        print("Required HA adapter tests were skipped", flush=True)
        successful = False
    raise SystemExit(0 if successful else 1)


def run_integration():
    names = select_integration_tests(os.environ.get("SAUNA_INTEGRATION_TESTS", ""))
    # Named tests need the same module priority as integration discovery.
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(INTEGRATION_ROOT))
    print("INTEGRATION_TESTS", json.dumps(names or ["all"]), flush=True)
    loader = unittest.defaultTestLoader
    suite = (
        loader.loadTestsFromNames(names)
        if names
        else loader.discover(str(INTEGRATION_ROOT))
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    successful = result.wasSuccessful() and result.testsRun > 0
    if os.environ.get("HA_TEST_REQUIRED") and result.skipped:
        print("Required HA integration tests were skipped", flush=True)
        successful = False
    raise SystemExit(0 if successful else 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("outputs", "install-ha", "core", "ha", "integration")
    )
    arguments = parser.parse_args()
    {
        "outputs": outputs,
        "install-ha": install_ha,
        "core": run_core,
        "ha": run_ha,
        "integration": run_integration,
    }[arguments.action]()
