"""Idempotente Fachereignisse; keine Musik- oder Aktorausgabe."""
from .contracts import ConsumerEvent


def gang_changes(before, after, received_at):
    if after is None:
        return ()
    old = {}
    if before is not None and before.session_id == after.session_id:
        t = before.timeline
        old = {g.gang_id: g for g in (*t.completed, *t.retracted, *((t.active,) if t.active else ()))}
    timeline = after.timeline
    result = []
    def emit(kind, gang, effective, received, ref, delivery="live"):
        result.append(ConsumerEvent(
            f"{after.session_id}:{kind}:{ref}", kind, after.session_id,
            effective, received, ref, gang.gang_id, delivery=delivery,
        ))
    for gang in (*timeline.completed, *((timeline.active,) if timeline.active else ())):
        previous = old.get(gang.gang_id)
        if previous is None:
            emit("gang_started", gang, gang.started_at, gang.detected_at, gang.recognition_event_id)
            if gang.started_at < gang.detected_at:
                emit("phase_corrected", gang, gang.started_at, gang.detected_at,
                     gang.recognition_event_id, "archive_correction")
        previous_infusions = {e.event_id for e in previous.infusion_events} if previous else set()
        for event in gang.infusion_events:
            if event.event_id not in previous_infusions:
                if event == gang.infusion_events[0]:
                    emit("gang_confirmed", gang, event.effective_at, event.detected_at, event.event_id)
                emit("infusion", gang, event.effective_at, event.detected_at, event.event_id)
        if gang.ended_at is not None and (previous is None or previous.ended_at is None):
            emit("gang_ended", gang, gang.ended_at, received_at, gang.end_event_id)
    old_retracted = {g.gang_id for g in before.timeline.retracted} if before and before.session_id == after.session_id else set()
    for gang in timeline.retracted:
        if gang.gang_id not in old_retracted:
            emit("gang_retracted", gang, gang.started_at, received_at, gang.recognition_event_id)
            emit("phase_corrected", gang, gang.started_at, received_at,
                 f"retract:{gang.recognition_event_id}", "archive_correction")
    return tuple(result)
