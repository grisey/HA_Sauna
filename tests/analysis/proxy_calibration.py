"""Fit explicit four-channel templates and inspect their frozen continuations.

Linux only::

    python tests/analysis/proxy_calibration.py --output DIR

Only manifest-bound original T/RH measurements enter the mathematics. Presence,
stored detector outputs and replay expectations never supply features or exact
event times. R01/R02 provide derived form witnesses; only R03 has a complete,
user-confirmed door/infusion inventory. R04 is inspected last, with coefficients,
methods and error bands already frozen. Previously inspected R04 is not an
independent untouched validation set. No output is an event probability.
"""

from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter, OrderedDict
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import gzip
import hashlib
import html
import importlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import time
from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
HERE = Path(__file__).resolve().parent
EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)
CHANNELS = ("upper_temperature", "upper_humidity",
            "lower_temperature", "lower_humidity")
KINDS = ("door_open", "door_close", "infusion")


class WorkLimitError(RuntimeError):
    """An explicit work limit is incomplete analysis, never negative evidence."""


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def finite(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def at(seconds):
    return EPOCH + timedelta(seconds=seconds)


def seconds(stamp):
    return (stamp - EPOCH).total_seconds()


def serializable(value, places=9):
    if is_dataclass(value) and not isinstance(value, type):
        return serializable(asdict(value), places)
    if isinstance(value, datetime):
        return seconds(value)
    if isinstance(value, dict):
        return {str(key): serializable(item, places) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [serializable(item, places) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Nonfinite derived output")
        # Executable coefficients, normalization and empirical bands must
        # round-trip exactly; decimal shortening belongs only to SVG views.
        return value
    return value


def encoded(value, places=9):
    return (json.dumps(serializable(value, places), sort_keys=True,
                       ensure_ascii=False, allow_nan=False,
                       separators=(",", ":")) + "\n").encode("utf-8")


def write_json(path, value, places=9):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(encoded(value, places))
    temporary.replace(path)


def file_identity(path):
    try:
        name = str(path.relative_to(ROOT))
    except ValueError:
        name = str(path)
    payload = path.read_bytes()
    return {"file": name, "sha256": digest(payload), "bytes": len(payload)}


def validate(policy, references):
    """Fail malformed or unbounded input before importing the numerical owner."""
    if policy.get("schema_version") != 1 or references.get("schema_version") != 1:
        raise ValueError("Policy and references require schema_version 1")
    if policy.get("session_ids") != ["R01", "R02", "R03", "R04"]:
        raise ValueError("Exactly ordered R01-R04 is required")
    if (policy.get("training_session_ids") != ["R01", "R02", "R03"]
            or policy.get("holdout_session_ids") != ["R04"]):
        raise ValueError("R04 must remain outside all calibration selection")
    if policy["calibration"]["channels"] != list(CHANNELS):
        raise ValueError("The four declared original channels are required")
    for section in ("sampling", "localization", "limits"):
        for key, value in policy[section].items():
            if key == "interpretation":
                continue
            allow_zero = key in ("minimum_bic_gain", "maximum_resolved_contrary_channels")
            if not finite(value) or value < 0 or value == 0 and not allow_zero:
                raise ValueError(f"Positive finite {section}.{key} required")
    for key, value in policy["limits"].items():
        if type(value) is not int:
            raise ValueError(f"Integer work limit required: {key}")
    for key in ("minimum_consistent_channels", "maximum_resolved_contrary_channels"):
        if type(policy["localization"][key]) is not int:
            raise ValueError(f"Integer localization count required: {key}")
    if policy["sampling"]["minimum_span_fraction"] > 1:
        raise ValueError("Support span fraction must be <=1")
    methods = policy["method_search"]
    if (methods["bases"] != ["P2", "P3", "M1", "M2"]
            or not methods["durations_s"]
            or any(not finite(item) or item <= 0
                   for item in methods["durations_s"])
            or not 0 < methods["binding_fraction"] < 1
            or not 0 < methods["hinge_knot_x"] < 1):
        raise ValueError("Explicit positive durations and four declared bases required")
    if len(methods["durations_s"]) * len(methods["bases"]) > methods["maximum_methods_per_profile"]:
        raise ValueError("Declared method budget is too small")
    if [item["kind"] for item in references["profiles"]] != list(KINDS):
        raise ValueError("One definition for each declared profile kind required")
    identities = set()
    for reference in (*references["form_witnesses"],
                      *references["event_references"]):
        if reference["id"] in identities:
            raise ValueError("Reference identities must be unique")
        identities.add(reference["id"])
        start, end = reference["allowed_onset_s"]
        if (reference["recording"] not in policy["training_session_ids"]
                or reference["kind"] not in KINDS
                or not finite(start) or not finite(end) or not 0 <= start < end):
            raise ValueError("Invalid or holdout-derived calibration reference")
    if any(item["recording"] != "R03"
           for item in references["event_references"]):
        raise ValueError("Only R03 currently has event inventory targets")
    calibration = policy["calibration"]
    if (not 0 < calibration["residual_quantile"] <= 1
            or calibration["residual_limit_multiplier"] < 1
            or calibration["minimum_gain"] < 0):
        raise ValueError("Invalid empirical error-band policy")


def resolve_numerics(policy):
    """Consume the existing catalogue; the analysis has no second defaults set."""
    owner = policy["numerical_owner"]
    path = ROOT / owner["file"]
    catalogue = json.loads(path.read_bytes())
    parameters = {item["key"]: item for item in catalogue["parameters"]}
    policy["numerics"] = {field: parameters[key]["default"]
                          for field, key in owner["parameters"].items()}
    policy["resolved_numerical_owner"] = {**file_identity(path),
                                           "mapping": owner["parameters"],
                                           "values": policy["numerics"]}


def derive_noise_floors(indices, policy):
    """A declared quantization assumption from training originals, never R04."""
    floors, provenance = [], {}
    for role in CHANNELS:
        counts = {recording: len(indices[recording][role].points)
                  for recording in policy["training_session_ids"]}
        values = sorted({Decimal(str(value))
                         for recording in policy["training_session_ids"]
                         for _, value in indices[recording][role].points})
        differences = [right - left for left, right in zip(values, values[1:])
                       if right > left]
        if not differences:
            raise ValueError(f"No observed positive value step for numerical floor: {role}")
        step = min(differences)
        floor = float(step) / math.sqrt(12.0)
        if not finite(floor) or floor <= 0:
            raise ValueError(f"Unusable observed quantization step: {role}")
        floors.append(floor)
        provenance[role] = {"training_recordings": policy["training_session_ids"],
                            "original_count_by_recording": counts,
                            "distinct_value_count": len(values),
                            "positive_adjacent_value_difference_count": len(differences),
                            "smallest_positive_value_step_decimal": str(step),
                            "quantization_model_floor": floor,
                            "model_assumption": "uniform rounding error: sigma=step/sqrt(12)",
                            "actual_noise": "max(quantization_model_floor, frozen pre-onset detrended residual MAD)",
                            "sensor_accuracy_claim": False, "R04_used": False}
    policy["calibration"]["noise_floors"] = floors
    policy["derived_noise_floor_provenance"] = provenance


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_owners():
    reader = load_module("_proxy_calibration_reader", HERE / "proxy_forms.py")
    axis = load_module("_proxy_calibration_axis", HERE / "replay_time_axis.py")
    name = "_proxy_calibration_core"
    package_path = ROOT / "custom_components" / "ha_sauna" / "core"
    package = ModuleType(name)
    package.__path__ = [str(package_path)]
    package.__package__ = name
    package.__spec__ = importlib.util.spec_from_loader(name, loader=None,
                                                     is_package=True)
    sys.modules[name] = package
    core = importlib.import_module(f"{name}.curve_change")
    return reader, axis, core


class Work:
    """Shared, bounded numerical cache; identical pre-onset fits run once."""

    def __init__(self, module, policy):
        self.module, self.policy = module, policy
        self.started = time.monotonic()
        self.last_progress = self.started
        self.counts = Counter()
        self.cache = OrderedDict()
        self.baseline_cache = OrderedDict()
        self.trend_cache = OrderedDict()
        original = module._least_squares
        original_baseline = module._calibration_baseline
        original_trend = module.curve_trend

        def cached(rows, values, weights):
            key = (tuple(tuple(row) for row in rows), tuple(values), tuple(weights))
            if key in self.cache:
                self.counts["least_squares_cache_hits"] += 1
                self.cache.move_to_end(key)
                result = self.cache[key]
                return None if result is None else list(result)
            self.counts["least_squares_calls"] += 1
            if self.counts["least_squares_calls"] > policy["limits"]["maximum_total_least_squares_calls"]:
                raise WorkLimitError("maximum_total_least_squares_calls exceeded")
            result = original(rows, values, weights)
            self.cache[key] = None if result is None else tuple(result)
            if len(self.cache) > policy["limits"]["maximum_cache_entries"]:
                self.cache.popitem(last=False)
            return None if result is None else list(result)

        module._least_squares = cached

        def cached_baseline(samples, origin, floor, policy):
            self.counts["calibration_baseline_calls"] += 1
            # Full immutable original values/times, origin, numerical floor and
            # every CurvePolicy field define this pure calculation. No source
            # identity, candidate state or result from another window is used.
            key = (tuple(tuple(sample) for sample in samples), origin, floor, policy)
            if key in self.baseline_cache:
                self.counts["calibration_baseline_cache_hits"] += 1
                self.baseline_cache.move_to_end(key)
                return self.baseline_cache[key]
            self.counts["calibration_baseline_evaluations"] += 1
            result = original_baseline(samples, origin, floor, policy)
            self.baseline_cache[key] = result
            if len(self.baseline_cache) > self.policy["limits"]["maximum_cache_entries"]:
                self.baseline_cache.popitem(last=False)
            # Both the successful tuple and None are immutable exact results.
            return result

        def cached_trend(samples, *, policy):
            self.counts["curve_trend_calls"] += 1
            key = (tuple(tuple(sample) for sample in samples), policy)
            if key in self.trend_cache:
                self.counts["curve_trend_cache_hits"] += 1
                self.trend_cache.move_to_end(key)
                return self.trend_cache[key]
            self.counts["curve_trend_evaluations"] += 1
            result = original_trend(samples, policy=policy)
            self.trend_cache[key] = result
            if len(self.trend_cache) > self.policy["limits"]["maximum_cache_entries"]:
                self.trend_cache.popitem(last=False)
            # CurveTrendFeatures is frozen; None remains unresolved on reuse.
            return result

        module._calibration_baseline = cached_baseline
        module.curve_trend = cached_trend

    def call(self, name, *args, **kwargs):
        if name == "curve_template_value":
            self.counts["curve_value_calls"] += 1
            if self.counts["curve_value_calls"] > self.policy["limits"]["maximum_curve_value_calls"]:
                raise WorkLimitError("maximum_curve_value_calls exceeded")
            return getattr(self.module, name)(*args, **kwargs)
        self.counts["owner_calls"] += 1
        self.counts[name] += 1
        if self.counts["owner_calls"] > self.policy["limits"]["maximum_owner_calls"]:
            raise WorkLimitError("maximum_owner_calls exceeded")
        return getattr(self.module, name)(*args, **kwargs)

    def progress(self, phase, **details):
        now = time.monotonic()
        if now - self.last_progress >= self.policy["reporting"]["progress_interval_s"]:
            print(json.dumps({"phase": phase, "elapsed_s": round(now - self.started, 3),
                              "work": dict(self.counts), **details}), flush=True)
            self.last_progress = now


class OriginalIndex:
    """Prepare originals once; every later selection uses received-time indices."""

    def __init__(self, role, records, policy):
        self.role, self.policy = role, policy
        self.times, self.points, self.source_indices, self.bad_times = [], [], [], []
        self.exclusions = Counter()
        self.cache = OrderedDict()
        previous = None
        for record in records:
            stamp = record.get("t")
            if record.get("origin") != "samples":
                self.exclusions["initial_snapshot"] += 1
                continue
            if not finite(stamp):
                self.exclusions["invalid_received_time"] += 1
                continue
            if previous is not None and stamp < previous:
                raise ValueError(f"Original time order regresses: {role}")
            previous = stamp
            age = record.get("reported_age_s", 0.0)
            eligible = (record.get("fit_eligible") and finite(record.get("value"))
                        and finite(age) and 0 <= age <= policy["sampling"]["maximum_reported_age_s"])
            if not eligible:
                self.bad_times.append(stamp)
                self.exclusions[record.get("invalid_reason", "stale_or_invalid_original")] += 1
                continue
            self.times.append(stamp)
            self.points.append((at(stamp), record["value"]))
            self.source_indices.append(record["source_index"])
        self.times = tuple(self.times)
        self.points = tuple(self.points)
        self.source_indices = tuple(self.source_indices)
        self.bad_times.sort()

    def window(self, start, end, *, minimum, start_open=False, end_open=False):
        lo = (bisect_right if start_open else bisect_left)(self.times, start)
        hi = (bisect_left if end_open else bisect_right)(self.times, end)
        key = (lo, hi, start, end, minimum)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        stamps = self.times[lo:hi]
        points = self.points[lo:hi]
        span = stamps[-1] - stamps[0] if stamps else 0.0
        gap = max((right - left for left, right in zip(stamps, stamps[1:])), default=0.0)
        bad = bisect_right(self.bad_times, end) - bisect_left(self.bad_times, start)
        reasons = []
        if len(stamps) < minimum:
            reasons.append("too_few_distinct_originals")
        if len(set(stamps)) != len(stamps):
            reasons.append("duplicate_received_times")
        if span < (end - start) * self.policy["sampling"]["minimum_span_fraction"]:
            reasons.append("insufficient_observed_span")
        if gap > self.policy["sampling"]["maximum_continuity_gap_s"]:
            reasons.append("original_receipt_gap")
        if not stamps or end - stamps[-1] > self.policy["sampling"]["maximum_latest_age_s"]:
            reasons.append("stale_latest_original")
        if bad:
            reasons.append("invalid_or_stale_original_in_window")
        quality = {"eligible": not reasons, "reasons": reasons,
                   "original_count": len(points), "requested_start_s": start,
                   "requested_end_s": end, "first_s": stamps[0] if stamps else None,
                   "last_s": stamps[-1] if stamps else None, "span_s": span,
                   "maximum_gap_s": gap, "invalid_count": bad,
                   "prepared_index_range": [lo, hi],
                   "sample_row_index_first": self.source_indices[lo] if stamps else None,
                   "sample_row_index_last": self.source_indices[hi - 1] if stamps else None}
        result = (points, quality)
        self.cache[key] = result
        if len(self.cache) > self.policy["limits"]["maximum_cache_entries"]:
            self.cache.popitem(last=False)
        return result


def channel_windows(indices, start, end, policy, *, before=False, continuation=False):
    minimum = (policy["calibration"]["min_continuation_samples"] if continuation
               else policy["numerics"]["min_side_samples"])
    channels, quality = [], {}
    for role in CHANNELS:
        points, detail = indices[role].window(start, end, minimum=minimum,
                                             start_open=continuation,
                                             end_open=before)
        channels.append((role, points))
        quality[role] = detail
    return tuple(channels), quality


def eligible(quality):
    return all(item["eligible"] for item in quality.values())


def grid(start, end, count):
    if count < 2:
        return [start]
    return [start + (end - start) * index / (count - 1) for index in range(count)]


def localize(reference, definition, indices, work, policy):
    """Finite four-channel search; uncertain form witnesses stay outside the fit."""
    config = policy["localization"]
    minimum = policy["numerics"]["min_side_samples"]
    start, end = reference["allowed_onset_s"]
    evaluated = {}
    derived_only = reference["label_status"] == "derived_form_witness_only"

    def channel_evidence(role, onset):
        index = indices[role]
        floor = policy["calibration"]["noise_floors"][CHANNELS.index(role)]
        pre, pre_q = index.window(onset - config["before_window_s"], onset,
                                  minimum=minimum, end_open=True)
        post, post_q = index.window(onset, onset + config["after_window_s"],
                                     minimum=minimum)
        result = {"status": "insufficient_original_support", "bic_gain": None,
                  "before_count": pre_q["original_count"],
                  "after_count": post_q["original_count"],
                  "support_reasons": [*pre_q["reasons"], *post_q["reasons"]]}
        if pre_q["eligible"] and post_q["eligible"]:
            points = (*pre, *post)
            scale = config["before_window_s"]
            xs = [(seconds(stamp) - onset) / scale for stamp, _ in points]
            center = points[0][1]
            values = [value - center for _, value in points]
            line_rows = [[1.0, x] for x in xs]
            hinge_rows = [[1.0, x, max(0.0, x)] for x in xs]
            weights = [1.0] * len(points)
            line = work.call("_least_squares", line_rows, values, weights)
            hinge = work.call("_least_squares", hinge_rows, values, weights)
            if line is not None and hinge is not None:
                line_rss = sum((value - sum(a * b for a, b in zip(row, line)))**2
                               for row, value in zip(line_rows, values))
                hinge_rss = sum((value - sum(a * b for a, b in zip(row, hinge)))**2
                                for row, value in zip(hinge_rows, values))
                count = len(points)
                gain = count * math.log((line_rss + count * floor**2)
                                        / (hinge_rss + count * floor**2)) - math.log(count)
                sign = definition["reference_sign"]
                # Door witnesses require concordant T/RH changes. For the only
                # labelled infusion, humidity supplies direction and all four
                # original channels still require coherent temporal support.
                directional = sign * hinge[2] > 0
                if reference["kind"] == "door_close" and role.endswith("temperature"):
                    directional = directional and hinge[1] < 0
                elif reference["kind"] != "door_close":
                    directional = directional and sign * (hinge[1] + hinge[2]) > 0
                result.update(status="supports" if directional and gain > config["minimum_bic_gain"]
                              else "contrary" if not directional and gain > config["minimum_bic_gain"]
                              else "unresolved", bic_gain=gain,
                              reference_slope_before=hinge[1] / scale,
                              reference_slope_after=(hinge[1] + hinge[2]) / scale,
                              reference_slope_change=hinge[2] / scale,
                              line_rss=line_rss, hinge_rss=hinge_rss)
        return result

    def evaluate(onset):
        key = round(onset, 9)
        if key in evaluated:
            return evaluated[key]
        if len(evaluated) >= policy["limits"]["maximum_localization_candidates_per_reference"]:
            raise WorkLimitError("maximum_localization_candidates_per_reference exceeded")
        channels = {role: channel_evidence(role, onset) for role in CHANNELS}
        positive = [role for role, item in channels.items() if item["status"] == "supports"]
        contrary = [role for role, item in channels.items() if item["status"] == "contrary"]
        temporal_support = all(item["status"] != "insufficient_original_support"
                               for item in channels.values())
        primary = channels[definition["reference_channel"]]
        combination = (len(positive) >= config["minimum_consistent_channels"]
                       and len(contrary) <= config["maximum_resolved_contrary_channels"]
                       and any(role.endswith("temperature") for role in positive)
                       and any(role.endswith("humidity") for role in positive)
                       and any(role.startswith("upper") for role in positive)
                       and any(role.startswith("lower") for role in positive))
        supported = temporal_support and primary["status"] == "supports"
        if derived_only:
            supported = supported and combination
        result = {"onset_s": onset, "channels": channels,
                  "status": "supports" if supported else "rejects",
                  "four_channel_form_consistent": combination,
                  "bic_gain": primary["bic_gain"],
                  "concordant_channels": positive, "resolved_contrary_channels": contrary}
        evaluated[key] = result
        return result

    step = config["candidate_step_s"]
    count = int((end - start) / step) + 1
    for index in range(count):
        evaluate(start + index * step)
        work.progress("four_channel_landmark_search", reference=reference["id"],
                      candidate=index + 1, total=count)
    evaluate(end)
    supported = sorted((item for item in evaluated.values() if item["status"] == "supports"),
                       key=lambda item: item["onset_s"])
    clusters = []
    for item in supported:
        if not clusters or item["onset_s"] - clusters[-1][-1]["onset_s"] > policy["event_merging"][reference["kind"]]:
            clusters.append([])
        clusters[-1].append(item)
    # Separated consistent forms are separate mathematical witnesses; no fixed
    # physical event count or stay identity is inferred from a broad context.
    unambiguous = bool(clusters)
    best = None
    selected_witnesses = []
    if unambiguous:
        representatives = [max(items, key=lambda item: (item["bic_gain"], -item["onset_s"]))
                           for items in clusters]
        if not derived_only:
            representatives = [max(representatives, key=lambda item: (item["bic_gain"], -item["onset_s"]))]
        for representative in representatives:
            refine_start = max(start, representative["onset_s"] - step)
            refine_end = min(end, representative["onset_s"] + step)
            refinement_step = config["refinement_step_s"]
            for index in range(int((refine_end - refine_start) / refinement_step) + 1):
                evaluate(refine_start + index * refinement_step)
            candidates = [item for item in evaluated.values()
                          if item["status"] == "supports" and refine_start <= item["onset_s"] <= refine_end]
            selected_witnesses.append(max(candidates, key=lambda item: (item["bic_gain"], -item["onset_s"])))
        best = selected_witnesses[0]
    return {"reference": reference, "selected": best,
            "selected_witnesses": selected_witnesses,
            "status": "derived_landmarks" if best else "no_supported_form_witness",
            "form_cluster_count": len(clusters),
            "form_clusters": [{"onset_range_s": [items[0]["onset_s"], items[-1]["onset_s"]],
                               "candidate_count": len(items),
                               "representative": max(items, key=lambda item: item["bic_gain"])}
                              for items in clusters],
            "candidate_count": len(evaluated),
            "candidate_results": list(evaluated.values()),
            "time_meaning": "mathematical landmark; no exact physical action time"}


def build_example(landmark, indices, duration, work, policy):
    onset = landmark["selected"]["onset_s"]
    binding_until = onset + duration * policy["method_search"]["binding_fraction"]
    before, before_q = channel_windows(indices, onset - policy["sampling"]["before_window_s"],
                                        onset, policy, before=True)
    prefix, prefix_q = channel_windows(indices, onset, binding_until, policy)
    later, later_q = channel_windows(indices, binding_until, onset + duration,
                                      policy, continuation=True)
    _, context_q = channel_windows(indices, onset - policy["sampling"]["before_window_s"],
                                     onset + duration, policy)
    quality = {"before": before_q, "binding_prefix": prefix_q, "continuation": later_q,
               "whole_original_context": context_q}
    if not all(eligible(item) for item in quality.values()):
        return None, quality
    complete = tuple((role, (*pre, *middle, *post))
                     for (role, pre), (_, middle), (_, post)
                     in zip(before, prefix, later, strict=True))
    example = work.module.CurveCalibrationExample(
        onset_at=at(onset), binding_until=at(binding_until), channels=complete)
    return example, quality


def infusion_shape_geometry(profile, work):
    """Consume the existing form owner's geometry; no new fit or root rules."""
    shape = next(item.profile for item in profile.channels
                 if item.name == profile.reference_channel)
    count = len(shape.coefficients)
    resolution = count * math.ulp(max(abs(value) for value in shape.coefficients))
    compatible, turn, turn_kind, reason, targets, opposite = work.call(
        "_form_turn", shape.coefficients, shape.knot_x,
        {"hinge1": 1, "hinge2": 2}.get(shape.model, 0), "peak", resolution, count)
    return {"eligible": compatible is True, "compatibility": compatible,
            "peak_coordinate_x": turn, "turn_kind": turn_kind,
            "geometry_reason": reason, "target_turn_count": targets,
            "opposite_turn_count": opposite, "owner": "curve_change._form_turn",
            "duration_seconds": profile.duration_seconds,
            "meaning": "learned relative-shape geometry; an observed peak is still required"}


def continuation_assessment(profile, indices, onset, work, policy, *, earliest,
                            decision_at_s=None):
    """Bind once; only genuinely later original observations can confirm."""
    duration = profile.duration_seconds
    binding_until = onset + duration * policy["method_search"]["binding_fraction"]
    if decision_at_s is not None and (
            not finite(decision_at_s) or not binding_until < decision_at_s <= onset + duration):
        raise ValueError("Explicit decision must follow binding within the unchanged profile horizon")
    before, before_q = channel_windows(indices, onset - policy["sampling"]["before_window_s"],
                                        onset, policy, before=True)
    prefix, prefix_q = channel_windows(indices, onset, binding_until, policy)
    _, context_q = channel_windows(indices, onset - policy["sampling"]["before_window_s"],
                                     binding_until, policy)
    result = {"onset_at_s": onset, "candidate_at_s": binding_until,
              "confirmation_at_s": None, "status": "insufficient_binding_support",
              "support": {"before": before_q, "binding_prefix": prefix_q,
                          "binding_original_context": context_q},
              "score": None, "amplitude": None}
    if not eligible(before_q) or not eligible(prefix_q) or not eligible(context_q):
        return result, None
    binding = work.call("bind_curve_template", profile, before, prefix,
                        onset_at=at(onset), binding_until=at(binding_until))
    if binding is None:
        result["status"] = "binding_unresolved"
        return result, None
    result["amplitude"] = binding.amplitude
    result["frozen_binding"] = serializable(binding)
    end = onset + duration
    first = min(end, binding_until + policy["sampling"]["minimum_confirmation_delay_s"])
    ends = [end]
    if decision_at_s is not None:
        ends = [decision_at_s]
    elif earliest:
        step = policy["sampling"]["confirmation_step_s"]
        ends = [first + index * step for index in range(int((end - first) / step) + 1)]
        if not ends or ends[-1] < end:
            ends.append(end)
    result["status"] = "insufficient_new_original_support"
    for decision in ends:
        later, later_q = channel_windows(indices, binding_until, decision, policy,
                                          continuation=True)
        _, entire_q = channel_windows(indices, onset - policy["sampling"]["before_window_s"],
                                        decision, policy)
        if not eligible(later_q) or not eligible(entire_q):
            result["support"]["continuation"] = later_q
            result["support"]["confirmation_original_context"] = entire_q
            continue
        score = work.call("score_curve_continuation", binding, later)
        result["support"]["continuation"] = later_q
        result["support"]["confirmation_original_context"] = entire_q
        if score is None:
            result["status"] = "continuation_unresolved"
            continue
        result.update(score=serializable(score), status=score.status,
                      evaluated_at_s=decision,
                      threshold_margins={
                          "null_gain": {"value": score.score_gain,
                                        "threshold": profile.policy.minimum_gain,
                                        "operator": ">", "unit": "mean_twice_huber_loss",
                                        "margin": score.score_gain - profile.policy.minimum_gain},
                          "total_error": {"value": score.template_loss,
                                          "threshold": score.residual_limit,
                                          "operator": "<=", "unit": "mean_twice_huber_loss",
                                          "margin": score.residual_limit - score.template_loss},
                          "channels": {item.name: {
                              "value": item.template_loss, "threshold": item.residual_limit,
                              "operator": "<=", "unit": "mean_twice_huber_loss",
                              "margin": item.residual_limit - item.template_loss}
                              for item in score.channels}})
        result["template_status"] = score.status
        if score.status == "supports" and profile.reference_channel.endswith("temperature"):
            reference_points = dict(before)[profile.reference_channel]
            phase_points, phase_q = indices[profile.reference_channel].window(
                onset, decision, minimum=policy["numerics"]["min_side_samples"])
            pre_trend = work.call("curve_trend", reference_points, policy=profile.policy.curve)
            post_trend = (work.call("curve_trend", phase_points, policy=profile.policy.curve)
                          if phase_q["eligible"] else None)
            if profile.reference_sign < 0:
                semantic_support = post_trend is not None and post_trend.direction == -1
                rule = "observed falling temperature after the bound onset"
            else:
                semantic_support = (pre_trend is not None and pre_trend.direction == -1
                                    and post_trend is not None and post_trend.slope > pre_trend.slope)
                rule = "observed prior fall followed by braking; post-onset slope may remain negative"
            result["event_geometry"] = {"rule": rule, "supports": semantic_support,
                                         "before_trend": serializable(pre_trend),
                                         "after_trend": serializable(post_trend)}
            if not semantic_support:
                result["status"] = "template_support_without_observed_door_flanks"
                continue
        if score.status == "supports" and profile.reference_channel.endswith("humidity"):
            geometry = infusion_shape_geometry(profile, work)
            phase_points, phase_q = indices["upper_humidity"].window(
                onset, decision, minimum=2 * policy["numerics"]["min_side_samples"])
            observed = (work.call("curve_form", phase_points, policy=profile.policy.curve,
                                  form="peak") if phase_q["eligible"] else None)
            result["event_geometry"] = {"learned_shape": geometry,
                                         "observed_peak": serializable(observed)}
            if (not geometry["eligible"] or observed is None
                    or observed.evidence != "supports"):
                result["status"] = "template_support_without_observed_infusion_peak"
                continue
        if result["status"] == "supports":
            result["confirmation_at_s"] = decision
            break
    return result, binding


def counterexample_onsets(kind, session, references, policy):
    config = policy["counterexamples"]
    guard = config["reference_guard_s"]
    windows = [item["allowed_onset_s"] for item in references["event_references"]
               if item["kind"] == kind]
    start = policy["sampling"]["before_window_s"]
    end = session["analysis_window"]["end_s"] - max(policy["method_search"]["durations_s"])
    count = max(0, int((end - start) / config["anchor_step_s"]) + 1)
    onsets = [start + index * config["anchor_step_s"] for index in range(count)]
    onsets = [onset for onset in onsets
              if not any(left - guard <= onset <= right + guard for left, right in windows)]
    if len(onsets) > policy["limits"]["maximum_counterexample_anchors_per_profile"]:
        raise WorkLimitError("maximum_counterexample_anchors_per_profile exceeded")
    return onsets


def train_kind(definition, landmarks, sessions, indices, work, policy, references):
    kind = definition["kind"]
    available = []
    for landmark in landmarks:
        if landmark["reference"]["kind"] != kind:
            continue
        for index, selected in enumerate(landmark["selected_witnesses"]):
            reference = dict(landmark["reference"])
            if reference["label_status"] == "derived_form_witness_only":
                reference["source_context_id"] = reference["id"]
                reference["id"] = f"{reference['id']}-form-{index + 1:03d}"
            available.append({"reference": reference, "selected": selected})
    if len(available) > policy["limits"]["maximum_training_witnesses_per_profile"]:
        raise WorkLimitError("maximum_training_witnesses_per_profile exceeded")
    config = policy["calibration"]
    numerical = work.module.CurvePolicy(**policy["numerics"])
    calibration_policy = work.module.CurveCalibrationPolicy(
        curve=numerical, channels=CHANNELS, noise_floors=tuple(config["noise_floors"]),
        **{key: config[key] for key in (
            "min_examples", "min_continuation_samples", "residual_quantile",
            "residual_limit_multiplier", "residual_loss_floor", "minimum_gain",
            "max_phase_x")})
    negative_onsets = counterexample_onsets(kind, sessions["R03"], references, policy)
    method_reports, candidates = [], []
    for duration in policy["method_search"]["durations_s"]:
        examples, example_reports, usable = [], [], []
        for landmark in available:
            reference = landmark["reference"]
            example, support = build_example(landmark, indices[reference["recording"]],
                                              duration, work, policy)
            example_reports.append({"id": reference["id"], "recording": reference["recording"],
                                    "label_status": reference["label_status"],
                                    "onset_s": landmark["selected"]["onset_s"],
                                    "eligible": example is not None, "support": support})
            if example is not None:
                examples.append(example)
                usable.append(landmark)
        for basis in policy["method_search"]["bases"]:
            method_id = f"{kind}-{basis}-D{duration:g}"
            knot = policy["method_search"]["hinge_knot_x"] if basis.startswith("M") else None
            profile = work.call("fit_curve_calibration", tuple(examples), policy=calibration_policy,
                                duration_seconds=duration,
                                reference_channel=definition["reference_channel"],
                                reference_sign=definition["reference_sign"], basis=basis,
                                knot_x=knot)
            report = {"id": method_id, "basis": basis, "duration_seconds": duration,
                      "knot_x": knot, "training_examples": example_reports,
                      "status": "fit_unresolved" if profile is None else "raw_profile",
                      "profile": serializable(profile), "positive_assessments": [],
                      "known_counterexample_assessments": []}
            if profile is not None:
                for landmark in usable:
                    reference = landmark["reference"]
                    assessment, _ = continuation_assessment(
                        profile, indices[reference["recording"]], landmark["selected"]["onset_s"],
                        work, policy, earliest=True)
                    report["positive_assessments"].append({
                        "reference_id": reference["id"], "recording": reference["recording"],
                        "target_status": reference["label_status"], **assessment})
                for onset in negative_onsets:
                    assessment, _ = continuation_assessment(profile, indices["R03"], onset,
                                                            work, policy, earliest=True)
                    report["known_counterexample_assessments"].append(assessment)
                actual_targets = [item for item in references["event_references"]
                                  if item["kind"] == kind]
                matched = {item["reference_id"] for item in report["positive_assessments"]
                           if item["recording"] == "R03" and item["status"] == "supports"}
                false_claims = sum(item["status"] == "supports"
                                   for item in report["known_counterexample_assessments"])
                misses = len(actual_targets) - len(matched)
                losses = [item["score"]["template_loss"] for item in report["positive_assessments"]
                          if item["score"] is not None]
                mean_loss = sum(losses) / len(losses) if losses else None
                report.update(known_counterexample_supports=false_claims,
                              unmatched_R03_reference_count=misses,
                              mean_positive_continuation_loss=mean_loss,
                              separates_declared_training_checks=(false_claims == 0 and misses == 0))
                candidates.append(((false_claims, misses,
                                    mean_loss if mean_loss is not None else math.inf,
                                    len(method_reports)), profile, report))
            method_reports.append(report)
            write_json(work.output / "calibration-progress.json", {
                "schema_version": 1, "completion": "incomplete",
                "phase": "finite_method_search", "kind": kind,
                "completed_methods": method_reports,
                "work": dict(work.counts)})
            work.progress("finite_method_search", kind=kind, method=method_id)
    if not candidates:
        return None, {"kind": kind, "status": "no_numerical_profile", "released": False,
                      "calibration_separation_only": False,
                      "methods": method_reports, "selected_method_id": None}
    _, selected, selected_report = min(candidates, key=lambda item: item[0])
    separating = selected_report["separates_declared_training_checks"]
    return selected, {"kind": kind,
                      "status": "separates_declared_training_checks" if separating else "non_separating",
                      "released": False, "calibration_separation_only": separating,
                      "release_scope": "No product release. Apparent training separation is preliminary until the finer complete R03 scan; no independent validation exists.",
                      "selected_method_id": selected_report["id"], "methods": method_reports}


def coordinate(record, axis, axis_owner):
    result = dict(record)
    for field in ("onset_at_s", "candidate_at_s", "confirmation_at_s", "evaluated_at_s"):
        value = result.get(field)
        if finite(value):
            result[field.replace("_s", "_sequence_s")] = axis_owner.sequence_time(axis, value)
    result["time_axis"] = axis
    return result


def cluster_supports(records, kind, policy):
    """First actual confirmation owns an event; subsequent fits cannot replace it."""
    events = []
    distance = policy["event_merging"][kind]
    for record in sorted(records, key=lambda item: (item["confirmation_at_s"], item["onset_at_s"])):
        prior = next((item for item in events
                      if abs(item["onset_at_s"] - record["onset_at_s"]) <= distance), None)
        if prior is None:
            event = dict(record)
            event["merged_hypothesis_count"] = 1
            event["merged_onset_range_s"] = [record["onset_at_s"], record["onset_at_s"]]
            events.append(event)
        else:
            prior["merged_hypothesis_count"] += 1
            prior["merged_onset_range_s"][0] = min(prior["merged_onset_range_s"][0], record["onset_at_s"])
            prior["merged_onset_range_s"][1] = max(prior["merged_onset_range_s"][1], record["onset_at_s"])
    return sorted(events, key=lambda item: (item["confirmation_at_s"], item["onset_at_s"]))


def scan_session(session, indices, profiles, axes, axis_owner, work, policy, stream):
    recording = session["id"]
    start = policy["sampling"]["before_window_s"]
    step = policy["sampling"]["scan_step_s"]
    end = session["analysis_window"]["end_s"]
    anchor_count = max(0, int((end - start) / step) + 1)
    if anchor_count > policy["limits"]["maximum_anchors_per_session"]:
        raise WorkLimitError("maximum_anchors_per_session exceeded")
    statuses = {kind: Counter() for kind in KINDS}
    supported = {kind: [] for kind in KINDS}
    coverage = {kind: {"first_onset_s": None, "last_onset_s": None,
                       "tail_without_full_horizon_s": None} for kind in KINDS}
    for index in range(anchor_count):
        onset = start + index * step
        for kind in KINDS:
            profile = profiles.get(kind)
            if profile is None:
                statuses[kind]["no_profile"] += 1
                continue
            if onset + profile.duration_seconds > end:
                statuses[kind]["end_of_recording_without_confirmation_horizon"] += 1
                coverage[kind]["tail_without_full_horizon_s"] = profile.duration_seconds
                continue
            assessment, _ = continuation_assessment(profile, indices, onset, work, policy,
                                                    earliest=True)
            record = coordinate({"recording": recording, "kind": kind, **assessment},
                                axes[recording], axis_owner)
            statuses[kind][record["status"]] += 1
            if coverage[kind]["first_onset_s"] is None:
                coverage[kind]["first_onset_s"] = onset
            coverage[kind]["last_onset_s"] = onset
            if policy["reporting"]["write_all_anchor_scores"]:
                stream.write(encoded(record, policy["reporting"]["decimal_places"]))
            if record["status"] == "supports":
                supported[kind].append(record)
        work.progress("frozen_full_recording_scan", recording=recording,
                      anchor=index + 1, total_anchors=anchor_count)
    return {"recording": recording, "time_axis": axes[recording],
            "analysis_window": session["analysis_window"], "anchor_count": anchor_count,
            "excluded_initial_onset_interval_s": [0.0, start],
            "coverage": coverage, "status_counts": {key: dict(value) for key, value in statuses.items()},
            "events": [event for kind in KINDS
                       for event in cluster_supports(supported[kind], kind, policy)],
            "score_stream": "anchor-scores.jsonl.gz",
            "scope": "Entire manifest-bound recording; only finite scan-grid onsets with complete causal support can confirm. Insufficient and boundary coverage stays explicit."}


def assign_quality(scans, references, policy):
    comparisons, ambiguous = [], []
    for scan in scans:
        targets = [item for item in (*references["event_references"],
                                    *references.get("evaluation_references", []))
                   if item["recording"] == scan["recording"]]
        used = set()
        for reference in targets:
            left, right = reference["allowed_onset_s"]
            uncertainty = reference.get("time_uncertainty_s", 0.0)
            candidates = [event for event in scan["events"]
                          if left - uncertainty <= event["onset_at_s"] <= right + uncertainty]
            if reference["kind"] == "door_cycle":
                openings = [event for event in candidates if event["kind"] == "door_open"]
                closings = [event for event in candidates if event["kind"] == "door_close"]
                pairs = [(opening, closing) for opening in openings for closing in closings
                         if opening["onset_at_s"] < closing["onset_at_s"]]
                available = [pair for pair in pairs if all(id(event) not in used for event in pair)]
                matches = list(min(available, key=lambda pair: max(event["confirmation_at_s"] for event in pair))) if available else []
                multiplicity = len(pairs)
            else:
                same_kind = [event for event in candidates if event["kind"] == reference["kind"]]
                available = [event for event in same_kind if id(event) not in used]
                matches = [min(available, key=lambda event: event["confirmation_at_s"])] if available else []
                multiplicity = len(same_kind)
            for event in matches:
                used.add(id(event))
            status = ("no_support_in_broad_window" if not matches else
                      "multiple_claims_in_reference_window" if multiplicity > 1 else
                      "supported_in_broad_window")
            comparisons.append({"reference": reference,
                                "status": status, "matches": matches,
                                "candidate_match_count": multiplicity,
                                "assignment": "one-to-one earliest actual confirmation",
                                "exact_action_timing": "unassessable; reference is approximate"})
            if status != "supported_in_broad_window":
                ambiguous.append({"reason": "missing_or_multiple_reference_assignments",
                                  "recording": scan["recording"],
                                  "kind": "door_open" if reference["kind"] == "door_cycle" else reference["kind"],
                                  "reference_id": reference["id"],
                                  "onset_at_s": sum(reference["allowed_onset_s"]) / 2,
                                  "reference_window_s": reference["allowed_onset_s"],
                                  "events": matches, "candidate_match_count": multiplicity})
        for event in scan["events"]:
            if id(event) in used:
                continue
            if scan["recording"] == "R03":
                guard = policy["counterexamples"]["reference_guard_s"]
                guarded = any(item["kind"] == event["kind"]
                              and item["allowed_onset_s"][0] - guard <= event["onset_at_s"]
                              <= item["allowed_onset_s"][1] + guard for item in targets)
                reason = ("additional_claim_near_uncertain_reference_edge" if guarded
                          else "additional_claim_outside_complete_R03_inventory")
            else:
                reason = "positive_claim_without_complete_event_reference"
            ambiguous.append({"reason": reason, **event})
    return comparisons, ambiguous


def r03_inventory(comparisons, scans):
    """The user confirmed two cycles and one infusion, not five physical events."""
    relevant = [item for item in comparisons if item["reference"]["recording"] == "R03"]
    cycle_ids = sorted({item["reference"]["cycle_id"] for item in relevant
                        if "cycle_id" in item["reference"]})
    cycles = []
    for cycle_id in cycle_ids:
        flanks = [item for item in relevant if item["reference"].get("cycle_id") == cycle_id]
        openings = [event for item in flanks for event in item["matches"] if event["kind"] == "door_open"]
        closings = [event for item in flanks for event in item["matches"] if event["kind"] == "door_close"]
        supported = (len(flanks) == 2 and all(item["status"] == "supported_in_broad_window" for item in flanks)
                     and len(openings) == len(closings) == 1
                     and openings[0]["onset_at_s"] < closings[0]["onset_at_s"])
        cycles.append({"cycle_id": cycle_id, "supported": supported,
                       "opening": openings, "closing": closings,
                       "meaning": "ordered mathematical climate flanks of an approximately labelled cycle"})
    infusions = [item for item in relevant if item["reference"]["kind"] == "infusion"]
    scan = next(item for item in scans if item["recording"] == "R03")
    counts = Counter(item["kind"] for item in scan["events"])
    exact_inventory_count = (counts["door_open"] == 2 and counts["door_close"] == 2
                             and counts["infusion"] == 1)
    return {"recording": "R03", "expected_door_cycles": 2, "expected_infusions": 1,
            "supported_door_cycles": sum(item["supported"] for item in cycles),
            "supported_infusions": sum(item["status"] == "supported_in_broad_window" for item in infusions),
            "observed_claim_counts": dict(counts), "cycle_assignments": cycles,
            "separates_complete_inventory": exact_inventory_count and all(item["supported"] for item in cycles)
            and len(infusions) == 1 and infusions[0]["status"] == "supported_in_broad_window",
            "exact_mechanical_timing": "unassessable; climate landmarks are not door contacts"}


def curve_artifact(case, case_id, sessions, indices, profiles, work, policy, output):
    """Original points plus unchanged expectations; SVG is only a view of them."""
    recording, kind = case["recording"], case["kind"]
    profile = profiles.get(kind)
    onset = case["onset_at_s"]
    duration = profile.duration_seconds if profile is not None else max(policy["method_search"]["durations_s"])
    padding = policy["reporting"]["curve_padding_s"]
    start = max(0.0, onset - policy["sampling"]["before_window_s"] - padding)
    end = min(sessions[recording]["analysis_window"]["end_s"], onset + duration + padding)
    binding = None
    if profile is not None:
        _, binding = continuation_assessment(profile, indices[recording], onset,
                                             work, policy, earliest=False)
    rows, panels = {}, []
    for role in CHANNELS:
        original = indices[recording][role]
        lo, hi = bisect_left(original.times, start), bisect_right(original.times, end)
        points = original.points[lo:hi]
        records = []
        for source_index, (stamp, value) in zip(original.source_indices[lo:hi], points, strict=True):
            expected = (work.call("curve_template_value", binding, role, stamp)
                        if binding is not None else None)
            null = (work.call("curve_template_value", binding, role, stamp, null=True)
                    if binding is not None else None)
            records.append({"t_s": seconds(stamp), "observed": value,
                            "expected": expected, "null": null,
                            "sample_row_index": source_index})
        rows[role] = records
        limit = policy["limits"]["maximum_curve_points_per_case_channel"]
        chosen = (records if len(records) <= limit else
                  [records[round(index * (len(records) - 1) / (limit - 1))]
                   for index in range(limit)])
        values = [item[key] for item in chosen for key in ("observed", "expected", "null")
                  if item[key] is not None]
        if not values:
            panels.append(f'<text x="55" y="{len(panels) * 150 + 30}">{html.escape(role)}: no original support</text>')
            continue
        low, high = min(values), max(values)
        extent = max(high - low, 1e-9)
        top = len(panels) * 150 + 30

        def xy(item, key):
            return (55 + 820 * (item["t_s"] - start) / max(end - start, 1e-9),
                    top + 105 - 95 * (item[key] - low) / extent)

        paths = []
        for key, color in (("observed", "#222"), ("expected", "#1565c0"), ("null", "#888")):
            path = " ".join(f"{x:.3f},{y:.3f}" for item in chosen if item[key] is not None
                            for x, y in [xy(item, key)])
            paths.append(f'<polyline points="{path}" fill="none" stroke="{color}" stroke-width="1.2"/>')
        lines = "".join(f'<line x1="{55 + 820 * (value - start) / max(end - start, 1e-9):.3f}" '
                        f'y1="{top}" x2="{55 + 820 * (value - start) / max(end - start, 1e-9):.3f}" '
                        f'y2="{top + 110}" stroke="{color}" stroke-dasharray="3 3"/>'
                        for value, color in ((onset, "#b71c1c"),
                                             (onset + duration * policy["method_search"]["binding_fraction"], "#7b1fa2")))
        panels.append(f'<g><text x="55" y="{top - 8}">{html.escape(role)} '
                      f'[{low:.3f}, {high:.3f}] — {len(records)} originals</text>'
                      f'{lines}{"".join(paths)}</g>')
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="660" viewBox="0 0 900 660">'
           f'<rect width="900" height="660" fill="white"/><g font-family="sans-serif" font-size="12">'
           f'{"".join(panels)}<text x="55" y="630">{html.escape(case_id)} | '
           f't={start:.3f}–{end:.3f}s | black original; blue frozen template; gray null</text>'
           f'<text x="55" y="650">Red: mathematical onset. Purple: binding. Display may be thinned; cases.jsonl.gz retains originals.</text>'
           '</g></svg>')
    path = output / "curves" / f"{case_id}.svg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg, encoding="utf-8")
    return {"case_id": case_id, "recording": recording, "kind": kind,
            "window_s": [start, end], "channels": rows,
            "curve_file": str(path.relative_to(output)),
            "sources": sessions[recording]["provenance"],
            "binding": serializable(binding)}


def run(args, policy, references):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    resolve_numerics(policy)
    policy["calibration"]["residual_loss_floor"] = math.ulp(1.0)
    reader, axis_owner, module = load_owners()
    work = Work(module, policy)
    work.output = output
    manifest_path = TESTS / "replay_manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    entries = manifest["sessions"]
    if [item["id"] for item in entries] != policy["session_ids"]:
        raise ValueError("Manifest order differs from declared calibration corpus")
    provenance = {"manifest": file_identity(manifest_path),
                  "policy": file_identity(args.policy.resolve()),
                  "references": file_identity(args.references.resolve()),
                  "runner": file_identity(Path(__file__).resolve()),
                  "reader": file_identity(HERE / "proxy_forms.py"),
                  "time_axis_owner": file_identity(HERE / "replay_time_axis.py"),
                  "numerical_owner": file_identity(Path(module.__file__).resolve()),
                  "numerical_defaults_read_for_method_declaration": file_identity(
                      ROOT / "custom_components" / "ha_sauna" / "defaults.json"),
                  "loading": "isolated curve_change package; no HA init/controller/detector/presence/observed outputs"}
    sessions, indices = {}, {}
    # Read training sources first. R04 is not even loaded until after selection.
    for entry in entries:
        if entry["id"] not in policy["training_session_ids"]:
            continue
        session = reader.read_session(entry)
        sessions[entry["id"]] = session
        indices[entry["id"]] = {role: OriginalIndex(role, session["original_series"][role], policy)
                                 for role in CHANNELS}
    derive_noise_floors(indices, policy)
    write_json(output / "calibration-progress.json", {
        "schema_version": 1, "completion": "incomplete", "phase": "training_inputs_prepared",
        "provenance": provenance, "policy": policy,
        "training_inputs": {identity: session["provenance"] for identity, session in sessions.items()}})
    definitions = {item["kind"]: item for item in references["profiles"]}
    landmarks = []
    for reference in (*references["form_witnesses"], *references["event_references"]):
        landmarks.append(localize(reference, definitions[reference["kind"]],
                                  indices[reference["recording"]], work, policy))
        write_json(output / "landmarks-progress.json", {
            "schema_version": 1, "completion": "incomplete", "landmarks": landmarks,
            "work": dict(work.counts)})
        work.progress("reference_landmarks", reference=reference["id"])
    profiles, calibration_reports = {}, []
    for definition in references["profiles"]:
        profile, report = train_kind(definition, landmarks, sessions, indices,
                                     work, policy, references)
        profiles[definition["kind"]] = profile
        calibration_reports.append(report)
    frozen = {"schema_version": 1, "provenance": provenance,
              "profile_contract": "yhat_c=B_c+V_c*(t-tau)+A*g_c((t-tau)/D). B,V only pre-tau; A bound once on the prefix. Later originals never refit parameters.",
              "time_contract": "D is fixed in seconds per selected profile; tau is a local mathematical onset. Sequence coordinates are explicit in scored records.",
              "profiles": {kind: serializable(profile) for kind, profile in profiles.items()},
              "selection": [{key: value for key, value in report.items() if key != "methods"}
                            for report in calibration_reports],
              "release_status": "not_released_for_product_use",
              "independent_validation": False,
              "event_probability": "not estimated"}
    places = policy["reporting"]["decimal_places"]
    write_json(output / "profile.json", frozen, places)
    profile_hash = file_identity(output / "profile.json")
    calibration = {"schema_version": 1, "provenance": provenance, "policy": policy,
                   "references": references, "landmarks": landmarks,
                   "methods": calibration_reports, "frozen_profile": profile_hash,
                   "source_groups": [{"id": "R01-R03", "recordings": ["R01", "R02", "R03"],
                                      "independent_folds": 1}],
                   "holdout": {"recording": "R04", "used_for_selection": False,
                               "previously_inspected": True, "independent_untouched_validation": False}}
    write_json(output / "calibration.json", calibration, places)
    # The entire holdout is now loaded and scored with the already persisted profile.
    for entry in entries:
        if entry["id"] not in policy["holdout_session_ids"]:
            continue
        session = reader.read_session(entry)
        sessions[entry["id"]] = session
        indices[entry["id"]] = {role: OriginalIndex(role, session["original_series"][role], policy)
                                 for role in CHANNELS}
    axes = axis_owner.recording_time_axes(
        manifest, durations_s={identity: session["duration_s"]
                               for identity, session in sessions.items()})
    scans = []
    with (output / "anchor-scores.jsonl.gz").open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as stream:
            for recording in policy["session_ids"]:
                scans.append(scan_session(sessions[recording], indices[recording], profiles,
                                           axes, axis_owner, work, policy, stream))
    comparisons, ambiguous = assign_quality(scans, references, policy)
    inventory = r03_inventory(comparisons, scans)
    # Rejected interpretations at a broadly searched location remain visible.
    # They do not become training examples or additional event assertions.
    for landmark in landmarks:
        reference = landmark["reference"]
        unresolved = [item for item in landmark["candidate_results"]
                      if item["status"] != "supports"
                      and item["channels"][definitions[reference["kind"]]["reference_channel"]]["status"] == "supports"]
        if unresolved:
            representative = max(unresolved, key=lambda item: item["bic_gain"])
            ambiguous.append({"reason": "contrasting_channel_form_excluded_from_training",
                              "recording": reference["recording"], "kind": reference["kind"],
                              "reference_id": reference["id"], "onset_at_s": representative["onset_s"],
                              "localization_evidence": representative})
    diagnostics = []
    for hypothesis in references["diagnostic_hypotheses"]:
        profile = profiles.get(hypothesis["kind"])
        if profile is None:
            assessment = {"status": "no_profile", "onset_at_s": hypothesis["onset_s"]}
        else:
            assessment, _ = continuation_assessment(profile, indices[hypothesis["recording"]],
                                                    hypothesis["onset_s"], work, policy, earliest=True)
        record = coordinate({**hypothesis, **assessment}, axes[hypothesis["recording"]], axis_owner)
        diagnostics.append(record)
        ambiguous.append({"reason": "explicit_unlabelled_diagnostic_hypothesis", **record})
    if len(ambiguous) > policy["limits"]["maximum_ambiguous_cases"]:
        raise WorkLimitError("maximum_ambiguous_cases exceeded; no silent case truncation")
    with (output / "cases.jsonl.gz").open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as stream:
            for index, case in enumerate(ambiguous):
                case_id = f"case-{index + 1:05d}"
                curves = curve_artifact(case, case_id, sessions, indices, profiles,
                                         work, policy, output)
                stream.write(encoded(curves, places))
                case.update(case_id=case_id, curve_file=curves["curve_file"],
                            originals_file="cases.jsonl.gz", originals_record_index=index,
                            original_window_s=curves["window_s"])
                work.progress("ambiguous_original_curves", case=index + 1, total=len(ambiguous))
    extra = sum(case["reason"] == "additional_claim_outside_complete_R03_inventory" for case in ambiguous)
    missing = sum(item["status"] == "no_support_in_broad_window" for item in comparisons)
    multiple = sum(item["status"] == "multiple_claims_in_reference_window" for item in comparisons)
    final_separation = (inventory["separates_complete_inventory"] and not missing and not multiple
                        and not extra and all(item["calibration_separation_only"] for item in calibration_reports))
    quality = {"schema_version": 1, "provenance": provenance, "frozen_profile": profile_hash,
               "scans": scans, "event_reference_comparison": comparisons,
               "diagnostic_hypotheses": diagnostics,
               "complete_inventory_assessment": inventory,
               "reference_assessment_counts": {"missing_broad_reference_matches": missing,
                                                "multiple_claim_windows": multiple,
                                                "additional_claims_outside_guarded_R03_inventory": extra},
               "recognition_result": "separates_declared_checks_without_independent_validation" if final_separation
               else "non_separating",
               "product_release": False,
               "calibration_separation_only": all(item["calibration_separation_only"] for item in calibration_reports),
               "mathematical_R03_scan_separation": inventory["separates_complete_inventory"],
               "hypothesis_clustering_scope": "The 20-second merge window only deduplicates nearby hypotheses; it is not an infusion episode/decay rule or detector lifecycle.",
               "per_recording_input": {identity: {"provenance": session["provenance"],
                                                   "counts": session["counts"],
                                                   "original_exclusions": {role: dict(indices[identity][role].exclusions)
                                                                           for role in CHANNELS}}
                                       for identity, session in sessions.items()},
               "interpretation": "Causal frozen-template inspection of originals, not detector integration, event probability, or proof from passing tests. R01/R02/R04 unmatched claims remain unassigned, not automatic false positives.",
               "work": dict(work.counts), "elapsed_s": time.monotonic() - work.started,
               "completion": "completed"}
    write_json(output / "quality.json", quality, places)
    write_json(output / "ambiguous-cases.json", {"schema_version": 1, "frozen_profile": profile_hash,
                                                "cases": ambiguous, "case_count": len(ambiguous),
                                                "truncated": False,
                                                "interpretation": "Each positive claim without complete assignment, missing/multiple reference assignment and explicit diagnostic hypothesis has original curves. No unknown case is silently turned into a negative label."}, places)
    outputs = [file_identity(path) for path in sorted(output.rglob("*"))
               if path.is_file() and path.name not in ("artifact-hashes.json", "failure.json")]
    write_json(output / "artifact-hashes.json", {"schema_version": 1, "inputs": provenance,
                                               "outputs": outputs, "completion": "completed"}, places)
    print(json.dumps({"completion": "completed", "recognition_result": quality["recognition_result"],
                      "output": str(output), "work": dict(work.counts)}), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--policy", type=Path, default=HERE / "proxy_calibration_policy.json")
    parser.add_argument("--references", type=Path, default=HERE / "proxy_calibration_references.json")
    args = parser.parse_args(argv)
    if sys.platform != "linux":
        parser.error("Calibration and original replay analysis run exclusively on Linux")
    policy = json.loads(args.policy.read_bytes())
    references = json.loads(args.references.read_bytes())
    validate(policy, references)
    try:
        run(args, policy, references)
    except Exception as error:
        write_json(args.output / "failure.json", {
            "schema_version": 1, "completion": "incomplete",
            "error_type": type(error).__name__, "error": str(error),
            "interpretation": "Failure/work-limit exhaustion is not an event rejection. Completed raw-profile artifacts remain available; no threshold is relaxed."})
        raise


if __name__ == "__main__":
    main()
