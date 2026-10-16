"""Causal ventilation-form features on every unchanged, bound climate prefix.

Run on Linux: python tests/analysis/ventilation_forms.py --output DIR
This descriptive inventory calls the fixed-original core owners only. It does
not replay a controller, fit BIC models, estimate noise, admit candidates, or
assign physical door/ventilation times. R04 remains evaluation input.
"""

from __future__ import annotations

import argparse
from collections import Counter, deque
from datetime import datetime, timedelta
import gzip
from heapq import merge
import importlib
import importlib.util
import io
import json
from pathlib import Path
import sys
from time import perf_counter
from types import ModuleType

from proxy_forms import (
    ROOT,
    ROLES,
    TESTS,
    bound_file,
    number,
    read_session,
    sha,
    write_json,
)


POSITIONS = ("upper", "lower")
SETTINGS_PATH = TESTS / "settings.json"
SETTINGS_PAYLOAD = SETTINGS_PATH.read_bytes()
# Same mathematical time origin as original_prefix, owned by test settings.
# It is not a recording timestamp or a physical event label.
ORIGIN = datetime.fromisoformat(
    json.loads(SETTINGS_PAYLOAD)["fixtures"]["session"]["origin"]
)
GATES = (
    "previous_water_fall",
    "water_fall_braking",
    "current_water_flat",
    "temperature_still_falling",
)


def encoded(value):
    """One deterministic, finite JSON representation for files and hashes."""
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def core_owners():
    """Load each core module once, without integration/controller imports."""
    name = "_ventilation_forms_core"
    path = ROOT / "custom_components" / "ha_sauna" / "core"
    package = ModuleType(name)
    package.__path__ = [str(path)]
    package.__package__ = name
    package.__spec__ = importlib.util.spec_from_loader(
        name,
        loader=None,
        is_package=True,
    )
    sys.modules[name] = package
    curves = importlib.import_module(f"{name}.curve_change")
    parameters = importlib.import_module(f"{name}.parameters")
    files = [
        Path(module.__file__)
        for key, module in sys.modules.items()
        if key.startswith(f"{name}.") and getattr(module, "__file__", None)
    ]
    files.extend(
        (
            path.parent / "defaults.json",
            SETTINGS_PATH,
            Path(__file__),
            Path(__file__).with_name("proxy_forms.py"),
        )
    )
    provenance = [
        {"file": str(file.relative_to(ROOT)), "sha256": sha(file.read_bytes())}
        for file in sorted(set(files))
    ]
    return curves, parameters.Parameters, provenance


def case_parameters(entry, parameter_type):
    """Resolve the bound configuration exactly as sessions/runner.py does."""
    payload, binding = bound_file(TESTS / entry["file"], entry["sha256"])
    fixture = json.loads(payload)
    spec = fixture.get("configuration", {})
    program = spec.get("program", {})
    supplied = dict(spec.get("parameters", {}))
    for key, parameter in (
        ("start_c", "target_temperature_c"),
        ("end_c", "final_temperature_c"),
        ("gangs", "temperature_gangs"),
    ):
        if key in program:
            supplied[parameter] = program[key]
    if program.get("targets"):
        program = {**program, "steps": program["targets"]}
    if program.get("steps"):
        supplied.update(
            target_temperature_c=program["steps"][0],
            final_temperature_c=program["steps"][-1],
            temperature_gangs=len(program["steps"]),
        )
    values = parameter_type(supplied).as_dict()
    provenance = {
        "fixture": binding,
        "fixture_read_count": 2,
        "sample_read_count": 1,
        "additional_fixture_read_reason": (
            "bound configuration/parameters; read_session owns samples"
        ),
        "stored_parameter_overrides": spec.get("parameters", {}),
        "program_overrides": spec.get("program", {}),
        "supplied_parameters_after_program": supplied,
        "supplied_parameters_sha256": sha(encoded(supplied).encode("utf-8")),
        "effective_parameters": values,
        "effective_parameters_sha256": sha(encoded(values).encode("utf-8")),
        "basis": (
            "bound fixture configuration/program plus unchanged Parameters catalogue"
        ),
    }
    return values, provenance


def channel_arguments(values, position, quantity):
    """Same catalogue arguments as test_original_curve_evidence; no tuning."""
    return {
        "count": values["curve_original_samples"],
        "scale": values[f"curve_original_noise_{quantity}_{position}"],
        "max_gap_seconds": values[f"curve_original_gap_{quantity}_{position}_seconds"],
    }


