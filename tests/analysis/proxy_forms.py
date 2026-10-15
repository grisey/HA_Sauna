"""Retrospective T/RH/AH shape inventory; never run the sauna or its detector.

Run on Linux: python tests/analysis/proxy_forms.py --output DIR
Only manifest-bound climate inputs are consumed. This is exploratory descriptive
analysis, not an event classifier, replay, benchmark, or independent validation.
"""

from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
import gzip
import hashlib
import importlib
import importlib.util
from itertools import groupby
import json
import math
from pathlib import Path
import sys
from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
ROLES = (
    "upper_temperature", "upper_humidity",
    "lower_temperature", "lower_humidity",
)
CHANNELS = (*ROLES, "upper_absolute_humidity", "lower_absolute_humidity")
UNITS = {
    "temperature": "°C", "humidity": "% RH", "absolute_humidity": "g/m³",
}


def sha(payload):
    return hashlib.sha256(payload).hexdigest()


def bound_file(path, expected):
    payload = path.read_bytes()
    actual = sha(payload)
    if actual != expected:
        raise ValueError(f"Checksum mismatch: {path}")
    return payload, {"file": str(path.relative_to(ROOT)), "sha256": actual}


def moisture_owner():
    """Load the unchanged core package without executing HA's integration init."""
    name = "_proxy_forms_core"
    package_path = ROOT / "custom_components" / "ha_sauna" / "core"
    package = ModuleType(name)
    package.__path__ = [str(package_path)]
    package.__package__ = name
    package.__spec__ = importlib.util.spec_from_loader(name, loader=None,
                                                     is_package=True)
    sys.modules[name] = package
    module = importlib.import_module(f"{name}.moisture")
    return module.absolute_humidity, {
        "file": str(Path(module.__file__).relative_to(ROOT)),
        "sha256": sha(Path(module.__file__).read_bytes()),
        "function": "absolute_humidity",
        "loading": "isolated core package; no integration init/controller/runtime",
    }


def number(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value))


def clean_original(value):
    # Preserve even invalid source values in a standards-compliant JSON file.
    if isinstance(value, float) and not math.isfinite(value):
        return {"nonfinite_original": repr(value)}
    return value


def original_point(role, row, index, *, initial=False):
    age = row.get("reported_age_s")
    supplied_age = "reported_age_s" in row
    age_valid = not supplied_age or (number(age) and age >= 0)
    input_t = 0.0 if initial else row.get("t")
    t = -float(age) if initial and supplied_age and age_valid else input_t
    if initial and not supplied_age:
        t = None
    value = row.get("value")
    reason = None
    if not number(value):
        reason = "nonfinite_or_nonnumeric_value"
    elif role.endswith("humidity") and not 0 <= value <= 100:
        reason = "relative_humidity_outside_0_100"
    elif role.endswith("temperature") and not -100 <= value <= 200:
        reason = "temperature_outside_owner_equation_domain"
    elif not number(input_t) or input_t < 0:
        reason = "invalid_received_time"
    elif not age_valid:
        reason = "invalid_reported_age"
    point = {
        "t": t, "value": clean_original(value), "source_index": index,
        "origin": "initial_state" if initial else "samples",
        "valid": reason is None,
        "fit_eligible": reason is None and not initial,
        "time_basis": ("reconstructed_reported_time" if initial
                       else "original_received_time"),
    }
    if initial:
        point["input_t"] = 0.0
    if supplied_age:
        point["reported_age_s"] = clean_original(age)
        if age_valid:
            point["report_t"] = -float(age) if initial else input_t - age
    if reason:
        point["invalid_reason"] = reason
    return point


