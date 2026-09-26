"""Pure duration calculation for the session's next oven cooling phase.

The calculator consumes observations only.  It neither changes phase state nor
creates commands, so its result can be calculated before a cooling phase starts
and then retained by the controller as that phase's fixed duration.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from math import exp, isfinite, log

from .contracts import ContactorMark, ReadinessPause
from .timeline import utc


@dataclass(frozen=True)
class OvenCoolingResult:
    """The proposed cooling duration and the evidence used to derive it."""

    duration_seconds: float
    base_minutes: float
    maximum_minutes: float
    half_life_minutes: float
    heat_idle_ratio: float
    weighted_heat_minutes: float
    weighted_idle_minutes: float
    window_started_at: datetime
    window_ended_at: datetime
    quality: str  # complete | incomplete


def calculate_oven_cooling(
    *,
    contactor_history: Iterable[ContactorMark],
    readiness_pauses: Iterable[ReadinessPause],
    session_started_at: datetime,
    at: datetime,
    window_started_at: datetime | None = None,
    readiness_complete: bool = True,
    after_run_minutes: float = 5,
    oven_cooling_max_minutes: float = 15,
    oven_cooling_half_life_minutes: float = 15,
    oven_cooling_heat_idle_ratio: float = 2,
) -> OvenCoolingResult:
    """Calculate an exponentially weighted cooling duration.

    ``window_started_at`` is normally the end of the last *fully completed*
    oven cooling phase.  With no prior completed phase it is the session start.
    Contactor ``None`` and periods before its first known mark deliberately
    remain unknown: they make the evidence incomplete but provide no idle
    credit.  Heat is confirmed contactor-on time across every session phase;
    idle is only corrected readiness-pause time with confirmed contactor-off.
    """
    session_started_at = utc(session_started_at)
    at = utc(at)
    start = (
        utc(window_started_at) if window_started_at is not None else session_started_at
    )
    if start < session_started_at:
        start = session_started_at
    if start > at:
        raise ValueError("Cooling window cannot start after its calculation time")
    base = _positive_finite("after_run_minutes", after_run_minutes, allow_zero=True)
    maximum = _positive_finite("oven_cooling_max_minutes", oven_cooling_max_minutes)
    half_life = _positive_finite(
        "oven_cooling_half_life_minutes", oven_cooling_half_life_minutes
    )
    ratio = _positive_finite(
        "oven_cooling_heat_idle_ratio", oven_cooling_heat_idle_ratio
    )
    # A saved base higher than a newly introduced default maximum remains valid:
    # it is an existing configured duration, never silently reduced.
    maximum = max(maximum, base)

    marks = _ordered_marks(contactor_history)
    heat, known = _weighted_contactor_time(
        marks, start, at, at, half_life, state=True
    )
    idle, idle_known = _weighted_idle_time(
        marks, _merged_pauses(readiness_pauses, start, at), at, half_life
    )
    duration = base + min(maximum - base, max(0.0, heat / ratio - idle))
    return OvenCoolingResult(
        duration_seconds=duration * 60,
        base_minutes=base,
        maximum_minutes=maximum,
        half_life_minutes=half_life,
        heat_idle_ratio=ratio,
        weighted_heat_minutes=heat,
        weighted_idle_minutes=idle,
        window_started_at=start,
        window_ended_at=at,
        quality=(
            "complete" if known and idle_known and readiness_complete else "incomplete"
        ),
    )


def _positive_finite(name: str, value: float, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    number = float(value)
    if not isfinite(number) or number < 0 or (number == 0 and not allow_zero):
        required = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{name} must be {required}")
    return number


def _ordered_marks(
    history: Iterable[ContactorMark],
) -> list[tuple[datetime, bool | None]]:
    # Python's stable sort retains source order for marks sharing a timestamp;
    # the final mark at that instant is therefore the state for the next span.
    return sorted(
        ((utc(mark.at), mark.state) for mark in history), key=lambda item: item[0]
    )


def _weighted_contactor_time(
    marks: list[tuple[datetime, bool | None]],
    start: datetime,
    end: datetime,
    evaluation_at: datetime,
    half_life_minutes: float,
    *,
    state: bool,
) -> tuple[float, bool]:
    current: bool | None = None
    for mark_at, mark_state in marks:
        if mark_at <= start:
            current = mark_state
        else:
            break
    points = [mark for mark in marks if start < mark[0] < end]
    cursor, total, complete = start, 0.0, current is not None
    for mark_at, mark_state in points:
        if current is None:
            complete = False
        elif current is state:
            total += _weighted_minutes(
                cursor, mark_at, evaluation_at, half_life_minutes
            )
        cursor, current = mark_at, mark_state
    if cursor < end:
        if current is None:
            complete = False
        elif current is state:
            total += _weighted_minutes(cursor, end, evaluation_at, half_life_minutes)
    return total, complete


def _merged_pauses(
    pauses: Iterable[ReadinessPause], start: datetime, end: datetime
) -> tuple[tuple[datetime, datetime], ...]:
    clipped = []
    for pause in pauses:
        left, right = max(utc(pause.started_at), start), min(utc(pause.ended_at), end)
        if left < right:
            clipped.append((left, right))
    clipped.sort()
    merged: list[tuple[datetime, datetime]] = []
    for left, right in clipped:
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))
        else:
            merged.append((left, right))
    return tuple(merged)


def _weighted_idle_time(
    marks: list[tuple[datetime, bool | None]],
    pauses: tuple[tuple[datetime, datetime], ...],
    end: datetime,
    half_life_minutes: float,
) -> tuple[float, bool]:
    total, complete = 0.0, True
    for left, right in pauses:
        amount, known = _weighted_contactor_time(
            marks, left, right, end, half_life_minutes, state=False
        )
        total += amount
        complete = complete and known
    return total, complete


def _weighted_minutes(
    left: datetime, right: datetime, now: datetime, half_life_minutes: float
) -> float:
    """Analytically integrate ``2 ** (-age / half_life)`` over an interval."""
    if right <= left:
        return 0.0
    half_life_seconds = half_life_minutes * 60
    age_left = (now - left).total_seconds()
    age_right = (now - right).total_seconds()
    return (
        half_life_seconds
        / log(2)
        * (
            exp(-log(2) * age_right / half_life_seconds)
            - exp(-log(2) * age_left / half_life_seconds)
        )
        / 60
    )
