"""Replay bound originals through the current product Detector on Linux.

Each complete manifest time group has exactly one Detector. ``enabled=True``
and ``allowed=None`` declare a standalone measurement comparison, not historical
operation, controller admission, heating, occupancy truth or actuator behaviour.
No historical actions, feedback or detection outputs enter this comparison.
"""

from __future__ import annotations

import argparse
from collections import Counter, deque
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta
from enum import Enum
import gzip
import importlib
import importlib.util
from itertools import groupby
import json
import math
from pathlib import Path
import platform
import re
import sys
import time


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_MANIFEST = ROOT / "tests/replay_measurement_manifest.json"


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reader = _module("_detector_replay_reader", HERE / "proxy_forms.py")
axis_owner = _module("_detector_replay_axis", HERE / "replay_time_axis.py")


def _canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _identity(path):
    path = Path(path).resolve()
    try:
        name = str(path.relative_to(ROOT))
    except ValueError:
        name = path.name
    return {"file": name, "sha256": reader.sha(path.read_bytes())}


def relative(value, origin):
    """Serialize original diagnostics without altering their comparisons."""
    if isinstance(value, datetime):
        return (value - origin).total_seconds()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, Enum):
        return relative(value.value, origin)
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: relative(getattr(value, field.name), origin)
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        if any(
            not isinstance(key.value if isinstance(key, Enum) else key, str)
            for key in value
        ):
            items = [
                {
                    "key_type": type(key).__name__,
                    "key": relative(key, origin),
                    "value": relative(item, origin),
                }
                for key, item in value.items()
            ]
            return {"mapping_items": sorted(items, key=_canonical)}
        result = {}
        for key, item in value.items():
            key = key.value if isinstance(key, Enum) else key
            if not isinstance(key, str):
                raise TypeError(f"Unsupported diagnostic key: {type(key).__name__}")
            result[key] = relative(item, origin)
        return result
    if isinstance(value, (set, frozenset)):
        return {
            "set_type": type(value).__name__,
            "items": sorted((relative(item, origin) for item in value), key=_canonical),
        }
    if isinstance(value, (tuple, list, deque)):
        return [relative(item, origin) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return {"nonfinite_original": repr(value)}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Unsupported original diagnostic: {type(value).__name__}")


def load_inputs(manifest_path, recordings=None):
    """Read bound sample streams once; select only whole time groups.

    Fixture paths retain the existing read_session convention: relative to
    repository tests/, including when an alternative manifest is supplied.
    This function imports no product modules.
    """
    manifest_path = Path(manifest_path).resolve()
    manifest = json.loads(manifest_path.read_bytes())
    identities = [entry["id"] for entry in manifest["sessions"]]
    # Validate complete manifest membership before selecting any source file.
    axis_owner.recording_time_axes(manifest)
    if isinstance(recordings, str):
        recordings = (
            [identity.strip() for identity in recordings.split(",")]
            if recordings.strip()
            else None
        )
    selected = identities if recordings is None else list(recordings)
    if (
        not selected
        or any(not identity for identity in selected)
        or len(selected) != len(set(selected))
    ):
        raise ValueError("Select distinct nonempty recording IDs")
    wanted = set(selected)
    if wanted - set(identities):
        raise ValueError(f"Unknown recordings: {sorted(wanted - set(identities))}")
    groups = []
    for group in manifest["time_groups"]:
        members = {member["id"] for member in group["recordings"]}
        if members & wanted:
            if members - wanted:
                raise ValueError(
                    f"Select the complete time group {group['id']}: {sorted(members)}",
                )
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", group["id"]):
                raise ValueError("Time group ID is not a safe artifact name")
            groups.append(group)
    sessions = {
        entry["id"]: reader.read_session(entry)
        for entry in manifest["sessions"]
        if entry["id"] in wanted
    }
    # Validate gaps against all declared durations without rereading samples of
    # unselected groups. The same bound reader validates selected durations.
    durations = {
        identity: session["duration_s"] for identity, session in sessions.items()
    }
    selected_manifest = {
        "sessions": [entry for entry in manifest["sessions"] if entry["id"] in wanted],
        "time_groups": groups,
    }
    axes = axis_owner.recording_time_axes(selected_manifest, durations_s=durations)
    fixtures = {}
    for entry in manifest["sessions"]:
        if entry["id"] in wanted:
            payload, _ = reader.bound_file(
                reader.TESTS / entry["file"], entry["sha256"]
            )
            fixtures[entry["id"]] = json.loads(payload)
    return {
        "manifest": manifest,
        "manifest_identity": _identity(manifest_path),
        "sessions": sessions,
        "fixtures": fixtures,
        "axes": axes,
        "groups": groups,
    }


def load_owners():
    """Use the existing isolated product package loader, without HA setup."""
    reader.moisture_owner()
    names = ("detector", "models", "parameters")
    modules = {
        name: importlib.import_module(f"_proxy_forms_core.{name}") for name in names
    }
    files = {
        Path(module.__file__).resolve()
        for name, module in sys.modules.items()
        if name.startswith("_proxy_forms_core.") and getattr(module, "__file__", None)
    }
    files.update(
        (
            Path(__file__),
            HERE / "proxy_forms.py",
            HERE / "replay_time_axis.py",
            ROOT / "custom_components/ha_sauna/defaults.json",
            ROOT / "tests/settings.json",
        )
    )
    return modules, [_identity(path) for path in sorted(files)]


def _state(detector, origin):
    """Project owner state only; never derive a replacement candidate or gate."""
    episode = getattr(detector, "door_episode", None)
    component = getattr(detector, "_original_infusion_candidate", None)
    infusion = getattr(detector, "_infusion_episode", None)

    def picked(value, keys):
        return (
            {key: value[key] for key in keys if key in value}
            if value is not None
            else None
        )

    door_state = picked(
        episode,
        (
            "positions",
            "started_at",
            "first_observed_at",
            "sources",
            "epochs",
            "original_context",
            "original_evaluation_context",
            "original_thermal_context",
            "original_open",
            "original_close",
            "original_ventilation_newest",
            "original_ventilation_context",
            "original_ventilation_floor",
            "original_ventilation_reset_reason",
            "retained_heating_only",
            "invalid",
        ),
    )
    if door_state is not None:
        for key in (
            "original_ventilation",
            "original_ventilation_rh",
            "original_ventilation_rh_confirmed",
            "original_ventilation_complete",
        ):
            if key in episode:
                # Retain owner anchors; stored windows already have prefix diagnostics.
                door_state[key] = picked(
                    episode[key],
                    (
                        "received_at",
                        "newest",
                        "context",
                        "support",
                        "evaluated",
                        "initial_form",
                        "maintained_form",
                        "candidate_at",
                        "candidate_originals",
                        "bound_support",
                        "confirmable",
                        "proven",
                        "close_conflict",
                        "confirmation_received_at",
                        "confirmation_originals",
                        "paired_support",
                    ),
                )

    return relative(
        {
            "door_open": detector.open,
            "door_episode": door_state,
            "original_infusion_candidate": picked(
                component,
                (
                    "context",
                    "positions",
                    "received_at",
                    "newest",
                    "onsets",
                    "assignment",
                    "continuing",
                    "strong",
                    "qualified",
                    "fall_ready",
                    "fall_confirmed",
                    "rise_score",
                    "water_state",
                    "branch_floors",
                    "support_end",
                    "water_end",
                    "rise_ended",
                ),
            ),
            "infusion_episode": picked(
                infusion,
                (
                    "phase",
                    "positions",
                    "onset_at",
                    "observed_at",
                    "confirmed_at",
                    "confirmed_detection",
                    "level_end_at",
                    "end_basis",
                    "interrupted",
                    "last_rise_at",
                    "context",
                    "decay_floor",
                ),
            ),
            "original_sources": [
                {
                    "position": key[0],
                    "quantity": key[1],
                    **picked(value, ("source", "epoch", "started_at")),
                }
                for key, value in getattr(detector, "_curve_series", {}).items()
            ],
            "curve_candidates": [
                {
                    "route": key[0],
                    "position": key[1],
                    "quantity": key[2],
                    "candidate_id": value.candidate_id,
                    "onset_at": value.onset_at,
                    "context": value.context,
                }
                for key, value in getattr(detector, "_curve_candidates", {}).items()
            ],
        },
        origin,
    )


class _PrefixIterator:
    def __init__(self, values, begin, finish):
        self.values = tuple.__iter__(values)
        self.begin, self.finish_callback = begin, finish
        self.active = None

    def __iter__(self):
        return self

    def finish(self, aborted=False, boundary="next_prefix"):
        if self.active is not None:
            self.finish_callback(*self.active, aborted=aborted, boundary=boundary)
            self.active = None

    def __next__(self):
        self.finish()
        prefix = next(self.values)
        self.active = (prefix, self.begin(prefix))
        return prefix


class _ObservedPrefixes(tuple):
    """Preserve the original loop and tuple slicing, observing its boundaries."""

    def __new__(cls, values, begin, finish):
        instance = super().__new__(cls, values)
        instance.begin, instance.finish_callback = begin, finish
        instance.iterators = []
        return instance

    def __iter__(self):
        iterator = _PrefixIterator(self, self.begin, self.finish_callback)
        self.iterators.append(iterator)
        return iterator

    def finish(self, aborted=False):
        for iterator in self.iterators:
            iterator.finish(aborted, boundary="owner_return")


def instrument(detector, origin, emit, *, module=None):
    """Observe each actual prefix once; no owner splitting, retries or refits.

    Original traces overwrite prefix fields within a raster. The iterator saves
    each complete field before the following prefix can replace it, including
    early loop exits. Identity checks distinguish a new trace from a stale field
    left by a skipped branch. Original methods and prefix tuples stay intact.
    """
    originals = {}
    trace_keys = ("original_forms", "original_ventilation", "original_infusion")
    for name, trace_index in (
        ("_original_door_evidence", 4),
        ("_original_infusion_evidence", 5),
    ):
        original = getattr(detector, name)
        originals[name] = original

        def observed(
            *args, _original=original, _name=name, _trace_index=trace_index, **kwargs
        ):
            trace = args[_trace_index]

            def begin(_prefix):
                return (
                    _state(detector, origin),
                    {key: trace.get(key) for key in trace_keys},
                    relative(trace["decisions"], origin),
                )

            def finish(prefix, before, *, aborted, boundary):
                state, previous_fields, previous_decisions = before
                emit(
                    {
                        "type": "original_prefix",
                        "owner": _name,
                        "aborted": aborted,
                        "boundary": boundary,
                        "raster_at": trace["at"],
                        "received_at": prefix.received_at,
                        "original": prefix.original,
                        "source_context": prefix.context,
                        "before": state,
                        "after": _state(detector, origin),
                        "diagnostic": {
                            key: trace[key]
                            for key in trace_keys
                            if key in trace and trace[key] is not previous_fields[key]
                        },
                        "decisions": {
                            key: value
                            for key, value in trace["decisions"].items()
                            if relative(value, origin) != previous_decisions.get(key)
                        },
                    }
                )

            prefixes = _ObservedPrefixes(args[0], begin, finish)
            completed = False
            try:
                result = _original(prefixes, *args[1:], **kwargs)
                completed = True
                return result
            finally:
                prefixes.finish(aborted=not completed)

        setattr(detector, name, observed)
    original_sample = detector._sample
    originals["_sample"] = original_sample

    def sampled(*args, **kwargs):
        before = _state(detector, origin)
        completed = False
        try:
            result = original_sample(*args, **kwargs)
            completed = True
            return result
        finally:
            after = _state(detector, origin)
            if after != before or not completed:
                emit(
                    {
                        "type": "sample_state",
                        "raster_at": args[0],
                        "before": before,
                        "after": after,
                        "aborted": not completed,
                    }
                )

    detector._sample = sampled
    original_consume = detector._consume
    originals["_consume"] = original_consume

    def consumed(*args, **kwargs):
        before = _state(detector, origin)
        completed = False
        try:
            result = original_consume(*args, **kwargs)
            completed = True
            return result
        finally:
            after = _state(detector, origin)
            if after != before or not completed:
                emit(
                    {
                        "type": "input_consumption_state",
                        "raster_at": args[0],
                        "before": before,
                        "after": after,
                        "aborted": not completed,
                    }
                )

    detector._consume = consumed
    original_reset = detector._reset_original_ventilation
    originals["_reset_original_ventilation"] = original_reset

    def reset(*args, **kwargs):
        before = _state(detector, origin)
        result = original_reset(*args, **kwargs)
        emit(
            {
                "type": "original_ventilation_reset",
                "reason": args[1],
                "water": kwargs.get("water", True),
                "episode_started_at": args[0].get("started_at"),
                "before": before,
                "after": _state(detector, origin),
            }
        )
        return result

    detector._reset_original_ventilation = reset
    original_water_window = getattr(module, "original_water_window", None)
    if original_water_window is not None:

        def water_window(*args, **kwargs):
            result = original_water_window(*args, **kwargs)
            pairs = args[0]
            positions = []
            episode = detector.door_episode
            if pairs and episode is not None:
                positions = [
                    position
                    for position, state in episode.get(
                        "original_ventilation_water",
                        {},
                    ).items()
                    if state["pairs"] and state["pairs"][-1] is pairs[-1]
                ]
            emit(
                {
                    "type": "original_water_window",
                    "positions": positions,
                    "received_at": kwargs.get("at"),
                    "count": kwargs.get("count"),
                    "max_gap_seconds": kwargs.get("max_gap_seconds"),
                    "last_pair": pairs[-1] if pairs else None,
                    "original_result": result,
                }
            )
            return result

        module.original_water_window = water_window

    def restore():
        for name in originals:
            delattr(detector, name)
        if original_water_window is not None:
            module.original_water_window = original_water_window

    return restore


def _events(group, sessions, axes):
    events = []
    for member_index, member in enumerate(group["recordings"]):
        identity = member["id"]
        for role, points in sessions[identity]["original_series"].items():
            for point in points:
                if point["origin"] != "samples":
                    continue
                local = point["t"]
                if not reader.number(local) or local < 0:
                    raise ValueError(f"{identity}: invalid original received time")
                events.append(
                    (
                        axis_owner.sequence_time(axes[identity], local),
                        member_index,
                        point["source_index"],
                        identity,
                        role,
                        point,
                    )
                )
    events.sort(key=lambda item: item[:3])
    return events


def replay_group(
    group,
    sessions,
    axes,
    modules,
    origin,
    output_dir,
    *,
    fixtures=None,
    diagnostics=True,
):
    """Return one continuous group result; diagnostics=False is a parity probe."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    identity = group["id"]
    started_wall, started_cpu = time.perf_counter(), time.process_time()
    next_progress = started_wall
    progress_interval = json.loads((ROOT / "tests/settings.json").read_bytes())[
        "runner"
    ]["detector_replay_progress_s"]
    if not reader.number(progress_interval) or progress_interval <= 0:
        raise ValueError("detector_replay_progress_s must be positive finite seconds")
    diagnostic_path = output_dir / f"{identity}.diagnostics.jsonl.gz"
    detection_path = output_dir / f"{identity}.detections.json"
    progress_path = output_dir / f"{identity}.progress.json"
    parameters = modules["parameters"].Parameters({})
    effective = parameters.as_dict()
    counts = Counter()
    active = {"received_at_s": None, "inputs": []}
    events = _events(group, sessions, axes)
    input_anchors = {}
    for seconds, _, source_index, recording, role, point in events:
        input_anchors.setdefault(origin + timedelta(seconds=seconds), []).append(
            {
                "recording_id": recording,
                "source_index": source_index,
                "role": role,
                "local_t_s": point["t"],
            }
        )
    duration = max(
        axes[member["id"]]["origin_offset_s"] + sessions[member["id"]]["duration_s"]
        for member in group["recordings"]
    )
    if events:
        duration = max(duration, events[-1][0])
    detections = []
    accepted = 0
    previous, largest_gaps = {}, {}
    stream = gzip.GzipFile(filename=str(diagnostic_path), mode="wb", mtime=0)

    def emit(row):
        counts[row["type"]] += 1
        row = {"sequence": sum(counts.values()), "input_context": active, **row}
        if "received_at" in row:
            row["original_input_anchors"] = input_anchors.get(row["received_at"], [])
        stream.write(_canonical(relative(row, origin)) + b"\n")

    detector = modules["detector"].Detector(
        parameters,
        origin,
        observer=(lambda trace: emit({"type": "raster", "diagnostic": trace}))
        if diagnostics
        else None,
    )
    restore = (
        instrument(detector, origin, emit, module=modules["detector"])
        if diagnostics
        else lambda: None
    )
    evaluated_through = (
        math.ceil(duration / detector.grid_seconds) * detector.grid_seconds
    )

    def progress(seconds, status):
        record = {
            "id": identity,
            "status": status,
            "at_s": seconds,
            "recording_end_s": duration,
            "evaluated_through_s": evaluated_through,
            "accepted_originals": accepted,
            "consumed_originals": accepted - len(detector.pending),
            "pending_originals": len(detector.pending),
            "diagnostic_counts": dict(counts),
            "work": {
                "wall_seconds": time.perf_counter() - started_wall,
                "cpu_seconds": time.process_time() - started_cpu,
            },
        }
        progress_path.write_bytes(_canonical(record) + b"\n")
        stream.flush()
        print(
            f"DETECTOR {identity} {status} t={seconds:g}s "
            f"originals={accepted}/{len(events)} "
            f"wall={record['work']['wall_seconds']:.1f}s",
            flush=True,
        )
        return record

    # Every product grid is visited at its own virtual time, including empty
    # gaps. Originals at the same time precede that grid, in source-index order.
    schedule = [(event[0], 0, event[1], event[2], event) for event in events]
    schedule.extend(
        (index * detector.grid_seconds, 1, 0, 0, None)
        for index in range(math.ceil(duration / detector.grid_seconds) + 1)
    )
    schedule.sort(key=lambda item: item[:4])
    try:
        for seconds, batch in groupby(schedule, key=lambda item: item[0]):
            rows = list(batch)
            at = origin + timedelta(seconds=seconds)
            active.update(
                received_at_s=seconds,
                inputs=[
                    {
                        "recording_id": row[4][3],
                        "source_index": row[4][2],
                        "role": row[4][4],
                        "local_t_s": row[4][5]["t"],
                    }
                    for row in rows
                    if row[4] is not None
                ],
            )
            for _, kind, _, _, event in rows:
                if kind == 1:
                    detections.extend(detector.advance(at, enabled=True))
                    continue
                _, _, _, recording, role, point = event
                position, quantity = role.split("_", 1)
                value = point["value"] if reader.number(point["value"]) else None
                measurement = modules["models"].Measurement(
                    modules["models"].Position(position),
                    modules["models"].Quantity(quantity),
                    value,
                    str(point["value"]),
                    f"bound-role:{role}",
                    at,
                )
                if not detector.accept(measurement):
                    raise ValueError(
                        f"Causal original rejected: {recording}/{point['source_index']}"
                    )
                accepted += 1
                if role in previous:
                    largest_gaps[role] = max(
                        largest_gaps.get(role, 0), seconds - previous[role]
                    )
                previous[role] = seconds
            if time.perf_counter() >= next_progress:
                progress(seconds, "running")
                next_progress = time.perf_counter() + progress_interval
        if detector.pending or accepted != len(events):
            raise ValueError("The terminal product grid did not consume every original")
        completed = progress(evaluated_through, "completed")
        result = {
            "id": identity,
            "recordings": [member["id"] for member in group["recordings"]],
            "duration_s": duration,
            "detector_instances": 1,
            "original_end_s": events[-1][0] if events else None,
            "recording_end_s": duration,
            "evaluated_through_s": evaluated_through,
            "terminal_processing_s": evaluated_through - duration,
            "accepted_originals": accepted,
            "consumed_originals": accepted - len(detector.pending),
            "pending_originals": len(detector.pending),
            "work": completed["work"],
            "original_rows": len(events),
            "rows_by_role": dict(Counter(event[4] for event in events)),
            "largest_received_gap_s_by_role": largest_gaps,
            "parameters": effective,
            "parameters_sha256": reader.sha(_canonical(effective)),
            "recording_parameter_provenance": {
                member["id"]: {
                    "parameter_source": fixtures[member["id"]]
                    .get("configuration", {})
                    .get("parameter_source"),
                    "recorded_parameters_sha256": (
                        reader.sha(
                            _canonical(
                                fixtures[member["id"]]
                                .get("configuration", {})
                                .get("parameters", {}),
                            )
                        )
                        if "parameters"
                        in fixtures[member["id"]].get("configuration", {})
                        else None
                    ),
                    "configuration_sha256": reader.sha(
                        _canonical(
                            fixtures[member["id"]].get("configuration", {}),
                        )
                    ),
                    "recorded_overrides_applied": False,
                    "recording_sections": fixtures[member["id"]].get(
                        "recording_sections", []
                    ),
                }
                for member in group["recordings"]
            }
            if fixtures is not None
            else {},
            "time_axes": {
                member["id"]: axes[member["id"]] for member in group["recordings"]
            },
            "input_provenance": {
                member["id"]: sessions[member["id"]]["provenance"]
                for member in group["recordings"]
            },
            "diagnostic_counts": dict(counts),
            "rejected": detector.rejected,
            "detections": relative(detections, origin),
            "final_owner_state": _state(detector, origin),
            "diagnostics_file": diagnostic_path.name if diagnostics else None,
            "detections_file": detection_path.name,
        }
        detection_path.write_bytes(_canonical(result["detections"]) + b"\n")
        return result
    finally:
        restore()
        stream.close()


def run_replay(manifest_path, output_dir, recordings=None):
    inputs = load_inputs(manifest_path, recordings)
    modules, provenance = load_owners()
    settings = json.loads((ROOT / "tests/settings.json").read_bytes())
    origin = datetime.fromisoformat(settings["fixtures"]["session"]["origin"])
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": 1,
        "manifest": inputs["manifest_identity"],
        "product_sources": provenance,
        "context": {
            "enabled": True,
            "allowed": None,
            "controller": False,
            "heating_evidence": False,
            "parameters": "Current product Parameters({}); recorded overrides are provenance only",
            "initial_state": "No snapshot injected; only original received samples, including across recording boundaries",
            "initial_door_state": "Unchanged product constructor starts closed; this is not physical ground truth",
            "time": "Relative group seconds; each unchanged product grid runs after all originals received at that time; final ceil grid consumes the last originals without creating measurements",
            "sources": "Stable functional-role source within a time group; no archive-boundary reset",
            "scope": "Standalone measurement comparison; no historical control lifecycle, actuator, occupancy or event truth claims",
            "diagnostics": "Original observer traces and observed original-prefix boundaries; no replacement scores or extra owner evaluations",
            "limitations": "No independent event labels or hit/false-positive rates; no heating-only route without confirmed heating evidence",
        },
        "status": "running",
        "active_group": None,
        "groups": [],
    }
    report_path = output_dir / "detector-replay.json"
    report_path.write_bytes(_canonical(report) + b"\n")
    try:
        for group in inputs["groups"]:
            report["active_group"] = group["id"]
            report_path.write_bytes(_canonical(report) + b"\n")
            print(f"DETECTOR {group['id']} BEGIN", flush=True)
            report["groups"].append(
                replay_group(
                    group,
                    inputs["sessions"],
                    inputs["axes"],
                    modules,
                    origin,
                    output_dir,
                    fixtures=inputs["fixtures"],
                )
            )
            report_path.write_bytes(_canonical(report) + b"\n")
            print(f"DETECTOR {group['id']} END", flush=True)
        report.update(status="completed", active_group=None)
    except BaseException as error:
        report.update(status="failed", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        report_path.write_bytes(_canonical(report) + b"\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="Manifest with fixture paths relative to repository tests/",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--recordings", help="Comma-separated IDs; select only complete time groups"
    )
    args = parser.parse_args()
    if platform.system() != "Linux":
        parser.error("Product Detector replays run exclusively on Linux")
    run_replay(args.manifest, args.output, args.recordings)


if __name__ == "__main__":
    main()