def read_session(entry):
    fixture_path = TESTS / entry["file"]
    payload, fixture_provenance = bound_file(fixture_path, entry["sha256"])
    fixture = json.loads(payload)
    if fixture["id"] != entry["id"] or fixture["kind"] != "real_session":
        raise ValueError(f"Not the declared real recording: {fixture_path}")
    samples_path = TESTS / entry["samples_file"]
    if fixture_path.parent / fixture["samples_file"] != samples_path:
        raise ValueError(f"Different sample bindings: {entry['id']}")
    if fixture["samples_sha256"] != entry["samples_sha256"]:
        raise ValueError(f"Different sample hashes: {entry['id']}")
    payload, samples_provenance = bound_file(samples_path, entry["samples_sha256"])
    series = {role: [] for role in ROLES}
    for role in ROLES:
        if role in fixture.get("initial_state", {}):
            spec = fixture["initial_state"][role]
            if not isinstance(spec, dict):
                spec = {"value": spec}
            series[role].append(original_point(role, spec, -1, initial=True))
    counts = Counter()
    previous_time = None
    rows = gzip.decompress(payload).splitlines()
    for index, line in enumerate(rows):
        if not line.strip():
            continue
        row = json.loads(line)
        role = row.get("role", "missing_role")
        counts[role] += 1
        if role not in ROLES:
            continue
        point = original_point(role, row, index)
        t = row.get("t")
        if number(t):
            if previous_time is not None and t < previous_time:
                raise ValueError(f"Received-time order regresses: {entry['id']}")
            previous_time = t
        series[role].append(point)
    total = sum(counts.values())
    if total != entry["samples_count"] or total != fixture["samples_count"]:
        raise ValueError(f"Sample row count mismatch: {entry['id']}")
    source = fixture.get("source", {})
    duration = fixture["duration_s"]
    if not number(duration) or duration <= 0:
        raise ValueError(f"Invalid recording duration: {entry['id']}")
    observed_times = [p["t"] for points in series.values() for p in points
                      if p["origin"] == "samples" and number(p["t"])]
    analysis_end = max([duration, *observed_times])
    return {
        "id": entry["id"], "duration_s": duration,
        "analysis_window": {"start_s": 0, "end_s": analysis_end,
                            "extends_fixture_duration": analysis_end > duration},
        "provenance": {
            "fixture": fixture_provenance, "samples": samples_provenance,
            "source_sha256": source.get("source_sha256", []),
            "timestamp_basis": source.get("timestamp_basis"),
            "transformations": source.get("transformations", []),
            "read_count_per_bound_file": 1,
        },
        "counts": {
            "all_sample_rows": total, "sample_rows_by_role": dict(counts),
            "excluded_roles": {role: {"count": count,
                "reason": "not an allowed T/RH input; neither feature nor label"}
                for role, count in counts.items() if role not in ROLES},
            "original_sources": {role: {
                "total": len(points),
                "valid": sum(p["valid"] for p in points),
                "invalid": sum(not p["valid"] for p in points),
                "initial": sum(p["origin"] == "initial_state" for p in points),
                "fit_eligible": sum(p["fit_eligible"] for p in points),
            } for role, points in series.items()},
        },
        "original_series": series,
    }


def make_ah(series, position, owner, sampling):
    """Derived event series: latest received pair; never an original measurement."""
    temperature = series[f"{position}_temperature"]
    humidity = series[f"{position}_humidity"]
    events = []
    for channel, points in (("temperature", temperature), ("humidity", humidity)):
        for index, point in enumerate(points):
            at = point.get("input_t", point["t"])
            if number(at):
                events.append((at, point["source_index"], channel, index, point))
    events.sort(key=lambda e: (e[0], e[1], e[2]))
    latest = {}
    result = []
    for at, batch in groupby(events, key=lambda e: e[0]):
        updates = list(batch)
        for _, _, channel, index, point in updates:
            latest[channel] = (index, point)
        if len(latest) != 2:
            continue
        ti, tp = latest["temperature"]
        hi, hp = latest["humidity"]
        tt, ht = tp["t"], hp["t"]
        tr, hr = tp.get("report_t", tt), hp.get("report_t", ht)
        ta, ha = (at - tr if number(tr) else None,
                  at - hr if number(hr) else None)
        skew = abs(tr - hr) if number(tr) and number(hr) else None
        reason = None
        if not tp["valid"] or not hp["valid"]:
            reason = "invalid_source_pair"
        elif ta is None or ha is None:
            reason = "unknown_original_source_time"
        elif min(ta, ha) < 0 or max(ta, ha) > sampling["maximum_pair_age_s"]:
            reason = "source_pair_age_outside_analysis_limit"
        elif skew > sampling["maximum_pair_skew_s"]:
            reason = "source_pair_skew_outside_analysis_limit"
        row = {
            "t": at, "value": None, "valid": reason is None,
            "fit_eligible": reason is None and tp["fit_eligible"]
            and hp["fit_eligible"],
            "temperature_index": ti, "humidity_index": hi,
            "temperature_t": tt, "humidity_t": ht,
            "temperature_age_s": ta, "humidity_age_s": ha,
            "temperature_report_t": tr, "humidity_report_t": hr,
            "temperature_received_age_s": at - tt if number(tt) else None,
            "humidity_received_age_s": at - ht if number(ht) else None,
            "received_pair_skew_s": abs(tt - ht)
            if number(tt) and number(ht) else None,
            "pair_skew_s": skew,
            "updated_channel": "+".join(sorted({u[2] for u in updates})),
            "simultaneous_updates": len(updates),
            "origin": "derived_latest_received_pair",
        }
        if reason is None:
            try:
                row["value"] = owner(tp["value"], hp["value"])
            except ValueError as error:
                row.update(valid=False, fit_eligible=False,
                           invalid_reason=str(error))
        else:
            row["invalid_reason"] = reason
        result.append(row)
    return result


