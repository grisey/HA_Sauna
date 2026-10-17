"""Read failed infusion-update ownership before replay teardown.

This helper does not evaluate forms, alter sources or consume the queued update.
Its caller serializes the returned data with the existing archive serializer.
"""


def capture(runtime):
    detector = runtime.detector
    if detector is None or not detector._infusion_updates:
        return None
    update = detector._infusion_updates[0]
    detection = update.confirmed_detection
    source = runtime._detector_infusion_events.get(id(detection))
    controller = runtime.controller

    def window(item):
        return {
            name: getattr(item, name)
            for name in ("start_at", "newest_at", "b", "v", "e_minus", "e",
                         "scale", "r_minus", "r")
        }

    def component(item):
        if item is None:
            return None
        result = {name: item.get(name) for name in (
            "received_at", "newest", "context", "continuity", "onsets",
            "assignment", "continuing", "strong", "qualified", "fall_confirmed",
            "rise_ended", "fall_ready", "branch_floors", "connection_unknown",
            "rise_score", "threshold", "humidity_rise_onset", "support_end", "water_end",
        )}
        result["binding_windows"] = {
            name: {"temperature": window(pair.temperature), "humidity": window(pair.humidity)}
            for name, pair in item.get("binding_windows", ())
        }
        result["humidity_kinks"] = {
            name: {key: getattr(kink, key) for key in (
                "status", "factor", "history_count", "history_start", "history_end",
                "history_scale", "catalog_scale", "used_scale", "tolerance", "delta",
                "effective_change",
            )} if kink is not None else None
            for name, kink in item.get("humidity_kinks", ())
        }
        result["decision_windows"] = {
            key: {name: {"temperature": window(pair.temperature),
                         "humidity": window(pair.humidity)} for name, pair in item.get(key, ())}
            for key in ("rise_windows", "fall_windows", "early_confirmation_windows")
        }
        result["water"] = {
            str(position): {key: value.get(key) for key in ("change", "complete", "support_overflow")}
            for position, value in item.get("water", {}).items()
        }
        result["peak_times"] = {
            str(position): point[0] for position, point in item.get("peaks", {}).items()
        }
        result["onset_witnesses"] = {
            str(position): {
                "candidate_id": witness.candidate_id,
                "onset_at": witness.onset_at,
                "context": witness.context,
                "first_observed_at": witness.first_observed_at,
            }
            for position, witness in item.get("onset_witnesses", {}).items()
        }
        result["onset_intervals"] = {
            str(position): {"previous_at": edge.previous[0], "current_at": edge.current[0]}
            for position, edge in item.get("onset_intervals", {}).items()
        }
        return result

    def episode(item):
        if item is None:
            return None
        result = {name: item.get(name) for name in (
            "phase", "positions", "onset_at", "observed_at", "context",
            "confirmed_at", "confirmed_detection", "reported_end", "evidence_end",
            "level_end_at", "booked_end_at", "end_basis", "interrupted",
            "confirmation_ready", "confirmation_basis", "event_identity",
            "followup_received_at", "original_background",
        )}
        result["is_global_owner"] = item is detector._infusion_episode
        result["same_detection_as_failed_update"] = item.get("confirmed_detection") is detection
        result["original_component"] = component(item.get("original_component"))
        result["original_start_component"] = component(item.get("original_start_component"))
        result["original_end_windows"] = {
            name: window(value) for name, value in item.get("original_end_windows", ())
        }
        return result

    sessions = (*controller.completed_sessions,
                *((runtime.session,) if runtime.session is not None else ()))
    source_events = [
        event for session in sessions for event in session.timeline.processed
        if source is not None and event.event_id == source[1]
    ]
    return {
        "schema": 1,
        "queue_length": len(detector._infusion_updates),
        "failed_update": update,
        "runtime_source": {
            "event_id": source[1] if source is not None else None,
            "exact_detection_identity": source is not None and source[0] is detection,
            "source_events": source_events,
        },
        "global_owner": episode(detector._infusion_episode),
        "selected_position": detector._infusion_owner,
        "local_owners": {
            str(position): {
                "episode": episode(state["infusion"]),
                "candidate": component(state["infusion_candidate"]),
                "queued_updates": [
                    {"episode_is_local": item is state["infusion"],
                     "same_detection": item.get("confirmed_detection") is detection,
                     "end": end}
                    for item, end in state["infusion_updates"]
                ],
            }
            for position, state in detector._position_evidence.items()
        },
        "admission": {
            "infusion_since": detector._curve_infusion_since,
            "closed_since": detector._curve_closed_since,
            "close_effective_at": detector._curve_close_effective_at,
            "recognition_gates": controller._recognition_gates,
            "gang_temperature_gates": controller._gang_temperature_gates,
        },
        "proposal_status": [
            {"key": key, "status": value}
            for key, value in detector._curve_proposal_status.items() if key[0] == "infusion"
        ],
        "trace": {
            key: detector.diagnostic.get(key)
            for key in ("at", "original_infusion", "position_selection", "position_evidence",
                        "infusion_episode", "infusion_end_conflict")
        },
    }
