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
    values["cases"] = [case["id"] for case in derived + replays]
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
