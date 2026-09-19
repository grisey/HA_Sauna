"""Phasenbezogene Zeitanzeige aus den bestehenden Fristen des Ablaufkerns."""
from datetime import timedelta


def phase_timer(controller, now):
    session = controller.session
    if session is None:
        return None

    def remaining(kind, label, end):
        return {"kind": kind, "label": label, "seconds": max(0, (end - now).total_seconds()), "mode": "remaining"}

    def elapsed(kind, label, start):
        return {"kind": kind, "label": label, "seconds": max(0, (now - start).total_seconds()), "mode": "elapsed"}

    if not session.operation_enabled:
        end = next((d.due_at for d in session.deadlines if d.purpose == "session_gap"), None)
        return remaining("session_gap", "Sitzung endet in", end) if end else None
    if session.timeline.active:
        return elapsed("gang", "Saunagang seit", session.timeline.active.started_at)
    if session.after_run:
        return remaining("after_run", "Nachlauf noch", session.after_run.ends_at)
    if session.cooling and session.cooling.started_at:
        return remaining("cooling", "Zwangskühlung noch", session.cooling.ends_at)
    if controller.cooling_wait_until:
        return remaining("person_wait", "Wartezeit auf Personenerkennung", controller.cooling_wait_until)
    end = session.thermostat.cooldown_until
    if end and now < end:
        return remaining("thermostat_pause", "Heizpause noch", end)
    if session.heating.reported_heating and session.heating.intervals:
        end = session.heating.intervals[-1].started_at + timedelta(seconds=controller.parameters.seconds("minimum_heating_minutes"))
        if now < end:
            return remaining("minimum_heating", "Mindestheizzeit noch", end)
    if controller.phase == "bereit":
        return elapsed("ready", "Bereit seit", controller.phase_since or session.ready_at or session.started_at)
    return elapsed("heating", "Aufheizen seit", controller.phase_since or session.started_at)
