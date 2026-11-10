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
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

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
