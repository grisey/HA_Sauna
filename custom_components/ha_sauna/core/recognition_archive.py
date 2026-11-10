"""Archiveinträge der Erkennung für das normale Archivprofil.

Die Einträge werden nur aus dem gelesen, was die Erkennung ohnehin liefert
(Diagnose je Messschritt, Entscheidungsprotokoll, gemeldete Episoden); die
Erkennung selbst bleibt unverändert.

* ``recognition_state``: Wechsel des wahrscheinlichsten Zustands mit
  Zeitpunkt und Wahrscheinlichkeit des neuen Zustands. Die vollständigen
  Wahrscheinlichkeiten je Messschritt (``detector_trace``) bleiben dem Profil
  „analysis“ vorbehalten.
* ``recognition_rejected``: ein von der Öffnungsform verworfener
  Öffnungskandidat mit Zeitpunkt, Entscheidungszeit, Grund und dem Verhältnis
  seines Zustands zur nächstbesten Erklärung.
* ``recognition_episode``: Episode verworfener Öffnungskandidaten mit Beginn,
  Ende, Kandidaten und Ergebnis (``rejected`` oder ``door``: vom Gangmodell
  über die Anwesenheit als Türvorgang übernommen).

Zu einer übernommenen Erkennung (Tür auf, Tür zu, Aufguss) liefert
``decision`` dieselbe Größe wie für verworfene Kandidaten: Zustand, nächstbeste
Erklärung und deren Verhältnis bei der Entscheidung des Zustandsmodells (L-B4-4).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

STATE = "recognition_state"
REJECTED_CANDIDATE = "recognition_rejected"
EPISODE = "recognition_episode"


class RecognitionArchive:
    """Archiveinträge einer Erkennung (je Sitzung eine Instanz)."""

    def __init__(self) -> None:
        self._state: str | None = None
        self._decisions = 0

    def state_change(self, trace) -> dict | None:
        """Eintrag, wenn ein anderer Zustand als bisher am wahrscheinlichsten ist.

        Bei gleicher Wahrscheinlichkeit bleibt der bisherige Zustand.
        """
        probabilities = trace.get("state_probabilities") if isinstance(trace, Mapping) else None
        if not isinstance(probabilities, Mapping) or not probabilities:
            return None
        top = max(probabilities.values())
        if self._state in probabilities and probabilities[self._state] >= top:
            return None
        self._state = next(name for name, value in probabilities.items() if value == top)
        return {"at": trace.get("at"), "state": self._state, "probability": top}

    def rejected_candidates(self, decisions: Sequence[Mapping]) -> list[dict]:
        """Seit dem letzten Aufruf verworfene Öffnungskandidaten aus dem Entscheidungsprotokoll."""
        start, self._decisions = self._decisions, len(decisions)
        out = []
        for index in range(start, len(decisions)):
            decision = decisions[index]
            if decision.get("event") != "door_open_rejected":
                continue
            # the candidate's own decision: its state against the next best explanation
            candidate = next((
                earlier for earlier in reversed(decisions[:index])
                if earlier.get("event") == "door_open"
                and earlier.get("effective_at") == decision["effective_at"]
            ), {})
            out.append({
                "kind": "door_open",
                "effective_at": decision["effective_at"],
                "decided_at": decision["decided_at"],
                "reason": decision.get("reason"),
                "state": candidate.get("state"),
                "against": candidate.get("against"),
                "ratio": candidate.get("ratio"),
            })
        return out


_EXPLAINED = ("door_open", "door_close", "infusion")


def _moment(value):
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


def decision(detection, detections: Sequence, decisions: Sequence[Mapping]) -> dict | None:
    """Entscheidung des Zustandsmodells zu einer gelieferten Erkennung, sonst ``None``.

    ``detections`` und ``decisions`` sind Liefer- und Entscheidungsprotokoll derselben
    Erkennung (``ClimateRecognizer.detections``, ``.decisions``), ``detection`` ein
    Eintrag des ersten. Jede Entscheidung über Tür auf, Tür zu und Aufguss steht dort
    mit Zustand, nächstbester Erklärung und Verhältnis; zugeordnet wird ohne Eingriff:

    * Tür auf: die Entscheidung mit demselben Zeitpunkt; jeder Kandidat beginnt an
      einem eigenen Messschritt.
    * Tür zu: die n-te Entscheidung über einen Schluss, auf die keine Verwerfung der
      Öffnung folgt, für den n-ten gelieferten Schluss; sie fällt im selben Messschritt.
    * Aufguss: die n-te Entscheidung über einen Aufguss für den n-ten gelieferten; ein
      bei offener Tür bestätigter Aufguss folgt dem Schluss in seiner Reihenfolge.

    Liegt die gefundene Entscheidung nach Zeitpunkt oder Bestätigung der Erkennung,
    gilt ``None``.
    """
    kind = getattr(detection.kind, "value", detection.kind)
    if kind not in _EXPLAINED:
        return None
    position = next((index for index in range(len(detections) - 1, -1, -1)
                     if detections[index] is detection), None)
    if position is None:
        return None
    if kind == "door_open":
        effective = detection.effective_at.isoformat()
        found = next((entry for entry in reversed(decisions)
                      if entry.get("event") == kind and entry.get("effective_at") == effective), None)
    else:
        number = sum(1 for item in detections[:position + 1]
                     if getattr(item.kind, "value", item.kind) == kind)
        made = [entry for index, entry in enumerate(decisions)
                if entry.get("event") == kind and not (
                    kind == "door_close" and index + 1 < len(decisions)
                    and decisions[index + 1].get("event") == "door_open_rejected")]
        found = made[number - 1] if number <= len(made) else None
    if found is None or "ratio" not in found:
        return None
    decided = _moment(found["decided_at"])
    if decided > detection.detected_at or (
            kind == "door_close" and decided != detection.detected_at) or (
            kind != "door_close" and _moment(found["effective_at"]) > detection.effective_at):
        return None
    return {"state": found.get("state"), "against": found.get("against"), "ratio": found["ratio"]}


def episode_record(rejected, door: bool) -> dict:
    """Eintrag einer gemeldeten Episode (``recognition.RejectedOpening``) mit ihrem Ergebnis."""
    return {
        "opened_at": rejected.opened_at,
        "closed_at": rejected.closed_at,
        "close_candidate_at": rejected.close_candidate_at,
        "ended_at": rejected.ended_at,
        "decided_at": rejected.decided_at,
        "candidates": list(rejected.candidates),
        "result": "door" if door else "rejected",
    }
