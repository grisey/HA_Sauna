"""Audit native windows, kink gates and water pairs over whole time groups on Linux.

Only the existing stateless product owners are evaluated. This is neither a
Detector replay nor evidence of event admission, confirmation or historical
operation. No snapshot, recorded detection or recorded parameter override is
used as an input. Select cases from the resulting complete original stream.
"""

from __future__ import annotations

import argparse
from collections import Counter, deque
from dataclasses import fields
from datetime import datetime, timedelta
import gzip
import importlib
import importlib.util
import json
from pathlib import Path
import platform
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


replay = _module("_original_window_audit_replay", HERE / "detector_replay.py")


def load_owners():
    """Reuse the isolated core loader without importing or running Detector."""
    replay.reader.moisture_owner()
    modules = {
        name: importlib.import_module(f"_proxy_forms_core.{name}")
        for name in ("curve_change", "parameters", "models")
    }
    paths = {
        Path(module.__file__).resolve()
        for name, module in sys.modules.items()
        if name.startswith("_proxy_forms_core.") and getattr(module, "__file__", None)
    }
    paths.update(
        (
            Path(__file__),
            HERE / "detector_replay.py",
            HERE / "proxy_forms.py",
            HERE / "replay_time_axis.py",
            ROOT / "custom_components/ha_sauna/defaults.json",
            ROOT / "custom_components/ha_sauna/core/detector.py",
            ROOT / "tests/settings.json",
        )
    )
    return modules, [replay._identity(path) for path in sorted(paths)]


def contrast_record(contrasts):
    """Project native scalar fields; reference the already exported windows."""
    if contrasts is None:
        return None
    record = {
        field.name: getattr(contrasts, field.name)
        for field in fields(contrasts)
        if field.name != "positions"
    }
    for name in ("humidity_rise_onset", "humidity_fall_onset"):
        record[name] = getattr(contrasts, name)
    record["positions"] = [
        {
            "position": position,
            "temperature": {"$ref": f"#/channel_windows/{position}_temperature"},
            "humidity": {"$ref": f"#/channel_windows/{position}_humidity"},
        }
        for position, _ in contrasts.positions
    ]
    return record


def kink_record(position, kink):
    """Keep native scalar evidence and reference its already exported RH window."""
    if kink is None:
        return None
    return {
        **{
            field.name: getattr(kink, field.name)
            for field in fields(kink)
            if field.name != "window"
        },
        "window": {"$ref": f"#/channel_windows/{position}_humidity"},
    }


def humidity_onset_gates(contrasts, kinks, *, significance, factor):
    """Compare raw product onsets with their additional directed kink gate.

    Only scalar results of the product owners are consumed here. In particular,
    the factor range reuses delta/used_scale and never refits original samples.
    Missing support remains visible; positive-factor binding requires every
    expected position, just as in Detector._original_humidity_kink_gate.
    """
    result = {}
    for name, direction in (("rise", 1), ("fall", -1)):
        value = (
            getattr(contrasts, f"humidity_{name}_onset")
            if contrasts is not None
            else None
        )
        raw = value > significance if value is not None else None
        positions = {
            position: direction * kink.effective_change > 0
            if kink is not None
            else None
            for position, kink in kinks.items()
        }
        directed = bool(positions) and (
            factor == 0 or all(passed is True for passed in positions.values())
        )
        factor_range = None
        if raw is True:
            complete = bool(kinks) and all(kink is not None for kink in kinks.values())
            factor_range = {
                "status": "ready" if complete else "unknown",
                "minimum": 0.0,
                "minimum_inclusive": True,
                "maximum": min(
                    direction * kink.delta / kink.used_scale for kink in kinks.values()
                )
                if complete
                else None,
                "maximum_inclusive": False,
            }
        result[name] = {
            "raw": {
                "result": raw,
                "value": value,
                "operator": ">",
                "threshold": significance,
            },
            "kink": {
                "result": directed,
                "direction": direction,
                "factor": factor,
                "applied": factor != 0,
                "positions": positions,
            },
            "with_kink": raw and directed if raw is not None else None,
            "factor_zero_result": raw,
            "allowed_factor_range": factor_range,
        }
    return result


