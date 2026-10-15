"""Stdout evidence for synthetic commands through real runtime consumers."""

import json
from dataclasses import asdict
from datetime import datetime


def emit_model_input_evidence(probe, runtime, values, *, previous_session_id):
    """Read observed state after the caller's real runtime invocation completes."""
    session = runtime.session

    def decision_value(decision):
        if decision is None:
            return None
        return {
            "at": decision.at,
            "heat": decision.heat,
            "reason": decision.reason,
            "created_at": decision.created_at,
            "has_session_id": decision.session_id is not None,
            "same_previous_session": (
                decision.session_id == previous_session_id
                if previous_session_id is not None else None
            ),
        }

    def iso_datetime(value):
        if isinstance(value, datetime):
            return value.isoformat()
        raise TypeError(f"Unsupported evidence value: {type(value).__name__}")

    evidence = {
        "probe": probe,
        "scope": {
            "input": "explicit synthetic input",
            "execution": "real Runtime and Controller",
            "transport": "replaced by test harness",
        },
        "consumer_inputs": [
            {"type": type(value).__name__, "value": asdict(value)} for value in values
        ],
        "observed_after_call": {
            "control_mode": runtime.controller.control_mode,
            "configuration_control_mode": runtime.configuration.control_mode,
            "heater_override": runtime.controller.heater_override,
            "heater_override_ends_at": runtime.controller.heater_override_ends_at,
            "target_temperature": runtime.controller.target_temperature,
            "program": {
                "selected_program_id": runtime.configuration.selected_program_id,
                "parameters": runtime.configuration.parameters.as_dict(),
            },
            "session": {
                "exists": session is not None,
                "had_previous_session": previous_session_id is not None,
                "same_previous_session": (
                    session.session_id == previous_session_id
                    if session is not None and previous_session_id is not None else None
                ),
                "started_at": session.timeline.session_started_at if session else None,
                "operation_enabled": session.operation_enabled if session else None,
                "operation_off_at": session.operation_off_at if session else None,
                "base_phases": (
                    [asdict(mark) for mark in session.base_phases] if session else []
                ),
                "deadlines": (
                    [{"purpose": item.purpose, "due_at": item.due_at}
                     for item in session.deadlines] if session else []
                ),
            },
            "completed_session_count": len(runtime.controller.completed_sessions),
            "last_decision": decision_value(runtime.controller.last_decision),
            "automatic_decision": decision_value(runtime.controller.automatic_decision),
        },
    }
    print("MODEL_INPUT_EVIDENCE " + json.dumps(
        evidence, sort_keys=True, allow_nan=False, default=iso_datetime,
    ))