def seconds(stamp):
    return (stamp - ORIGIN).total_seconds() if stamp is not None else None


def sample(record):
    return record["at"], record["value"]


def identity(record):
    """Reference the unchanged bound original without repeating its value."""
    return {
        "role": record["role"],
        "channel_index": record["channel_index"],
        "source_index": record["original"]["source_index"],
        "t": record["original"]["t"],
        "source_reason": record["source_reason"],
    }


def source_span(records):
    """Consecutive channel indices identify every original in this interval."""
    return {
        "count": len(records),
        "first": identity(records[0]) if records else None,
        "last": identity(records[-1]) if records else None,
    }


def gate(value, operator, threshold, unit, reason=None):
    result = None
    if value is not None and threshold is not None:
        result = value > threshold if operator == ">" else value <= threshold
    return {
        "value": value,
        "operator": operator,
        "threshold": threshold,
        "unit": unit,
        "result": result,
        "reason": reason
        if result is None
        else "comparison_passed"
        if result
        else "comparison_failed",
    }


class PositionFeatures:
    """Source bookkeeping only; all numerical windows belong to curve_change."""

    def __init__(self, position, values, curves):
        self.position, self.values, self.curves = position, values, curves
        self.count = values["curve_original_samples"]
        self.raw = {
            quantity: deque(maxlen=2 * self.count)
            for quantity in ("temperature", "humidity")
        }
        self.latest = {}
        self.channel_counts = Counter()
        # Every RH original is represented exactly once in settled or pending.
        # Invalid settled entries remain placeholders; they are never filtered.
        self.settled = deque(maxlen=2 * self.count)
        self.pending = deque()
        self.pending_support = None
        self.previous_prefix_newest = None

    @property
    def humidity_gap(self):
        return self.values[f"curve_original_gap_humidity_{self.position}_seconds"]

    def ingest(self, role, original, at):
        quantity = role.removeprefix(f"{self.position}_")
        previous = self.latest.get(quantity)
        source_reason = original.get("invalid_reason")
        if (
            previous is not None
            and at is not None
            and previous["at"] is not None
            and at <= previous["at"]
        ):
            source_reason = "nonincreasing_original_channel_time"
        record = {
            "role": role,
            "original": original,
            "at": at,
            "channel_index": self.channel_counts[quantity],
            "value": original["value"] if not source_reason else None,
            "source_reason": source_reason,
        }
        self.channel_counts[quantity] += 1
        self.raw[quantity].append(record)
        self.latest[quantity] = record
        if quantity == "humidity":
            self.pending.append(
                {"humidity": record, "left": self.latest.get("temperature")}
            )
        return record

    def resolve_pairs(self, at):
        """Yield every completed intermediate window, including counterforms.

        A new T original can close several waiting RH brackets. Each RH is
        visited in source order and each resulting window is emitted; only a
        genuine unresolved suffix is left queued. At most two T originals are
        passed to the existing pairing owner, avoiding repeated prefix scans.
        """
        while self.pending:
            entry = self.pending[0]
            humidity, left = entry["humidity"], entry["left"]
            right = self.latest.get("temperature")
            bracket = [point for point in (left, right) if point is not None]
            if left is right and bracket:
                bracket = bracket[:1]
            arguments = {
                "temperature_scale": self.values[
                    f"curve_original_noise_temperature_{self.position}"
                ],
                "humidity_scale": self.values[
                    f"curve_original_noise_humidity_{self.position}"
                ],
                "max_gap_seconds": self.values[
                    f"curve_original_gap_temperature_{self.position}_seconds"
                ],
            }
            support = self.curves.original_water_pair_support(
                sample(humidity),
                tuple(map(sample, bracket)),
                at=at,
                **arguments,
            )
            self.pending_support = {"status": support.status, "reason": support.reason}
            # Before the first T arrives, an equal-time T may still become
            # available in a later source row. Retain that RH identity as well.
            first_temperature_pending = (
                support.reason == "left_temperature_missing" and right is None
            )
            if support.status == "pending" or first_temperature_pending:
                if first_temperature_pending:
                    self.pending_support["waiting_reason"] = (
                        "first_temperature_original_not_yet_received"
                    )
                break
            self.pending.popleft()
            paired = {
                "humidity": humidity,
                "pair": support.pair,
                "status": support.status,
                "reason": support.reason,
                "bracket": bracket,
            }
            self.settled.append(paired)
            self.pending_support = None
            yield paired
        if not self.pending:
            self.pending_support = None

    def raw_window(self, quantity, at):
        records = tuple(self.raw[quantity])
        arguments = channel_arguments(self.values, self.position, quantity)
        window = self.curves.original_window(
            tuple(map(sample, records)),
            at=at,
            **arguments,
        )
        reason = None
        if window is None:
            if len(records) < 2 * self.count:
                reason = "insufficient_originals"
            elif any(record["source_reason"] for record in records):
                reason = "invalid_original_in_exact_2k_support"
            elif at is None:
                reason = "invalid_prefix_time"
            elif any(record["at"] is None for record in records):
                reason = "invalid_original_time"
            elif any(
                (later["at"] - earlier["at"]).total_seconds()
                > arguments["max_gap_seconds"]
                for earlier, later in zip(records, records[1:])
            ):
                reason = "original_gap_exceeds_catalogue_limit"
            elif (
                not 0
                <= (at - records[-1]["at"]).total_seconds()
                <= (arguments["max_gap_seconds"])
            ):
                reason = "newest_original_not_fresh"
            else:
                reason = "owner_rejected_original_window"
        output = {
            "status": "ready" if window is not None else "unknown",
            "reason": reason,
            "arguments": arguments,
            "previous_sources": source_span(records[: -self.count]),
            "current_sources": source_span(records[-self.count :]),
            "invalid_sources": [
                identity(record) for record in records if record["source_reason"]
            ],
        }
        if window is not None:
            output["features"] = {
                name: getattr(window, name)
                for name in (
                    "b",
                    "v",
                    "e_minus",
                    "e",
                    "mean_minus",
                    "mean",
                    "r_minus",
                    "r",
                )
            }
            output.update(
                start_t=seconds(window.start_at), newest_t=seconds(window.newest_at)
            )
        return window, output

    def paired_identity(self, entry):
        output = {
            "humidity": identity(entry["humidity"]),
            "status": entry["status"],
            "reason": entry["reason"],
        }
        pair = entry["pair"]
        if pair is None:
            output["temperature_support"] = [
                identity(record) for record in entry["bracket"]
            ]
            return output
        for key, point in (
            ("temperature_before", pair.temperature_before),
            ("temperature_after", pair.temperature_after),
        ):
            output[key] = identity(
                next(record for record in entry["bracket"] if sample(record) == point)
            )
        output.update(
            temperature=pair.temperature,
            lower_density=pair.lower_density,
            upper_density=pair.upper_density,
            available_t=seconds(pair.available_at),
        )
        return output

    def snapshot(self, at, *, prefix=False):
        temperature, thermal = self.raw_window("temperature", at)
        humidity, rh = self.raw_window("humidity", at)
        # Revalidate against the current causal prefix, including prefixes
        # without a newly resolved RH pair. No old ready result is cached.
        support = self.curves.original_water_window(
            tuple(item["pair"] for item in self.settled),
            count=self.count,
            max_gap_seconds=self.humidity_gap,
            at=at,
        )
        water = support.features
        significance = self.values["curve_original_significance"]
        water_reason = support.reason or "water_window_unavailable"
        water_gates = {
            "previous_water_fall": gate(
                -water.b if water else None,
                ">",
                significance * water.e_minus if water else None,
                "g/m³/s",
                water_reason,
            ),
            "water_fall_braking": gate(
                water.v - water.b if water else None,
                ">",
                significance * (water.e + water.e_minus) if water else None,
                "g/m³/s",
                water_reason,
            ),
            "current_water_flat": gate(
                abs(water.v) if water else None,
                "<=",
                significance * water.e if water else None,
                "g/m³/s",
                water_reason,
            ),
        }
        deviation = water.normalized_level_deviation() if water else None
        water_gates["temperature_still_falling"] = gate(
            -temperature.v if temperature else None,
            ">",
            significance * temperature.e if temperature else None,
            "°C/s",
            thermal["reason"],
        )
        settled = tuple(self.settled)
        water_output = {
            "status": support.status,
            "reason": support.reason,
            "level_deviation_comparison_diagnostic": {
                **gate(
                    deviation,
                    "<=",
                    significance,
                    "normalized",
                    "owner_level_deviation_unknown" if water else water_reason,
                ),
                "role": "diagnostic_only; excluded from form qualification",
            },
            "previous_rh_sources": source_span(
                tuple(entry["humidity"] for entry in settled[: -self.count])
            ),
            "current_rh_sources": source_span(
                tuple(entry["humidity"] for entry in settled[-self.count :])
            ),
            "last_pair": self.paired_identity(settled[-1]) if settled else None,
            "invalid_pairs": [
                {"humidity": identity(entry["humidity"]), "reason": entry["reason"]}
                for entry in settled
                if entry["pair"] is None
            ],
        }
        if water is not None:
            lower, upper = water.constant_bounds(significance)
            water_output.update(
                features={
                    "b": water.b,
                    "v": water.v,
                    "e_minus": water.e_minus,
                    "e": water.e,
                    "D": deviation,
                },
                start_t=seconds(water.start_at),
                newest_t=seconds(water.newest_at),
                available_t=seconds(water.available_at),
                newest_paired_rh_age_s=(at - water.newest_at).total_seconds(),
                old_envelope_intersection_diagnostic={
                    "lower": lower,
                    "upper": upper,
                    "compatible": water.constant_compatible(significance),
                    "unscaled_lower": water.constant_lower,
                    "unscaled_upper": water.constant_upper,
                    "role": "diagnostic_only; excluded from form qualification",
                },
            )
        newest = {
            "temperature": seconds(temperature.newest_at) if temperature else None,
            "humidity": seconds(humidity.newest_at) if humidity else None,
            "paired_rh": seconds(water.newest_at) if water else None,
            "right_temperature_partner": seconds(water.available_at) if water else None,
        }
        fresh = {
            key: (
                value > self.previous_prefix_newest[key]
                if value is not None
                and self.previous_prefix_newest
                and self.previous_prefix_newest[key] is not None
                else None
            )
            for key, value in newest.items()
        }
        pending = {
            "count": len(self.pending),
            "first": identity(self.pending[0]["humidity"]) if self.pending else None,
            "last": identity(self.pending[-1]["humidity"]) if self.pending else None,
            "first_support": self.pending_support,
        }
        complete = all(window is not None for window in (temperature, humidity, water))
        results = [item["result"] for item in water_gates.values()]
        all_gates = None if None in results else all(results)
        output = {
            "position": self.position,
            "complete_support": complete,
            "raw_temperature": thermal,
            "raw_humidity": rh,
            "water": water_output,
            "pending_suffix": pending,
            "latest_received_originals": {
                quantity: {
                    "source": identity(record),
                    "age_s": (at - record["at"]).total_seconds()
                    if at is not None and record["at"] is not None
                    else None,
                }
                for quantity, record in self.latest.items()
            },
            "newest_support_times_s": newest,
            "advanced_since_previous_source_prefix": fresh,
            "gates": water_gates,
            "all_four_form_gates": all_gates,
            "qualified_form_only": all_gates if complete else None,
            "blocking_gates": [
                key for key, item in water_gates.items() if item["result"] is False
            ],
            "unknown_gates": [
                key for key, item in water_gates.items() if item["result"] is None
            ],
            "support_reason": None if complete else "incomplete_original_support",
        }
        if prefix:
            self.previous_prefix_newest = newest
        return output


