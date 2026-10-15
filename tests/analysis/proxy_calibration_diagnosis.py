"""Fixed factor diagnosis of a hash-bound climate calibration; Linux only.

python tests/analysis/proxy_calibration_diagnosis.py --baseline DIR --output DIR

The saved profile is reproduced before the five declared comparisons. Original
bindings, raw scores and effective vetoes remain separate. This does not tune,
activate or implement a detector. Only the existing numerical and event-gate
owners execute fits and decisions. All saved floats retain full precision.
"""

from __future__ import annotations

import argparse
from collections import Counter, OrderedDict
from copy import deepcopy
from dataclasses import fields, replace
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
KINDS = ("door_open", "door_close", "infusion")
VARIANTS = ("baseline", "without_temperature_bands", "without_all_bands",
            "r03_shape_original_bands", "r03_shape_r03_bands")


def sha(payload):
    return hashlib.sha256(payload).hexdigest()


def encode(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                       separators=(",", ":")) + "\n").encode("utf-8")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(encode(value))
    temporary.replace(path)


def identity(path):
    payload = path.read_bytes()
    return {"file": str(path), "sha256": sha(payload), "bytes": len(payload)}


def load_json(path):
    return json.loads(path.read_bytes())


def exact_fields(cls, data):
    if not isinstance(data, dict) or set(data) != {field.name for field in fields(cls)}:
        raise ValueError(f"Exact typed fields required for {cls.__name__}")
    return dict(data)


def decode_profile(module, data):
    """No eval, tolerance, coefficient rounding or normalization on import."""
    if data is None:
        return None
    values = exact_fields(module.CurveCalibrationProfile, data)
    policy = exact_fields(module.CurveCalibrationPolicy, values["policy"])
    policy["curve"] = module.CurvePolicy(**exact_fields(module.CurvePolicy, policy["curve"]))
    policy["channels"] = tuple(policy["channels"])
    policy["noise_floors"] = tuple(policy["noise_floors"])
    values["policy"] = module.CurveCalibrationPolicy(**policy)
    channels = []
    for item in values["channels"]:
        channel = exact_fields(module.CurveTemplateChannel, item)
        shape = exact_fields(module.CurveProfile, channel["profile"])
        shape["coefficients"] = tuple(shape["coefficients"])
        channel["profile"] = module.CurveProfile(**shape)
        channels.append(module.CurveTemplateChannel(**channel))
    values["channels"] = tuple(channels)
    return module.CurveCalibrationProfile(**values)


