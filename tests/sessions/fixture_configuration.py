"""Read the canonical configuration as data, without importing product decisions.

Derived curves are materialized against this catalog and bind its complete
content. A changed catalog requires deliberate rematerialization; an old curve
must never silently run against a new limit. Historical replays own their
recorded installation configuration and do not use this module.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
CATALOG = REPOSITORY / "custom_components/ha_sauna/defaults.json"
CONFIGURATION = REPOSITORY / "tests/fixtures/scenarios/configuration.json"


def catalog_configuration():
    payload = CATALOG.read_bytes()
    catalog = json.loads(payload)
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
    return resolved, {item["id"]: item for item in catalog["programs"]}, hashlib.sha256(payload).hexdigest()


def configuration_reference():
    _, _, digest = catalog_configuration()
    return {
        "schema_version": 2,
        "source": CATALOG.relative_to(REPOSITORY).as_posix(),
        "source_sha256": digest,
        "basis": "Canonical factory catalog consumed at materialization; no copied parameter values. Explicit user selections and measured climate inputs remain fixture data.",
    }


def bound_parameters():
    binding = json.loads(CONFIGURATION.read_text(encoding="utf-8"))
    parameters, _, digest = catalog_configuration()
    if binding != configuration_reference() or binding["source_sha256"] != digest:
        raise ValueError(
            "Stale derived session fixtures: defaults.json changed. "
            "Materialize and review tests/sessions/build_fixtures.py before running them."
        )
    return parameters
