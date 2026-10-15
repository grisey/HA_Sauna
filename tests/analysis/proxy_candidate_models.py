"""Causal climate form hypotheses, consuming the existing numerical owners.

Linux only: python tests/analysis/proxy_candidate_models.py --output DIR
No controller, detector, replay expectation or presence input is imported.
The new pulse family is an experiment, not an event classifier. All adjustable
analysis limits live in proxy_candidate_policy.json. Model scores are never
converted into event probabilities.
"""

from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import importlib.util
from itertools import combinations
import json
import math
from pathlib import Path
from statistics import median
import time


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = Path(__file__).with_name("proxy_candidate_policy.json")
EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)
MEASURED = (
    "upper_temperature", "upper_humidity", "lower_temperature", "lower_humidity",
)
CHANNELS = (*MEASURED, "upper_absolute_humidity", "lower_absolute_humidity")


class WorkLimitError(RuntimeError):
    """A declared numerical budget was exhausted; never negative evidence."""


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def finite_number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def validate_policy(policy):
    """Reject malformed policy before loading sources or numerical owners."""
    if (not isinstance(policy, dict) or type(policy.get("schema_version")) is not int
            or policy.get("schema_version") != 1):
        raise ValueError("Policy schema_version must be 1")
    if policy.get("session_ids") != ["R01", "R02", "R03", "R04"]:
        raise ValueError("Policy must contain exactly ordered R01–R04")
    positive = {
        "sampling": ("anchor_step_s", "minimum_span_fraction",
                     "maximum_gap_fraction", "maximum_latest_age_s",
                     "maximum_continuity_gap_s",
                     "maximum_pair_age_s", "maximum_pair_skew_s"),
        "numerics": ("huber_delta",),
        "hypotheses": ("maximum_pending_age_s", "maximum_frozen_span_s",
                       "tail_window_s", "peak_merge_tolerance_s"),
    }
    integers = {
        "numerics": ("min_side_samples", "max_points", "max_candidates"),
        "competition": ("influential_point_refits",),
        "limits": ("maximum_anchors_per_session",
                   "maximum_owner_fit_calls_per_anchor"),
        "hypotheses": ("minimum_new_confirmation_points",
                       "tail_level_confirmations", "minimum_new_level_points"),
        "reporting": ("progress_interval_anchors",),
    }
    try:
        for section, keys in positive.items():
            for key in keys:
                value = policy[section][key]
                if not finite_number(value) or value <= 0:
                    raise ValueError(f"Policy {section}.{key} must be positive")
        for section, keys in integers.items():
            for key in keys:
                value = policy[section][key]
                if (not isinstance(value, int) or isinstance(value, bool)
                        or value < 1):
                    raise ValueError(f"Policy {section}.{key} must be an integer >=1")
        for section, key in (
            ("numerics", "robust_iterations"), ("reporting", "decimal_places"),
        ):
            value = policy[section][key]
            if (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                raise ValueError(f"Policy {section}.{key} must be an integer >=0")
        gain = policy["competition"]["minimum_score_gain"]
        if not finite_number(gain) or gain < 0:
            raise ValueError("Policy minimum_score_gain must be finite and >=0")
        windows = policy["sampling"]["windows_s"]
        if (not isinstance(windows, list) or not windows
                or any(not finite_number(value) or value <= 0 for value in windows)
                or sorted(set(windows)) != windows):
            raise ValueError("Policy windows_s must be increasing positive numbers")
        if not isinstance(policy["reporting"]["include_all_model_scores"], bool):
            raise ValueError("Policy include_all_model_scores must be boolean")
        if any(policy["sampling"][key] > 1
               for key in ("minimum_span_fraction", "maximum_gap_fraction")):
            raise ValueError("Policy support fractions must be <=1")
        numerics = policy["numerics"]
        if numerics["min_side_samples"] < 3:
            raise ValueError("Policy min_side_samples must be >=3")
        if numerics["max_points"] < 3 * numerics["min_side_samples"]:
            raise ValueError("Policy max_points must cover three pulse segments")
        if numerics["max_candidates"] < 2:
            raise ValueError("Policy max_candidates must allow a knot pair")
        if policy["competition"]["influential_point_refits"] != 1:
            raise ValueError("This experiment defines exactly one influence refit")
    except (KeyError, TypeError) as error:
        raise ValueError(f"Incomplete or malformed policy: {error}") from error
    return policy


def load_owners(policy):
    """Import the reader and the isolated core; do not initialize HA."""
    reader_path = Path(__file__).with_name("proxy_forms.py")
    spec = importlib.util.spec_from_file_location("_proxy_candidate_reader", reader_path)
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    ah, moisture_provenance = reader.moisture_owner()
    module = importlib.import_module("_proxy_forms_core.curve_change")
    owner = NumericalOwner(module, policy)
    provenance = {
        "reader": {"file": str(reader_path.relative_to(ROOT)),
                   "sha256": digest(reader_path.read_bytes())},
        "curve_owner": {"file": str(Path(module.__file__).relative_to(ROOT)),
                        "sha256": digest(Path(module.__file__).read_bytes()),
                        "loading": "isolated core package; no HA integration init"},
        "absolute_humidity_owner": moisture_provenance,
    }
    return reader, ah, owner, provenance


class NumericalOwner:
    """Count calls in the consumed owner, with a hard per-anchor fit limit."""

    def __init__(self, module, policy):
        self.module = module
        self.policy = module.CurvePolicy(**policy["numerics"])
        self.limit = policy["limits"]["maximum_owner_fit_calls_per_anchor"]
        self.counts = Counter()
        self.anchor_fits = 0
        original_fit = getattr(module, "_candidate_original_fit", module._fit)
        original_solve = getattr(module, "_candidate_original_solve", module._least_squares)
        module._candidate_original_fit = original_fit
        module._candidate_original_solve = original_solve

        def counted_fit(*args, **kwargs):
            if self.anchor_fits >= self.limit:
                raise WorkLimitError("maximum_owner_fit_calls_per_anchor exceeded")
            self.anchor_fits += 1
            self.counts["robust_fit_calls"] += 1
            return original_fit(*args, **kwargs)

        def counted_solve(*args, **kwargs):
            self.counts["least_squares_calls"] += 1
            return original_solve(*args, **kwargs)

        module._fit = counted_fit
        module._least_squares = counted_solve

    def begin_anchor(self):
        self.anchor_fits = 0

    def call(self, method, *args, **kwargs):
        self.counts[method] += 1
        return getattr(self.module, method)(*args, **kwargs)


def serializable(value, places):
    if isinstance(value, datetime):
        return (value - EPOCH).total_seconds()
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Nonfinite derived output")
        return round(value, places)
    if isinstance(value, dict):
        return {key: serializable(item, places) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [serializable(item, places) for item in value]
    return value


def write_json(path, value, places):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(serializable(value, places), ensure_ascii=False,
                                    allow_nan=False, sort_keys=True,
                                    separators=(",", ":")) + "\n", encoding="utf-8")
    temporary.replace(path)


class OriginalChannel:
    """One prepared input; indexing never interpolates or looks beyond an anchor."""

    def __init__(self, channel, records):
        self.channel = channel
        self.records = records
        self.points = []
        self.times = []
        self.bad_times = []
        self._gap_cache = {}
        self.reasons = Counter()
        previous = None
        for index, record in enumerate(records):
            at = record.get("t")
            if record.get("origin") == "initial_state":
                self.reasons["initial_snapshot_excluded"] += 1
                continue
            if not finite_number(at):
                self.reasons["unknown_time_excluded"] += 1
                continue
            if previous is not None and at < previous:
                raise ValueError(f"Original time order regresses: {channel}")
            previous = at
            if record.get("fit_eligible") and finite_number(record.get("value")):
                self.points.append((EPOCH + timedelta(seconds=at), record["value"]))
                self.times.append(at)
            else:
                self.bad_times.append(at)
                self.reasons[record.get("invalid_reason", "ineligible_original")] += 1
        self.bad_times.sort()

    def support(self, anchor, window, sampling, minimum_points, *, start=None):
        start = anchor - window if start is None else start
        lo, hi = bisect_left(self.times, start), bisect_right(self.times, anchor)
        points = self.points[lo:hi]
        stamps = self.times[lo:hi]
        span = stamps[-1] - stamps[0] if points else 0.0
        gap = max((b - a for a, b in zip(stamps, stamps[1:])), default=0.0)
        bad = (bisect_right(self.bad_times, anchor)
               - bisect_left(self.bad_times, start))
        reasons = []
        if len(points) < minimum_points:
            reasons.append("too_few_originals")
        if span < (anchor - start) * sampling["minimum_span_fraction"]:
            reasons.append("insufficient_observed_span")
        if (gap > (anchor - start) * sampling["maximum_gap_fraction"]
                or gap > sampling["maximum_continuity_gap_s"]):
            reasons.append("original_gap")
        if not points or anchor - stamps[-1] > sampling["maximum_latest_age_s"]:
            reasons.append("stale_latest_original")
        if any(a >= b for a, b in zip(stamps, stamps[1:])):
            reasons.append("nonunique_received_times")
        if bad:
            reasons.append("invalid_original_breaks_context")
        quality = {
            "eligible": not reasons, "reasons": reasons, "n_original": len(points),
            "start_s": stamps[0] if points else None,
            "end_s": stamps[-1] if points else None,
            "requested_start_s": start, "decision_at_s": anchor,
            "span_s": span, "maximum_gap_s": gap, "invalid_originals": bad,
            "source_index_range": [lo, hi],
        }
        return points if not reasons else [], quality

    def interruptions(self, after, anchor, sampling):
        """Explicit invalids and unobserved gaps, with the right end received."""
        bad = self.bad_times[bisect_right(self.bad_times, after):
                             bisect_right(self.bad_times, anchor)]
        result = [{"kind": "invalid_original", "at_s": at} for at in bad]
        limit = sampling["maximum_continuity_gap_s"]
        if limit not in self._gap_cache:
            gaps = [(left, right) for left, right in zip(self.times, self.times[1:])
                    if right - left > limit]
            self._gap_cache[limit] = ([right for _, right in gaps], gaps)
        ends, gaps = self._gap_cache[limit]
        for left, right in gaps[bisect_right(ends, after):bisect_right(ends, anchor)]:
            result.append({"kind": "original_receipt_gap", "at_s": right,
                           "start_s": left, "duration_s": right - left})
        return result


def trend(owner, points):
    feature = owner.call("curve_trend", points, policy=owner.policy)
    return asdict(feature) if feature is not None else None


def common_models(owner, samples, *, fixed_knots=None, search_count=None,
                  selected_override=None):
    """One support/noise group; consume the owner fitter instead of copying it.

    fixed_knots are datetimes frozen at proposal time, not a new optimum.
    _level_models can be unresolved for several reasons, including score ties.
    It is never replaced with an infinite loss or a zero probability.
    """
    module, numerical = owner.module, owner.policy
    selected = (module._bounded_samples(samples, numerical.max_points, None)
                if selected_override is None else list(selected_override))
    if selected_override is not None and len(selected) > numerical.max_points:
        raise ValueError("Selected override exceeds max_points")
    if fixed_knots is not None and selected_override is None:
        # Include both original knot observations without changing their times.
        selected = module._bounded_samples(samples, numerical.max_points - 2, None)
        by_time = dict(selected)
        originals = dict(samples)
        if any(knot not in originals for knot in fixed_knots):
            return {"status": "fixed_knot_missing_from_context"}
        by_time.update({knot: originals[knot] for knot in fixed_knots})
        selected = sorted(by_time.items())
    if len(selected) < 3 * numerical.min_side_samples:
        return {"status": "insufficient_bounded_support"}
    raw = [value for _, value in selected]
    if all(value == samples[0][1] for _, value in samples):
        return {"status": "exact_constant", "level": samples[0][1],
                "noise_scale": 0.0, "models": [], "selected_count": len(selected)}
    first, last = selected[0][0], selected[-1][0]
    span = (last - first).total_seconds()
    if span <= 0:
        return {"status": "degenerate_time_span"}
    times = [(stamp - first).total_seconds() / span for stamp, _ in selected]
    level_result = owner.call("_level_models", times, raw, numerical)
    if level_result is None:
        return {"status": "owner_common_comparison_unresolved",
                "selected_count": len(selected), "exact_trend": trend(owner, samples)}
    noise, level, level_score, _, _, _, _ = level_result
    center = median(raw)
    values = [value - center for value in raw]
    smooth_result = owner.call("_smooth_models", times, values, noise, numerical)
    if smooth_result is None:
        return {"status": "owner_smooth_comparison_unresolved"}
    quadratic, smooth = smooth_result
    log_n = math.log(len(values))
    models = [{"model": "L", "score": level_score, "loss": level_score - log_n,
               "parameters": 1,
               "search_count": 1, "search_cost": 0.0,
               "coefficients": [level - center]}]
    for score, name, _, coefficients in smooth:
        models.append({"model": {"linear": "P1", "quadratic": "P2",
                                 "cubic": "P3"}[name],
                       "score": score, "loss": score - len(coefficients) * log_n,
                       "parameters": len(coefficients),
                       "search_count": 1, "search_cost": 0.0,
                       "coefficients": coefficients})
    min_side = numerical.min_side_samples
    feasible = list(range(min_side - 1, len(selected) - min_side))
    knots = [feasible[index] for index in
             module._indices(len(feasible), numerical.max_candidates)]
    pairs = [(a, b) for a, b in combinations(knots, 2) if b - a >= min_side]
    for order in (1, 2):
        candidates = []
        for index in knots:
            knot = times[index]
            rows = [row + [max(0.0, at - knot)**order]
                    for row, at in zip(quadratic, times, strict=True)]
            fitted = owner.call("_fit", rows, values, noise, numerical)
            if fitted is None:
                continue
            coefficients, loss = fitted
            candidates.append({"model": f"M{order}", "loss": loss,
                "score": loss + 5 * log_n + 2 * math.log(len(knots)),
                "parameters": 5, "search_count": len(knots),
                "search_cost": 2 * math.log(len(knots)),
                "knots_s": [(selected[index][0] - EPOCH).total_seconds()],
                "coefficients": coefficients})
        if candidates:
            models.append(min(candidates, key=lambda item: item["score"]))
    fixed_pair = (tuple(bisect_left([stamp for stamp, _ in selected], knot)
                        for knot in fixed_knots) if fixed_knots is not None else None)
    searched_pairs = list(pairs)
    if fixed_pair is not None and fixed_pair not in pairs:
        pairs.append(fixed_pair)
    pulse_candidates = []
    pair_attempts = 0
    for a, b in pairs:
        if a + 1 < min_side or b - a < min_side or len(selected) - b - 1 < min_side:
            continue
        pair_attempts += 1
        k1, k2 = times[a], times[b]
        rows = [[1.0, at, max(0.0, at - k1), max(0.0, at - k2)] for at in times]
        fitted = owner.call("_fit", rows, values, noise, numerical)
        if fitted is None:
            continue
        coefficients, loss = fitted
        slopes = [coefficients[1], coefficients[1] + coefficients[2],
                  coefficients[1] + coefficients[2] + coefficients[3]]
        residuals = module._residuals(rows, values, coefficients)
        is_fixed = fixed_pair == (a, b)
        evaluated_pairs = (search_count if is_fixed and search_count is not None
                           else len(searched_pairs))
        # A frozen pair may be the only geometrically admissible pair after
        # bounded selection; its proposal search cost is still retained.
        if evaluated_pairs < 1:
            continue
        pulse_candidates.append({"model": "PULS", "loss": loss,
            "score": loss + 6 * log_n + 2 * math.log(evaluated_pairs),
            "parameters": 6, "search_count": evaluated_pairs,
            "search_cost": 2 * math.log(evaluated_pairs),
            "knots_s": [(selected[index][0] - EPOCH).total_seconds()
                        for index in (a, b)],
            "coefficients": coefficients,
            "segment_slopes_per_s": [slope / span for slope in slopes],
            "has_rise_and_decline": slopes[1] > 0 and slopes[2] < 0,
            "has_rise_onset": coefficients[2] > 0,
            "is_fixed_pair": is_fixed,
            "is_search_pair": (a, b) in searched_pairs,
            "most_influential_at_s": (selected[max(
                range(len(residuals)), key=lambda i: (abs(residuals[i]), i)
            )][0] - EPOCH).total_seconds()})
    if pulse_candidates:
        candidates = [item for item in pulse_candidates if item["has_rise_and_decline"]
                      and (fixed_pair is None or item["is_fixed_pair"])]
        if candidates:
            models.append(min(candidates, key=lambda item: item["score"]))
        others = [item for item in pulse_candidates if not item["has_rise_and_decline"]
                  and item["is_search_pair"]]
        if others:
            countermodel = dict(min(others, key=lambda item: item["score"]))
            countermodel["model"] = "OTHER_2HINGE"
            models.append(countermodel)
    best_baseline = min((item for item in models if item["model"] in
                         ("L", "P1", "P2", "P3")), key=lambda item: item["score"])
    best_other = min((item for item in models if item["model"] != "PULS"),
                     key=lambda item: item["score"])
    for model in models:
        model["gain_over_smooth"] = best_baseline["score"] - model["score"]
        if model["model"] == "PULS":
            model["gain_over_other"] = best_other["score"] - model["score"]
    replacement = None
    if fixed_pair is not None:
        fixed = next((item for item in models if item["model"] == "PULS"), None)
        other_pulses = [item for item in pulse_candidates if item["has_rise_and_decline"]
                        and item["is_search_pair"] and not item["is_fixed_pair"]]
        if fixed is not None and other_pulses:
            optimum = min(other_pulses, key=lambda item: item["score"])
            # Moving two frozen times must repay two time parameters and the
            # actual new pair search. Tiny optimum movements are not rejection.
            cost = 2 * log_n + optimum["search_cost"]
            replacement = {"replacement_knots_s": optimum["knots_s"],
                "gain": fixed["score"] - optimum["score"], "required_gain": cost,
                "displaces_fixed_geometry": fixed["score"] - optimum["score"] > cost}
    return {"status": "resolved", "noise_scale": noise,
            "selected_count": len(selected), "original_count": len(samples),
            "selected_times_s": [(stamp - EPOCH).total_seconds() for stamp, _ in selected],
            "coordinate_start_s": (first - EPOCH).total_seconds(),
            "coordinate_span_s": span, "value_center": center,
            "single_knots_tested": len(knots),
            "pulse_pairs_tested": pair_attempts, "pulse_search_pairs": len(searched_pairs),
            "search_knots_s": [(selected[index][0] - EPOCH).total_seconds() for index in knots],
            "fixed_geometry_replacement": replacement, "models": models,
            "best_smooth": best_baseline["model"],
            "best_model": min(models, key=lambda item: item["score"])["model"]}


def pulse_assessment(owner, samples, result, policy, *, influence=True):
    """A score plus observed original flanks; no physical event implication."""
    candidate = next((item for item in result.get("models", [])
                      if item["model"] == "PULS"), None)
    minimum = policy["competition"]["minimum_score_gain"]
    if candidate is None or candidate["gain_over_other"] <= minimum:
        return None
    if (result.get("fixed_geometry_replacement") or {}).get("displaces_fixed_geometry"):
        return {"status": "better_later_geometry_displaces_frozen_hypothesis",
                "supported": False, "counterevidence": True,
                "replacement": result["fixed_geometry_replacement"]}
    knots = [EPOCH + timedelta(seconds=at) for at in candidate["knots_s"]]
    middle = [sample for sample in samples if knots[0] < sample[0] <= knots[1]]
    after = [sample for sample in samples if sample[0] > knots[1]]
    rise, decline = trend(owner, middle), trend(owner, after)
    answer = {"status": "score_candidate", "knots_s": candidate["knots_s"],
              "search_count": candidate["search_count"],
              "has_rise_onset": candidate["has_rise_onset"],
              "middle_trend": rise, "after_trend": decline,
              "influence_status": "not_requested", "supported": False,
              "counterevidence": False}
    if rise is None or decline is None:
        answer["status"] = "original_flanks_unresolved"
        return answer
    if rise["direction"] != 1 or decline["direction"] != -1:
        answer["status"] = "original_flanks_do_not_support_pulse"
        answer["counterevidence"] = True
        return answer
    if influence:
        removed = EPOCH + timedelta(seconds=candidate["most_influential_at_s"])
        if removed in knots:
            # Keep original knot times, but the influential knot observation
            # cannot be both removed and used as an original support anchor.
            answer["influence_status"] = "influential_point_is_fixed_knot"
            return answer
        retained = [sample for sample in samples if sample[0] != removed]
        selected_times = set(result["selected_times_s"])
        selected_retained = [sample for sample in retained
                            if (sample[0] - EPOCH).total_seconds() in selected_times]
        rescue = common_models(owner, retained, fixed_knots=knots,
                               search_count=candidate["search_count"],
                               selected_override=selected_retained)
        rescued = next((item for item in rescue.get("models", [])
                        if item["model"] == "PULS"), None)
        answer["influence_status"] = rescue["status"]
        if rescued is None or rescued["gain_over_other"] <= minimum:
            answer["status"] = "influential_point_check_unresolved_or_rejected"
            answer["counterevidence"] = rescue["status"] == "resolved"
            return answer
        rescue_rise = trend(owner, [point for point in retained
                                   if knots[0] < point[0] <= knots[1]])
        rescue_decline = trend(owner, [point for point in retained if point[0] > knots[1]])
        if (rescue_rise is None or rescue_decline is None
                or rescue_rise["direction"] != 1 or rescue_decline["direction"] != -1):
            answer["status"] = "influential_point_flanks_unresolved_or_rejected"
            answer["counterevidence"] = (rescue_rise is not None
                                          and rescue_decline is not None)
            return answer
        answer["influence_status"] = "survives_one_point_removal"
    answer.update(status="observed_rise_peak_decline", supported=True)
    return answer


def owner_forms(owner, samples, result, policy):
    """Separate API assessments; their scores never join the common ranking."""
    minimum = policy["competition"]["minimum_score_gain"]
    forms = {}
    candidates = [item for item in result.get("models", [])
                  if item["model"] in ("M1", "M2")
                  and item["gain_over_smooth"] > minimum]
    if candidates:
        model = max(candidates, key=lambda item: item["gain_over_smooth"])
        feature = owner.call("curve_change", samples, policy=owner.policy,
            onset_at=EPOCH + timedelta(seconds=model["knots_s"][0]),
            hinge_order=int(model["model"][-1]))
        forms["change"] = asdict(feature) if feature is not None else None
        forms["change_model"] = model["model"]
        if feature is not None:
            forms["observed_before_trend"] = trend(owner, [sample for sample in samples
                                                          if sample[0] < feature.onset_at])
    # Smooth valley/peak geometry is already covered by the owner's recovery
    # family. Only assess plausible geometry, avoiding two full extra searches
    # on every monotonic climate window.
    smooth = next((item for item in result.get("models", [])
                   if item["model"] == result.get("best_smooth")), None)
    if smooth and len(smooth["coefficients"]) >= 3:
        coefficients = smooth["coefficients"]
        start = coefficients[1]
        end = sum(index * value for index, value in enumerate(coefficients))
        if start < 0 < end:
            feature = owner.call("curve_recovery", samples, policy=owner.policy)
            forms["smooth_recovery"] = asdict(feature) if feature is not None else None
        elif start > 0 > end:
            feature = owner.call("curve_recovery", [(stamp, -value)
                for stamp, value in samples], policy=owner.policy)
            forms["smooth_peak_reflected"] = asdict(feature) if feature is not None else None
    return forms


def new_original_count(channel, after, anchor):
    lo, hi = bisect_right(channel.times, after), bisect_right(channel.times, anchor)
    return len(set(channel.times[lo:hi]))


class PulseTrace:
    """Fixed-geometry analysis hypotheses, never detector events or commands."""

    def __init__(self, channel, policy):
        self.channel = channel
        self.policy = policy
        self.pending = None
        self.active = None
        self.items = []
        self.previous_anchor = None
        self.rejected_onset_not_before = None
        self.rejected_onsets = set()

    def advance(self, anchor, original, current_pulses, owner):
        policy, parameters = self.policy, self.policy["hypotheses"]
        interruptions = original.interruptions(
            self.previous_anchor if self.previous_anchor is not None else anchor,
            anchor, policy["sampling"])
        self.previous_anchor = anchor
        if interruptions:
            if self.active is not None and self.active.get("level_confirmed_at_s") is None:
                self.active["continuity_interrupted_at_s"].extend(
                    item["at_s"] for item in interruptions)
                self.active["interruption_details"].extend(interruptions)
                self.active["level_checks"].clear()
            if self.pending is not None:
                self.pending.update(status="source_interruption_before_confirmation",
                                    continuity_interrupted_at_s=interruptions)
                self.items.append(self.pending)
                self.pending = None
            self.rejected_onset_not_before = max(item["at_s"] for item in interruptions)
        if self.pending is not None:
            pending = self.pending
            expired = (anchor - pending["proposed_at_s"] > parameters["maximum_pending_age_s"]
                       or anchor - pending["context_start_s"]
                       > parameters["maximum_frozen_span_s"])
            if expired:
                pending["status"] = "expired_without_fresh_fixed_geometry_confirmation"
                self.items.append(pending)
                self.pending = None
            elif new_original_count(original, pending["newest_original_s"], anchor
                                    ) >= parameters["minimum_new_confirmation_points"]:
                points, quality = original.support(anchor, 0, policy["sampling"],
                    3 * owner.policy.min_side_samples, start=pending["context_start_s"])
                if points:
                    knots = [EPOCH + timedelta(seconds=value) for value in pending["knots_s"]]
                    compared = common_models(owner, points, fixed_knots=knots,
                                             search_count=pending["search_count"])
                    assessed = pulse_assessment(owner, points, compared, policy)
                    if assessed is not None and assessed["supported"]:
                        pending.update(status="confirmed_form_hypothesis",
                            confirmed_at_s=anchor, fresh_confirmation=assessed,
                            confirmation_support=quality, event_probability=None,
                            physical_event_time_s=None, event_kind=None)
                        self.accept(pending, parameters)
                        self.pending = None
                    elif ((assessed is None and compared["status"] == "resolved")
                          or (assessed is not None and assessed.get("counterevidence"))):
                        pending.update(status="rejected_fixed_geometry",
                            rejected_at_s=anchor, counterevidence=assessed,
                            rejection_basis=("resolved_competing_form" if assessed is None
                                             else assessed["status"]))
                        self.items.append(pending)
                        self.pending = None
                        self.rejected_onsets.add(pending["knots_s"][0])
        # Proposals are not accumulated as independent evidence or events.
        current_pulses = [pulse for pulse in current_pulses
            if pulse["knots_s"][0] not in self.rejected_onsets
            and (self.rejected_onset_not_before is None
                 or pulse["knots_s"][0] > self.rejected_onset_not_before)]
        if self.pending is None and current_pulses:
            proposal = max(current_pulses, key=lambda item: item["gain_over_other"])
            if (self.active is None or proposal["knots_s"][1]
                    > self.active["latest_peak_s"] + parameters["peak_merge_tolerance_s"]):
                self.pending = {"channel": self.channel,
                    "status": "pending_fixed_geometry_confirmation", "event_kind": None,
                    "proposed_at_s": anchor, "confirmed_at_s": None,
                    "knots_s": proposal["knots_s"],
                    "context_start_s": proposal["context_start_s"],
                    "newest_original_s": proposal["newest_original_s"],
                    "search_count": proposal["search_count"],
                    "first_assessment": proposal["assessment"]}
        if self.active is not None:
            self.advance_level(anchor, original, owner)

    def accept(self, proposal, parameters):
        peak = proposal["knots_s"][1]
        level_at = (self.active.get("level_confirmed_at_s")
                    if self.active is not None else None)
        if (level_at is not None and (proposal["knots_s"][0] <= level_at
                or not proposal.get("first_assessment", {}).get("has_rise_onset"))):
            proposal["status"] = "stale_or_unresolved_onset_after_level"
            self.items.append(proposal)
            return
        if (self.active is not None and peak <= self.active["latest_peak_s"]
                + parameters["peak_merge_tolerance_s"]):
            proposal["status"] = "duplicate_geometry_no_new_form"
            self.items.append(proposal)
            return
        if self.active is None or self.active.get("level_confirmed_at_s") is not None:
            self.active = {"channel": self.channel,
                "status": "open_rise_peak_decline_form", "event_kind": None,
                "physical_event_time_s": None, "event_probability": None,
                "initial_proposal": proposal, "latest_peak_s": peak,
                "subsequent_peaks_before_level": [], "level_checks": [],
                "continuity_interrupted_at_s": [],
                "interruption_details": [],
                "level_confirmed_at_s": None,
                "interpretation": "climate form only; physical infusion attribution unresolved"}
            self.items.append(self.active)
        else:
            self.active["subsequent_peaks_before_level"].append(proposal)
            self.active["latest_peak_s"] = peak
            self.active["level_checks"].clear()
            self.active["interpretation"] = (
                "repeated peak before a confirmed new level; same open form hypothesis; "
                "physical continuation or separate input remains unresolved")

    def advance_level(self, anchor, original, owner):
        active, parameters = self.active, self.policy["hypotheses"]
        if active["level_confirmed_at_s"] is not None:
            return
        interruptions = original.interruptions(active["latest_peak_s"], anchor,
                                              self.policy["sampling"])
        known = set(active["continuity_interrupted_at_s"])
        for interruption in interruptions:
            if interruption["at_s"] not in known:
                active["continuity_interrupted_at_s"].append(interruption["at_s"])
                active["interruption_details"].append(interruption)
                active["level_checks"].clear()
        tail = parameters["tail_window_s"]
        if anchor - tail <= active["latest_peak_s"]:
            return
        points, quality = original.support(anchor, tail, self.policy["sampling"],
                                           2 * owner.policy.min_side_samples)
        if not points:
            active["level_checks"].clear()
            return
        checks = active["level_checks"]
        if (checks and new_original_count(original, checks[-1]["newest_original_s"], anchor)
                < parameters["minimum_new_level_points"]):
            return
        level = owner.call("curve_level", points, policy=owner.policy)
        if level is None or level.evidence != "supports":
            checks.clear()
            return
        checks.append({"decision_at_s": anchor,
                       "newest_original_s": (points[-1][0] - EPOCH).total_seconds(),
                       "support": quality, "owner_level": asdict(level)})
        if len(checks) >= parameters["tail_level_confirmations"]:
            interrupted = bool(active["continuity_interrupted_at_s"])
            active.update(status=("level_after_source_interruption" if interrupted
                                  else "rise_peak_decline_then_new_level_form"),
                          level_confirmed_at_s=anchor,
                          fitted_new_level=level.level,
                          level_rule="freely fitted latest tail; no earlier baseline target",
                          repeated_windows_are_independent=False)
            if interrupted:
                active["interpretation"] = (
                    "a new local level was observed after a source interruption; "
                    "continuous decay from the earlier peak is unobserved")

    def finish(self):
        if self.pending is not None:
            self.pending["status"] = "recording_ended_before_fresh_confirmation"
            self.items.append(self.pending)
        return self.items


def climate_interpretations(observations):
    """Supply existing routes with form evidence, without identifying causes."""
    result = []
    latest = {channel: values[0] for channel, values in observations.items() if values}
    for position in ("upper", "lower"):
        humidity = latest.get(f"{position}_humidity", {})
        temperature = latest.get(f"{position}_temperature", {})
        rh_trend = humidity.get("tail_trend")
        t_trend = temperature.get("tail_trend")
        rh_level = humidity.get("tail_level")
        if rh_trend is not None and rh_trend["direction"] == 1:
            smooth_windows = [row["window_s"]
                for row in observations.get(f"{position}_humidity", [])
                if row.get("comparison", {}).get("best_model") in ("L", "P1", "P2", "P3")]
            result.append({"position": position, "form": "moisture_build_up",
                "could_supply": "humidity_build_up/person_proxy evidence",
                "smooth_countermodel_windows_s": smooth_windows,
                "limit": "positive RH trend alone cannot identify a person or distinguish an infusion"})
        if (rh_trend is not None and t_trend is not None
                and rh_trend["direction"] == -1 and t_trend["direction"] == -1):
            result.append({"position": position, "form": "joint_temperature_humidity_fall",
                "could_supply": "opening/air_exchange candidate evidence",
                "limit": "mechanical opening and its real time remain unresolved"})
        if (rh_level is not None and rh_level["evidence"] == "supports"
                and t_trend is not None and t_trend["direction"] == -1):
            result.append({"position": position, "form": "rh_level_while_temperature_falls",
                "could_supply": "ventilation subsection within a possible door sequence",
                "limit": "RH flattening does not imply AH flattening or a mechanical closure"})
        for channel, row in ((f"{position}_temperature", temperature),
                             (f"{position}_humidity", humidity)):
            change = row.get("owner_forms", {}).get("change")
            if change is None or change["evidence"] != "supports":
                continue
            positive = (change["slope_change"] > 0 or change["curvature_change"] > 0)
            after = change.get("after_trend")
            before = row.get("owner_forms", {}).get("observed_before_trend")
            if (positive and before is not None and before["direction"] == -1
                    and after is not None and after["direction"] == -1):
                result.append({"channel": channel, "form": "fall_braking_without_recovery",
                    "could_supply": "short or long closure-form evidence in preceding door context",
                    "limit": "still falling originals are allowed; no mechanical closure claim"})
    return result


def analyse_session(session, policy, owner):
    start_wall, start_cpu = time.monotonic(), time.process_time()
    before_counts = owner.counts.copy()
    arrays = dict(session["original_series"])
    arrays.update({f"{position}_absolute_humidity": points for position, points
                   in session["absolute_humidity_series"].items()})
    originals = {channel: OriginalChannel(channel, arrays[channel]) for channel in CHANNELS}
    traces = {channel: PulseTrace(channel, policy) for channel in CHANNELS
              if channel.endswith("humidity")}
    end = session["analysis_window"]["end_s"]
    step = policy["sampling"]["anchor_step_s"]
    count = math.floor(end / step) + 1
    anchors = [index * step for index in range(count)]
    if anchors[-1] != end:
        anchors.append(end)
    if len(anchors) > policy["limits"]["maximum_anchors_per_session"]:
        raise WorkLimitError(f"{session['id']}: maximum_anchors_per_session exceeded")
    rows, quality_counts, model_counts = [], Counter(), Counter()
    maximum_fits, completed = 0, True
    failure = None
    for index, anchor in enumerate(anchors):
        owner.begin_anchor()
        observations, proposals = {}, {channel: [] for channel in traces}
        try:
            for channel, original in originals.items():
                channel_rows = []
                tail, tail_quality = original.support(anchor,
                    policy["hypotheses"]["tail_window_s"], policy["sampling"],
                    2 * owner.policy.min_side_samples)
                tail_trend = trend(owner, tail) if tail else None
                level = (owner.call("curve_level", tail, policy=owner.policy)
                         if tail and channel.endswith("humidity") else None)
                for window in policy["sampling"]["windows_s"]:
                    samples, quality = original.support(anchor, window, policy["sampling"],
                                                       3 * owner.policy.min_side_samples)
                    record = {"window_s": window, "quality": quality,
                              "tail_quality": tail_quality, "tail_trend": tail_trend,
                              "tail_level": asdict(level) if level is not None else None}
                    if not samples:
                        for reason in quality["reasons"]:
                            quality_counts[f"{channel}/{reason}"] += 1
                        channel_rows.append(record)
                        continue
                    comparison = common_models(owner, samples)
                    quality_counts[f"{channel}/{comparison['status']}"] += 1
                    record["comparison"] = comparison
                    if comparison["status"] == "resolved":
                        model_counts[f"{channel}/{window:g}/{comparison['best_model']}"] += 1
                        record["owner_forms"] = owner_forms(owner, samples, comparison, policy)
                        if channel in traces:
                            assessed = pulse_assessment(owner, samples, comparison, policy)
                            record["pulse"] = assessed
                            if assessed is not None and assessed["supported"]:
                                pulse = next(item for item in comparison["models"]
                                             if item["model"] == "PULS")
                                proposals[channel].append({**pulse, "assessment": assessed,
                                    "context_start_s": quality["start_s"],
                                    "newest_original_s": quality["end_s"]})
                    if not policy["reporting"]["include_all_model_scores"]:
                        comparison.pop("models", None)
                    channel_rows.append(record)
                observations[channel] = channel_rows
            for channel, trace in traces.items():
                trace.advance(anchor, originals[channel], proposals[channel], owner)
            rows.append({"decision_at_s": anchor, "channels": observations,
                         "interpretation_candidates": climate_interpretations(observations)})
        except WorkLimitError as error:
            completed, failure = False, str(error)
            maximum_fits = max(maximum_fits, owner.anchor_fits)
            break
        maximum_fits = max(maximum_fits, owner.anchor_fits)
        if (index + 1) % policy["reporting"]["progress_interval_anchors"] == 0:
            print(f"{session['id']}: {index + 1}/{len(anchors)} causal anchors; "
                  f"{time.monotonic() - start_wall:.1f}s", flush=True)
    return {"schema_version": 1, "session_id": session["id"], "completed": completed,
        "failure": failure, "source_provenance": session["provenance"],
        "source_counts": session["counts"], "analysis_window": session["analysis_window"],
        "original_preparation": {channel: {"eligible_originals": len(original.points),
            "excluded": dict(original.reasons)} for channel, original in originals.items()},
        "work": {"wall_seconds": time.monotonic() - start_wall,
            "cpu_seconds": time.process_time() - start_cpu,
            "scheduled_anchors": len(anchors), "completed_anchors": len(rows),
            "maximum_owner_fits_at_one_anchor": maximum_fits,
            "owner_calls": dict(owner.counts - before_counts),
            "quality_counts": dict(sorted(quality_counts.items())),
            "best_model_counts": dict(sorted(model_counts.items()))},
        "rows": rows, "form_hypotheses": {channel: trace.finish()
                                             for channel, trace in traces.items()}}


def probability_requirements():
    return {"event_probability": None, "status": "not_estimable_from_this_analysis",
        "reason": "No complete annotation coverage, independent calibration and held-out evaluation are consumed.",
        "valid_references": "Explicit user-confirmed curve assignments are valid expert references with their stated time uncertainty; no contact sensor is required.",
        "required": [
            "Opening/closure/infusion assignments, including explicit user confirmations, with provenance and time uncertainty.",
            "Complete negative observation intervals and relevant confusable climate forms.",
            "Whole independent source groups; R01/R02 share a source and cannot be split.",
            "Predetermined event horizon, matching rule, missed-event/false-alarm/latency metrics.",
            "Disjoint development, calibration and untouched test groups with uncertainty intervals.",
        ], "forbidden_substitutes": ["normalized BIC or softmax", "bootstrap stability",
            "overlapping-window hit rate", "old detector outputs", "presence as door/infusion truth"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--policy", default=DEFAULT_POLICY, type=Path)
    args = parser.parse_args(argv)
    try:
        policy_payload = args.policy.read_bytes()
        policy = validate_policy(json.loads(policy_payload))
    except (OSError, ValueError) as error:
        parser.error(str(error))
    reader, ah, owner, owner_provenance = load_owners(policy)
    manifest_path = ROOT / "tests" / "replay_manifest.json"
    manifest_payload = manifest_path.read_bytes()
    manifest = json.loads(manifest_payload)
    entries = manifest["sessions"]
    if [entry["id"] for entry in entries] != policy["session_ids"]:
        raise ValueError("Manifest must contain exactly ordered R01–R04")
    args.output.mkdir(parents=True, exist_ok=True)
    places = policy["reporting"]["decimal_places"]
    summaries, source_groups = [], {}
    run_wall, run_cpu = time.monotonic(), time.process_time()
    completed = True
    provenance = {**owner_provenance,
        "manifest": {"file": str(manifest_path.relative_to(ROOT)),
                     "sha256": digest(manifest_payload)},
        "runner": {"file": str(Path(__file__).relative_to(ROOT)),
                   "sha256": digest(Path(__file__).read_bytes())},
        "policy": {"file": str(args.policy), "sha256": digest(policy_payload)}}
    for entry in entries:
        print(f"Reading {entry['id']} once; starting causal form analysis", flush=True)
        read_wall, read_cpu = time.monotonic(), time.process_time()
        session = reader.read_session(entry)
        session["absolute_humidity_series"] = {position:
            reader.make_ah(session["original_series"], position, ah, policy["sampling"])
            for position in ("upper", "lower")}
        read_work = {"wall_seconds": time.monotonic() - read_wall,
                     "cpu_seconds": time.process_time() - read_cpu}
        for source_hash in session["provenance"]["source_sha256"]:
            source_groups.setdefault(source_hash, []).append(entry["id"])
        result = analyse_session(session, policy, owner)
        result.update(policy=policy, provenance=provenance,
                      probability_assessment=probability_requirements())
        result["work"]["read_and_ah"] = read_work
        filename = f"session-{entry['id']}.json"
        write_json(args.output / filename, result, places)
        summary = {"session_id": entry["id"], "file": filename,
                   "completed": result["completed"], "work": result["work"],
                   "form_hypothesis_counts": {channel: len(items)
                       for channel, items in result["form_hypotheses"].items()}}
        summaries.append(summary)
        # A completed per-recording file survives a later timeout/failure.
        write_json(args.output / "progress.json", {"completed": False,
            "provenance": provenance, "sessions": summaries}, places)
        print(f"Saved {filename}: {result['work']['completed_anchors']} anchors, "
              f"{result['work']['wall_seconds']:.1f}s wall", flush=True)
        if not result["completed"]:
            completed = False
            break
        del session, result
    index = {"schema_version": 1, "completed": completed, "policy": policy,
        "provenance": provenance, "sessions": summaries,
        "work": {"wall_seconds": time.monotonic() - run_wall,
                 "cpu_seconds": time.process_time() - run_cpu},
        "source_groups": [{"source_sha256": source_hash, "session_ids": ids,
            "independence": "not established; overlapping source groups stay together"}
            for source_hash, ids in sorted(source_groups.items())],
        "probability_assessment": probability_requirements(),
        "methodology": {
            "time": "original relative received times; decision_at is a causal anchor",
            "physical_times": "unobserved; model knots are geometry, never real door times",
            "support": "actual originals only; identical support and scale in direct competition",
            "noise": "consumed curve_change._level_models; no local Huber/MAD/QR copy",
            "missing": "None/unresolved is missing evidence, never an infinite score or zero probability",
            "causality": "each fit uses t<=decision_at; fixed knots and new originals confirm pulses",
            "AH": "dependent latest-pair view; never added to independent T/RH likelihood",
            "scores": "Huber+BIC+explicit actual knot search cost; no event probabilities",
            "API_groups": "owner trend/level/recovery/change scores have separate supports/scales",
            "hypotheses": "climate hypotheses only; no detector/controller/command execution",
            "scope": "all four full recordings with one predeclared analysis policy",
            "work": "one source read; bounded original selection, knot grid, IRLS and fit budget",
        }}
    write_json(args.output / "index.json", index, places)
    write_json(args.output / "progress.json", index, places)
    return 0 if completed else 2


if __name__ == "__main__":
    raise SystemExit(main())