def solve3(matrix, vector):
    rows = [list(row) + [value] for row, value in zip(matrix, vector)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda k: abs(rows[k][col]))
        rows[col], rows[pivot] = rows[pivot], rows[col]
        divisor = rows[col][col]
        if abs(divisor) < 1e-12:
            return None
        rows[col] = [x / divisor for x in rows[col]]
        for other in range(3):
            if other != col:
                factor = rows[other][col]
                rows[other] = [a - factor * b
                               for a, b in zip(rows[other], rows[col])]
    return [row[-1] for row in rows]


def local_fit(points, times, bad_times, anchor, window, sampling):
    half = window / 2
    lo = bisect_left(times, anchor - half)
    hi = bisect_right(times, anchor + half)
    support = points[lo:hi]
    n = len(support)
    first, last = (times[lo], times[hi - 1]) if n else (None, None)
    span = last - first if n else 0.0
    max_gap = max((b["t"] - a["t"] for a, b in zip(support, support[1:])),
                  default=0.0)
    unique_times = 1 + sum(b["t"] > a["t"]
                           for a, b in zip(support, support[1:])) if n else 0
    interrupted = (bisect_right(bad_times, anchor + half)
                   > bisect_left(bad_times, anchor - half))
    bilateral = n > 1 and first < anchor < last
    quality = {
        "n": n, "unique_times": unique_times,
        "span_s": span, "max_gap_s": max_gap,
        "bilateral": bilateral, "interrupted": interrupted,
        "first_t": first, "last_t": last, "fit_eligible": False,
        "linear": None, "quadratic": None,
    }
    if (unique_times < sampling["minimum_points"] or not bilateral or interrupted
            or span < window * sampling["minimum_span_fraction"]
            or max_gap > window * sampling["maximum_gap_fraction"]):
        return quality
    # Normalize original times around the anchor, avoiding large-time powers.
    # A local y offset similarly avoids cancellation in the residual sum.
    y0 = support[0]["value"]
    sx = sx2 = sx3 = sx4 = sy = sxy = sx2y = sy2 = 0.0
    for point in support:
        x = (point["t"] - anchor) / half
        y = point["value"] - y0
        x2 = x * x
        sx += x
        sx2 += x2
        sx3 += x2 * x
        sx4 += x2 * x2
        sy += y
        sxy += x * y
        sx2y += x2 * y
        sy2 += y * y
    denominator = n * sx2 - sx * sx
    if denominator <= 1e-12:
        return quality
    b = (n * sxy - sx * sy) / denominator
    a = (sy - b * sx) / n
    linear_rss = max(0.0, sy2 - a * sy - b * sxy)
    coefficient = solve3(((n, sx, sx2), (sx, sx2, sx3), (sx2, sx3, sx4)),
                         (sy, sxy, sx2y))
    if coefficient is None:
        return quality
    qa, qb, qc = coefficient
    quadratic_rss = max(0.0, sy2 - qa * sy - qb * sxy - qc * sx2y)
    quality.update(
        fit_eligible=True,
        linear={"value": a + y0, "d1": b / half * 60,
                "rmse": math.sqrt(linear_rss / n), "residual_dof": n - 2},
        quadratic={"value": qa + y0, "d1": qb / half * 60,
                   "d2": 2 * qc / (half * half) * 3600,
                   "rmse": math.sqrt(quadratic_rss / n),
                   "residual_dof": n - 3},
    )
    return quality


def build_features(session, sampling):
    all_series = dict(session["original_series"])
    all_series.update({f"{p}_absolute_humidity": rows
                       for p, rows in session["absolute_humidity_series"].items()})
    prepared = {}
    for channel, points in all_series.items():
        good = sorted((p for p in points if p["fit_eligible"]),
                      key=lambda p: p["t"])
        bad = sorted(p["t"] for p in points
                     if not p["fit_eligible"] and number(p["t"])
                     and p["origin"] != "initial_state")
        prepared[channel] = (good, [p["t"] for p in good], bad)
    step = sampling["feature_step_s"]
    duration = session["analysis_window"]["end_s"]
    anchors = [i * step for i in range(math.floor(duration / step) + 1)]
    if anchors[-1] != duration:
        anchors.append(duration)
    rows = []
    eligible = Counter()
    for anchor in anchors:
        channels = {}
        for channel, (points, times, bad_times) in prepared.items():
            channel_fits = {}
            for window in sampling["fit_windows_s"]:
                key = window_key(window)
                fit = local_fit(points, times, bad_times, anchor, window, sampling)
                channel_fits[key] = fit
                eligible[f"{channel}/{key}"] += fit["fit_eligible"]
            channels[channel] = channel_fits
        rows.append({"t": anchor, "channels": channels})
    session["feature_rows"] = rows
    session["coverage"] = {
        "start_s": 0, "end_s": duration, "feature_anchors": len(rows),
        "feature_step_s": step, "eligible_fits_by_channel_and_scale": dict(eligible),
        "all_original_points_retained": True,
        "noncandidate_intervals": "available in the complete original series",
        "no_operation_presence_or_phase_gating": True,
        "original_time_ranges": {channel: {
            "sample_min_t": min((p["t"] for p in points
                                  if p["origin"] == "samples" and number(p["t"])),
                                 default=None),
            "sample_max_t": max((p["t"] for p in points
                                  if p["origin"] == "samples" and number(p["t"])),
                                 default=None),
            "initial_times": [p["t"] for p in points if p["origin"] == "initial_state"],
        } for channel, points in session["original_series"].items()},
    }