def add_summary(counts, snapshot):
    counts["rows"] += 1
    counts[f"support:{'complete' if snapshot['complete_support'] else 'unknown'}"] += 1
    counts[f"qualified_form_only:{snapshot['qualified_form_only']}"] += 1
    for key, item in snapshot["gates"].items():
        counts[f"gate:{key}:{item['result']}"] += 1
    counts["prefixes_with_pending_suffix"] += bool(snapshot["pending_suffix"]["count"])
    if snapshot["qualified_form_only"] is None:
        counts[f"unknown_support:{snapshot['water']['status']}"] += 1
    water = snapshot["water"]
    old = water.get("old_envelope_intersection_diagnostic")
    deviation = water["level_deviation_comparison_diagnostic"]["result"]
    counts[f"diagnostic:D_le_s:{deviation}"] += 1
    if old is not None:
        counts[f"diagnostic:old_intersection:{old['compatible']}"] += 1
    if old is not None and deviation is True:
        if not old["compatible"]:
            counts["diagnostic:D_le_s_and_old_intersection_empty"] += 1
            for key in snapshot["blocking_gates"]:
                counts[f"diagnostic:form_blocker_with_D_le_s_empty_old:{key}"] += 1
    if deviation is True:
        for key in snapshot["blocking_gates"]:
            counts[f"diagnostic:form_blocker_with_D_le_s:{key}"] += 1
    if snapshot["qualified_form_only"] is True:
        counts[f"diagnostic:qualified_form_D_le_s:{deviation}"] += 1


