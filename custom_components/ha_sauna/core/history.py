"""Shared display context around a sauna session, without control effects."""

from datetime import datetime, timedelta

HISTORY_CONTEXT_SECONDS = 15 * 60


def measurement_window(
    started_at: datetime, ended_at: datetime | None, now: datetime
) -> dict:
    """Return the requested chart bounds and whether its trailing data are final."""
    margin = timedelta(seconds=HISTORY_CONTEXT_SECONDS)
    return {
        "started_at": (started_at - margin).isoformat(),
        "ended_at": ((ended_at or now) + margin).isoformat(),
        "complete": ended_at is not None and now >= ended_at + margin,
    }