def window_key(value):
    return f"{value:g}"


def fit_value(row, channel, scale, field):
    fit = row["channels"][channel][window_key(scale)]
    model = "quadratic" if field == "d2" else "linear"
    return fit[model][field] if fit["fit_eligible"] else None


def percentile(sorted_values, value):
    if not sorted_values:
        return None
    # Midrank prevents a flat/quantized run of zeros becoming an extreme.
    return (bisect_left(sorted_values, value) + bisect_right(sorted_values, value)) / (
        2 * len(sorted_values))


def quantile(values, probability):
    values = sorted(values)
    if not values:
        return None
    index = (len(values) - 1) * probability
    left = math.floor(index)
    return values[left] + (values[math.ceil(index)] - values[left]) * (index - left)


def reason_item(channel, scale, field, value, rank, at, form):
    return {"channel": channel, "scale_s": scale, "quantity": field,
            "value": value, "rank": rank, "t": at, "form": form,
            "kind": field, "label": f"{channel}: {form} ({scale:g} s)"}


def find_candidates(session, policy):
    rows = session["feature_rows"]
    sampling, selection = policy["sampling"], policy["selection"]
    pools = {}
    for channel in CHANNELS:
        for scale in sampling["fit_windows_s"]:
            for field in ("d1", "d2"):
                pools[channel, scale, field] = sorted(
                    abs(value) for row in rows
                    if (value := fit_value(row, channel, scale, field)) is not None)
    by_index = defaultdict(list)
    step = sampling["feature_step_s"]
    for i, row in enumerate(rows):
        at = row["t"]
        for channel in CHANNELS:
            for scale in sampling["fit_windows_s"]:
                for field in ("d1", "d2"):
                    value = fit_value(row, channel, scale, field)
                    if value is None or value == 0:
                        continue
                    rank = percentile(pools[channel, scale, field], abs(value))
                    if rank >= selection["extreme_rank"]:
                        form = ("Anstieg" if value > 0 else "Abfall") if field == "d1" else (
                            "positive Krümmung" if value > 0 else "negative Krümmung")
                        evidence = reason_item(channel, scale, field,
                                               value, rank, at, form)
                        fit = row["channels"][channel][window_key(scale)]
                        evidence.update(support_start_s=fit["first_t"],
                                        support_end_s=fit["last_t"])
                        by_index[i].append(evidence)
                offset = max(1, round(scale / (2 * step)))
                if i < offset or i + offset >= len(rows):
                    continue
                before = fit_value(rows[i - offset], channel, scale, "d1")
                after = fit_value(rows[i + offset], channel, scale, "d1")
                if before is None or after is None or before == 0:
                    continue
                rank_before = percentile(pools[channel, scale, "d1"], abs(before))
                rank_after = percentile(pools[channel, scale, "d1"], abs(after))
                form = None
                if before * after < 0 and min(rank_before, rank_after) >= selection["form_rank"]:
                    form = "Spitzenfolge" if before > 0 else "Talfolge"
                elif (rank_before >= selection["form_rank"]
                      and abs(after) <= abs(before) * selection["flattening_ratio"]):
                    form = "Abflachung nach Anstieg" if before > 0 else "Abflachung nach Abfall"
                if form:
                    evidence = reason_item(channel, scale, "d1_sequence", after,
                                           rank_before, at, form)
                    evidence.update(before_t=rows[i - offset]["t"], before_d1=before,
                                    after_t=rows[i + offset]["t"], after_d1=after)
                    bf = rows[i - offset]["channels"][channel][window_key(scale)]
                    af = rows[i + offset]["channels"][channel][window_key(scale)]
                    evidence.update(support_start_s=bf["first_t"],
                                    support_end_s=af["last_t"],
                                    before_support=[bf["first_t"], bf["last_t"]],
                                    after_support=[af["first_t"], af["last_t"]])
                    by_index[i].append(evidence)
    ranked = sorted(by_index, key=lambda i: (
        -max(r["rank"] for r in by_index[i]),
        -len(by_index[i]), rows[i]["t"]))
    chosen = []
    for index in ranked:
        at = rows[index]["t"]
        if all(abs(at - rows[other]["t"]) >= selection["minimum_center_separation_s"]
               for other in chosen):
            chosen.append(index)
        if len(chosen) >= selection["maximum_shape_cases_per_session"]:
            break
    shape_count = len(chosen)
    shape_indices = set(chosen)
    control_added = 0
    target = selection["control_interval_s"] / 2
    while target <= session["analysis_window"]["end_s"]:
        index = min(range(len(rows)), key=lambda i: abs(rows[i]["t"] - target))
        if all(abs(rows[index]["t"] - rows[other]["t"])
               >= selection["minimum_center_separation_s"] for other in chosen):
            chosen.append(index)
            control_added += 1
        target += selection["control_interval_s"]
    cases = []
    for serial, index in enumerate(sorted(chosen), 1):
        row = rows[index]
        reasons = sorted(by_index[index], key=lambda r: (-r["rank"], r["channel"],
                                                         r["scale_s"], r["quantity"]))
        is_control = index not in shape_indices
        # A scheduled control may coincide with a low-ranked search proposal.
        primary = reasons[:selection["maximum_evidence_items"]]
        forms = list(dict.fromkeys(r["form"] for r in primary))
        window = {"start_s": max(0, row["t"] - selection["case_before_s"]),
                  "end_s": min(session["analysis_window"]["end_s"], row["t"] + selection["case_after_s"]),
                  "center_s": row["t"]}
        if primary:
            window["start_s"] = max(0, min(window["start_s"],
                min(r["support_start_s"] for r in primary)))
            window["end_s"] = min(session["analysis_window"]["end_s"], max(window["end_s"],
                max(r["support_end_s"] for r in primary)))
        case = {
            "id": f"{session['id']}-F{serial:03d}", "session_id": session["id"],
            "status": "ungeklärt", "window": window,
            "selection_reasons": primary if primary else [{
                "kind": "control", "label": "Kontrollfenster",
                "form": "gleichmäßig verteiltes Kontrollfenster",
                "t": row["t"], "reason": "covers ordinary and unmarked curve sections"}],
            "question": ("Welche physische Ursache erklärt " + "; ".join(
                f"{e['channel']}: {e['form']} ({e['scale_s']:g} s)" for e in primary[:3])
                         + "; tragen AH und beide Messpositionen dieselbe Deutung?"
                         if forms else "Welche ruhigen oder bisher unmarkierten Formen liegen hier vor; sind Abweichungen durch beide Positionen und AH gestützt?"),
            "curve_evidence": primary + level_evidence(row, policy),
            "competing_interpretations": [
                "Thermischer Verlauf oder Luftaustausch können T/RH gemeinsam verändern.",
                "Feuchteeintrag oder Umverteilung können ähnliche RH-Formen erzeugen; AH ist von T/RH abhängig.",
                "Zeitversetzte Sensormeldungen, Quantisierung und lokale Durchmischung können Spitzen oder Krümmung mitprägen.",
            ],
            "assignment_limit": "Keine unabhängige Ereignisannotation; die Form begründet keine bestätigte Tür-, Aufguss- oder Anwesenheitszuordnung.",
            "selection_kind": "control" if is_control else "shape_proposal",
        }
        attach_slices(case, session)
        cases.append(case)
    summary = {
        "session_id": session["id"], "all_feature_anchors": len(rows),
        "anchors_with_any_shape_proposal": len(by_index),
        "retained_shape_centers": shape_count, "added_control_centers": control_added,
        "total_cases": len(cases), "ranks_scope": "within this recording, channel, scale and derivative",
        "cap_applied": shape_count >= selection["maximum_shape_cases_per_session"]
        and len(ranked) > shape_count,
        "proposed_centers_before_separation": len(ranked),
        "centers_removed_by_separation_or_cap": len(ranked) - shape_count,
        "not_an_exhaustive_event_list": True,
    }
    return cases, summary