def analyse_session(session, values, curves, emit, parameter_hash):
    states = {
        position: PositionFeatures(position, values, curves) for position in POSITIONS
    }
    # Initial snapshots never enter the original-prefix windows, matching
    # original_prefix in test_original_curve_evidence. Source rows remain exact.
    events = merge(
        *(
            [
                (point["source_index"], role, point)
                for point in session["original_series"][role]
                if point["origin"] == "samples"
            ]
            for role in ROLES
        ),
        key=lambda item: item[0],
    )
    counts = {
        kind: {position: Counter() for position in POSITIONS}
        for kind in ("prefixes", "pair_resolutions")
    }
    input_hash = session["provenance"]["samples"]["sha256"]
    source_row = 0
    for source_row, (source_index, role, point) in enumerate(events, 1):
        at = ORIGIN + timedelta(seconds=point["t"]) if number(point["t"]) else None
        position = role.split("_", 1)[0]
        state = states[position]
        record = state.ingest(role, point, at)
        common = {
            "case_id": session["id"],
            "climate_source_row": source_row,
            "source_index": source_index,
            "at_s": point["t"],
            "input_sha256": input_hash,
            "parameters_sha256": parameter_hash,
        }
        for entry in state.resolve_pairs(at):
            snapshot = state.snapshot(at)
            emit(
                {
                    "kind": "pair_resolution",
                    **common,
                    "resolved_rh_channel_index": entry["humidity"]["channel_index"],
                    "position": snapshot,
                }
            )
            add_summary(counts["pair_resolutions"][position], snapshot)
        positions = {
            key: value.snapshot(at, prefix=True) for key, value in states.items()
        }
        emit(
            {
                "kind": "source_prefix",
                **common,
                "input": identity(record),
                "positions": positions,
                "both_positions_qualified_form_only": (
                    all(value["qualified_form_only"] for value in positions.values())
                    if all(value["complete_support"] for value in positions.values())
                    else None
                ),
            }
        )
        for key, snapshot in positions.items():
            add_summary(counts["prefixes"][key], snapshot)
    return {
        "id": session["id"],
        "climate_source_prefixes": source_row,
        "input_counts": session["counts"],
        "provenance": session["provenance"],
        "counts": counts,
        "final_pending_suffix": {
            key: {
                "count": len(state.pending),
                "first": identity(state.pending[0]["humidity"])
                if state.pending
                else None,
                "last": identity(state.pending[-1]["humidity"])
                if state.pending
                else None,
            }
            for key, state in states.items()
        },
    }


