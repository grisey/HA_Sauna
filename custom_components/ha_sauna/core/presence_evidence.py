"""Belegung der Kabine aus dem Verlauf eines Präsenzsensors.

Nach einem Türvorgang stehen zwei Erklärungen gegeneinander: jemand ist in
der Kabine (Aufenthalt) oder sie ist leer.  Der Präsenzverlauf unterscheidet
sie über vier gelernte Zeiten:

* Haltezeit: Nach dem Verlassen meldet der Sensor noch so lange Anwesenheit,
  wie seine Abwesenheitsverzögerung und das Verlassen seines Bereichs dauern.
  Sie besteht aus einer eingestellten Verzögerung und einer kurzen Wegzeit
  und ist darum schwach geschwänzt (Gammaverteilung).
* Aufenthalt: Zeit vom Schließen nach einem Eintritt bis zur ersten
  Abwesenheitsmeldung (lognormal).
* Aussetzer: Abwesenheitsmeldung, obwohl jemand in der Kabine ist, bis zur
  nächsten Anwesenheit (lognormal).
* Leerlauf: Abwesenheit bei leerer Kabine bis zur nächsten Anwesenheitsmeldung
  (lognormal).

Ein Anwesenheitsabschnitt, der mit dem Türschluss beginnt, ist beim Aufenthalt
eine Aufenthaltsdauer und bei leerer Kabine eine Haltezeit.  Ein
Abwesenheitsabschnitt ist ein Aussetzer oder Leerlauf; kehrt die Anwesenheit
bei offener Tür zurück, belegt das nichts.  Weitere Anwesenheitsabschnitte
sind unter beiden Erklärungen gleich wahrscheinlich.  Ein unbekannter oder
nicht verfügbarer Zustand schließt den laufenden Abschnitt mit seiner bis
dahin beobachteten Dauer ab; mit dem nächsten bekannten Zustand beginnt ein
neuer Abschnitt.

Entschieden wird beim Beweisverhältnis der Erkennung.  Dieses Modul enthält
keine Wartezeit; die Zeit bis zur Entscheidung folgt allein aus den gelernten
Verteilungen.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

RESOLUTION_S = 1.0  # shortest distinguishable duration of a presence report


def lognormal_logpdf(x: float, mu: float, sigma: float) -> float:
    x = max(x, RESOLUTION_S)
    z = (math.log(x) - mu) / sigma
    return -0.5 * z * z - math.log(sigma * math.sqrt(2.0 * math.pi)) - math.log(x)


def _normal_logsf(z: float) -> float:
    if z < 30.0:
        return math.log(0.5 * math.erfc(z / math.sqrt(2.0)))
    return -0.5 * z * z - math.log(z * math.sqrt(2.0 * math.pi))


def lognormal_logsf(x: float, mu: float, sigma: float) -> float:
    return _normal_logsf((math.log(max(x, RESOLUTION_S)) - mu) / sigma)


def gamma_logpdf(x: float, shape: float, scale: float) -> float:
    x = max(x, RESOLUTION_S)
    return ((shape - 1.0) * math.log(x) - x / scale
            - math.lgamma(shape) - shape * math.log(scale))


def gamma_logsf(x: float, shape: float, scale: float) -> float:
    """log Q(shape, x/scale): regularisierte obere unvollständige Gammafunktion."""
    a, x = shape, max(x, RESOLUTION_S) / scale
    prefix = a * math.log(x) - x - math.lgamma(a)
    if x < a + 1.0:
        term = total = 1.0 / a
        n = a
        for _ in range(1000):
            n += 1.0
            term *= x / n
            total += term
            if abs(term) < abs(total) * 1e-15:
                break
        lower = math.exp(prefix) * total
        return math.log1p(-min(lower, 1.0 - 1e-300))
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    return prefix + math.log(h)


@dataclass(frozen=True)
class PresenceTiming:
    """Gelernte Zeiten des Präsenzsensors (siehe Modulbeschreibung)."""

    hold_shape: float
    hold_scale: float
    stay: tuple[float, float]
    dropout: tuple[float, float]
    vacancy: tuple[float, float]

    @classmethod
    def from_parameters(cls, parameters: Mapping) -> PresenceTiming:
        """Aus dem Block ``presence`` der Erkennungsgrößen."""
        data = parameters["presence"]
        shape, scale = data["hold"]
        return cls(float(shape), float(scale), tuple(map(float, data["stay"])),
                   tuple(map(float, data["dropout"])), tuple(map(float, data["vacancy"])))

    def hold_logpdf(self, x: float) -> float:
        return gamma_logpdf(x, self.hold_shape, self.hold_scale)

    def hold_logsf(self, x: float) -> float:
        return gamma_logsf(x, self.hold_shape, self.hold_scale)

    def first_presence(self, duration: float, *, ended: bool) -> float:
        """log(Aufenthalt : Haltezeit) eines Anwesenheitsabschnitts ab dem Schluss."""
        if ended:
            return lognormal_logpdf(duration, *self.stay) - self.hold_logpdf(duration)
        return lognormal_logsf(duration, *self.stay) - self.hold_logsf(duration)

    def absence(self, duration: float, *, ended: bool, carried: float = 0.0) -> float:
        """log(Aussetzer : Leerlauf) eines Abwesenheitsabschnitts.

        ``carried``: Abwesenheit, die schon vor der Türöffnung bestand.  Vorher
        war jemand in der Kabine; unter Aufenthalt setzt sich dieser Aussetzer
        fort, die leere Kabine beginnt erst mit der Öffnung.
        """
        total = duration + carried
        if ended:
            dropout = lognormal_logpdf(total, *self.dropout)
            vacancy = lognormal_logpdf(duration, *self.vacancy)
        else:
            dropout = lognormal_logsf(total, *self.dropout)
            vacancy = lognormal_logsf(duration, *self.vacancy)
        if carried > 0:
            dropout -= lognormal_logsf(carried, *self.dropout)
        return dropout - vacancy


def occupancy_log_ratio(
    timing: PresenceTiming,
    states: Sequence[tuple[float, bool | None]],
    *,
    start: float,
    at: float,
    close: float | None = None,
) -> float:
    """log(Aufenthalt : leer) des Präsenzverlaufs von ``start`` bis ``at``.

    ``states`` sind zeitlich geordnete Zustandswechsel (Sekunden, ``True``
    anwesend, ``False`` abwesend, ``None`` unbekannt); der letzte Eintrag
    bis ``start`` gilt als Zustand zu Beginn.  ``close`` ist der Türschluss
    des Türvorgangs, falls die Tür zu ist; ohne ihn ist die Tür seit
    ``start`` offen.  Beim Eintritt ist ``start`` der Türschluss selbst.
    """
    state: bool | None = None
    began = start
    for when, value in states:
        if when > start:
            break
        if value != state:
            began = when
        state = value
    carried = start - began if state is False else 0.0
    marks = [(when, value) for when, value in states if start < when <= at]
    if close is not None and start <= close <= at:
        marks.append((close, "close"))
        marks.sort(key=lambda item: (item[0], item[1] != "close"))
    total = 0.0
    since = start
    first = False                                       # inside the presence section from the close
    door_closed = close is not None and close <= start
    if door_closed and state is True and close == start:
        first = True
    for when, value in marks:
        if value == "close":
            door_closed = True
            if state is True:
                first, since = True, when
            continue
        if value == state:
            continue
        if value is None:
            # The section ends unobserved: only its observed length counts.
            if state is True and first:
                total += timing.first_presence(when - since, ended=False)
            elif state is False:
                total += timing.absence(when - since, ended=False, carried=carried)
            first = False
        elif state is True and first:
            total += timing.first_presence(when - since, ended=True)
            first = False
        elif state is False and value is True and door_closed:
            total += timing.absence(when - since, ended=True, carried=carried)
        state, since, carried = value, when, 0.0
    if state is True and first:
        total += timing.first_presence(at - since, ended=False)
    elif state is False:
        total += timing.absence(at - since, ended=False, carried=carried)
    return total


# ---------------------------------------------------------------------------
# Lernen aus bewerteten Aufzeichnungen


def _nelder_mead(f, x0: Sequence[float], step: float = 0.5, iterations: int = 4000) -> list[float]:
    n = len(x0)
    simplex = [list(x0)] + [[x0[j] + (step if j == i else 0.0) for j in range(n)] for i in range(n)]
    values = [f(p) for p in simplex]
    for _ in range(iterations):
        order = sorted(range(n + 1), key=values.__getitem__)
        simplex, values = [simplex[i] for i in order], [values[i] for i in order]
        if abs(values[-1] - values[0]) < 1e-10:
            break
        centroid = [sum(p[j] for p in simplex[:-1]) / n for j in range(n)]
        worst = simplex[-1]
        reflected = [c + (c - w) for c, w in zip(centroid, worst, strict=True)]
        fr = f(reflected)
        if fr < values[0]:
            expanded = [c + 2.0 * (c - w) for c, w in zip(centroid, worst, strict=True)]
            fe = f(expanded)
            simplex[-1], values[-1] = (expanded, fe) if fe < fr else (reflected, fr)
        elif fr < values[-2]:
            simplex[-1], values[-1] = reflected, fr
        else:
            contracted = [c + 0.5 * (w - c) for c, w in zip(centroid, worst, strict=True)]
            fc = f(contracted)
            if fc < values[-1]:
                simplex[-1], values[-1] = contracted, fc
            else:
                best = simplex[0]
                simplex = [best] + [[b + 0.5 * (p[j] - b) for j, b in enumerate(best)]
                                    for p in simplex[1:]]
                values = [values[0]] + [f(p) for p in simplex[1:]]
    return simplex[min(range(n + 1), key=values.__getitem__)]


def fit_lognormal(durations: Iterable[float], censored: Iterable[float] = ()) -> tuple[float, float]:
    """Lognormal (log-Mittel, log-Streuung) mit rechtszensierten Dauern."""
    durations, censored = list(durations), list(censored)
    logs = [math.log(max(x, RESOLUTION_S)) for x in durations]
    mu0 = sum(logs) / len(logs) if logs else math.log(max(max(censored, default=60.0), 1.0))

    def nll(p):
        mu, sigma = p[0], math.exp(p[1])
        return -(sum(lognormal_logpdf(x, mu, sigma) for x in durations)
                 + sum(lognormal_logsf(x, mu, sigma) for x in censored))

    mu, log_sigma = _nelder_mead(nll, [mu0, 0.0])
    return mu, math.exp(log_sigma)


def fit_hold(durations: Iterable[float], censored: Iterable[float], stay: tuple[float, float]
             ) -> tuple[float, float, float]:
    """Haltezeit (Gamma) nach Austritten, gemischt mit weiter gesehenen Personen.

    Nach einem Austritt kann der Sensor eine Person weiter erfassen, etwa vor
    der Tür.  Diese Abschnitte verhalten sich wie ein Aufenthalt; sie gehören
    nicht zur Haltezeit des Sensors.  Ergebnis: Form, Skala und Anteil der
    weiter gesehenen Personen.
    """
    durations, censored = list(durations), list(censored)

    def mix(hold, seen, share):
        return max(hold + math.log(1.0 - share), seen + math.log(share)) + math.log1p(
            math.exp(-abs(hold + math.log(1.0 - share) - seen - math.log(share))))

    def nll(p):
        shape, scale, share = math.exp(p[0]), math.exp(p[1]), 1.0 / (1.0 + math.exp(-p[2]))
        return -(sum(mix(gamma_logpdf(x, shape, scale), lognormal_logpdf(x, *stay), share)
                     for x in durations)
                 + sum(mix(gamma_logsf(x, shape, scale), lognormal_logsf(x, *stay), share)
                       for x in censored))

    best = None
    for share0 in (-2.0, -1.0, 0.0):
        p = _nelder_mead(nll, [math.log(1.5), math.log(8.0), share0])
        if best is None or nll(p) < nll(best):
            best = p
    return math.exp(best[0]), math.exp(best[1]), 1.0 / (1.0 + math.exp(-best[2]))