def level_evidence(row, policy):
    scale = policy["comparison"]["level_window_s"]
    return [{"channel": channel, "scale_s": scale, "quantity": "value",
             "value": value, "t": row["t"], "form": "lokales Niveau am Fenstermittelpunkt"}
            for channel in CHANNELS
            if (value := fit_value(row, channel, scale, "value")) is not None]


def attach_slices(case, session):
    start, end = case["window"]["start_s"], case["window"]["end_s"]
    originals, ah, refs = {}, {}, {}
    for name, series in (("original_series", session["original_series"]),
                         ("absolute_humidity_series", session["absolute_humidity_series"])):
        target = originals if name == "original_series" else ah
        for channel, points in series.items():
            indices = [i for i, p in enumerate(points)
                       if number(p["t"]) and start <= p["t"] <= end]
            target[channel] = [points[i] for i in indices]
            refs[f"{name}/{channel}"] = {
                "json_pointer": f"/sessions/{session['_index']}/{name}/{channel}",
                "start_index": indices[0] if indices else None,
                "end_index_exclusive": indices[-1] + 1 if indices else None,
                "count": len(indices),
            }
    features = [row for row in session["feature_rows"] if start <= row["t"] <= end]
    case["original_references"] = {"corpus_file": "corpus.json", "series": refs}
    case["curve_slices"] = {"original_series": originals,
                            "absolute_humidity_series": ah,
                            "feature_rows": features}


