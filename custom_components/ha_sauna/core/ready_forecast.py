"""Voraussichtliche Bereitzeit aus dem Temperaturverlauf (BN-3, Anlass B2).

Ohne HA-Abhängigkeit, ohne feste Grenzen (V02); entschieden wird mit
150 : 1 (V03). Steuerung, Erkennung und Gangmodell lesen das Ergebnis nicht.

Abschnitt: ab dem Tiefpunkt der Temperatur seit dem Anker (Austrittsschluss
bzw. Heizbeginn) bis jetzt, Länge L, n Messwerte. Zwei Erklärungen:

* Erholung oder Krümmung (K): eine einzige Kurve über den ganzen Abschnitt,
  eine abklingende Erholung mit bis zu zwei Zeitkonstanten
  ``a + c₁·exp(−x/τ₁) + c₂·exp(−x/τ₂)`` mit τ₁, τ₂ bis L oder eine einzelne
  abklingende bzw. beschleunigende Kurve ``a + c·exp((t−N)/τ)``; τ aus einem
  logarithmischen Raster von L/(n−1) bis L·(n−1), die Gerade ist darin der
  Grenzfall τ → ∞.
* Wieder linear (G): bis zu einem Zeitpunkt r eine einzelne solche Kurve, ab r
  eine Gerade. r ist unbekannt und gleichverteilt auf A + k·L/10, k = 1…9.

Beide haben gleich viele freie Größen. Der Bayes-Faktor G : K folgt aus den
Restquadratsummen mit der effektiven Stichprobengröße (Autokorrelation erster
Ordnung der Residuen) und wird über r gemittelt. „Linear und gut abschätzbar“
gilt bei einem Faktor von mindestens 150 und einem mit 150 : 1 positiven
Anstieg der Geraden. Die Bereitzeit ist der nach den Faktoren gewichtete
Zeitpunkt, an dem die Geraden den Sollwert erreichen.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import erf, exp, isfinite, log, sqrt

import numpy as np

EVIDENCE_RATIO = 150.0
STARTS = 10  # Raster des Linearitätsbeginns r (Zehntel des Abschnitts)
TAUS = 16  # Raster der Zeitkonstanten


@dataclass(frozen=True)
class ReadyForecast:
    """Entschiedene Schätzung: Zeitpunkte als Sekunden der Eingabezeitachse."""

    ready_at: float
    slope_c_per_s: float
    linear_from: float
    minimum_at: float
    log_evidence: float


def _effective(residual: np.ndarray) -> float:
    n = len(residual)
    if n < 4:
        return float(n)
    centered = residual - residual.mean()
    denominator = float(centered @ centered)
    if denominator <= 0:
        return float(n)
    rho = float(centered[1:] @ centered[:-1]) / denominator
    rho = min(max(rho, 0.0), 1.0 - 1.0 / n)
    return max(2.0, n * (1 - rho) / (1 + rho))


def _single(basis: np.ndarray, y: np.ndarray):
    """Least squares y ~ 1 + basis_k for every row k: (rss, a, b)."""
    n = y.shape[0]
    sb = basis.sum(axis=1)
    sbb = (basis * basis).sum(axis=1)
    sy = float(y.sum())
    sby = basis @ y
    syy = float(y @ y)
    det = n * sbb - sb * sb
    ok = det > 1e-12 * np.maximum(n * sbb, 1e-300)
    with np.errstate(divide="ignore", invalid="ignore"):
        b = np.where(ok, (n * sby - sb * sy) / np.where(ok, det, 1.0), 0.0)
    a = (sy - b * sb) / n
    return np.maximum(syy - a * sy - b * sby, 0.0), a, b


def _taus(length: float, count: int) -> np.ndarray:
    spread = max(count - 1, 1)
    return np.geomspace(length / spread, length * spread, TAUS)


def _curve(t, y, start, now, taus):
    """Best single curve (falling, rising exponential or the line): (rss, fit)."""
    basis = np.vstack([
        np.exp(-(t - start)[None, :] / taus[:, None]),
        np.exp((t - now)[None, :] / taus[:, None]),
        ((t - start) / max(now - start, 1e-300))[None, :],
    ])
    rss, a, b = _single(basis, y)
    k = int(np.argmin(rss))
    return float(rss[k]), a[k] + b[k] * basis[k]


def _recovery(t, y, start, now, taus):
    """Best curve of K: a single curve or a recovery with two time constants.

    The two time constants of a recovery reach at most the length of the
    section; longer ones would rebuild the straight rise itself.
    """
    single_rss, single_fit = _curve(t, y, start, now, taus)
    taus = taus[taus <= (now - start) * (1 + 1e-9)]
    e = np.exp(-(t - start)[None, :] / taus[:, None])
    i, j = np.triu_indices(len(taus), 1)
    gram = e @ e.T
    sums = e.sum(axis=1)
    ey = e @ y
    n = float(len(t))
    matrices = np.empty((len(i), 3, 3))
    matrices[:, 0, 0] = n
    matrices[:, 0, 1] = matrices[:, 1, 0] = sums[i]
    matrices[:, 0, 2] = matrices[:, 2, 0] = sums[j]
    matrices[:, 1, 1] = gram[i, i]
    matrices[:, 2, 2] = gram[j, j]
    matrices[:, 1, 2] = matrices[:, 2, 1] = gram[i, j]
    right = np.stack([np.full(len(i), float(y.sum())), ey[i], ey[j]], axis=1)
    try:
        coef = np.linalg.solve(matrices + 1e-12 * np.eye(3)[None], right[..., None])[..., 0]
    except np.linalg.LinAlgError:
        return single_rss, single_fit
    rss = np.maximum(float(y @ y) - (coef * right).sum(axis=1), 0.0)
    k = int(np.argmin(rss))
    if not rss[k] < single_rss:
        return single_rss, single_fit
    return float(rss[k]), coef[k, 0] + coef[k, 1] * e[i[k]] + coef[k, 2] * e[j[k]]


def _line(t, y):
    span = t[-1] - t[0]
    xs = (t - t[0]) / span
    rss, a, b = _single(xs[None, :], y)
    slope = float(b[0]) / span
    return float(rss[0]), float(a[0]) + float(b[0]) * xs, slope, float(a[0]) - slope * t[0]


def _upper_tail(t: float, dof: float) -> float:
    """P(T > t) of Student's t, normal approximation (Wallace)."""
    if dof <= 0:
        return 0.5
    z = t * (1 - 1 / (4 * dof)) / sqrt(1 + t * t / (2 * dof))
    return 0.5 * (1 - erf(z / sqrt(2)))


