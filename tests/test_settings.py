"""Read-only access to the exclusive test settings and input catalogue.

This module deliberately contains no expected product outcomes. Invalid paths
fail immediately rather than silently falling back to a second default.
"""

from copy import deepcopy
import json
from pathlib import Path

PATH = Path(__file__).with_name("settings.json")
_SETTINGS = json.loads(PATH.read_text(encoding="utf-8"))


def setting(path):
    value = _SETTINGS
    for key in path.split("."):
        value = value[key]
    return deepcopy(value)


def timeout(name):
    return setting(f"runner.timeouts_s.{name}")


def browser_timeout(name):
    return setting(f"browser.timeouts_ms.{name}")


def fixture(name):
    return setting(f"fixtures.{name}")