def descriptors(policy):
    scales = policy["sampling"]["fit_windows_s"]
    level_scale = policy["comparison"]["level_window_s"]
    result = {}
    for channel in CHANNELS:
        result[f"{channel}/value/{window_key(level_scale)}"] = (channel, level_scale, "value")
        for scale in scales:
            for field in ("d1", "d2"):
                result[f"{channel}/{field}/{window_key(scale)}"] = (channel, scale, field)
    return result


def feature_families(specs):
    def choose(channels, derivatives=False):
        return [key for key, (channel, _, field) in specs.items()
                if channel in channels and (derivatives or field == "value")]
    temperatures = [c for c in CHANNELS if c.endswith("temperature")]
    rh = list(ROLES[1::2])
    climate = list(ROLES)
    return {
        "T": choose(temperatures), "RH": choose(rh),
        "T_RH": choose(climate), "T_RH_AH": choose(CHANNELS),
        "T_RH_derivatives": choose(climate, True),
        "T_RH_AH_derivatives": choose(CHANNELS, True),
        "upper_all": choose([c for c in CHANNELS if c.startswith("upper_")], True),
        "both_all": choose(CHANNELS, True),
    }


def distance(a, b, keys, normalization, minimum_fraction):
    differences = []
    for key in keys:
        va, vb = a.get(key), b.get(key)
        scale = normalization[key]["scale"]
        if va is not None and vb is not None and scale is not None:
            differences.append(((va - vb) / scale) ** 2)
    usable_keys = sum(normalization[key]["scale"] is not None for key in keys)
    if not differences or len(differences) < usable_keys * minimum_fraction:
        return None
    return math.sqrt(sum(differences) / len(differences))


def descriptor_coverage(a, b, keys, normalization):
    scalable = [key for key in keys if normalization[key]["scale"] is not None]
    shared = [key for key in scalable if a.get(key) is not None and b.get(key) is not None]
    return {"declared": len(keys), "nonconstant": len(scalable),
            "shared": len(shared), "shared_descriptors": shared,
            "missing_descriptors": [key for key in scalable if key not in shared],
            "constant_descriptors_excluded": [key for key in keys if key not in scalable]}


