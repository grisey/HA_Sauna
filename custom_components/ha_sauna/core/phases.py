"""Pure, exclusive phase projection; never rewrites observations or switches."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from .contracts import PhaseInterval, PhaseProjection, ReadinessPause
from .timeline import utc


def _get(value, key, default=None):
    return value.get(key, default) if isinstance(value, Mapping) else getattr(value, key, default)


def _time(value):
    return utc(datetime.fromisoformat(value) if isinstance(value, str) else value)


def _merge(items, item):
    if items and items[-1].ended_at == item.started_at:
        previous = items[-1]
        if isinstance(item, ReadinessPause):
            items[-1] = ReadinessPause(previous.started_at, item.ended_at)
            return
        if (previous.phase, previous.source_id, previous.complete) == (item.phase, item.source_id, item.complete):
            items[-1] = PhaseInterval(previous.started_at, item.ended_at, item.phase, item.source_id, item.complete)
            return
    items.append(item)


def project_session(session, now) -> PhaseProjection:
    """Project [session start, min(now, end)) from independent factual tracks.

    Gang objects overlay base phases at their effective start. Retractions remove
    only that overlay, exposing the independently maintained base phase. Missing
    evidence remains unknown, including unknown contactor state (never a pause).
    """
    timeline = _get(session, "timeline")
    start = _time(_get(timeline, "session_started_at"))
    end = min(_time(now), _time(_get(session, "ended_at") or now))
    if end <= start:
        return PhaseProjection((), ())
    notes = []
    marks = list((_time(_get(m, "at")), _get(m, "phase"), _get(m, "operation_enabled")) for m in _get(session, "base_phases", ()))
    contacts = list((_time(_get(m, "at")), _get(m, "state")) for m in _get(session, "contactor_history", ()))
    # Preserve insertion order for simultaneous state changes.
    marks = sorted(marks, key=lambda m: m[0])
    contacts = sorted(contacts, key=lambda m: m[0])
    if not marks or marks[0][0] > start:
        notes.append("base_phase_history_incomplete")
    if not contacts or contacts[0][0] > start:
        notes.append("contactor_history_incomplete")
    overlays = []
    retracted = {_get(g, "gang_id") for g in _get(timeline, "retracted", ())}
    for gang in (*_get(timeline, "completed", ()), *([_get(timeline, "active")] if _get(timeline, "active") else [])):
        if _get(gang, "gang_id") in retracted:
            continue
        if _get(gang, "detected_at") and _time(_get(gang, "started_at")) < _time(_get(gang, "detected_at")):
            notes.append("gang_start_backdated")
        overlays.append((_time(_get(gang, "started_at")), _time(_get(gang, "ended_at") or end), "saunagang", _get(gang, "gang_id"), True))
    if retracted:
        notes.append("retracted_gangs_removed")
    for phase in (*_get(session, "after_run_history", ()), *([_get(session, "after_run")] if _get(session, "after_run") else [])):
        requested = _get(phase, "requested_at")
        if requested:
            # The logical cooling demand includes gaps without confirmed OFF.
            # Its physical evidence remains separate in active_intervals.
            logical_end = _time(_get(phase, "ends_at") or end)
            if _time(requested) < logical_end:
                overlays.append((_time(requested), logical_end, "nachlauf", _get(phase, "phase_id"), True))
            continue
        spans = _get(phase, "active_intervals", ())
        complete = bool(spans)
        if not spans:
            notes.append("cooling_pause_history_incomplete")
            spans = ((_get(phase, "started_at"), _get(phase, "paused_at") or _get(phase, "ends_at") or end),)
        for left, right in spans:
            overlays.append((_time(left), _time(right or end), "nachlauf", _get(phase, "phase_id"), complete))
    # Historical forced cooling is read-only evidence, never a request to the
    # current controller. A requested but unstarted cycle has no running span.
    for cycle in (*_get(session, "cooling_history", ()), *([_get(session, "cooling")] if _get(session, "cooling") else [])):
        if not _get(cycle, "started_at"):
            continue
        spans = _get(cycle, "active_intervals", ())
        complete = bool(spans)
        if not spans:
            notes.append("legacy_forced_cooling_pause_history_incomplete")
            spans = ((_get(cycle, "started_at"), _get(cycle, "paused_at") or _get(cycle, "ends_at") or end),)
        for left, right in spans:
            overlays.append((_time(left), _time(right or end), "zwangskühlung", _get(cycle, "cycle_id"), complete))
    points = {start, end}
    points.update(at for at, *_ in marks + contacts if start < at < end)
    points.update(at for left, right, *_ in overlays for at in (left, right) if start < at < end)
    intervals, pauses = [], []
    ordered = sorted(points)
    for left, right in zip(ordered, ordered[1:]):
        prior = [m for m in marks if m[0] <= left]
        phase, enabled = prior[-1][1:] if prior else ("unknown", None)
        source, complete = None, phase != "unknown"
        if enabled is False:
            phase = "aus"
        else:
            active = [o for o in overlays if o[0] <= left < o[1]]
            # Cooling takes precedence over contradictory imported gang evidence.
            active.sort(key=lambda o: (o[2] in ("nachlauf", "zwangskühlung"), o[0]))
            if active:
                _, _, phase, source, complete = active[-1]
        _merge(intervals, PhaseInterval(left, right, phase, source, complete))
        known = [m for m in contacts if m[0] <= left]
        if phase == "bereit" and enabled is True and known and known[-1][1] is False:
            _merge(pauses, ReadinessPause(left, right))
    incomplete = any(n.endswith("incomplete") for n in notes) or any(not p.complete for p in intervals)
    return PhaseProjection(tuple(intervals), tuple(pauses), tuple(dict.fromkeys(notes)), not incomplete)


def project_archive(session, records, now) -> PhaseProjection:
    """Read-only compatibility projection from evidence in original records.

    A legacy gang record is an overlay, not a new background-phase mark.  When
    that gang was later retracted, the preceding independently stored base mark
    remains the evidence for the exposed interval.  Without such a mark the
    interval remains unknown; this function never infers heating from missing
    data or from temperatures alone.  All records must be supplied
    independently of history endpoint pagination.
    """
    if session.get("base_phases"):
        return project_session(session, now)
    reconstructed = dict(session)
    retracted_ids = {
        _get(gang, "gang_id")
        for gang in _get(_get(session, "timeline"), "retracted", ())
        if _get(gang, "gang_id")
    }
    marks, contacts, legacy_gangs, revisions = [], [], [], []
    for record in records:
        payload = record["payload"]
        at = record["received_at"]
        if record["kind"] == "phase":
            phase = payload.get("phase")
            if phase in ("aus", "aufheizen", "bereit", "manuell"):
                marks.append({"at": at, "phase": phase, "operation_enabled": phase != "aus"})
            # A legacy gang record is an overlay, so it must not erase the
            # most recent independently observed background phase.  In
            # particular, a retracted gang exposes that phase again.
            elif phase == "saunagang":
                marks.append({"at": at, "phase": "unknown", "operation_enabled": True})
                legacy_gangs.append(at)
            # Unlike a gang, old cooling records can continue after their
            # independent session object is absent.  Do not extend a former
            # readiness mark over this unknown legacy background.
            elif phase in ("nachlauf", "kuehlung", "zwangskühlung"):
                marks.append({"at": at, "phase": "unknown", "operation_enabled": True})
        elif record["kind"] == "session":
            revisions.append((at, payload))
            # A stored ready timestamp is direct evidence of the former
            # controller decision. Raw temperatures alone do not prove that
            # decision without all contemporary validity and target rules.
            ready = payload.get("ready_at")
            if ready:
                marks.append({"at": ready, "phase": "bereit", "operation_enabled": True})
            off = payload.get("operation_off_at")
            if off and payload.get("operation_enabled") is False:
                marks.append({"at": off, "phase": "aus", "operation_enabled": False})
        elif record["kind"] == "source_state" and payload.get("role") == "heater":
            contacts.append({"at": at, "state": {"on": True, "off": False}.get(payload.get("state"))})
    # The preceding phase is sufficient only after a stored revision confirms
    # that operation continued, readiness was still absent and no cooling was
    # active.  This repairs a retracted legacy candidate without turning every
    # missing old gang background into heating.
    for gang_at in legacy_gangs:
        next_mark = next((item["at"] for item in marks if item["at"] > gang_at), None)
        retracted = any(
            at == gang_at
            and _get(_get(_get(payload, "timeline"), "active", {}), "gang_id")
            in retracted_ids
            for at, payload in revisions
        )
        confirmed = retracted and any(
            at >= gang_at
            and (next_mark is None or at < next_mark)
            and payload.get("operation_enabled") is True
            and payload.get("ready_at") is None
            and not payload.get("after_run")
            and not payload.get("cooling")
            for at, payload in revisions
        )
        if confirmed:
            marks = [
                mark
                for mark in marks
                if not (mark["at"] == gang_at and mark["phase"] == "unknown")
            ]
    reconstructed["base_phases"] = marks
    reconstructed["contactor_history"] = contacts
    projection = project_session(reconstructed, now)
    return PhaseProjection(projection.intervals, projection.readiness_pauses,
                           projection.corrections + ("legacy_projection_incomplete",), False)
