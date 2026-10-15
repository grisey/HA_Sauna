"""Read the canonical configuration as data, without importing product decisions.

Derived curves bind resolved parameter values and saved programme inputs.
Presentation metadata can change without rematerializing identical climate
curves. A changed effective value still fails before execution. Historical
replays own their recorded configuration and do not use this module.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
CATALOG = REPOSITORY / "custom_components/ha_sauna/defaults.json"
CONFIGURATION = REPOSITORY / "tests/fixtures/scenarios/configuration.json"


def catalog_configuration(catalog=None):
    catalog = json.loads(CATALOG.read_bytes()) if catalog is None else catalog
    declared = {item["key"]: item["default"] for item in catalog["parameters"]}
    resolved = {}

    def resolve(key, trail=()):
        if key in trail:
            raise ValueError(f"Cyclic fixture parameter reference: {trail + (key,)}")
        value = declared[key]
        if isinstance(value, dict):
            if set(value) != {"parameter"}:
                raise ValueError(f"Unsupported default reference: {key}")
            value = resolve(value["parameter"], trail + (key,))
        resolved[key] = value
        return value

    for key in declared:
        resolve(key)
    programs = {item["id"]: item for item in catalog["programs"]}
    projection = {"parameters": resolved, "programs": {
        identity: {key: item[key] for key in ("start_c", "end_c", "distribution_gangs")}
        for identity, item in programs.items()
    }}
    payload = json.dumps(projection, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return resolved, programs, hashlib.sha256(payload).hexdigest()


def configuration_reference():
    _, _, digest = catalog_configuration()
    return {
        "schema_version": 3,
        "source": CATALOG.relative_to(REPOSITORY).as_posix(),
        "source_sha256": hashlib.sha256(CATALOG.read_bytes()).hexdigest(),
        "effective_sha256": digest,
        "projection": "all resolved parameter defaults and programme start/end/distribution values",
        "basis": "Canonical configuration values consumed at materialization; no copied defaults. Presentation metadata and UI preferences do not alter fixture inputs. Changed effective values require explicit review before rebinding; unchanged input curves remain byte-bound.",
    }


def bound_parameters():
    binding = json.loads(CONFIGURATION.read_text(encoding="utf-8"))
    parameters, _, digest = catalog_configuration()
    if (binding.get("schema_version") != 3
            or binding.get("source") != CATALOG.relative_to(REPOSITORY).as_posix()
            or binding.get("projection") != configuration_reference()["projection"]
            or binding.get("effective_sha256") != digest):
        raise ValueError(
            "Stale derived session fixtures: effective configuration values changed. "
            "Review the inputs and their binding before running them; only rematerialize "
            "when the declared climate curves or schedules must change."
        )
    return parameters
