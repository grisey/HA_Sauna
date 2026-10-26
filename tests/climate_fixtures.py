"""Gemeinsame synthetische Klimawerte für Tests von Messweg, Archiv und Laufzeit."""

from datetime import timedelta

from test_foundation import T0

from custom_components.ha_sauna.core.models import Measurement


def measurement(pos, quantity, value, second, source=None, measured=None):
    return Measurement(pos, quantity, value, str(value), source or f"sensor.{pos}_{quantity}",
                       T0 + timedelta(seconds=second), measured)


def trace(second):
    """Temperatur und relative Feuchte: ruhig, Abfall, Plateau, Anstieg mit Sprung, Plateau, Abfall."""
    if second < 70:
        return 70, 40
    if second < 85:
        return 70 - .3 * (second - 70), 40 - .08 * (second - 70)
    if second < 150:
        return 65.5, 38.8
    if second < 260:
        return 65.5 + .025 * (second - 150), 38.8 + .02 * (second - 150) + (3 if second >= 240 else 0)
    if second < 280:
        return 68.25, 44
    return 68.25 - .05 * (second - 280), 44 - .06 * (second - 280)