def main():
    started = perf_counter()
    # Same manifest loader as proxy_forms; the manifest owns the case IDs.
    manifest_path = TESTS / "replay_manifest.json"
    manifest_payload = manifest_path.read_bytes()
    manifest = json.loads(manifest_payload)
    entries = manifest["sessions"]
    manifest_ids = [entry["id"] for entry in entries]
    if not manifest_ids or len(manifest_ids) != len(set(manifest_ids)):
        raise ValueError("Replay manifest requires distinct, nonempty session IDs")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--cases",
        nargs="+",
        choices=manifest_ids,
        metavar="MANIFEST_ID",
        help="Manifest IDs; default: all in manifest order",
    )
    args = parser.parse_args()
    requested = args.cases if args.cases is not None else manifest_ids
    if len(requested) != len(set(requested)):
        parser.error("--cases requires distinct manifest IDs")
    entries = [entry for entry in entries if entry["id"] in requested]
    curves, parameter_type, owner_files = core_owners()
    metadata = {
        "schema_version": 1,
        "kind": "metadata",
        "scope": (
            "all unchanged original climate prefixes; descriptive form features only"
        ),
        "case_ids": [entry["id"] for entry in entries],
        "parameter_basis": (
            "per-case bound configuration/program resolved by Parameters; "
            "unchanged catalogue, no search"
        ),
        "manifest": {
            "file": str(manifest_path.relative_to(ROOT)),
            "sha256": sha(manifest_payload),
        },
        "time_origin": {
            "file": str(SETTINGS_PATH.relative_to(ROOT)),
            "sha256": sha(SETTINGS_PAYLOAD),
            "setting": "fixtures.session.origin",
            "value": ORIGIN.isoformat(),
        },
        "owners_and_loader": owner_files,
        "assumptions": {
            "source": (
                "fixed fixture role bindings; no inferred runtime source epochs "
                "or device identities"
            ),
            "time": (
                "original received t in seconds; one prefix per climate source "
                "row in original order"
            ),
            "initial_state": (
                "excluded, as in original_prefix; no fabricated zero-time originals"
            ),
            "references": (
                "zero-based sample source_index and climate-only channel_index; "
                "compact consecutive ranges reference unchanged input files"
            ),
            "pairing": (
                "only already received immediate T neighbors; existing owner "
                "supplies interpolation and envelopes"
            ),
            "windows": (
                "exactly K previous/K current consecutive identities; "
                "invalid pair slots retained"
            ),
            "pending": (
                "complete settled window retained while a consecutive RH suffix "
                "awaits T; no unresolved internal RH skipped"
            ),
            "pending_serialization": (
                "first/last channel indices and count describe the entire "
                "consecutive queued suffix; every RH input and resolution emitted"
            ),
            "support": (
                "existing owners validate T/RH gaps, raw freshness and water "
                "support on every current prefix; unknown/negative rows retained"
            ),
            "water_age_contract": (
                "the water owner deliberately retains complete historical windows "
                "during ordinary trailing pending; paired RH age is reported "
                "without adding a separate age gate"
            ),
            "continuity_limit": (
                "owner-supported trailing blocks plus current raw freshness; "
                "no detector opening floors, candidate resets or source epoch "
                "state, and no continuity claim across older excluded interruptions"
            ),
            "gates": (
                "four existing arithmetic comparisons; each known comparison "
                "remains visible even when other support is unknown"
            ),
            "qualification": (
                "qualified_form_only additionally requires both raw windows "
                "and water support; no product admission/hold/confirmation semantics"
            ),
            "freshness": (
                "newest original times and advancement from the preceding source "
                "prefix, without candidate memory"
            ),
            "rms": (
                "existing normalized_level_deviation; empirical point widths, "
                "not an estimated noise distribution; D<=s is diagnostic only "
                "and never a form gate or qualification condition"
            ),
            "old_intersection": (
                "existing constant_bounds/constant_compatible diagnostics only"
            ),
            "evaluation": (
                "R04 is unchanged evaluation data; no threshold search or calibration"
            ),
            "complexity": (
                "O(N*K) owner windows; monotone pending queue; no BIC/controller replay"
            ),
            "labels": (
                "no actions, presence, observed outputs or expectations used "
                "as features or event truth"
            ),
        },
        "gate_order": GATES,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    destination = args.output / "ventilation_forms.jsonl.gz"
    sessions = []
    with destination.open("wb") as raw:
        with gzip.GzipFile(
            filename="", mode="wb", fileobj=raw, compresslevel=1, mtime=0
        ) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline="\n") as stream:

                def emit(row):
                    stream.write(encoded(row) + "\n")

                emit(metadata)
                for entry in entries:
                    case_started = perf_counter()
                    print(f"Reading original prefixes: {entry['id']}", flush=True)
                    session = read_session(entry)
                    values, parameter_provenance = case_parameters(
                        entry,
                        parameter_type,
                    )
                    parameter_hash = parameter_provenance["effective_parameters_sha256"]
                    session["provenance"]["read_count_scope"] = "read_session only"
                    session["provenance"]["total_read_counts"] = {
                        "fixture": 2,
                        "samples": 1,
                    }
                    emit(
                        {
                            "kind": "session_input",
                            "case_id": session["id"],
                            "provenance": session["provenance"],
                            "parameters": parameter_provenance,
                            "counts": session["counts"],
                        }
                    )
                    result = analyse_session(
                        session,
                        values,
                        curves,
                        emit,
                        parameter_hash,
                    )
                    result["parameters"] = parameter_provenance
                    result["elapsed_seconds"] = perf_counter() - case_started
                    sessions.append(result)
                    print(
                        f"{entry['id']}: "
                        f"{result['climate_source_prefixes']} prefixes, "
                        f"{result['elapsed_seconds']:.3f}s",
                        flush=True,
                    )
    summary = {
        "schema_version": 1,
        "scope": metadata["scope"],
        "manifest": metadata["manifest"],
        "owners_and_loader": owner_files,
        "case_ids": metadata["case_ids"],
        "features_file": destination.name,
        "features_bytes": destination.stat().st_size,
        "sessions": sessions,
        "elapsed_seconds": perf_counter() - started,
    }
    write_json(args.output / "summary.json", summary)
    print(
        encoded(
            {
                "features_file": str(destination),
                "summary_file": str(args.output / "summary.json"),
                "case_ids": metadata["case_ids"],
                "climate_source_prefixes": sum(
                    session["climate_source_prefixes"] for session in sessions
                ),
                "elapsed_seconds": summary["elapsed_seconds"],
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
