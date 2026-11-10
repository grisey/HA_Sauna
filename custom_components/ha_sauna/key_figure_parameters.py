"""Mitgelieferte gelernte Größen der Kennzahlen mit Herkunft und Lernmenge.

``key_figure_parameters.json`` enthält das Ergebnis von
``core.key_figures.learn_door_sensor_lag`` für die bestätigte Lernmenge.
Neu geschätzt wird nur auf ausdrückliche Bestätigung; der Wert ist kein
Werkstandard der Einstellungen.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

PATH = Path(__file__).with_name("key_figure_parameters.json")


@lru_cache(maxsize=1)
def _load() -> dict:
    return json.loads(PATH.read_text(encoding="utf-8"))


def door_sensor_lag() -> float:
    """Nachlauf des Temperaturfühlers nach dem Türschließen in Sekunden (K-S21)."""
    return _load()["door_sensor_lag"]["value_seconds"]
