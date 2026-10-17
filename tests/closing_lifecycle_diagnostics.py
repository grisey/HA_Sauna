"""Record executed position decisions without evaluating another product path."""

import gzip
import json
import os
from pathlib import Path


class ClosingLifecycleDiagnostics:
    """Observe native calls, route selection and owner changes in one input run."""

    ROUTES = frozenset((
        "door_open", "door_open_heating", "door_close", "ventilation",
        "ventilation_complete",
    ))
    FORM_KEYS = (
        "received_at", "positions", "evaluable", "new_original",
        "candidate_received_at", "confirmation_received_at",
        "candidate_originals", "confirmation_originals",
        "opening_candidate_reset_reason", "closing_candidate_reset_reason",
        "opening_confirmation", "resolved_opening_counterform",
        "local_temperature_braking", "bound_temperature_braking",
        "complete_fresh_original_block", "fresh_temperature_support_after_correction",
        "new_temperature_level", "sustained_temperature_braking",
        "discrete_temperature_braking", "temperature_counterform",
        "later_original_confirmation", "temperature_close_terms",
        "temperature_braking_witnesses", "followup", "temperature_level_followup",
    )

    @staticmethod
    def _snapshot(value):
        # Only deliberately selected plain containers cross this boundary;
        # source datetimes/enums are serialized without copying product state.
        return json.loads(json.dumps(value, default=str))

    @staticmethod
    def _position(value):
        return value.value if value is not None else None

    @staticmethod
    def _window(value):
        if value is None:
            return None
        return {key: getattr(value, key) for key in (
            "start_at", "newest_at", "b", "v", "e_minus", "e",
        )}

    def __init__(self, testcase, detector, case):
        self.detector = detector
        self.case = case
        self.records = []
        self.local = {}
        self.selection = None
        self.selection_index = None
        self.saved = {}
        for name, wrapper in (
            ("_original_door_position_evidence", self._local),
            ("_select_position", self._select),
            ("_adopt_door_position", self._adopt),
            ("_reset_position_doors", self._reset),
        ):
            self.saved[name] = getattr(detector, name)
            setattr(detector, name, wrapper)
        testcase.addCleanup(self.finish)

    def _local(self, prefixes, channels, now, decision_at, trace, state, **kwargs):
        result = self.saved["_original_door_position_evidence"](
            prefixes, channels, now, decision_at, trace, state, **kwargs,
        )
        prefix = prefixes[-1]
        episode = state["door"] or {}
        form = trace.get("original_forms", {})
        self.local[channels[0].value] = self._snapshot({
            "at": now, "prefix_at": prefix.received_at,
            "changed_position": self._position(prefix.changed_position),
            "local_result": result,
            "forms": {key: form[key] for key in self.FORM_KEYS if key in form},
            "fall_support": trace.get("temperature_fall_support"),
            "native_falls": {position.value: {
                "received_at": fall.get("received_at"),
                "recovery_received_at": fall.get("recovery_received_at"),
                "connection_unknown": fall.get("connection_unknown", False),
                "fall_window": self._window(fall.get("window")),
                "braking_window": self._window(fall.get("braking_window")),
                "recovery_window": self._window(fall.get("recovery_window")),
            } for position, fall in episode.get("original_temperature_falls", {}).items()},
            "event_identity": episode.get("event_identity"),
            "episode_started_at": episode.get("started_at"),
        })
        return result

    def _select(self, route, results, owner, trace):
        selected = self.saved["_select_position"](route, results, owner, trace)
        if route in self.ROUTES:
            self.selection = self._snapshot({
                "type": "selection", "route": route, "at": trace["at"],
                "open_before": self.detector.open,
                "context": self.detector._curve_door_context,
                "owner_argument": self._position(owner),
                "decision": trace.get("position_selection", {}).get(route),
                "local": self.local,
            })
            self.selection_index = len(self.records)
            self.records.append(self.selection)
        return selected

    def _adopt(self, position, now):
        before = self.detector._door_owner
        result = self.saved["_adopt_door_position"](position, now)
        after = self.detector._door_owner
        if before != after:
            self.records.append(self._snapshot({
                "type": "owner_change", "at": now,
                "before": self._position(before), "after": self._position(after),
                "open": self.detector.open,
                "context": self.detector._curve_door_context,
                "preceding_selection_index": self.selection_index,
                "preceding_route": self.selection.get("route") if self.selection else None,
            }))
        return result

    def _reset(self):
        before = self.detector._door_owner
        result = self.saved["_reset_position_doors"]()
        self.records.append(self._snapshot({
            "type": "owner_reset", "before": self._position(before),
            "after": self._position(self.detector._door_owner),
            "context": self.detector._curve_door_context,
            "closed_at": self.detector.closed_at,
        }))
        return result

    def finish(self):
        for name, original in self.saved.items():
            setattr(self.detector, name, original)
        path = None
        directory = os.environ.get("SAUNA_CORE_EVIDENCE")
        if directory:
            target = Path(directory) / "closing-lifecycle-diagnostics" / f"{self.case}.jsonl.gz"
            target.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(target, "wt", encoding="utf-8") as stream:
                for record in self.records:
                    stream.write(json.dumps(record, sort_keys=True) + "\n")
            path = str(target)
        print("Closing lifecycle executed diagnosis: " + json.dumps({
            "case": self.case, "record_count": len(self.records), "artifact": path,
            "owner_changes": [r for r in self.records if r["type"] != "selection"],
            "recomputed": False,
        }, sort_keys=True))