def audit_group(group, inputs, modules, parameters, origin, output_dir, provenance):
    """Cache only unchanged channel fits; recheck every channel's age per prefix."""
    owner = modules["curve_change"]
    position_type = modules["models"].Position
    quantity_type = modules["models"].Quantity
    expected = tuple(position.value for position in position_type)
    roles = tuple(replay.reader.ROLES)
    if set(roles) != {
        f"{position}_{quantity.value}"
        for position in expected
        for quantity in quantity_type
    }:
        raise ValueError("Reader roles differ from the required product T/RH positions")
    settings = parameters.as_dict()
    count = settings["curve_original_samples"]
    points = {role: deque(maxlen=settings["curve_max_points"]) for role in roles}
    cached = {role: None for role in roles}
    cached_kinks = {position: None for position in expected}
    gaps = {
        role: settings[f"curve_original_gap_{quantity}_{position}_seconds"]
        for role in roles
        for position, quantity in (role.split("_", 1),)
    }
    scales = {
        role: settings[f"curve_original_noise_{quantity}_{position}"]
        for role in roles
        for position, quantity in (role.split("_", 1),)
    }
    pending_water = {position: {} for position in expected}
    events = replay._events(group, inputs["sessions"], inputs["axes"])
    diagnostic_path = Path(output_dir) / f"{group['id']}.original-windows.jsonl.gz"
    counts = Counter()
    kink_helper_calls = Counter()
    onset_counts = {
        direction: Counter(
            {
                "raw_true": 0,
                "raw_false": 0,
                "raw_unknown": 0,
                "with_kink_true": 0,
                "with_kink_false": 0,
                "with_kink_unknown": 0,
                "raw_true_suppressed": 0,
            }
        )
        for direction in ("rise", "fall")
    }
    started = time.perf_counter()
    consumed = 0
    last_at = None
    metadata = {
        "type": "metadata",
        "schema_version": 2,
        "group": group,
        "manifest": inputs["manifest_identity"],
        "sources": provenance,
        "parameters": settings,
        "parameters_sha256": replay.reader.sha(replay._canonical(settings)),
        "expected_positions": expected,
        "time_axes": {
            member["id"]: inputs["axes"][member["id"]] for member in group["recordings"]
        },
        "input_provenance": {
            member["id"]: inputs["sessions"][member["id"]]["provenance"]
            for member in group["recordings"]
        },
        "context": {
            "time": "Group-relative received time; same ordering as detector_replay._events",
            "sources": (
                "Stable bound-role sources within each whole time group, "
                "as in detector_replay"
            ),
            "initial_state": "Excluded; only bound received sample originals are consumed",
            "parameters": "Current product Parameters({}); recorded overrides are not applied",
            "support": (
                "Product original_window, OriginalPositionWindows, "
                "original_contrasts, original_kink_tolerance and original_water_pair_support"
            ),
            "contrast_projection": (
                "Native scalar fields and humidity_rise_onset/humidity_fall_onset properties; "
                "positions reference this row's full "
                "channel_windows instead of duplicating OriginalPositionWindows"
            ),
            "kink_tolerance": {
                "owner": "custom_components.ha_sauna.core.curve_change.original_kink_tolerance",
                "factor_parameter": "curve_original_kink_factor",
                "history_parameter": "curve_original_kink_history_windows",
                "projection": (
                    "Native scalar fields; window references this row's fresh RH channel_window"
                ),
                "gate": (
                    "Raw humidity_rise_onset/humidity_fall_onset > curve_original_significance; "
                    "with_kink additionally requires direction * effective_change > 0 "
                    "at every expected position. Factor 0 preserves raw, including unknown support"
                ),
                "allowed_factor_range": (
                    "For raw=true with complete helper support, 0 <= factor < "
                    "min(direction * delta / used_scale). Missing helper support leaves "
                    "the positive-factor range unknown; factor 0 still preserves raw. "
                    "The interval varies only factor, retaining the configured history parameter"
                ),
                "counts": (
                    "Counts are dependent original-prefix counts, including unchanged cached "
                    "RH windows on T originals; they are neither events nor independent statistics"
                ),
            },
            "cache": (
                "Only the changed role is fitted; every cached role is checked "
                "for freshness at each prefix. Kink support is evaluated once per changed "
                "valid RH window; cached helper support is available only with its fresh "
                "referenced RH window"
            ),
            "continuity": (
                "Per-role sensor timeout clears retained originals; "
                "archive boundaries do not reset a group"
            ),
            "unsupported": (
                "Invalid originals, changing input time basis and "
                "equal/regressing per-role timestamps abort the audit"
            ),
            "humidity_minima": (
                "Not supplied; recovery and closing that require an "
                "event-relative minimum remain unknown"
            ),
            "water": (
                "Native pair support only; no positive-water qualifier, "
                "candidate baseline or event association"
            ),
            "scope": (
                "No Detector instance, grid, BIC fit, episode, controller or "
                "event-state claim; parity with saved owner prefixes must be "
                "checked separately"
            ),
        },
    }
    completion = {"type": "completion", "group_id": group["id"], "status": "running"}
    with gzip.GzipFile(filename=str(diagnostic_path), mode="wb", mtime=0) as stream:

        def emit(row):
            counts[row["type"]] += 1
            stream.write(replay._canonical(replay.relative(row, origin)) + b"\n")

        emit(metadata)
        try:
            for seconds, _, source_index, recording, role, point in events:
                at = origin + timedelta(seconds=seconds)
                if (
                    not point["valid"]
                    or not point["fit_eligible"]
                    or point["time_basis"] != "original_received_time"
                ):
                    raise ValueError(
                        f"Unsupported original validity/time basis: {recording}/{source_index}",
                    )
                if points[role] and at <= points[role][-1][0]:
                    raise ValueError(
                        f"Unsupported same-time correction or time regression: "
                        f"{recording}/{source_index}/{role}",
                    )
                if (
                    points[role]
                    and (at - points[role][-1][0]).total_seconds()
                    > settings["sensor_timeout_seconds"]
                ):
                    points[role].clear()
                points[role].append((at, point["value"]))
                cached[role] = owner.original_window(
                    tuple(points[role]),
                    count=count,
                    scale=scales[role],
                    max_gap_seconds=gaps[role],
                    at=at,
                )
                available = {
                    name: feature
                    if feature is not None
                    and (at - feature.newest_at).total_seconds() <= gaps[name]
                    else None
                    for name, feature in cached.items()
                }
                changed_position, quantity = role.split("_", 1)
                if quantity == "humidity":
                    cached_kinks[changed_position] = None
                    if available[role] is not None:
                        cached_kinks[changed_position] = owner.original_kink_tolerance(
                            samples=points[role],
                            window=available[role],
                            factor=settings["curve_original_kink_factor"],
                            min_history_windows=settings[
                                "curve_original_kink_history_windows"
                            ],
                            max_gap_seconds=gaps[role],
                        )
                        kink_helper_calls[role] += 1
                kinks = {
                    name: kink
                    if kink is not None
                    and (kink.window is available[f"{name}_humidity"])
                    else None
                    for name, kink in cached_kinks.items()
                }
                windows = {}
                for position in expected:
                    temperature = available[f"{position}_temperature"]
                    humidity = available[f"{position}_humidity"]
                    if temperature is not None and humidity is not None:
                        windows[position] = owner.OriginalPositionWindows(
                            temperature, humidity
                        )
                contrasts = owner.original_contrasts(
                    windows, expected_positions=expected
                )
                onset_gates = humidity_onset_gates(
                    contrasts,
                    kinks,
                    significance=settings["curve_original_significance"],
                    factor=settings["curve_original_kink_factor"],
                )
                position, quantity = role.split("_", 1)
                pending = pending_water[position]
                if quantity == "humidity":
                    pending[at] = {"sample": points[role][-1], "last_status": None}
                water_support = []
                for stamp, item in tuple(pending.items()):
                    # An invalid result may change only if another original has
                    # the same received timestamp (for example an exact T pair).
                    if item["last_status"] == "invalid" and stamp < at:
                        del pending[stamp]
                        continue
                    support = owner.original_water_pair_support(
                        item["sample"],
                        tuple(points[f"{position}_temperature"]),
                        temperature_scale=scales[f"{position}_temperature"],
                        humidity_scale=scales[f"{position}_humidity"],
                        max_gap_seconds=gaps[f"{position}_temperature"],
                        at=at,
                    )
                    item["last_status"] = support.status
                    water_support.append(
                        {
                            "position": position,
                            "humidity_sample": item["sample"],
                            "original_support": support,
                        }
                    )
                    if support.status == "ready":
                        del pending[stamp]
                if len(pending) > settings["curve_max_points"]:
                    raise ValueError(
                        f"Unresolved native water-pair support exceeds retention: {position}",
                    )
                emit(
                    {
                        "type": "original_prefix",
                        "received_at": at,
                        "recording_id": recording,
                        "source_index": source_index,
                        "local_t_s": point["t"],
                        "role": role,
                        "source": f"bound-role:{role}",
                        "time_basis": "received",
                        "original": True,
                        "value": point["value"],
                        "channel_windows": available,
                        "original_contrasts": contrast_record(contrasts),
                        "original_humidity_kinks": {
                            position: kink_record(position, kink)
                            for position, kink in kinks.items()
                        },
                        "humidity_onset_gates": onset_gates,
                        "water_support": water_support,
                    }
                )
                for direction, gate in onset_gates.items():
                    for name, passed in (
                        ("raw", gate["raw"]["result"]),
                        ("with_kink", gate["with_kink"]),
                    ):
                        status = (
                            "unknown"
                            if passed is None
                            else "true"
                            if passed
                            else "false"
                        )
                        onset_counts[direction][f"{name}_{status}"] += 1
                    if gate["raw"]["result"] is True and gate["with_kink"] is False:
                        onset_counts[direction]["raw_true_suppressed"] += 1
                consumed += 1
                last_at = at
            completion.update(status="completed")
        except BaseException as error:
            completion.update(status="failed", error=f"{type(error).__name__}: {error}")
            raise
        finally:
            completion.update(
                original_rows=len(events),
                consumed_originals=consumed,
                received_through=last_at,
                counts=dict(counts),
                humidity_onset_prefix_counts={
                    direction: dict(values)
                    for direction, values in onset_counts.items()
                },
                kink_helper_calls_by_role=dict(kink_helper_calls),
                pending_water={
                    position: len(pending)
                    for position, pending in pending_water.items()
                },
                wall_seconds=time.perf_counter() - started,
            )
            emit(completion)
    return {
        **completion,
        "diagnostics": replay._identity(diagnostic_path),
        "rows_by_role": dict(Counter(event[4] for event in events)),
    }


