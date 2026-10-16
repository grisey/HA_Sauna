"""Pure relative-time mapping owned by tests/replay_manifest.json.

Fixture times stay local to each recording. Only explicit time-group membership
establishes a shared axis; equal source hashes never imply continuity. Mapping
does not join samples, fill gaps, shift durations or load product modules.
"""

from __future__ import annotations

import math


def _seconds(value, label):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"{label} must be finite seconds")
    return value


def recording_time_axes(manifest, *, durations_s=None):
    """Return recording-id mappings with group, offset and preceding gap.

    Every manifest recording must occur exactly once. ``origin_offset_s`` maps
    local received seconds to group seconds. ``gap_from_previous_end_s`` is the
    unobserved interval from the previous fixture end to this recording origin,
    not a sample interval or an instruction to synthesize observations.
    Supplying bound fixture durations also checks each declared gap against the
    difference of recording origins at the source's microsecond precision.
    """
    recordings = [entry["id"] for entry in manifest["sessions"]]
    if len(set(recordings)) != len(recordings):
        raise ValueError("Duplicate manifest recording")
    axes, groups = {}, set()
    for group in manifest["time_groups"]:
        group_id = group["id"]
        if group_id in groups:
            raise ValueError(f"Duplicate time group: {group_id}")
        groups.add(group_id)
        members = group["recordings"]
        if not members or members[0]["id"] != group["origin_recording"]:
            raise ValueError(f"{group_id}: first recording must define the origin")
        previous, previous_offset = None, 0.0
        for member in members:
            identity = member["id"]
            if identity not in recordings or identity in axes:
                raise ValueError(
                    f"Unknown or duplicate time-axis recording: {identity}",
                )
            offset = _seconds(member["origin_offset_s"], "origin_offset_s")
            gap = member.get("gap_from_previous_end_s")
            if previous is None:
                if offset != 0 or gap is not None or "previous_recording" in member:
                    raise ValueError(f"{identity}: group origin must have zero offset")
            elif (
                member.get("previous_recording") != previous
                or offset < previous_offset
                or _seconds(gap, "gap_from_previous_end_s") < 0
            ):
                raise ValueError(f"{identity}: invalid preceding recording or gap")
            if previous is not None and durations_s is not None:
                duration = _seconds(durations_s[previous], "previous duration_s")
                if duration < 0 or not math.isclose(
                    offset - previous_offset - duration,
                    gap,
                    rel_tol=0.0,
                    abs_tol=0.000001,
                ):
                    raise ValueError(f"{identity}: gap differs from fixture duration")
            axes[identity] = {
                "group_id": group_id,
                "origin_recording": group["origin_recording"],
                "origin_offset_s": offset,
                "previous_recording": previous,
                "gap_from_previous_end_s": gap,
                "source_sha256": list(group["source_sha256"]),
            }
            previous, previous_offset = identity, offset
    if set(axes) != set(recordings):
        raise ValueError("Every manifest recording needs an explicit time group")
    return axes


def sequence_time(recording_axis, local_t_s):
    """Map local seconds to group seconds, including negative snapshot times."""
    return _seconds(local_t_s, "local_t_s") + _seconds(
        recording_axis["origin_offset_s"], "origin_offset_s"
    )
