"""Expose the exclusive test catalogue to GitHub Actions and dependency setup."""

import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys

from test_settings import setting, timeout

ROOT = Path(__file__).resolve().parents[1]
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


def outputs():
    values = {"settings": setting("runner"), "browser": setting("browser")}
    values["settings"]["step_timeouts_minutes"] = {
        key: math.ceil(seconds / 60)
        for key, seconds in values["settings"]["timeouts_s"].items()
    }
    derived = json.loads((ROOT / "tests/scenario_manifest.json").read_text())[
        "scenarios"
    ]
    replays = json.loads((ROOT / "tests/replay_manifest.json").read_text())["sessions"]
    values["checks"] = select_values(
        os.environ.get("SAUNA_TEST_CHECKS", ""), CHECKS, "checks"
    )
    cases_filter = os.environ.get("SAUNA_SESSION_CASES", "")
    if cases_filter.strip() and "sessions" not in values["checks"]:
        raise ValueError("session_cases requires sessions in checks")
    values["cases"] = select_values(
        cases_filter, [case["id"] for case in derived + replays], "session cases"
    )
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        for key, value in values.items():
            output.write(f"{key}={json.dumps(value, separators=(',', ':'))}\n")


def install_ha():
    # requirements.txt is the sole version lock for every HA/browser job.
    names = {"homeassistant", "securetar", "cronsim"}
    requirements = [
        line.strip()
        for line in (ROOT / "tests/browser/requirements.txt").read_text().splitlines()
    ]
    packages = [line for line in requirements if line.split("==")[0] in names]
    if len(packages) != len(names):
        raise ValueError("Incomplete or duplicate Home Assistant dependency pins")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", *packages],
        check=True,
        timeout=timeout("install_python"),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("outputs", "install-ha"))
    arguments = parser.parse_args()
    {"outputs": outputs, "install-ha": install_ha}[arguments.action]()