def forecast(times, temperatures, target: float, *, anchor: float | None = None,
             ratio: float = EVIDENCE_RATIO) -> ReadyForecast | None:
    """Decide at the last sample; ``None`` while no linear rise is established.

    ``times`` are ascending seconds, ``anchor`` the exit closing or the heating
    start on the same axis (default: the first sample).
    """
    t = np.asarray(times, dtype=float)
    y = np.asarray(temperatures, dtype=float)
    if (len(t) != len(y) or len(t) < 12 or not isfinite(target)
            or not np.all(np.isfinite(t)) or not np.all(np.isfinite(y))
            or y[-1] >= target):
        return None
    if anchor is not None:
        keep = t >= anchor
        t, y = t[keep], y[keep]
        if len(t) < 12:
            return None
    lowest = int(np.argmin(y))
    t, y = t[lowest:], y[lowest:]
    n = len(t)
    if n < 12 or t[-1] <= t[0]:
        return None
    start, now = float(t[0]), float(t[-1])
    length = now - start
    # Centred values keep the sums of squares well conditioned.
    offset = float(y.mean())
    y = y - offset
    target = target - offset
    taus = _taus(length, n)
    rss_k, fit_k = _recovery(t, y, start, now, taus)
    tiny = 1e-12
    parts = []
    for k in range(1, STARTS):
        split = int(np.searchsorted(t, start + length * k / STARTS))
        if split < 6 or n - split < 6:
            continue
        rss_line, fit_line, slope, intercept = _line(t[split:], y[split:])
        rss_early, fit_early = _curve(t[:split], y[:split], start, now, taus)
        rss_g = rss_early + rss_line
        fitted = np.concatenate([fit_early, fit_line]) if rss_g <= rss_k else fit_k
        n_eff = _effective(y - fitted)
        log_bf = -(n_eff / 2.0) * log((rss_g + tiny) / (rss_k + tiny))
        late = t[split:]
        n_late = _effective(y[split:] - fit_line)
        sxx = float(((late - late.mean()) ** 2).sum())
        se = (sqrt(rss_line / max(len(late) - 2, 1) / sxx * len(late) / n_late)
              if sxx > 0 else float("inf"))
        parts.append((log_bf, slope, intercept + slope * now, se, n_late,
                      start + length * k / STARTS))
    if not parts:
        return None
    peak = max(part[0] for part in parts)
    log_total = peak + log(sum(exp(part[0] - peak) for part in parts) / (STARTS - 1))
    if log_total < log(ratio):
        return None
    weights = [exp(part[0] - peak) for part in parts]
    total = sum(weights)
    falling = sum(
        weight * (1.0 if part[1] <= 0 or not isfinite(part[3])
                  else _upper_tail(part[1] / part[3], part[4] - 2))
        for weight, part in zip(weights, parts, strict=True)
    ) / total
    if falling > 1.0 / (ratio + 1):
        return None
    rising = [(weight, part) for weight, part in zip(weights, parts, strict=True) if part[1] > 0]
    weight = sum(item[0] for item in rising)
    ready = sum(w * (now + max((target - part[2]) / part[1], 0.0)) for w, part in rising) / weight
    return ReadyForecast(
        ready_at=ready,
        slope_c_per_s=sum(w * part[1] for w, part in rising) / weight,
        linear_from=sum(w * part[5] for w, part in rising) / weight,
        minimum_at=start,
        log_evidence=log_total,
    )
