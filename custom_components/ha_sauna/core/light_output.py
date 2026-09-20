"""Reine, seiteneffektfreie Ausgabeplanung fuer das Saunalicht."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from math import isfinite

from .light import linear
from .parameters import Parameters


def _seconds(value) -> float:
    """Zeitdifferenzen als Sekunden; die Eingaben duerfen datetime oder Zahlen sein."""
    if isinstance(value, timedelta):
        return value.total_seconds()
    return float(value)


def _remaining(now, ends_at) -> float | None:
    return None if ends_at is None else max(0.0, _seconds(ends_at - now))


def _percent(value) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
    ):
        raise ValueError("Helligkeit muss eine endliche Zahl von 0 bis 100 sein")
    return max(0.0, min(100.0, float(value)))


@dataclass(frozen=True)
class LightPlan:
    """Der gewünschte Lichtwert; ein Adapter setzt ihn bei Bedarf am Gerät um."""

    brightness_percent: float
    automatic: bool
    manual_override: bool


@dataclass
class _Motion:
    kind: str
    started_at: object
    start_percent: float
    low_percent: float
    fade_seconds: float
    fixed_percent: float | None = None


class LightOutput:
    """Plant Lichtwerte aus dem führenden Ablauf, ohne dessen Fristen zu verwalten.

    ``phase_key`` muss bei jedem neuen Ablaufobjekt wechseln. ``phase_ends_at``
    bleibt Eigentum des Controllers und wird bei jedem ``update`` neu gelesen.
    """

    _DIM_PHASES = frozenset(("nachlauf", "zwangskühlung"))
    _SESSION_PHASES = frozenset(("session_light",))

    def __init__(self, parameters: Parameters) -> None:
        self.parameters = parameters
        self._phase_key = None
        self._motion: _Motion | None = None
        self._manual: float | None = None
        self._manual_phase_key = None
        self._manual_ends_at = None
        self._last_automatic: float = 0.0
        self._resume_pending = False
        self._phase_paused = False
        self._paused_automatic: float | None = None
        self._phase_ends_at = None

    def set_manual(
        self, brightness_percent: float, *, phase_key=None, ends_at=None
    ) -> None:
        """Hält den Wert bis zum Phasenwechsel oder einer gesetzten Rückkehrfrist."""
        self._manual = _percent(brightness_percent)
        # Der Adapter kennt beim API-Aufruf den aktuellen Controllerzustand,
        # während der Planner noch den letzten Tick enthalten kann. Fehlt der
        # Schlüssel, bleibt das direkte Planer-API rückwärtskompatibel.
        self._manual_phase_key = self._phase_key if phase_key is None else phase_key
        self._manual_ends_at = ends_at
        self._resume_pending = False

    def return_to_automatic(self) -> None:
        """Beendet den Override; die nächste Planung beginnt beim letzten Auto-Wert."""
        if self._manual is not None:
            self._manual = None
            self._manual_phase_key = None
            self._manual_ends_at = None
            self._resume_pending = True

    def expire_manual(self, now) -> bool:
        """Return an automatic plan after an explicitly scheduled override ends."""
        if (
            self._manual is None
            or self._manual_ends_at is None
            or now < self._manual_ends_at
        ):
            return False
        self.return_to_automatic()
        return True

    def keep_manual(self) -> None:
        """Make an existing choice indefinite for genuine manual operation."""
        if self._manual is not None:
            self._manual_ends_at = None

    def finish_automatic(self) -> None:
        """Merkt das fachliche Ende der automatischen Ausgabe als AUS vor.

        Ein manueller Wert bleibt bewusst erhalten: Der Adapter kann den
        fälligen Timer-AUS-Dienst zugunsten einer neuen Außenlichtwahl
        überspringen, ohne dass deren spätere Rückkehr zur Automatik einen
        veralteten Helligkeitswert wieder aufbaut.
        """
        self._last_automatic = 0.0

    @property
    def manual_brightness(self) -> float | None:
        return self._manual

    @property
    def manual_ends_at(self):
        return self._manual_ends_at

    @property
    def last_automatic_brightness(self) -> float:
        """Der letzte reine Planwert für Adapter, die außerhalb einer Sitzung ruhen."""
        return self._last_automatic

    def update(
        self,
        now,
        phase_key,
        phase: str,
        temperature_target_percent: float,
        actual_percent: float,
        phase_ends_at=None,
        phase_started_at=None,
        *,
        phase_paused=False,
        phase_brightness_percent=None,
    ) -> LightPlan:
        """Gibt ausschließlich die nächste gewünschte Helligkeit zurück.

        ``phase_started_at`` beschreibt den führenden Ablauf für Adapter und
        Aufrufer, die Lichtbewegung beginnt jedoch bewusst beim beobachteten
        Eingangswert dieses Updates.
        """
        target, actual = _percent(temperature_target_percent), _percent(actual_percent)
        changed = phase_key != self._phase_key
        if changed:
            had_phase = self._phase_key is not None
            if self._manual_phase_key == self._phase_key:
                self._manual = None
                self._manual_phase_key = None
                self._manual_ends_at = None
            self._phase_key = phase_key
            self._motion = self._start_motion(
                now,
                phase,
                actual,
                phase_ends_at,
                phase_brightness_percent=phase_brightness_percent,
                transition=had_phase,
            )
            self._phase_paused = False
            self._paused_automatic = None
            self._phase_ends_at = phase_ends_at
        if phase in self._SESSION_PHASES and _remaining(now, phase_ends_at) == 0:
            self._last_automatic = 0.0
            return LightPlan(0.0, True, False)
        if phase_paused:
            if not self._phase_paused:
                # Der Controller behält den Phasenschlüssel beim Pausieren. Die
                # bis dahin geplante Nachlaufhelligkeit wird deshalb eingefroren,
                # statt mit der fehlenden Deadline auf das Normalziel zu springen.
                self._paused_automatic = (
                    self._automatic(now, phase, target, self._phase_ends_at)
                    if self._phase_ends_at is not None
                    else self._last_automatic
                )
                self._phase_paused = True
            if self._manual is not None:
                return LightPlan(self._manual, False, True)
            automatic = self._paused_automatic
            self._last_automatic = automatic
            return LightPlan(automatic, True, False)
        if self._manual is not None:
            return LightPlan(self._manual, False, True)
        if self._phase_paused:
            # Die wieder gestartete Frist beginnt exakt beim eingefrorenen Wert.
            # Dadurch gibt es weder beim Fortsetzen einen Sprung noch eine
            # vorzeitige Rückkehr zur temperaturabhängigen Normalhelligkeit.
            self._motion = _Motion("rise", now, self._paused_automatic, 0.0, 0.0)
            self._phase_paused = False
            self._paused_automatic = None
        self._phase_ends_at = phase_ends_at
        automatic = self._automatic(now, phase, target, phase_ends_at)
        if self._resume_pending:
            self._motion = _Motion(
                "resume",
                now,
                self._last_automatic,
                0.0,
                self.parameters.values["light_transition_seconds"],
                target,
            )
            self._resume_pending = False
            automatic = self._last_automatic
        self._last_automatic = automatic
        return LightPlan(automatic, True, False)

    def _start_motion(
        self,
        now,
        phase: str,
        actual: float,
        ends_at,
        *,
        phase_brightness_percent=None,
        transition=False,
    ) -> _Motion | None:
        if phase == "aus":
            return None
        duration = self.parameters.values["light_transition_seconds"]
        remaining = _remaining(now, ends_at)
        if phase in self._DIM_PHASES:
            low = (
                self.parameters.values["after_run_brightness_percent"]
                if phase == "nachlauf"
                else self.parameters.values["cooling_brightness_percent"]
            )
            fade = min(duration, remaining / 2) if remaining is not None else duration
            return _Motion("dim", now, actual, low, fade)
        if phase in self._SESSION_PHASES:
            fade = min(duration, remaining) if remaining is not None else duration
            return _Motion(
                "session",
                now,
                actual,
                0.0,
                fade,
                (
                    self.parameters.values["session_light_brightness_percent"]
                    if phase_brightness_percent is None
                    else _percent(phase_brightness_percent)
                ),
            )
        if transition:
            return _Motion("normal", now, actual, 0.0, duration)
        return None

    def _automatic(self, now, phase: str, target: float, ends_at) -> float:
        motion = self._motion
        if motion is None:
            return target
        elapsed = max(0.0, _seconds(now - motion.started_at))
        if motion.kind in ("resume", "normal"):
            # Das Ziel bleibt eine Live-Eingabe: Während der Rückkehr darf eine
            # neue Temperatur- oder Dämmerungslage nicht am alten Ziel hängen.
            return linear(motion.start_percent, target, elapsed, motion.fade_seconds)
        if motion.kind == "session":
            return linear(
                motion.start_percent, motion.fixed_percent, elapsed, motion.fade_seconds
            )
        if motion.kind == "rise":
            remaining = _remaining(now, ends_at)
            return (
                motion.start_percent
                if remaining is None
                else linear(motion.start_percent, target, elapsed, remaining + elapsed)
            )
        if elapsed <= motion.fade_seconds:
            return linear(
                motion.start_percent, motion.low_percent, elapsed, motion.fade_seconds
            )
        remaining = _remaining(now, ends_at)
        if remaining is None:
            return target
        rise_elapsed = elapsed - motion.fade_seconds
        # Die aktuelle Frist bestimmt die Steigung; sie wird nie im Motion-Zustand kopiert.
        return linear(
            motion.low_percent, target, rise_elapsed, remaining + rise_elapsed
        )
