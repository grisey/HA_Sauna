"""Read-only diagnostics on executed native witnesses and existing controls.

No detector result is replaced. Models run only when the test prints diagnostics,
after the normal input loop. Product form selection, gates and timing are intact.
"""

from dataclasses import asdict
from datetime import timedelta
import hashlib
import json
from unittest.mock import patch

from custom_components.ha_sauna.core.curve_change import curve_slope_transition_diagnostic
from custom_components.ha_sauna.core.models import Quantity
from custom_components.ha_sauna.core.timeline import Kind


def _support_digest(points):
    payload = json.dumps(points, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SlopeTransitionDiagnostics:
    """Capture immutable native support before event publication clears owners."""

    def __init__(self, test, detector):
        self.detector = detector
        self.latest = {}
        self.closures = []
        self.control = None
        original = detector._original_door_position_evidence

        def observe_position(prefixes, channels, now, decision_at, trace, state, **kwargs):
            result = original(prefixes, channels, now, decision_at, trace, state, **kwargs)
            episode = state["door"]
            if episode is not None:
                for fall in episode.get("original_temperature_falls", {}).values():
                    witness = fall.get("braking_form")
                    if witness is not None:
                        self.latest[witness.candidate_id] = witness
            return result

        original_observer = detector.observer

        def observe(row):
            if original_observer is not None:
                original_observer(row)
            if self.control is not None:
                self._capture_control()
                self.control["last_observed_at"] = row["at"]
            if Kind.DOOR_CLOSE not in row.get("signals", ()):
                return
            form = row.get("original_forms", {})
            witnesses = form.get("temperature_braking_witnesses", {})
            self.closures.append({
                "at": row["at"],
                "confirmation_received_at": form.get("confirmation_received_at"),
                "executed_terms": form.get("temperature_close_terms"),
                "discrete_braking": form.get("discrete_temperature_braking"),
                "level_close": form.get("sustained_temperature_braking"),
                "witnesses": {
                    position: (dict(summary), self.latest.get(summary["candidate_id"]))
                    for position, summary in witnesses.items()
                },
            })

        for name, replacement in (
            ("_original_door_position_evidence", observe_position),
            ("observer", observe),
        ):
            patched = patch.object(detector, name, replacement)
            patched.start()
            test.addCleanup(patched.stop)

    def print_closures(self, case, reference_intervals=(), *, events=()):
        closes = [event for event in events if event.kind == Kind.DOOR_CLOSE]
        for row in self.closures:
            records = {}
            for position, (summary, witness) in row["witnesses"].items():
                record = {"executed_witness": summary}
                if witness is None:
                    record["comparison"] = {
                        "result": "unresolved", "reason": "executed_support_not_captured",
                    }
                elif any(summary.get(name) != value for name, value in (
                    ("candidate_id", witness.candidate_id),
                    ("onset_at", witness.onset_at),
                    ("newest_at", witness.features.newest_at),
                    ("fresh_newest_at", witness.fresh_newest_at),
                    ("fresh_observed_at", witness.fresh_observed_at),
                )):
                    record["comparison"] = {
                        "result": "unresolved", "reason": "executed_support_mismatch",
                    }
                elif not witness.original_points or (
                    witness.features.newest_at != witness.original_points[-1][0]
                ):
                    # An unresolved refit can retain the old features while
                    # appending originals and clearing freshness. Such a
                    # historical witness cannot masquerade as a current fit.
                    record["comparison"] = {
                        "result": "unresolved", "reason": "native_refit_not_current",
                        "fit_newest_at": witness.features.newest_at,
                        "support_newest_at": (witness.original_points[-1][0]
                                              if witness.original_points else None),
                    }
                else:
                    record.update(
                        original_support_count=len(witness.original_points),
                        original_support_sha256=_support_digest(witness.original_points),
                        original_context=witness.context,
                        onset_within_reference=(any(
                            start <= witness.onset_at <= end
                            for start, end in reference_intervals
                        ) if reference_intervals else None),
                        comparison=curve_slope_transition_diagnostic(
                            witness.original_points,
                            onset_at=witness.onset_at,
                            policy=self.detector.curve_policy,
                        ),
                    )
                records[position] = record
            print("SLOPE_TRANSITION_EXECUTED_CLOSE=" + json.dumps({
                "case": case,
                "at": row["at"],
                "confirmation_received_at": row["confirmation_received_at"],
                "reference_intervals": reference_intervals,
                "events": [
                    {"effective_at": event.effective_at, "detected_at": event.detected_at}
                    for event in closes if event.trace_at == row["at"]
                ],
                "executed_terms": row["executed_terms"],
                "discrete_braking": row["discrete_braking"],
                "level_close": row["level_close"],
                "policy": asdict(self.detector.curve_policy),
                "positions": records,
                "capture_result": "captured" if records else "no_native_braking_witness",
            }, default=str, sort_keys=True))
        if not self.closures:
            print("SLOPE_TRANSITION_EXECUTED_CLOSE=" + json.dumps({
                "case": case, "capture_result": "no_executed_close",
                "reference_intervals": reference_intervals,
            }, default=str, sort_keys=True))

        for start, end in reference_intervals:
            print("SLOPE_TRANSITION_REFERENCE_COVERAGE=" + json.dumps({
                "case": case,
                "reference_interval": (start, end),
                "executed_closes_in_interval": [
                    {"effective_at": event.effective_at, "trace_at": event.trace_at}
                    for event in closes if start <= event.effective_at <= end
                ],
                "counterfactual_close_generated": False,
            }, default=str, sort_keys=True))

    def start_control(self, onset_at):
        """Retain actual fixture originals from the existing window, then append.

        This declared fixture join is not a detector event or inferred door
        timestamp. Both hypotheses receive the identical captured input.
        """
        floor = onset_at - timedelta(seconds=self.detector.p["curve_window_seconds"])
        self.control = {
            "onset_at": onset_at,
            "floor": floor,
            "last_observed_at": None,
            "points": {
                position: {
                    stamp: value for stamp, value in
                    self.detector._curve_series[position, Quantity.TEMPERATURE]["points"]
                    if stamp >= floor
                }
                for position in self.detector.positions
            },
        }
    def _capture_control(self):
        # Observer runs from _sample after advance has consumed the queued
        # originals. It includes the final batch and never peeks into pending.
        for position, originals in self.control["points"].items():
            series = self.detector._curve_series[position, Quantity.TEMPERATURE]
            originals.update(
                (stamp, value) for stamp, value in series["points"]
                if stamp >= self.control["floor"]
            )

    def print_control(self, case):
        records = {}
        for position, originals in self.control["points"].items():
            points = tuple(sorted(originals.items()))
            processed = self.detector._curve_series[position, Quantity.TEMPERATURE]["points"]
            processed_newest = processed[-1][0] if processed else None
            records[position.value] = {
                "processed_newest_at": processed_newest,
                "captured_newest_at": points[-1][0] if points else None,
                "includes_last_processed_original": bool(points and points[-1][0] == processed_newest),
                "original_support_count": len(points),
                "original_support_sha256": _support_digest(points),
                "comparison": curve_slope_transition_diagnostic(
                    points, onset_at=self.control["onset_at"],
                    policy=self.detector.curve_policy,
                ),
            }
        print("SLOPE_TRANSITION_FIXTURE_CONTROL=" + json.dumps({
            "case": case,
            "support_kind": "existing_fixture_originals_at_declared_join",
            "onset_at": self.control["onset_at"],
            "points_not_before": self.control["floor"],
            "last_observed_at": self.control["last_observed_at"],
            "policy": asdict(self.detector.curve_policy),
            "positions": records,
        }, default=str, sort_keys=True))