def run_audit(manifest_path, output_dir, recordings=None):
    """Read hash-bound whole groups, then evaluate only on the Linux target."""
    if platform.system() != "Linux":
        raise RuntimeError("Product original-window audits run exclusively on Linux")
    inputs = replay.load_inputs(manifest_path, recordings)
    modules, provenance = load_owners()
    parameters = modules["parameters"].Parameters({})
    settings = json.loads((ROOT / "tests/settings.json").read_bytes())
    origin = datetime.fromisoformat(settings["fixtures"]["session"]["origin"])
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "original-window-audit.json"
    report = {
        "schema_version": 2,
        "manifest": inputs["manifest_identity"],
        "sources": provenance,
        "status": "running",
        "active_group": None,
        "groups": [],
    }

    def save():
        report_path.write_bytes(
            replay._canonical(replay.relative(report, origin)) + b"\n"
        )

    save()
    try:
        for group in inputs["groups"]:
            report["active_group"] = group["id"]
            save()
            print(f"ORIGINAL WINDOWS {group['id']} BEGIN", flush=True)
            result = audit_group(
                group,
                inputs,
                modules,
                parameters,
                origin,
                output_dir,
                provenance,
            )
            report["groups"].append(result)
            print(
                f"ORIGINAL WINDOWS {group['id']} END "
                f"originals={result['consumed_originals']} "
                f"wall={result['wall_seconds']:.1f}s",
                flush=True,
            )
        report.update(status="completed", active_group=None)
    except BaseException as error:
        report.update(status="failed", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        save()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=replay.DEFAULT_MANIFEST,
        help="Manifest with fixture paths relative to repository tests/",
    )
    parser.add_argument(
        "--recordings", help="Comma-separated IDs; select only whole time groups"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if platform.system() != "Linux":
        parser.error("Product original-window audits run exclusively on Linux")
    run_audit(args.manifest, args.output, args.recordings)


if __name__ == "__main__":
    main()
