"""Nachlernen der Erkennung: gelernte Größen, Sitzungsbelege und Rückmeldungen.

Nach jeder abgeschlossenen Sitzung zieht ``RecognitionLearning`` aus deren
Messwerten und eigenen Erkennungen Belege (``recognition_learning``).  Sie
werden als „nicht freigegeben“ gespeichert; erst die ausdrückliche Freigabe
der Sitzung (Aktion „Sitzung für das Nachlernen freigeben“ oder Panel) lässt
sie in eine Schätzung ein.  Eine Rückmeldung „abgelehnt“ nimmt die Belege
eines Ereignisses heraus, „bestätigt“ hebt eine Ablehnung auf.  Neu geschätzt
werden die Modellgrößen erst auf Anforderung (Aktion „Erkennung nachlernen“):
Belege der mitgelieferten Lernaufzeichnungen bleiben als Startwissen
erhalten, freigegebene Sitzungen ergänzen sie.  Neue Größen gelten ab der
nächsten Sitzung.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from .core import recognition_learning as learning
from .core.recognition import recognition_event_id
from .core.recognition_model import CHANNELS
from .core.timeline import Kind, utc
from .recognition_parameters import shipped_evidence, shipped_parameters

STORAGE_VERSION = 1
# storage bound: evidence of the most recent sessions (older sessions leave the estimate)
SESSION_LIMIT = 40


def is_released(session: dict) -> bool:
    """Freigabe einer gespeicherten Sitzung; Belege ohne Status (älterer Lernstand) sind nicht freigegeben."""
    return session.get("released") is True


class RecognitionLearning:
    """Lernstand einer Sauna-Instanz; Speicherung übernimmt der Aufrufer."""

    def __init__(self, data: dict | None = None):
        data = data or {}
        self.sessions: list[dict] = list(data.get("sessions", []))
        self.rejected: set[str] = set(data.get("rejected", []))
        self.parameters: dict | None = data.get("parameters")
        self.pending: int = int(data.get("pending", 0))   # sessions, releases and feedback since the last estimate

    def as_dict(self) -> dict:
        return {
            "sessions": self.sessions,
            "rejected": sorted(self.rejected),
            "parameters": self.parameters,
            "pending": self.pending,
        }

    def current_parameters(self) -> dict:
        return self.parameters or shipped_parameters()

    def add_session(self, session_id: str, recognizer) -> bool:
        """Belege einer abgeschlossenen Sitzung aufnehmen (nur mit beiden Messpositionen)."""
        if tuple(recognizer.channels) != CHANNELS:
            return False

        def source(detection):
            return recognition_event_id(session_id, detection.kind, detection.effective_at)

        evidence = learning.session_evidence(
            recognizer, self.current_parameters(), session=session_id, source=source,
        )
        if evidence is None:
            return False
        events = [[source(d), Kind(d.kind).value, utc(d.effective_at).isoformat()] for d in recognizer.detections]
        self.sessions = [s for s in self.sessions if s["session"] != session_id]
        # new evidence starts unreleased: a release covers only the evidence it was given for
        self.sessions.append({
            "session": session_id, "started_at": utc(recognizer.origin).isoformat(), "released": False,
            "events": events, "evidence": learning.compact(evidence),
        })
        self.sessions = self.sessions[-SESSION_LIMIT:]
        self.pending += 1
        return True

    def release(self, session_id: str, released: bool) -> bool:
        """Gespeicherte Sitzung für das Nachlernen freigeben oder die Freigabe zurücknehmen."""
        session = next((s for s in self.sessions if s["session"] == session_id), None)
        if session is None:
            raise KeyError(session_id)
        if is_released(session) == released:
            return False
        session["released"] = released
        self.pending += 1
        return True

    def overview(self) -> list[dict]:
        """Gespeicherte Sitzungen, jüngste zuerst: Beginn, erkannte Türöffnungen und Aufgüsse, Freigabe."""
        return [
            {
                "session_id": s["session"],
                "started_at": s.get("started_at"),
                "door_openings": sum(e[1] == Kind.DOOR_OPEN.value for e in s["events"]),
                "infusions": sum(e[1] == Kind.INFUSION.value for e in s["events"]),
                "released": is_released(s),
            }
            for s in reversed(self.sessions)
        ]

    def estimate(self) -> dict:
        """Modellgrößen aus Startwissen und freigegebenen Sitzungsbelegen ohne abgelehnte Ereignisse."""
        released = [s["evidence"] for s in self.sessions if is_released(s)]
        evidence = learning.merge_evidence(shipped_evidence(), *released)
        params = learning.estimate(evidence, base=shipped_parameters(), rejected=frozenset(self.rejected))
        self.parameters = params
        self.pending = 0
        return params

    def find_event(self, *, event_id=None, kind=None, at=None, session_id=None) -> str | None:
        """Ereigniskennung zu einer Angabe: Kennung selbst oder Art und Zeit (nächstes Ereignis)."""
        if event_id:
            return event_id if any(e[0] == event_id for s in self.sessions for e in s["events"]) else None
        if kind is None or at is None:
            return None
        sessions = [s for s in self.sessions if session_id is None or s["session"] == session_id]
        at = utc(at)
        best = None
        for s in sessions:
            for eid, k, effective in s["events"]:
                if k != Kind(kind).value:
                    continue
                distance = abs((datetime.fromisoformat(effective) - at).total_seconds())
                if best is None or distance < best[0]:
                    best = (distance, eid)
        return best[1] if best else None

    def feedback(self, event_id: str, verdict: str) -> None:
        if verdict == "reject":
            self.rejected.add(event_id)
        elif verdict == "confirm":
            self.rejected.discard(event_id)
        else:
            raise ValueError("Rückmeldung muss reject oder confirm sein")
        self.pending += 1


async def async_setup_recognition_learning(hass, entry, runtime) -> RecognitionLearning:
    """Lernstand laden und an die Laufzeit binden: Belege nach jeder Sitzung, Schätzung auf Anforderung."""
    from homeassistant.helpers.storage import Store

    from .environment import outside_air

    store = Store(hass, STORAGE_VERSION, f"ha_sauna.recognition.{entry.entry_id}")
    await hass.async_add_executor_job(shipped_parameters)      # file read outside the event loop
    state = RecognitionLearning(await store.async_load())
    lock = asyncio.Lock()
    runtime.recognition_parameters = state.current_parameters()
    runtime.outside_air = lambda: outside_air(hass, runtime.configuration.bindings.values)
    runtime.recognition_learning = state

    async def update(work):
        async with lock:
            changed = await hass.async_add_executor_job(work)
            if not changed:
                return
            await store.async_save(state.as_dict())
            if not runtime.closed:
                runtime.recognition_parameters = state.current_parameters()

    def learn(session_id, recognizer):
        def work():
            return state.add_session(session_id, recognizer)

        hass.async_create_background_task(update(work), f"ha_sauna recognition evidence {session_id}")

    async def feedback(event_id, verdict):
        def work():
            state.feedback(event_id, verdict)
            return True

        await update(work)

    async def release(session_id, released):
        """Freigabe einer gespeicherten Sitzung setzen; unbekannte Sitzung: KeyError."""
        def work():
            return state.release(session_id, released)

        await update(work)

    async def relearn() -> dict:
        """Modellgrößen aus Startwissen und freigegebenen Belegen neu schätzen (gilt ab der nächsten Sitzung)."""
        summary = {}

        def work():
            summary["changes"] = state.pending
            state.estimate()
            summary["sessions"] = len(state.sessions)
            summary["released"] = sum(is_released(s) for s in state.sessions)
            summary["rejected"] = len(state.rejected)
            return True

        await update(work)
        return summary

    runtime.recognition_finished = learn
    runtime.recognition_feedback = feedback
    runtime.recognition_release = release
    runtime.recognition_relearn = relearn
    return state
