"""Heizzeit ausschließlich aus rückgemeldeten Zuständen; keine Befehlszeit."""

from dataclasses import replace
from datetime import timedelta

from .models import HeatingInterval, HeatingTime
from .timeline import utc


def advance(state: HeatingTime, at, reset_seconds: float) -> HeatingTime:
    at = utc(at)
    if state.accounted_at is not None and at < state.accounted_at:
        raise ValueError("Heizrückmeldungen müssen zeitlich geordnet sein")
    elapsed = state.elapsed_seconds
    reset_at = state.last_reset_at
    if state.reported_heating is True and state.accounted_at is not None:
        elapsed += (at - state.accounted_at).total_seconds()
    elif state.reported_heating is False and state.off_since is not None:
        due = state.off_since + timedelta(seconds=reset_seconds)
        if at >= due and (reset_at is None or reset_at < due):
            elapsed, reset_at = 0.0, due
    return replace(
        state, elapsed_seconds=elapsed, accounted_at=at, last_reset_at=reset_at
    )


def report(
    state: HeatingTime, heating: bool | None, at, reset_seconds: float
) -> HeatingTime:
    """None ist unbekannt und kann keine zusammenhängende Auszeit beweisen."""
    at = utc(at)
    if heating is not None and type(heating) is not bool:
        raise ValueError("Rückmeldung muss bool oder unbekannt sein")
    state = advance(state, at, reset_seconds)
    if state.reported_heating is heating:
        return state
    intervals = state.intervals
    if state.reported_heating is True:
        intervals = intervals[:-1] + (
            replace(
                intervals[-1],
                ended_at=at,
                end_reason="feedback_off" if heating is False else "feedback_unknown",
            ),
        )
    if heating is True:
        intervals += (HeatingInterval(at),)
    return replace(
        state,
        reported_heating=heating,
        off_since=at if heating is False else None,
        intervals=intervals,
    )
