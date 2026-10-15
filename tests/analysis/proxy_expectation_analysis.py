"""Inspect fixed climate hypotheses on later originals; run only on Linux.

Usage::

    python tests/analysis/proxy_expectation_analysis.py \
        --output DIR --cases-json "$SAUNA_EXPECTATION_CASES"

``--cases-json`` is a JSON string, never a filename. Its schema is::

    {"schema_version": 1, "cases": [{
      "id": "explicit-hypothesis", "recording": "manifest-id",
      "bind_at_s": 100.0, "through_s": 130.0, "direction": -1,
      "channels": [{"position": "upper", "quantity": "temperature",
                    "onset_at_s": 90.123456,
                    "floor_at_s": 70.0, "points_not_before_s": 60.0}]
    }]}

Times are seconds relative to the existing session input catalogue's origin.
Each channel needs its own exact original onset. Direction must be explicit at
case or channel level; a channel value takes precedence. Optional floors retain
the owner's source-segment beginning; they cannot lower it. A channels list may
contain any nonempty subset of the four T/RH roles. There are no recording-based
branches, event labels, tuned windows, new numerical defaults or fitted profiles.

The initial assessment uses only inputs received by bind_at_s. Later numerical
assessments require a genuinely later original in that channel. Corrections,
source epochs and expired anchors cannot supply fresh confirmation. M1 and M2
remain separate comparisons with their own fixed branch identity; no OR/min
selects an opening. Original pre/post-onset trends and the strictly new segment
are separate assessments with their own noise scales and are never score-added.

This prepares a future calibrated expectation profile. A fixed onset still
refits coefficients, so these results are not frozen value predictions, event
probabilities, event decisions or product validation.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta
import hashlib
import importlib
import importlib.util
from itertools import groupby
import json
import math
from pathlib import Path
import re
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
ROLES = (
    ("upper", "temperature"), ("upper", "humidity"),
    ("lower", "temperature"), ("lower", "humidity"),
)


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def finite_number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def serializable(value):
    if is_dataclass(value) and not isinstance(value, type):
        return serializable(asdict(value))
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): serializable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [serializable(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return {"nonfinite_original": repr(value)}
    return value


def encoded(value):
    return json.dumps(serializable(value), sort_keys=True, ensure_ascii=False,
                      allow_nan=False, separators=(",", ":")).encode("utf-8")


def write_json(path, value):
    """Keep completed cases available even if a later case/process fails."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(encoded(value) + b"\n")
    temporary.replace(path)


