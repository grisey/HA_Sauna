"""Reine Ofentimer-Anzeige; ohne Einfluss auf die Heizentscheidung."""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta


@dataclass(frozen=True)
class MechanicalTimer:
    cycle_id: str | None = None
    elapsed_seconds: float = 0
    running_since: datetime | None = None
    reset_pending: bool = False

    def elapsed_at(self, now):
        return self.elapsed_seconds + (max(0, (now - self.running_since).total_seconds())
            if self.running_since is not None else 0)

    def start(self, now, session_id):
        if self.running_since is not None:
            return self
        if self.cycle_id is None or self.reset_pending:
            return MechanicalTimer(cycle_id=session_id, running_since=now)
        return replace(self, running_since=now)

    def pause(self, now):
        return replace(self, elapsed_seconds=self.elapsed_at(now), running_since=None)

    def status(self, now, duration):
        remaining = max(0, duration - self.elapsed_at(now))
        state = ("idle" if self.cycle_id is None else "expired" if remaining == 0
                 else "running" if self.running_since is not None else "paused")
        ends = (self.running_since + timedelta(seconds=duration - self.elapsed_seconds)
                if self.running_since is not None else None)
        return {"state": state, "remaining_seconds": remaining, "ends_at": ends,
                "reset_pending": self.reset_pending, "duration_seconds": duration}