def compare_features(cases, sessions, policy):
    specs = descriptors(policy)
    families = feature_families(specs)
    centers = {(s["id"], row["t"]): row for s in sessions for row in s["feature_rows"]}
    vectors = []
    for case in cases:
        row = centers[case["session_id"], case["window"]["center_s"]]
        vectors.append({key: fit_value(row, *spec) for key, spec in specs.items()})
    normalization = {}
    for key in specs:
        values = [v[key] for v in vectors if v[key] is not None]
        q25, q50, q75 = (quantile(values, q) for q in (.25, .5, .75))
        spread = q75 - q25 if values else 0
        method = "interquartile_range"
        if spread == 0 and values:
            spread, method = max(values) - min(values), "observed_range_fallback"
        normalization[key] = {"median": q50, "q25": q25, "q75": q75,
                              "scale": spread if spread > 0 else None,
                              "method": method, "valid_case_count": len(values)}
    extensions = (
        ("T", "T_RH"), ("RH", "T_RH"), ("T_RH", "T_RH_AH"),
        ("T_RH", "T_RH_derivatives"),
        ("T_RH_AH", "T_RH_AH_derivatives"),
        ("T_RH_derivatives", "T_RH_AH_derivatives"),
        ("upper_all", "both_all"),
    )
    settings = policy["comparison"]
    comparisons, summaries = [], []
    for base, extended in extensions:
        added = [key for key in families[extended] if key not in families[base]]
        pairs = []
        for i, a in enumerate(vectors):
            for j in range(i + 1, len(vectors)):
                # Nearby, overlapping windows are not informative contrast pairs.
                ca, cb = cases[i], cases[j]
                if (ca["session_id"] == cb["session_id"]
                        and ca["window"]["start_s"] < cb["window"]["end_s"]
                        and cb["window"]["start_s"] < ca["window"]["end_s"]):
                    continue
                b = vectors[j]
                bd = distance(a, b, families[base], normalization,
                              settings["minimum_shared_fraction"])
                ad = distance(a, b, added, normalization,
                              settings["minimum_shared_fraction"])
                if bd is not None and ad is not None:
                    pairs.append((bd, ad, i, j))
        base_cut = quantile([p[0] for p in pairs], settings["base_similarity_quantile"])
        close = [p for p in pairs if p[0] <= base_cut] if pairs else []
        added_cut = quantile([p[1] for p in close], settings["added_contrast_quantile"])
        retained = sorted((p for p in close if p[1] >= added_cut and p[1] > 0),
                          key=lambda p: (-p[1], p[0], p[2], p[3])) if close else []
        retained = retained[:settings["maximum_pairs_per_extension"]]
        summaries.append({"base_family": base, "extended_family": extended,
                          "eligible_pairs": len(pairs), "base_distance_cut": base_cut,
                          "added_distance_cut_among_close_pairs": added_cut,
                          "retained_pairs": len(retained)})
        for bd, ad, i, j in retained:
            contrasts = []
            for key in added:
                va, vb = vectors[i].get(key), vectors[j].get(key)
                scale = normalization[key]["scale"]
                if va is not None and vb is not None and scale:
                    contrasts.append({"descriptor": key, "a": va, "b": vb,
                                      "normalized_difference": abs(va - vb) / scale})
            contrasts.sort(key=lambda c: (-c["normalized_difference"], c["descriptor"]))
            comparisons.append({
                "id": f"C{len(comparisons) + 1:03d}",
                "base_family": base, "added_family": extended,
                "added_descriptors": added,
                "case_a": cases[i]["id"], "case_b": cases[j]["id"],
                "base_distance": bd, "added_distance": ad,
                "base_coverage": descriptor_coverage(vectors[i], vectors[j],
                                                     families[base], normalization),
                "added_coverage": descriptor_coverage(vectors[i], vectors[j],
                                                      added, normalization),
                "added_contrasts": contrasts[:settings["maximum_contrasts_per_pair"]],
                "status": "ungeklärt",
                "interpretation": "Ähnliche Basismerkmale zeigen verschiedene Zusatzmerkmale; die physische Ereigniszuordnung bleibt offen.",
                "source_independence": "not established; consult source_groups in corpus.json",
            })
    return {
        "schema_version": 1, "families": families,
        "descriptor_schema": {key: {"channel": c, "scale_s": s, "quantity": f}
                              for key, (c, s, f) in specs.items()},
        "normalization": normalization,
        "methodology": {
            "pair_population": "same selected case set for every extension; nonoverlapping same-recording windows or cross-recording pairs; eligible coordinate/pair counts can differ with missing support and are reported",
            "distance": "RMS of differences divided by the descriptor IQR (range fallback); base and added dimensions scored separately",
            "normalization_population": "all selected case centers; R01/R02 may share source material and are not independent replicates",
            "selection": "lowest configured base-distance quantile, then highest added-distance quantile among those pairs",
        },
        "limitations": [
            "No independent event labels: no measured specificity, sensitivity, false-positive rate or causal identification.",
            "An added coordinate, particularly dependent AH, can distinguish feature vectors without improving event recognition.",
            "Selection and normalization are exploratory, corpus-dependent and evaluated on the same recordings.",
            "R01/R02 share a source hash; relative-time recordings do not establish whether portions overlap.",
        ],
        "selection_summary": summaries, "comparisons": comparisons,
    }


def rounded(value, places):
    if isinstance(value, float):
        return round(value, places)
    if isinstance(value, list):
        return [rounded(v, places) for v in value]
    if isinstance(value, dict):
        return {k: rounded(v, places) for k, v in value.items()}
    return value


def write_json(path, data):
    with path.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, allow_nan=False,
                  sort_keys=True, separators=(",", ":"))
        stream.write("\n")