def validate_cases(document):
    """Validate explicit hypotheses before importing any product modules."""
    if (not isinstance(document, dict)
            or type(document.get("schema_version")) is not int
            or document.get("schema_version") != 1
            or set(document) != {"schema_version", "cases"}
            or not isinstance(document["cases"], list)
            or not document["cases"]):
        raise ValueError("Expected schema_version 1 and a nonempty cases list")
    identities = set()
    for case in document["cases"]:
        required = {"id", "recording", "bind_at_s", "through_s", "channels"}
        if (not isinstance(case, dict) or not required <= set(case)
                or set(case) - required - {"direction"}):
            raise ValueError(
                "A case needs id, recording, bind_at_s, through_s, channels",
            )
        identity = case["id"]
        if (not isinstance(identity, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", identity) is None
                or identity in identities):
            raise ValueError("Case id must be unique and filename-safe")
        identities.add(identity)
        if not isinstance(case["recording"], str) or not case["recording"]:
            raise ValueError(f"{identity}: recording must be a manifest id")
        if (not finite_number(case["bind_at_s"]) or case["bind_at_s"] < 0
                or not finite_number(case["through_s"])
                or case["through_s"] <= case["bind_at_s"]):
            raise ValueError(f"{identity}: require 0 <= bind_at_s < through_s")
        if "direction" in case and (
            type(case["direction"]) is not int or case["direction"] not in (-1, 1)
        ):
            raise ValueError(f"{identity}: direction must be -1 or 1")
        channels = case["channels"]
        if not isinstance(channels, list) or not 1 <= len(channels) <= len(ROLES):
            raise ValueError(f"{identity}: channels must contain 1 to 4 explicit roles")
        seen = set()
        for channel in channels:
            required_channel = {"position", "quantity", "onset_at_s"}
            optional = {"direction", "floor_at_s", "points_not_before_s"}
            if (not isinstance(channel, dict)
                    or not required_channel <= set(channel)
                    or set(channel) - required_channel - optional):
                raise ValueError(f"{identity}: malformed channel")
            role = (channel["position"], channel["quantity"])
            if role not in ROLES or role in seen:
                raise ValueError(f"{identity}: channel must be a unique T/RH role")
            seen.add(role)
            direction = channel.get("direction", case.get("direction"))
            if type(direction) is not int or direction not in (-1, 1):
                raise ValueError(
                    f"{identity}/{role}: explicit direction -1 or 1 required",
                )
            for key in ("onset_at_s", "floor_at_s", "points_not_before_s"):
                if key in channel and (
                    not finite_number(channel[key])
                    or channel[key] < 0 or channel[key] > case["bind_at_s"]
                ):
                    raise ValueError(
                        f"{identity}/{role}: {key} must be in causal prefix",
                    )
    return document


def load_owners():
    """Reuse the bound reader and its isolated core package, without HA setup."""
    path = Path(__file__).with_name("proxy_forms.py")
    spec = importlib.util.spec_from_file_location("_expectation_reader", path)
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    reader.moisture_owner()  # Existing isolated-package loader, not an AH vote.
    names = ("curve_change", "detector", "models", "parameters")
    modules = {
        name: importlib.import_module(f"_proxy_forms_core.{name}") for name in names
    }
    files = [path, Path(__file__).resolve(), TESTS / "settings.json",
             ROOT / "custom_components/ha_sauna/defaults.json"]
    files.extend(Path(module.__file__) for module in modules.values())
    provenance = [{"file": str(item.relative_to(ROOT)),
                   "sha256": digest(item.read_bytes())} for item in files]
    return reader, modules, provenance


class FitCache:
    """Reuse only mathematically identical public-owner calls, including None."""

    def __init__(self, module, policy):
        self.module, self.policy = module, policy
        self.changes, self.trends = {}, {}
        self.counts = Counter()

    def _call(self, function, *args, **kwargs):
        original_fit = self.module._fit
        original_solve = self.module._least_squares

        def counted_fit(*fit_args, **fit_kwargs):
            self.counts["robust_fit_calls"] += 1
            return original_fit(*fit_args, **fit_kwargs)

        def counted_solve(*solve_args, **solve_kwargs):
            self.counts["least_squares_calls"] += 1
            return original_solve(*solve_args, **solve_kwargs)

        self.module._fit = counted_fit
        self.module._least_squares = counted_solve
        try:
            return function(*args, **kwargs)
        finally:
            self.module._fit = original_fit
            self.module._least_squares = original_solve

    def change(self, points, onset, floor, direction, order, context):
        key = (points, self.policy, onset, floor, direction, order, context)
        if key not in self.changes:
            self.counts["change_calls"] += 1
            self.changes[key] = self._call(
                self.module.curve_change, points,
                policy=self.policy, onset_at=onset, direction=direction,
                onset_not_before=floor, hinge_order=order,
            )
        else:
            self.counts["change_cache_hits"] += 1
        return self.changes[key]

    def trend(self, points, context):
        key = (points, self.policy, context)
        if key not in self.trends:
            self.counts["trend_calls"] += 1
            self.trends[key] = self._call(
                self.module.curve_trend, points, policy=self.policy,
            )
        else:
            self.counts["trend_cache_hits"] += 1
        return self.trends[key]


def source_state(series):
    return {key: series[key] for key in (
        "source", "time_basis", "epoch", "version", "started_at", "received_at",
        "last_original_version", "last_original_received_at",
    )}


def context_identity(series):
    return (series["source"], series["time_basis"], series["epoch"])


def assessment(features):
    return {"status": "unresolved" if features is None else features.evidence,
            "reason": "owner_returned_none" if features is None else None,
            "features": serializable(features)}


def trend_assessment(cache, points, context):
    features = cache.trend(points, context)
    return {
        "status": "unresolved" if features is None else "observed",
        "reason": "owner_returned_none" if features is None else None,
        "original_count": len(points), "support_sha256": digest(encoded(points)),
        "first_at": points[0][0] if points else None,
        "newest_at": points[-1][0] if points else None,
        "features": serializable(features),
    }


class BoundChannel:
    """One explicit temporal hypothesis; no candidate replacement or events."""

    def __init__(self, case, channel, detector, models, origin, at):
        self.spec = channel
        self.position = models.Position(channel["position"])
        self.quantity = models.Quantity(channel["quantity"])
        self.key = (self.position, self.quantity)
        self.onset = origin + timedelta(seconds=channel["onset_at_s"])
        self.direction = channel.get("direction", case.get("direction"))
        self.cutoff = (origin + timedelta(seconds=channel["points_not_before_s"])
                       if "points_not_before_s" in channel else None)
        series = detector._curve_series[self.key]
        declared_floor = (origin + timedelta(seconds=channel["floor_at_s"])
                          if "floor_at_s" in channel else None)
        self.floor = max(
            stamp for stamp in (declared_floor, series["started_at"] or origin)
            if stamp is not None
        )
        self.context = context_identity(series)
        points = detector._curve_points(*self.key, at, points_not_before=self.cutoff)
        self.first_newest = points[-1][0] if points else None
        self.initial_points = points
        self.used_points = dict(points)
        self.reason = None
        if not points:
            self.reason = "no_original_support_at_binding"
        elif self.onset not in self.used_points:
            self.reason = "onset_not_an_available_original_at_binding"
        elif self.onset < self.floor:
            self.reason = "onset_before_bound_source_floor"
        self.binding = {
            "position": self.position, "quantity": self.quantity,
            "direction": self.direction, "onset_at": self.onset,
            "declared_floor_at": declared_floor, "effective_floor_at": self.floor,
            "points_not_before": self.cutoff, "bound_at": at,
            "first_newest_at": self.first_newest,
            "source_state": source_state(series),
            "initial_support_sha256": digest(encoded(points)),
            "initial_original_points": points, "unresolved_reason": self.reason,
        }

    def support_at(self, detector, at, update=None):
        """Inspect source/anchor continuity without inventing a numerical prefix."""
        series = detector._curve_series[self.key]
        points = detector._curve_points(*self.key, at, points_not_before=self.cutoff)
        if self.reason is None and context_identity(series) != self.context:
            self.reason = "bound_source_context_interrupted"
        if (self.reason is None and update is not None
                and update["kind"] == "correction"
                and update["original_at"] in self.used_points):
            self.reason = "previously_assessed_original_corrected"
        if self.reason is None and not any(stamp == self.onset for stamp, _ in points):
            self.reason = "fixed_original_onset_expired"
        if (self.reason is None and detector._value(*self.key, at) is None):
            self.reason = "original_source_unavailable"
        return series, points

    def evaluate(self, detector, cache, at, update=None):
        started_wall, started_cpu = time.perf_counter(), time.process_time()
        counts = cache.counts.copy()
        series, points = self.support_at(detector, at, update)
        fresh = tuple(point for point in points
                      if self.first_newest is not None and point[0] > self.first_newest)
        reason = self.reason
        if update is not None and reason is None:
            if update["kind"] != "new_original" or not fresh:
                reason = "not_a_later_original_state"
        row = {
            "at": at, "position": self.position, "quantity": self.quantity,
            "phase": "binding" if update is None else "later_original_state",
            "onset_at": self.onset, "requested_change_direction": self.direction,
            "source_state": source_state(series), "input_update": update,
            "support_sha256": digest(encoded(points)), "original_points": points,
            "original_count": len(points), "fresh_original_count": len(fresh),
            "first_newest_at": self.first_newest,
            "newest_at": points[-1][0] if points else None,
            "reason": reason,
        }
        if reason is None:
            row["M1"] = assessment(cache.change(
                points, self.onset, self.floor, self.direction, 1, self.context,
            ))
            row["M2"] = assessment(cache.change(
                points, self.onset, self.floor, self.direction, 2, self.context,
            ))
            # Trends never inherit the model fit's sign or model noise scale.
            before = tuple(point for point in points
                           if self.floor <= point[0] < self.onset)
            after = tuple(point for point in points if point[0] > self.onset)
            row["before_trend"] = trend_assessment(cache, before, self.context)
            row["after_trend"] = trend_assessment(cache, after, self.context)
            row["new_original_trend"] = trend_assessment(cache, fresh, self.context)
            self.used_points.update(points)
        else:
            unresolved = {"status": "unresolved", "reason": reason, "features": None}
            for name in ("M1", "M2", "before_trend", "after_trend",
                         "new_original_trend"):
                row[name] = dict(unresolved)
        row["work"] = {
            "wall_seconds": time.perf_counter() - started_wall,
            "cpu_seconds": time.process_time() - started_cpu,
            "calls": dict(cache.counts - counts),
        }
        return row


def received_events(session):
    """Restore the reader's original received order; do not resample or smooth."""
    events = []
    for role, originals in session["original_series"].items():
        for point in originals:
            if point["origin"] != "samples":
                continue
            if not finite_number(point["t"]) or point["t"] < 0:
                raise ValueError("Bound source contains an invalid received time")
            events.append((point["t"], point["source_index"], role, point))
    events.sort(key=lambda item: (item[0], item[1]))
    return events


def feed_original(detector, models, origin, event):
    seconds, source_index, role, point = event
    position, quantity = role.split("_", 1)
    key = (models.Position(position), models.Quantity(quantity))
    series = detector._curve_series[key]
    previous = source_state(series)
    before_last = series["points"][-1] if series["points"] else None
    value = point["value"] if finite_number(point["value"]) else None
    at = origin + timedelta(seconds=seconds)
    measurement = models.Measurement(
        *key, value, str(point["value"]), f"bound-role:{role}", at,
    )
    accepted = detector.accept(measurement)
    if accepted:
        detector._consume(at)
    series = detector._curve_series[key]
    if not accepted:
        kind = "rejected_input"
    elif series["epoch"] != previous["epoch"]:
        kind = "source_context_reset"
    elif series["version"] == previous["version"]:
        kind = "duplicate"
    elif (series["last_original_version"] == series["version"]
          and series["points"]):
        kind = "new_original"
    else:
        kind = "correction"
    return key, {
        "kind": kind, "source_index": source_index, "received_at": at,
        "original_at": at, "source_role": role, "raw_value": point["value"],
        "normalized_value": value, "previous_last_point": before_last,
        "reported_age_s": point.get("reported_age_s"),
        "source_state_before": previous,
    }


def analyze_recording(cases, session, fixture, modules, origin, output):
    started_wall, started_cpu = time.perf_counter(), time.process_time()
    parameters = modules["parameters"].Parameters(
        fixture.get("configuration", {}).get("parameters", {}),
    )
    detector = modules["detector"].Detector(parameters, origin)
    cache = FitCache(modules["curve_change"], detector.curve_policy)
    events = received_events(session)
    timeline = [(event[0], 0, event[1], event) for event in events
                if event[0] <= max(case["through_s"] for case in cases)]
    timeline.extend((case["bind_at_s"], 1, index, case)
                    for index, case in enumerate(cases))
    timeline.extend((case["through_s"], 2, index, case)
                    for index, case in enumerate(cases))
    timeline.sort(key=lambda item: item[:3])
    active, results = {}, {}
    audit = Counter()
    for seconds, group in groupby(timeline, key=lambda item: item[0]):
        at = origin + timedelta(seconds=seconds)
        updates = {}
        for _, kind, _, item in group:
            if kind == 0:
                key, update = feed_original(detector, modules["models"], origin, item)
                audit[update["kind"]] += 1
                # Preserve all same-time source messages, including corrections.
                for state in active.values():
                    if (seconds <= state["spec"]["through_s"]
                            and key in state["channels"]):
                        state["result"]["input_audit"].append(update)
                        bound = state["channels"][key]
                        if (update["kind"] == "correction"
                                and update["original_at"] in bound.used_points):
                            bound.reason = "previously_assessed_original_corrected"
                # A same-time duplicate does not erase a genuinely new state
                # already received in this batch. A correction does replace it.
                if update["kind"] != "duplicate" or key not in updates:
                    updates[key] = update
            elif kind == 1:
                case = item
                result = {
                    "case": case, "recording": session["id"],
                    "source": session["provenance"],
                    "parameters": parameters.as_dict(),
                    "parameters_sha256": digest(encoded(parameters.as_dict())),
                    "curve_policy": asdict(detector.curve_policy),
                    "binding": [], "rows": [], "input_audit": [],
                    "status": "running", "event_probability": None,
                    "value_prediction": None,
                    "prediction_limitation": (
                        "fixed onset refits coefficients; no frozen forecast"
                    ),
                }
                channels = {}
                for spec in case["channels"]:
                    bound = BoundChannel(
                        case, spec, detector, modules["models"], origin, at,
                    )
                    channels[bound.key] = bound
                    result["binding"].append(bound.binding)
                    result["rows"].append(bound.evaluate(detector, cache, at))
                active[case["id"]] = {
                    "spec": case, "channels": channels, "result": result,
                }
                results[case["id"]] = result
            else:
                # A horizon is metadata, never a fabricated numerical prefix.
                state = active[item["id"]]
                state["finish_after_updates"] = True
        for identity, state in list(active.items()):
            if seconds > state["spec"]["bind_at_s"]:
                for key, update in updates.items():
                    if key not in state["channels"]:
                        continue
                    # No timer/duplicate checks; corrections remain explicit
                    # unresolved audit rows, never numerical confirmations.
                    if update["kind"] == "duplicate":
                        continue
                    state["result"]["rows"].append(
                        state["channels"][key].evaluate(detector, cache, at, update),
                    )
            if state.get("finish_after_updates"):
                result = state["result"]
                result["status"] = "completed"
                result["last_bound_source_received_s"] = (
                    events[-1][0] if events else None
                )
                result["source_ends_before_requested_horizon"] = (
                    not events or events[-1][0] < state["spec"]["through_s"]
                )
                result["terminal_support"] = []
                for bound in state["channels"].values():
                    series, points = bound.support_at(detector, at)
                    result["terminal_support"].append({
                        "position": bound.position, "quantity": bound.quantity,
                        "at": at, "status": (
                            "unresolved" if bound.reason
                            else "original_context_available"
                        ),
                        "reason": bound.reason,
                        "source_state": source_state(series),
                        "original_count": len(points), "numerically_rechecked": False,
                    })
                case_counts = Counter()
                for row in result["rows"]:
                    case_counts.update(row["work"]["calls"])
                result["work"] = {
                    "wall_seconds": sum(row["work"]["wall_seconds"]
                                        for row in result["rows"]),
                    "cpu_seconds": sum(row["work"]["cpu_seconds"]
                                       for row in result["rows"]),
                    "calls": dict(case_counts),
                    "scope": "case assessments; source preparation timed per recording",
                }
                write_json(output / f"case-{identity}.json", result)
                print(
                    f"Completed expectation case {identity}: "
                    f"{len(result['rows'])} rows",
                    flush=True,
                )
                del active[identity]
    return {
        "recording": session["id"], "source": session["provenance"],
        "bound_original_messages_read": len(events),
        "consumed_message_kinds": dict(audit),
        "numerical_calls": dict(cache.counts),
        "wall_seconds": time.perf_counter() - started_wall,
        "cpu_seconds": time.process_time() - started_cpu,
        "cases": [results[case["id"]] for case in cases],
    }


def run_analysis(document, output):
    """Read each bound original sample file once, without replaying events."""
    validate_cases(document)
    started_wall, started_cpu = time.perf_counter(), time.process_time()
    reader, modules, owners = load_owners()
    manifest_path = TESTS / "replay_manifest.json"
    manifest_payload = manifest_path.read_bytes()
    manifest = json.loads(manifest_payload)
    entries = {entry["id"]: entry for entry in manifest["sessions"]}
    settings_payload = (TESTS / "settings.json").read_bytes()
    origin = datetime.fromisoformat(
        json.loads(settings_payload)["fixtures"]["session"]["origin"],
    )
    grouped = defaultdict(list)
    for case in document["cases"]:
        if case["recording"] not in entries:
            raise ValueError(f"Unknown manifest recording: {case['recording']}")
        grouped[case["recording"]].append(case)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    result = {
        "schema_version": 1, "status": "running", "input": document,
        "input_sha256": digest(encoded(document)), "origin": origin,
        "owners": owners,
        "manifest": {"file": str(manifest_path.relative_to(ROOT)),
                     "sha256": digest(manifest_payload)},
        "semantics": {
            "purpose": (
                "preparatory fixed-hypothesis and observed-sequence investigation"
            ),
            "inputs": (
                "only bound original T/RH received messages; original source order"
            ),
            "time_basis": (
                "received, matching replay HADevice Measurement; "
                "reported_age metadata stays separate"
            ),
            "source_identity": (
                "bound-role alias; physical source ids were removed "
                "by the fixture transformation"
            ),
            "initial_snapshots": (
                "excluded: historical initial state is not a new original report"
            ),
            "owner": (
                "Detector.accept/_consume/_curve_points/_value; "
                "no advance, Runtime or event decisions"
            ),
            "prefixes": (
                "binding plus genuinely changed native channel states; "
                "no interpolated or timer points"
            ),
            "models": (
                "M1 and M2 fixed own onset, same points/policy/floor/direction; "
                "separate evidence, no OR/min"
            ),
            "trends": (
                "strict pre/post-onset originals and strictly "
                "post-binding-newest originals; independent scales"
            ),
            "floor": (
                "max(explicit floor, bound owner source started_at); "
                "fixed for the hypothesis"
            ),
            "correction": (
                "previously assessed corrected originals invalidate the bound "
                "hypothesis; never freshness"
            ),
            "unresolved": (
                "missing/expired original, broken context or owner None "
                "cannot reject or confirm an event"
            ),
            "probability": (
                "not estimated; scores are model comparisons, "
                "not calibrated event probabilities"
            ),
            "forecast": (
                "not available; coefficients refit on new prefixes, "
                "no calibrated frozen profile"
            ),
            "source_independence": (
                "not established; shared source hashes do not provide "
                "independent calibration and validation recordings"
            ),
        },
        "recordings": [],
    }
    write_json(output / "expectation-analysis.json", result)
    for recording, cases in grouped.items():
        entry = entries[recording]
        session = reader.read_session(entry)
        fixture_payload, _ = reader.bound_file(TESTS / entry["file"], entry["sha256"])
        # The existing climate reader returns no recorded configuration. Keep
        # its own read count scoped honestly; only the small fixture is reread.
        provenance = session["provenance"]
        provenance["reader_read_count_per_bound_file"] = provenance.pop(
            "read_count_per_bound_file",
        )
        provenance["analysis_total_read_counts"] = {"fixture": 2, "samples": 1}
        provenance["additional_fixture_read_purpose"] = (
            "consume the bound recorded Parameters input"
        )
        recording_result = analyze_recording(
            cases, session, json.loads(fixture_payload), modules, origin, output,
        )
        result["recordings"].append(recording_result)
        write_json(output / "expectation-analysis.json", result)
    result["status"] = "completed"
    result["work"] = {"wall_seconds": time.perf_counter() - started_wall,
                      "cpu_seconds": time.process_time() - started_cpu}
    write_json(output / "expectation-analysis.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--cases-json", required=True, help="Explicit schema-v1 JSON string",
    )
    args = parser.parse_args(argv)
    try:
        document = json.loads(args.cases_json)
        validate_cases(document)
    except (TypeError, ValueError) as error:
        parser.error(str(error))
    run_analysis(document, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
