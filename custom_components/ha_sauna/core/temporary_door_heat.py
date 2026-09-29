"""Disposable door-event eligibility for temporary heat.

This module deliberately does not know about timers, presence, gangs, feedback,
or thermostat state. The controller owns the resulting persistent demand and
clears it after confirmed heating has completed its configured minimum runtime.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TemporaryDoorHeatState:
    """State for one physical open/close cycle."""

    door_open: bool = False
    eligible_open: bool = False
    invalidated: bool = False
    seen_event_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class DoorHeatTransition:
    state: TemporaryDoorHeatState
    request: bool = False


def advance(
    state: TemporaryDoorHeatState,
    *,
    event_id: str | None,
    door_open: bool,
    enabled: bool,
    gang_active: bool = False,
    cooling: bool = False,
) -> DoorHeatTransition:
    """Apply one door edge and return at most one eligibility request pulse.

    A duplicate or anonymous event cannot request heat. Opening only arms an
    otherwise eligible cycle. Any open or close observed while operation is
    disabled, a gang is active, or cooling runs invalidates the current cycle;
    its later close is therefore inert.
    """

    if not event_id or event_id in state.seen_event_ids:
        return DoorHeatTransition(state)

    seen = state.seen_event_ids | {event_id}
    blocked = not enabled or gang_active or cooling
    if door_open:
        return DoorHeatTransition(
            TemporaryDoorHeatState(
                door_open=True,
                eligible_open=not blocked and not state.invalidated,
                invalidated=state.invalidated or blocked,
                seen_event_ids=seen,
            )
        )

    request = state.door_open and state.eligible_open and not state.invalidated and not blocked
    return DoorHeatTransition(
        TemporaryDoorHeatState(seen_event_ids=seen), request=request
    )