def validate_policy(policy):
    if policy["session_ids"] != ["R01", "R02", "R03", "R04"]:
        raise ValueError("The complete R01–R04 corpus is required")
    sampling = policy["sampling"]
    if sampling["feature_step_s"] <= 0 or sampling["minimum_points"] < 5:
        raise ValueError("Positive sampling step and at least five fit points required")
    if any(s <= 0 for s in sampling["fit_windows_s"]):
        raise ValueError("Positive fit windows required")
    if policy["comparison"]["level_window_s"] not in sampling["fit_windows_s"]:
        raise ValueError("Comparison level window must be a declared fit scale")
    if policy["selection"]["control_interval_s"] <= 0:
        raise ValueError("Positive control interval required")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--policy", type=Path,
                        default=Path(__file__).with_name("proxy_forms_policy.json"))
    args = parser.parse_args()
    policy_payload = args.policy.read_bytes()
    policy = json.loads(policy_payload)
    validate_policy(policy)
    manifest_path = TESTS / "replay_manifest.json"
    manifest_payload = manifest_path.read_bytes()
    manifest = json.loads(manifest_payload)
    entries = manifest["sessions"]
    if [e["id"] for e in entries] != policy["session_ids"]:
        raise ValueError("Manifest does not contain exactly ordered R01–R04")
    owner, owner_provenance = moisture_owner()
    sessions, cases, selection_summary = [], [], []
    for index, entry in enumerate(entries):
        print(f"Reading and analysing {entry['id']}", flush=True)
        session = read_session(entry)
        session["_index"] = index
        session["absolute_humidity_series"] = {
            position: make_ah(session["original_series"], position, owner,
                              policy["sampling"])
            for position in ("upper", "lower")}
        session["counts"]["derived_sources"] = {position: {
            "total": len(points), "valid": sum(p["valid"] for p in points),
            "invalid": sum(not p["valid"] for p in points),
            "fit_eligible": sum(p["fit_eligible"] for p in points),
        } for position, points in session["absolute_humidity_series"].items()}
        build_features(session, policy["sampling"])
        selected, summary = find_candidates(session, policy)
        cases.extend(selected)
        selection_summary.append(summary)
        sessions.append(session)
    comparison = compare_features(cases, sessions, policy)
    source_groups = defaultdict(list)
    for session in sessions:
        for source_hash in session["provenance"]["source_sha256"]:
            source_groups[source_hash].append(session["id"])
        del session["_index"]
    methodology = {
        "scope": "offline retrospective descriptive shape analysis of all four complete real recordings",
        "features": "only measured T/RH, product-owner-derived AH and local derivatives; no presence/actions/expectations/old outputs read as features or labels",
        "time": "t is original relative received time in seconds; initial snapshots retain reported age and reconstructed negative report_t/t, remain ineligible for fits",
        "initial_snapshots": "available at input_t=0; no fabricated zero-time original measurement; seeds never enter mixed-clock fits",
        "fits": "unweighted local linear and quadratic least squares on original received times; normalized local coordinates; both sides of each evaluation anchor",
        "derivatives": {"d1": "original channel unit per minute",
                        "d2": "original channel unit per minute squared",
                        "linear_d1": "linear slope ×60",
                        "quadratic_d1": "derivative at anchor ×60",
                        "quadratic_d2": "2×quadratic coefficient ×3600"},
        "units": UNITS,
        "evaluation_grid": "only fit anchors; never resampled or interpolated original observations",
        "AH": "latest received T/RH pair at either source update; simultaneous equal-time inputs bundled into the final pair; both source indices/times/ages retained; age/skew limits use report_t where supplied, received time otherwise; received ages/skew separately retained; seed-dependent rows excluded from fits",
        "AH_derivative_limit": "dependent transform; asynchronous held counterpart may produce steps; AH is no additional independent sensor",
        "quality": "invalid/ineligible observations break fit support; bilateral coverage/n/span/max-gap gates are descriptive quality checks, not significance tests",
        "not_a_latency_demonstration": "fits use future measurements; cannot establish an online response time",
        "labels": "all proposed physical assignments remain ungeklärt; no independent labels consumed",
        "selection": "within-recording derivative midranks and sign/flattening sequences, separated/capped centers, plus uniformly spaced control windows; complete curves remain available outside cases",
        "selection_parameters": "all limits/quantiles/scales are analyst choices in the policy, not product rules or event thresholds",
    }
    corpus = {
        "schema_version": 1, "methodology": methodology, "policy": policy,
        "provenance": {
            "manifest": {"file": str(manifest_path.relative_to(ROOT)),
                         "sha256": sha(manifest_payload)},
            "policy": {"file": args.policy.name, "sha256": sha(policy_payload)},
            "runner": {"file": str(Path(__file__).relative_to(ROOT)),
                       "sha256": sha(Path(__file__).read_bytes())},
            "absolute_humidity_owner": owner_provenance,
        },
        "source_groups": [{"source_sha256": key, "session_ids": value,
                           "independence": "not established; shared source may overlap"}
                          for key, value in sorted(source_groups.items())],
        "sessions": sessions,
    }
    # Originals must retain their exact parsed source values. Only derived
    # arrays are rounded, before serializing shared case excerpts as well.
    places = policy["sampling"]["derived_decimal_places"]
    for session in sessions:
        session["absolute_humidity_series"] = rounded(session["absolute_humidity_series"], places)
        session["feature_rows"] = rounded(session["feature_rows"], places)
    for case in cases:
        case["curve_slices"]["absolute_humidity_series"] = rounded(
            case["curve_slices"]["absolute_humidity_series"], places)
        case["curve_slices"]["feature_rows"] = rounded(case["curve_slices"]["feature_rows"], places)
        case["curve_evidence"] = rounded(case["curve_evidence"], places)
        case["selection_reasons"] = rounded(case["selection_reasons"], places)
    args.output.mkdir(parents=True, exist_ok=True)
    write_json(args.output / "corpus.json", corpus)
    write_json(args.output / "cases.json", {
        "schema_version": 1, "methodology": methodology,
        "selection_summary": selection_summary, "cases": cases})
    write_json(args.output / "feature_comparison.json", rounded(comparison, places))
    print(f"Wrote {len(sessions)} recordings, {len(cases)} unresolved windows, "
          f"{len(comparison['comparisons'])} exploratory contrast pairs", flush=True)


if __name__ == "__main__":
    main()