def load_calibration_owner():
    path = HERE / "proxy_calibration.py"
    spec = importlib.util.spec_from_file_location("_proxy_calibration_diagnosis_owner", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def validate_policy(policy):
    if policy.get("schema_version") != 1:
        raise ValueError("Diagnosis policy requires schema_version 1")
    if tuple(item["id"] for item in policy["variants"]) != VARIANTS:
        raise ValueError("Exactly the five predeclared factor variants are required")
    expected_modes = ("none", "temperature_channels_only", "all_residual_bands", "none", "none")
    if tuple(item["band_ablation"] for item in policy["variants"]) != expected_modes:
        raise ValueError("Only the declared two veto ablations are permitted")
    if policy["scope"]["full_recording"] != "R03" or policy["scope"]["window_recording"] != "R04":
        raise ValueError("Diagnosis must remain R03 full-grid plus R04 reference windows")
    for key, value in policy["limits"].items():
        if (not isinstance(value, (int, float)) or isinstance(value, bool)
                or not math.isfinite(value) or value <= 0):
            raise ValueError(f"Positive finite work limit required: {key}")


def verify_baseline(base, policy):
    output = base / "output" if (base / "output" / "profile.json").is_file() else base
    metadata_root = output.parent
    metadata = {name: (metadata_root / name).read_text().strip()
                for name in ("commit.txt", "exit-code.txt", "process-completed.txt")}
    if (metadata["commit.txt"] != policy["baseline"]["commit"]
            or metadata["exit-code.txt"] != "0"
            or metadata["process-completed.txt"] != "completed"):
        raise ValueError("Baseline commit/exit/completion metadata differs from the declared complete run")
    hashes_path = output / "artifact-hashes.json"
    if sha(hashes_path.read_bytes()) != policy["baseline"]["artifact_hashes_sha256"]:
        raise ValueError("Baseline artifact hash manifest differs from the predeclared run")
    hashes = load_json(hashes_path)
    if hashes.get("completion") != "completed":
        raise ValueError("An incomplete baseline cannot be reproduced")
    verified = [identity(hashes_path)]
    for name in policy["baseline"]["required_outputs"]:
        matches = [item for item in hashes["outputs"] if Path(item["file"]).name == name]
        if len(matches) != 1:
            raise ValueError(f"Ambiguous baseline artifact identity: {name}")
        actual = identity(output / name)
        if actual["sha256"] != matches[0]["sha256"] or actual["bytes"] != matches[0]["bytes"]:
            raise ValueError(f"Baseline artifact checksum mismatch: {name}")
        verified.append(actual)
    profile, calibration, quality = (load_json(output / name)
                                     for name in ("profile.json", "calibration.json", "quality.json"))
    profile_sha = sha((output / "profile.json").read_bytes())
    if (quality.get("completion") != "completed"
            or calibration["frozen_profile"]["sha256"] != profile_sha
            or quality["frozen_profile"]["sha256"] != profile_sha
            or profile["provenance"] != calibration["provenance"]
            or profile["provenance"] != quality["provenance"]):
        raise ValueError("Baseline documents disagree about their executable/input binding")
    source_checks = []
    for name, expected in profile["provenance"].items():
        if not isinstance(expected, dict) or "file" not in expected:
            continue
        actual = identity(ROOT / expected["file"])
        if name == "runner":
            source = (ROOT / expected["file"]).read_text()
            additions = [
                ("def continuation_assessment(profile, indices, onset, work, policy, *, earliest,\n                            decision_at_s=None):",
                 "def continuation_assessment(profile, indices, onset, work, policy, *, earliest):"),
                ('    if decision_at_s is not None and (\n            not finite(decision_at_s) or not binding_until < decision_at_s <= onset + duration):\n        raise ValueError("Explicit decision must follow binding within the unchanged profile horizon")\n', ""),
                ("    if decision_at_s is not None:\n        ends = [decision_at_s]\n    elif earliest:\n", "    if earliest:\n"),
            ]
            for addition, original in additions:
                if source.count(addition) != 1:
                    raise ValueError("Current assessment runner has an undeclared source change")
                source = source.replace(addition, original, 1)
            restored_sha = sha(source.encode("utf-8"))
            if restored_sha != expected["sha256"]:
                raise ValueError("Removing the declared additive decision API does not restore the baseline runner")
            source_checks.append({"owner": name, "baseline": expected, "current": actual,
                                  "compatibility": policy["baseline"]["runner_compatibility"],
                                  "baseline_source_restored_sha256": restored_sha,
                                  "only_declared_additive_change": True})
        else:
            if actual["sha256"] != expected["sha256"] or actual["bytes"] != expected["bytes"]:
                raise ValueError(f"Bound numerical/input owner changed: {name}")
            source_checks.append({"owner": name, "identity": actual, "exact": True})
    manifest = load_json(ROOT / profile["provenance"]["manifest"]["file"])
    fixture_metadata = {}
    for entry in manifest["sessions"]:
        for file_key, hash_key in (("file", "sha256"), ("samples_file", "samples_sha256")):
            path = ROOT / "tests" / entry[file_key]
            actual = identity(path)
            if actual["sha256"] != entry[hash_key]:
                raise ValueError(f"Manifest-bound recording checksum mismatch: {entry['id']} {file_key}")
            source_checks.append({"recording": entry["id"], "identity": actual, "exact": True})
        fixture_metadata[entry["id"]] = load_json(ROOT / "tests" / entry["file"])
    return output, profile, calibration, quality, manifest, fixture_metadata, {
        "github_run_id": policy["baseline"]["github_run_id"],
        "commit": policy["baseline"]["commit"], "artifacts": verified,
        "process_metadata": metadata,
        "source_checks": source_checks, "completion": "hashes_verified"}


def score_criteria(score, minimum_gain, mode):
    if score is None:
        return {"numerically_evaluable": False, "status": "unresolved"}
    channels = {}
    for item in score.channels:
        disabled = mode == "all_residual_bands" or (
            mode == "temperature_channels_only" and item.name.endswith("temperature"))
        channels[item.name] = {"enabled": not disabled, "raw_pass": item.within_residual_limit,
                               "value": item.template_loss, "threshold": item.residual_limit,
                               "operator": "<=", "margin": item.residual_limit - item.template_loss,
                               "effective_pass": True if disabled else item.within_residual_limit}
    total_enabled = mode != "all_residual_bands"
    total_pass = score.template_loss <= score.residual_limit
    gain_pass = score.score_gain > minimum_gain
    within = (not total_enabled or total_pass) and all(item["effective_pass"] for item in channels.values())
    return {"numerically_evaluable": True, "raw_kernel_status": score.status,
            "gain": {"enabled": True, "value": score.score_gain, "threshold": minimum_gain,
                     "operator": ">", "margin": score.score_gain - minimum_gain, "effective_pass": gain_pass},
            "total_band": {"enabled": total_enabled, "raw_pass": total_pass,
                           "value": score.template_loss, "threshold": score.residual_limit,
                           "operator": "<=", "margin": score.residual_limit - score.template_loss,
                           "effective_pass": total_pass if total_enabled else True},
            "channel_bands": channels, "effective_within_bands": within,
            "effective_template_status": "supports" if within and gain_pass else "rejects"}


class DiagnosisWork:
    """Observe raw values, memoize pure bindings/scores, change only named vetoes."""

    def __init__(self, cal, core, owner_policy, policy, output):
        self.cal, self.module, self.policy = cal, core, owner_policy
        self.diagnosis_policy = policy
        self.base = cal.Work(core, owner_policy)
        self.counts = self.base.counts
        self.output = output
        self.variant = policy["variants"][0]
        self.bindings, self.raw_scores = OrderedDict(), OrderedDict()
        self.raw_calls = []

    def check(self):
        self.base.check()

    def progress(self, phase, **details):
        self.base.progress(phase, **details)

    def memo(self, cache, key, method, *args, **kwargs):
        if key in cache:
            self.counts[f"diagnosis_{method}_cache_hits"] += 1
            cache.move_to_end(key)
            return cache[key]
        result = self.base.call(method, *args, **kwargs)
        cache[key] = result
        if len(cache) > self.diagnosis_policy["limits"]["maximum_cache_entries"]:
            cache.popitem(last=False)
        return result

    def call(self, name, *args, **kwargs):
        self.check()
        if name == "bind_curve_template":
            key = (args, tuple(sorted(kwargs.items())))
            return self.memo(self.bindings, key, name, *args, **kwargs)
        if name != "score_curve_continuation":
            return self.base.call(name, *args, **kwargs)
        key = (args, tuple(sorted(kwargs.items())))
        raw = self.memo(self.raw_scores, key, name, *args, **kwargs)
        binding = args[0]
        mode = self.variant["band_ablation"]
        criteria = score_criteria(raw, binding.profile.policy.minimum_gain, mode)
        effective = raw
        if raw is not None and mode != "none":
            channels = tuple(replace(item, within_residual_limit=True)
                             if mode == "all_residual_bands" or (
                                 mode == "temperature_channels_only" and item.name.endswith("temperature"))
                             else item for item in raw.channels)
            effective = replace(raw, channels=channels,
                                within_residual_limit=criteria["effective_within_bands"],
                                status=criteria["effective_template_status"])
        self.raw_calls.append({"raw_score": self.cal.serializable(raw),
                               "effective_score": self.cal.serializable(effective),
                               "effective_criteria": criteria})
        return effective


def project(record, keys):
    return {key: record[key] for key in keys if key in record}


def assessment(cal, work, profiles, indices, variant, recording, kind, onset,
               owner_policy, *, decision=None):
    work.counts["diagnosis_assessments"] += 1
    if work.counts["diagnosis_assessments"] > work.diagnosis_policy["limits"]["maximum_assessments"]:
        raise cal.WorkLimitError("maximum_assessments exceeded")
    work.variant = variant
    work.raw_calls = []
    profile = profiles[kind]
    if profile is None:
        result, binding = {"status": "profile_unresolved", "onset_at_s": onset,
                           "confirmation_at_s": None, "score": None}, None
    else:
        result, binding = cal.continuation_assessment(
            profile, indices[recording], onset, work, owner_policy, earliest=True,
            decision_at_s=decision)
    serialized = cal.serializable(result)
    binding_value = cal.serializable(binding)
    return {"variant": variant["id"], "recording": recording, "kind": kind,
            "onset_at_s": onset, "decision_at_s": decision, "assessment": serialized,
            "binding_sha256": sha(encode(binding_value)) if binding is not None else None,
            "raw_score_calls": work.raw_calls,
            "raw_score": work.raw_calls[-1]["raw_score"] if work.raw_calls else None,
            "effective_score": work.raw_calls[-1]["effective_score"] if work.raw_calls else None,
            "effective_criteria": work.raw_calls[-1]["effective_criteria"] if work.raw_calls else None,
            "geometry_time_note": "Single-decision traces have exact decision_at_s. Earliest summaries retain the existing owner's last-score semantics; stale geometry after a later reject is not an evaluated gate."}


def anchor_specs(sessions, references, owner_policy, policy):
    """Preserve baseline grid alignment; extra labelled edges are explicit."""
    start = owner_policy["sampling"]["before_window_s"]
    step = owner_policy["sampling"]["scan_step_s"]
    end = sessions["R03"]["analysis_window"]["end_s"]
    result = {( "R03", start + index * step): {"full_R03_grid"}
              for index in range(max(0, int((end - start) / step) + 1))}
    for reference in references["evaluation_references"]:
        if reference["recording"] != "R04":
            raise ValueError("Only the predeclared R04 references are permitted")
        left, right = reference["allowed_onset_s"]
        uncertainty = reference.get("time_uncertainty_s", 0.0)
        lower, upper = left - uncertainty, right + uncertainty
        first = math.ceil((lower - start) / step)
        last = math.floor((upper - start) / step)
        values = [start + index * step for index in range(first, last + 1)]
        if policy["scope"]["include_reference_boundaries"]:
            values.extend((left, right))
        if policy["scope"]["include_uncertainty_boundaries"]:
            values.extend((lower, upper))
        for value in values:
            result.setdefault(("R04", value), set()).add(reference["id"])
    for item in policy["scope"]["diagnostic_onsets"]:
        result.setdefault((item["recording"], item["onset_s"]), set()).add("explicit_diagnostic_onset")
    if len(result) > policy["limits"]["maximum_selected_anchors"]:
        raise ValueError("maximum_selected_anchors exceeded")
    return [{"recording": recording, "onset_s": onset, "scope": sorted(scope)}
            for (recording, onset), scope in sorted(result.items())]


def expected_records(output, specs, policy):
    selected = {(item["recording"], item["onset_s"]) for item in specs}
    records, count = {}, 0
    with gzip.open(output / "anchor-scores.jsonl.gz", "rt", encoding="utf-8") as stream:
        for line in stream:
            count += 1
            if count > policy["limits"]["maximum_baseline_stream_rows"]:
                raise ValueError("maximum_baseline_stream_rows exceeded")
            item = json.loads(line)
            if (item["recording"], item["onset_at_s"]) not in selected:
                continue
            key = (item["recording"], item["kind"], item["onset_at_s"])
            if key in records:
                raise ValueError("Duplicate baseline anchor identity")
            records[key] = item
    return records, count


def copy_bands(shape, bands):
    if shape is None:
        return None
    channels = tuple(replace(channel, residual_limit=limit.residual_limit)
                     for channel, limit in zip(shape.channels, bands.channels, strict=True))
    return replace(shape, channels=channels, residual_limit=bands.residual_limit)


def refit_r03(cal, work, original, calibration, indices, owner_policy, policy):
    profiles, reports = {}, []
    for kind in KINDS:
        baseline = original[kind]
        if kind == "infusion":
            profiles[kind] = baseline
            reports.append({"kind": kind, "status": "exact_baseline_profile_reused",
                            "reason": "The baseline infusion already has one R03 training example; no refit is authorized."})
            continue
        work.counts["diagnosis_profile_refits"] += 1
        if work.counts["diagnosis_profile_refits"] > policy["limits"]["maximum_profile_refits"]:
            raise cal.WorkLimitError("maximum_profile_refits exceeded")
        group = next(item for item in calibration["methods"] if item["kind"] == kind)
        method = next(item for item in group["methods"] if item["id"] == group["selected_method_id"])
        specifications = [item for item in method["training_examples"]
                          if item["recording"] == "R03" and item["eligible"]]
        examples, support_checks = [], []
        for spec in specifications:
            landmark = {"selected": {"onset_s": spec["onset_s"]}}
            example, quality = cal.build_example(landmark, indices["R03"],
                                                 baseline.duration_seconds, work, owner_policy)
            exact = cal.serializable(quality) == spec["support"]
            support_checks.append({"reference_id": spec["id"], "onset_s": spec["onset_s"],
                                   "exact_stored_original_support": exact,
                                   "binding_until_s": cal.seconds(example.binding_until) if example else None})
            if example is None or not exact:
                raise ValueError("R03 refit inputs differ from the original selected method")
            examples.append(example)
        fitted = work.call("fit_curve_calibration", tuple(examples), policy=baseline.policy,
                           duration_seconds=baseline.duration_seconds,
                           reference_channel=baseline.reference_channel,
                           reference_sign=baseline.reference_sign,
                           basis=method["basis"], knot_x=method["knot_x"])
        profiles[kind] = fitted
        reports.append({"kind": kind, "status": "fitted" if fitted else "fit_unresolved",
                        "original_selected_method": method["id"], "basis": method["basis"],
                        "duration_seconds": baseline.duration_seconds, "knot_x": method["knot_x"],
                        "examples": support_checks, "calibration_count": len(examples),
                        "unchanged": ["basis", "knot", "D", "onsets", "prefix", "numerics", "noise_floors"],
                        "amplitude": "A is rebound once on the same original prefix for the changed shape; this is a necessary linked consequence, not an independently held constant.",
                        "target_limit": "User-confirmed approximate R03 cycles with mathematically derived climate flanks; especially closing/braking landmarks are not exact door-contact labels. Same-session resubstitution, not independent validation."})
    variants = {
        "baseline": original,
        "without_temperature_bands": original,
        "without_all_bands": original,
        "r03_shape_original_bands": {kind: copy_bands(profiles[kind], original[kind])
                                      if kind != "infusion" else original[kind] for kind in KINDS},
        "r03_shape_r03_bands": profiles,
    }
    return variants, reports


def geometry_status(item):
    """A short-circuited or stale geometry is unknown, never contrary evidence."""
    if item.get("template_status") != "supports":
        return "unknown_not_evaluated_at_retained_score"
    geometry = item.get("event_geometry")
    if not isinstance(geometry, dict):
        return "unresolved"
    if "supports" in geometry:
        return "supports" if geometry["supports"] else "rejects"
    learned, observed = geometry.get("learned_shape"), geometry.get("observed_peak")
    if learned is None or observed is None:
        return "unresolved"
    if learned.get("eligible") and observed.get("evidence") == "supports":
        return "supports"
    return "rejects" if learned.get("eligible") is False or observed.get("evidence") == "rejects" else "unresolved"


def method_overlap(calibration, policy):
    """Conditional scalar frontier on retained endpoints, without new tuning."""
    reports = []
    for group in calibration["methods"]:
        for method in group["methods"]:
            if method["profile"] is None:
                reports.append({"id": method["id"], "kind": group["kind"], "status": "fit_unresolved",
                                "numerical_profile_available": False, "negative_evidence": False})
                continue
            positives = [item for item in method["positive_assessments"] if item["recording"] == "R03"]
            counters = method["known_counterexample_assessments"]

            def endpoint(item):
                score = item.get("score")
                geometry = geometry_status(item)
                evaluable = isinstance(score, dict) and isinstance(score.get("score_gain"), (int, float))
                band = score.get("within_residual_limit") if evaluable else None
                return {"reference_id": item.get("reference_id"), "onset_at_s": item.get("onset_at_s"),
                        "evaluated_at_s": item.get("evaluated_at_s"), "numerically_evaluable": evaluable,
                        "score_gain": score["score_gain"] if evaluable else None,
                        "within_original_bands": band, "geometry_status": geometry,
                        "retained_status": item["status"],
                        "frontier_eligible": evaluable and band is True and geometry == "supports"}

            positive_points = [endpoint(item) for item in positives]
            counter_points = [endpoint(item) for item in counters]
            eligible_positive = [item["score_gain"] for item in positive_points if item["frontier_eligible"]]
            eligible_counter = [item["score_gain"] for item in counter_points if item["frontier_eligible"]]
            lower = max([method["profile"]["policy"]["minimum_gain"], *eligible_counter])
            upper = min(eligible_positive) if eligible_positive else None
            all_positive = len(eligible_positive) == len(positive_points) and bool(positive_points)
            feasible = all_positive and upper is not None and lower < upper
            reports.append({"id": method["id"], "kind": group["kind"],
                            "numerical_profile_available": True, "selected": method["id"] == group["selected_method_id"],
                            "stored_training_check_separation": method["separates_declared_training_checks"],
                            "stored_counterexample_supports": method["known_counterexample_supports"],
                            "stored_unmatched_R03_references": method["unmatched_R03_reference_count"],
                            "positive_endpoints": positive_points, "counter_endpoints": counter_points,
                            "frontier": {"all_positive_endpoints_eligible": all_positive,
                                         "lower_inclusive": lower, "upper_exclusive": upper,
                                         "nonempty_retained_endpoint_interval": feasible,
                                         "overlap_when_all_positive_eligible": all_positive and upper is not None and lower >= upper,
                                         "new_detection_rate_estimated": False},
                            "status": "retained_endpoint_interval_exists" if feasible else "retained_endpoints_overlap" if all_positive
                            else "not_identifiable_with_missing_or_ineligible_positive_endpoints"})
    if len(reports) != policy["method_overlap"]["expected_method_entries"]:
        raise ValueError("Stored finite method inventory differs from policy")
    summary = []
    for kind in KINDS:
        methods = [item for item in reports if item["kind"] == kind]
        available = [item for item in methods if item["numerical_profile_available"]]
        summary.append({"kind": kind, "method_entries": len(methods), "available_profiles": len(available),
                        "fit_unresolved": len(methods) - len(available),
                        "stored_check_separating_methods": [item["id"] for item in available if item["stored_training_check_separation"]],
                        "retained_endpoint_intervals": [item["id"] for item in available if item["frontier"]["nonempty_retained_endpoint_interval"]],
                        "all_available_methods_fail_stored_declared_checks": all(not item["stored_training_check_separation"] for item in available) if available else None})
    return {"schema_version": 1, "methods": reports, "summary": summary,
            "training_source": "R01-R03 model learning; positive event endpoints from R03, counters from its coarse 20-second training grid",
            "limitation": policy["method_overlap"]["limitation"], "R04_used_for_selection": False}


def decision_schedule(profile, onset, owner_policy):
    binding = onset + profile.duration_seconds * owner_policy["method_search"]["binding_fraction"]
    end = onset + profile.duration_seconds
    first = min(end, binding + owner_policy["sampling"]["minimum_confirmation_delay_s"])
    step = owner_policy["sampling"]["confirmation_step_s"]
    ends = [first + index * step for index in range(int((end - first) / step) + 1)]
    if not ends or ends[-1] < end:
        ends.append(end)
    return ends


def evaluate_variant(cal, work, variant, profiles, specs, sessions, indices,
                     axes, axis_owner, owner_policy, stream):
    records, supported, statuses = {}, {}, {}
    for spec in specs:
        recording, onset = spec["recording"], spec["onset_s"]
        for kind in KINDS:
            profile = profiles[kind]
            horizon = work.baseline_profiles[kind].duration_seconds
            if onset + horizon > sessions[recording]["analysis_window"]["end_s"]:
                continue
            row = assessment(cal, work, profiles, indices, variant, recording, kind,
                             onset, owner_policy)
            row["scope"] = spec["scope"]
            key = (recording, kind, onset)
            records[key] = row
            statuses.setdefault((recording, kind), Counter())[row["assessment"]["status"]] += 1
            if row["assessment"]["status"] == "supports":
                event = cal.coordinate({"recording": recording, "kind": kind,
                                        **row["assessment"]}, axes[recording], axis_owner)
                supported.setdefault((recording, kind), []).append(event)
            stream.write(encode(row))
        work.progress("diagnosis_variant", variant=variant["id"], recording=recording, onset_s=onset)
    scans = []
    for recording in ("R03", "R04"):
        events = [event for kind in KINDS for event in cal.cluster_supports(
            supported.get((recording, kind), []), kind, owner_policy)]
        scans.append({"recording": recording, "events": events, "time_axis": axes[recording],
                      "analysis_window": sessions[recording]["analysis_window"],
                      "scope": "complete R03 baseline grid" if recording == "R03"
                      else "predeclared R04 reference windows, uncertainty edges and diagnostic onset only"})
    comparisons, ambiguous = cal.assign_quality(scans, work.references, owner_policy)
    inventory = cal.r03_inventory(comparisons, scans)
    counts = {
        "anchor_status_by_recording_kind": {f"{recording}:{kind}": dict(count)
                                             for (recording, kind), count in sorted(statuses.items())},
        "supported_onsets_by_recording_kind": {f"{recording}:{kind}": len(supported.get((recording, kind), []))
                                               for recording in ("R03", "R04") for kind in KINDS},
        "clustered_claims_by_recording_kind": {f"{scan['recording']}:{kind}": sum(event["kind"] == kind for event in scan["events"])
                                              for scan in scans for kind in KINDS},
        "reference_status_counts": dict(Counter(item["status"] for item in comparisons)),
    }
    return records, {"id": variant["id"], "source": variant, "counts": counts,
                     "scans": scans, "reference_comparisons": comparisons,
                     "r03_inventory": inventory, "ambiguous": ambiguous,
                     "product_activation": False}


def reproduction_check(cal, records, expected, policy, owner_policy):
    keys = policy["baseline_comparison_keys"]
    compared, mismatches, supplemental = 0, [], 0
    total_mismatches = 0
    for key, prior in expected.items():
        row = records.get(key)
        compared += 1
        old = project(prior, keys)
        new = project(row["assessment"], keys) if row is not None else None
        if encode(old) != encode(new):
            total_mismatches += 1
            if len(mismatches) < policy["limits"]["maximum_reported_mismatches"]:
                changed = [field for field in keys if old.get(field) != (new or {}).get(field)
                           or (field in old) != (field in (new or {}))]
                mismatches.append({"recording": key[0], "kind": key[1], "onset_at_s": key[2],
                                   "changed_keys": changed, "baseline_sha256": sha(encode(old)),
                                   "recomputed_sha256": sha(encode(new))})
    start = owner_policy["sampling"]["before_window_s"]
    step = owner_policy["sampling"]["scan_step_s"]
    for key in records:
        if key in expected:
            continue
        alignment = (key[2] - start) / step
        if alignment == int(alignment):
            total_mismatches += 1
            if len(mismatches) < policy["limits"]["maximum_reported_mismatches"]:
                mismatches.append({"recording": key[0], "kind": key[1], "onset_at_s": key[2],
                                   "error": "Baseline-grid record missing from stored anchor stream"})
        else:
            supplemental += 1
    return {"schema_version": 1, "delta0": total_mismatches == 0,
            "expected_selected_record_count": len(expected), "compared_record_count": compared,
            "mismatch_count": total_mismatches, "mismatch_details": mismatches,
            "mismatch_details_limited": total_mismatches > len(mismatches),
            "supplemental_nonbaseline_grid_record_count": supplemental,
            "comparison_keys": keys, "float_comparison": "exact canonical lossless JSON; no tolerance",
            "baseline_profile_training_repeated": False,
            "anchor_bindings_recomputed": True,
            "identical_anchor_bindings_reused_across_veto_variants": True,
            "scope": "R03 complete stored grid and R04 selected baseline-grid onsets; explicit non-grid reference edges have no stored counterpart."}


def record_key(row):
    return f"{row['recording']}:{row['kind']}:{row['onset_at_s']:g}"


def event_keys(report):
    return {f"{scan['recording']}:{event['kind']}:{event['onset_at_s']:g}:{event['confirmation_at_s']:g}"
            for scan in report["scans"] for event in scan["events"]}


def factor_changes(baseline, records, baseline_report, report, variant):
    transitions, failures, amplitudes = Counter(), [], []
    for key, row in records.items():
        prior = baseline[key]
        transitions[f"{prior['assessment']['status']} -> {row['assessment']['status']}"] += 1
        old_binding = prior["assessment"].get("frozen_binding")
        binding = row["assessment"].get("frozen_binding")
        if variant["band_ablation"] != "none":
            same_binding = prior["binding_sha256"] == row["binding_sha256"]
            old_calls = [item["raw_score"] for item in prior["raw_score_calls"]]
            calls = [item["raw_score"] for item in row["raw_score_calls"]]
            # Relaxed vetoes can confirm earlier, so compare the identical
            # common chronological prefix rather than invented later scores.
            count = min(len(old_calls), len(calls))
            same_scores = encode(old_calls[:count]) == encode(calls[:count])
            if not same_binding or not same_scores:
                failures.append({"anchor": record_key(row), "binding_equal": same_binding,
                                 "common_raw_scores_equal": same_scores})
        elif (variant["shape_source"] != "baseline" and key[1] != "infusion"
              and old_binding is not None and binding is not None):
            same_baseline = encode(old_binding["channels"]) == encode(binding["channels"])
            if not same_baseline:
                failures.append({"anchor": record_key(row), "before_B_V_noise_support_equal": False})
            amplitudes.append({"anchor": record_key(row), "baseline_A": old_binding["amplitude"],
                               "variant_A": binding["amplitude"],
                               "before_B_V_noise_support_equal": same_baseline,
                               "interpretation": "A rebound on the identical prefix because the relative shape changed"})
    baseline_refs = {item["reference"]["id"]: item["status"] for item in baseline_report["reference_comparisons"]}
    references = [{"reference_id": item["reference"]["id"],
                   "baseline_status": baseline_refs[item["reference"]["id"]], "variant_status": item["status"]}
                  for item in report["reference_comparisons"]
                  if baseline_refs[item["reference"]["id"]] != item["status"]]
    old_events, events = event_keys(baseline_report), event_keys(report)
    return {"anchor_status_transition_counts": dict(transitions),
            "added_event_keys": sorted(events - old_events), "removed_event_keys": sorted(old_events - events),
            "reference_status_changes": references, "binding_invariant_failures": failures,
            "amplitude_consequences": amplitudes,
            "cluster_count_limit": "Earlier confirmations can change cluster representatives and claim counts. A claim-count change alone is not an improvement or a monotone veto effect."}


def trace_specs(specs, calibration, policy):
    cases = {(item["recording"], kind, item["onset_s"]): set(item["scope"])
             for item in specs if item["recording"] == "R04" for kind in KINDS}
    for item in policy["scope"]["complete_trace_cases"]:
        cases.setdefault((item["recording"], item["kind"], item["onset_s"]), set()).add("explicit_quiet_false_claim")
    if policy["scope"]["trace_R03_training_landmarks_from_selected_methods"]:
        for group in calibration["methods"]:
            method = next(item for item in group["methods"] if item["id"] == group["selected_method_id"])
            for example in method["training_examples"]:
                if example["recording"] == "R03" and example["eligible"]:
                    cases.setdefault(("R03", group["kind"], example["onset_s"]), set()).add(example["id"])
    return [{"recording": recording, "kind": kind, "onset_s": onset, "scope": sorted(scope)}
            for (recording, kind, onset), scope in sorted(cases.items())]


def full_traces(cal, work, variants, profiles, cases, indices, owner_policy, output, policy):
    count, invariant_failures = 0, []
    raw_baseline = {}
    summaries = {variant["id"]: Counter() for variant in variants}
    path = output / "decision-traces.jsonl.gz"
    with path.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as stream:
            for variant in variants:
                for case in cases:
                    profile = profiles[variant["id"]][case["kind"]]
                    if profile is None:
                        continue
                    for decision in decision_schedule(profile, case["onset_s"], owner_policy):
                        count += 1
                        if count > policy["limits"]["maximum_trace_rows"]:
                            raise cal.WorkLimitError("maximum_trace_rows exceeded")
                        row = assessment(cal, work, profiles[variant["id"]], indices, variant,
                                         case["recording"], case["kind"], case["onset_s"],
                                         owner_policy, decision=decision)
                        row["trace_scope"] = case["scope"]
                        row["continues_after_first_support"] = True
                        row["unchanged_model_duration_seconds"] = profile.duration_seconds
                        key = (case["recording"], case["kind"], case["onset_s"], decision)
                        factual = {"binding_sha256": row["binding_sha256"], "raw_score": row["raw_score"]}
                        if variant["id"] == "baseline":
                            raw_baseline[key] = factual
                        elif variant["band_ablation"] != "none":
                            exact = encode(factual) == encode(raw_baseline[key])
                            row["factual_binding_and_raw_score_equal_to_baseline"] = exact
                            if not exact:
                                invariant_failures.append({"variant": variant["id"], "recording": key[0],
                                                           "kind": key[1], "onset_s": key[2], "decision_at_s": decision})
                        summaries[variant["id"]][row["assessment"]["status"]] += 1
                        stream.write(encode(row))
                    work.progress("diagnosis_complete_trace", variant=variant["id"],
                                  recording=case["recording"], onset_s=case["onset_s"], rows=count)
    return {"rows": count, "cases": cases, "file": identity(path),
            "status_counts_by_variant": {key: dict(value) for key, value in summaries.items()},
            "binding_or_raw_score_invariant_failures": invariant_failures,
            "schedule": "Exact baseline first/minimum-delay, step and final horizon; no earlier ad hoc decision points"}


def run(args, policy):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    base, saved, calibration, quality, manifest, fixtures, verification = verify_baseline(args.baseline.resolve(), policy)
    cal = load_calibration_owner()
    reader, axis_owner, core = cal.load_owners()
    originals = {kind: decode_profile(core, saved["profiles"][kind]) for kind in KINDS}
    for kind, profile in originals.items():
        if encode(cal.serializable(profile)) != encode(saved["profiles"][kind]):
            raise ValueError(f"Lossless typed profile round trip failed: {kind}")
    decoded = []
    for group in calibration["methods"]:
        for method in group["methods"]:
            profile = decode_profile(core, method["profile"])
            if encode(cal.serializable(profile)) != encode(method["profile"]):
                raise ValueError(f"Lossless stored method round trip failed: {method['id']}")
            decoded.append({"id": method["id"], "profile_available": profile is not None})
    owner_policy = deepcopy(calibration["policy"])
    owner_policy["limits"].update({key: value for key, value in policy["limits"].items()
                                    if key in owner_policy["limits"]})
    owner_policy["reporting"]["progress_interval_s"] = policy["reporting"]["progress_interval_s"]
    timeout = load_json(ROOT / owner_policy["runtime_owner"]["file"])
    for part in owner_policy["runtime_owner"]["path"]:
        timeout = timeout[part]
    owner_policy["limits"]["maximum_runtime_s"] = min(
        owner_policy["limits"]["maximum_runtime_s"],
        timeout - owner_policy["runtime_owner"]["completion_reserve_s"])
    work = DiagnosisWork(cal, core, owner_policy, policy, output)
    work.references = calibration["references"]
    work.baseline_profiles = originals
    sessions, indices = {}, {}
    for entry in manifest["sessions"]:
        if entry["id"] not in ("R03", "R04"):
            continue
        session = reader.read_session(entry)
        sessions[entry["id"]] = session
        indices[entry["id"]] = {role: cal.OriginalIndex(role, session["original_series"][role], owner_policy)
                                 for role in cal.CHANNELS}
    axes = axis_owner.recording_time_axes(manifest, durations_s={key: item["duration_s"]
                                                               for key, item in fixtures.items()})
    specs = anchor_specs(sessions, calibration["references"], owner_policy, policy)
    expected, stream_count = expected_records(base, specs, policy)
    if not expected:
        raise ValueError("No stored baseline anchor records match the predeclared scope")
    write_json(output / "input-verification.json", {**verification, "decoded_methods": decoded,
                                                   "baseline_stream_rows_read": stream_count,
                                                   "requested_anchors": specs})
    reports, rows = [], {}
    anchor_path = output / "variant-anchors.jsonl.gz"
    with anchor_path.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as stream:
            baseline_rows, baseline_report = evaluate_variant(
                cal, work, policy["variants"][0], originals, specs, sessions, indices,
                axes, axis_owner, owner_policy, stream)
            reproduction = reproduction_check(cal, baseline_rows, expected, policy, owner_policy)
            write_json(output / "baseline-reproduction.json", reproduction)
            if not reproduction["delta0"]:
                raise ValueError("Baseline reproduction is not delta0; factor variants were not executed")
            rows["baseline"] = baseline_rows
            reports.append(baseline_report)
            profile_variants, refits = refit_r03(cal, work, originals, calibration, indices,
                                                owner_policy, policy)
            profile_output = {"schema_version": 1, "baseline": verification,
                              "refit_reports": refits,
                              "variants": [{**variant,
                                            "profiles": {kind: cal.serializable(profile)
                                                         for kind, profile in profile_variants[variant["id"]].items()},
                                            "profile_sha256": {kind: sha(encode(cal.serializable(profile)))
                                                               for kind, profile in profile_variants[variant["id"]].items()}}
                                           for variant in policy["variants"]],
                              "infusion_exactly_reused": all(profiles["infusion"] is originals["infusion"]
                                                            for profiles in profile_variants.values()),
                              "product_activation": False}
            write_json(output / "profiles.json", profile_output)
            for variant in policy["variants"][1:]:
                variant_rows, report = evaluate_variant(
                    cal, work, variant, profile_variants[variant["id"]], specs, sessions,
                    indices, axes, axis_owner, owner_policy, stream)
                report["changes_vs_baseline"] = factor_changes(baseline_rows, variant_rows,
                                                               baseline_report, report, variant)
                rows[variant["id"]] = variant_rows
                reports.append(report)
    write_json(output / "variant-results.json", {"schema_version": 1, "variants": reports,
                                               "anchor_stream": identity(anchor_path),
                                               "product_activation": False})
    supported_sets = {variant: {key for key, row in records.items() if row["assessment"]["status"] == "supports"}
                      for variant, records in rows.items()}
    inclusion = []
    for left, right in (("baseline", "without_temperature_bands"),
                        ("without_temperature_bands", "without_all_bands")):
        lost = sorted(supported_sets[left] - supported_sets[right])
        inclusion.append({"subset": left, "superset": right, "holds": not lost,
                          "lost_supported_anchor_count": len(lost), "lost_anchor_keys": lost})
    cases = trace_specs(specs, calibration, policy)
    trace = full_traces(cal, work, policy["variants"], profile_variants, cases, indices,
                        owner_policy, output, policy)
    write_json(output / "trace-summary.json", trace)
    overlap = method_overlap(calibration, policy)
    write_json(output / "method-overlap.json", overlap)
    failures = [item for report in reports for item in report.get("changes_vs_baseline", {}).get("binding_invariant_failures", [])]
    invariants = {"band_ablation_supported_anchor_inclusion": inclusion,
                  "binding_or_raw_score_failures": failures,
                  "trace_binding_or_raw_score_failures": trace["binding_or_raw_score_invariant_failures"],
                  "infusion_exactly_reused": profile_output["infusion_exactly_reused"]}
    valid = (all(item["holds"] for item in inclusion) and not failures
             and not trace["binding_or_raw_score_invariant_failures"] and profile_output["infusion_exactly_reused"])
    diagnosis = {"schema_version": 1, "completion": "completed" if valid else "factor_invariant_failure",
                 "baseline": verification, "baseline_reproduction": reproduction,
                 "variants": [{key: value for key, value in report.items()
                               if key in ("id", "source", "counts", "r03_inventory", "changes_vs_baseline")}
                              for report in reports],
                 "factor_invariants": invariants, "refit_reports": refits,
                 "method_overlap_summary": overlap["summary"],
                 "trace": trace, "policy": policy,
                 "actual_owner_work_limits": owner_policy["limits"],
                 "work": dict(work.counts), "elapsed_s": time.monotonic() - work.base.started,
                 "interpretation": policy["interpretation"], "product_activation": False,
                 "limitations": ["R03-only fitting retains the same approximate target attribution and uses the evaluation session for resubstitution.",
                                 "Band-veto removal is a diagnostic intervention; additional supports are not an improvement claim.",
                                 "Method frontiers concern retained early-stopped endpoints. They do not prove that no later gain threshold could work.",
                                 "R04 scope is limited to predeclared reference/uncertainty windows and diagnostic onset; it is not another complete replay or an untouched holdout."]}
    write_json(output / "diagnosis.json", diagnosis)
    outputs = [identity(path) for path in sorted(output.rglob("*"))
               if path.is_file() and path.name not in ("artifact-hashes.json", "failure.json")]
    write_json(output / "artifact-hashes.json", {"schema_version": 1,
                                               "baseline": verification,
                                               "diagnosis_runner": identity(Path(__file__).resolve()),
                                               "diagnosis_policy": identity(args.policy.resolve()),
                                               "assessment_owner": identity(HERE / "proxy_calibration.py"),
                                               "outputs": outputs, "completion": diagnosis["completion"]})
    if not valid:
        raise ValueError("A required factor-isolation invariant failed; diagnosis artifacts retain the evidence")
    print(json.dumps({"completion": "completed", "baseline_delta0": True,
                      "variant_count": len(reports), "trace_rows": trace["rows"],
                      "output": str(output), "work": dict(work.counts)}), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--policy", type=Path, default=HERE / "proxy_calibration_diagnosis_policy.json")
    args = parser.parse_args(argv)
    if sys.platform != "linux":
        parser.error("Diagnosis fits and original replay analysis run exclusively on Linux")
    policy = load_json(args.policy)
    validate_policy(policy)
    try:
        run(args, policy)
    except Exception as error:
        write_json(args.output / "failure.json", {"schema_version": 1, "completion": "incomplete",
                                                 "error_type": type(error).__name__, "error": str(error),
                                                 "interpretation": "Failure or exhausted work is not negative event evidence. No parameter is relaxed."})
        raise


if __name__ == "__main__":
    main()
