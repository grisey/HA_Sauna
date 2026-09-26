"""Pure, session-scoped door pulses and the central cooling-duration policy.

A door cycle can produce one pulse. Suppression consumes the cycle, rather than
queuing a heating request for a later operating state. Timer tokens refer to the
opening event, so an obsolete scheduled callback cannot trigger a newer cycle.
"""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from math import isfinite

from .contracts import ReadinessPause
from .parameters import Parameters


@dataclass(frozen=True)
class DoorRequestState:
    session_id: str | None = None
    door_open: bool = False
    timer_token: str | None = None
    deadline: datetime | None = None
    consumed: bool = True
    seen_event_ids: frozenset[str] = frozenset()


def update_door_request(
    state: DoorRequestState,
    *,
    now: datetime,
    session_id: str | None,
    enabled: bool,
    heating: bool,
    cooling: bool,
    door_open: bool | None = None,
    event_id: str | None = None,
    open_delay_seconds: float | None = None,
    timer_token: str | None = None,
) -> tuple[DoorRequestState, bool]:
    """Apply a received door event or poll the currently armed deadline.

    Use stable ``event_id`` values for events. No event means a timer poll;
    callers with scheduled callbacks may pass their captured ``timer_token``.
    ``now`` is receipt/execution time, never a retrospectively assigned start.
    """
    if state.session_id != session_id:
        state = DoorRequestState(session_id=session_id)
    if not enabled or session_id is None:
        return replace(
            state, deadline=None, timer_token=None, consumed=True,
            door_open=state.door_open if door_open is None else door_open,
            seen_event_ids=state.seen_event_ids | ({event_id} if event_id else set()),
        ), False
    if heating or cooling:
        state = replace(state, consumed=True, deadline=None, timer_token=None)
    if door_open is not None:
        if not event_id:
            raise ValueError("door events require a stable event_id")
        if event_id in state.seen_event_ids:
            return state, False
        state = replace(state, seen_event_ids=state.seen_event_ids | {event_id})
        if door_open == state.door_open:
            return state, False
        if door_open:
            if open_delay_seconds is not None and (
                not isfinite(open_delay_seconds) or open_delay_seconds < 0
            ):
                raise ValueError("opening delay must be finite and nonnegative")
            suppressed = heating or cooling
            state = replace(
                state, door_open=True, consumed=suppressed,
                timer_token=event_id if not suppressed else None,
                deadline=(now + timedelta(seconds=open_delay_seconds))
                if open_delay_seconds is not None and not suppressed else None,
            )
        else:
            pulse = not state.consumed and not heating and not cooling
            return replace(state, door_open=False, consumed=True,
                           deadline=None, timer_token=None), pulse
    elif timer_token is not None and timer_token != state.timer_token:
        return state, False
    if heating or cooling:
        return replace(state, consumed=True, deadline=None, timer_token=None), False
    if (not state.consumed and state.deadline is not None
            and now >= state.deadline):
        return replace(state, consumed=True, deadline=None, timer_token=None), True
    return state, False


def cooling_duration(
    parameters: Parameters,
    readiness_pauses: tuple[ReadinessPause, ...] = (),
) -> float:
    """Return fixed configured seconds until the dynamic policy is decided.

    Individual corrected pauses are accepted at this single policy boundary.
    Their horizon, distribution requirements and conversion remain undecided.
    """
    return parameters.seconds("after_run_minutes")
