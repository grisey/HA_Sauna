"""Mitgelieferte Erkennungsgrößen, gelernt aus bewerteten Aufzeichnungen.

``recognition_parameters.json`` entsteht mit ``tools/recognition_learn.py``.
Es enthält die Modellgrößen und die Belege der Lernaufzeichnungen; die Belege
sind Startwissen für das Nachlernen und enthalten keine Messverläufe.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

PATH = Path(__file__).with_name("recognition_parameters.json")


@lru_cache(maxsize=1)
def _load() -> dict:
    return json.loads(PATH.read_text(encoding="utf-8"))


def shipped_parameters() -> dict:
    """Modellgrößen ohne Belege (nicht verändern: der Inhalt wird geteilt)."""
    return {key: value for key, value in _load().items() if key not in ("evidence", "training")}


def shipped_evidence() -> dict:
    """Belege der Lernaufzeichnungen (nicht verändern: der Inhalt wird geteilt)."""
    return _load()["evidence"]
