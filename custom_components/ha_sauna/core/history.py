"""Shared display context around a sauna session, without control effects."""

from datetime import datetime, timedelta

from .parameters import BY_KEY

HISTORY_CONTEXT_SECONDS = BY_KEY["history_context_seconds"].default


def measurement_window(
    started_at: datetime, ended_at: datetime | None, now: datetime, *,
    context_seconds: float = HISTORY_CONTEXT_SECONDS,
) -> dict:
    """Return the requested chart bounds and whether its trailing data are final."""
    margin = timedelta(seconds=context_seconds)
    return {
        "started_at": (started_at - margin).isoformat(),
        "ended_at": ((ended_at or now) + margin).isoformat(),
        "complete": ended_at is not None and now >= ended_at + margin,
    }
