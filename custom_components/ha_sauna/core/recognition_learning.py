"""Lernen der physikalischen Größen des Erkennungsmodells.

Zweistufig:

1. ``collect_evidence`` zieht aus einer Sitzung (Messwerte und Ereignisse)
   kompakte Belege: Rauschen ruhiger Abschnitte, Austauschkoeffizienten
   offener Türen, Anstiegs-, Abkling- und Erholungsraten, Heizwechsel,
   Zustandsdauern und Innovationshistogramme.  Die Ereignisse stammen aus der
   bewerteten Referenz (Erstlernen) oder aus der Erkennung selbst
   (Nachlernen).  Jeder ereignisbezogene Beleg trägt seine Quelle, damit eine
   spätere Ablehnung durch den Nutzer ihn wieder entfernt.
2. ``estimate`` bildet aus allen Belegen die Modellgrößen.  Belege der
   Lernsitzungen bleiben als Startwissen erhalten; neue Sitzungen ergänzen sie.

Nur bestätigte Ereignisse fließen ein.  Die Bedeutung der Ereignisse (was als
Tür, Lüften oder Aufguss gilt) lernt dieses Modul nicht: dafür braucht es die
Rückmeldung eines Menschen.
"""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from .timeline import Kind
from .recognition_model import (
    ALLOWED, CHANNELS, CLOSED, DECAY, INFUSION, MODES, NM, OPEN, RECOVERY, SHIFT,
    ModelParameters, StateModel, is_humidity, partner_temperature, saturation_humidity,
)

INNOVATION_BINS = np.concatenate([[0.0], np.geomspace(0.05, 60.0, 59)])
NU_GRID = (1.5, 2.0, 3.0, 4.0, 6.0, 10.0, 30.0, 100.0)
# steam turbulence κ (level variance per second relative to the squared event rate): likelihood profile grid
KAPPA_GRID = np.geomspace(1e-3, 16.0, 9)
SOURCE_Q_GRID = np.geomspace(1e-7, 1e-2, 11)        # wandering of the moisture source in an opening
STRAT_BINS = np.linspace(-20.0, 20.0, 401)          # histogram of the humidity difference upper − lower (g/m³)
STRAT_LAGS = 400                                    # autocorrelation of the difference over this many steps


def _slope(t, y):
    if len(t) < 3:
        return float("nan")
    u = t - t.mean()
    den = float((u * u).sum())
    return float((u * (y - y.mean())).sum() / den) if den > 0 else float("nan")


def _runs(mask, length):
    idx = np.where(mask)[0]
    if not len(idx):
        return []
    br = np.where(np.diff(idx) > 1)[0]
    starts = np.r_[idx[0], idx[br + 1]]
    ends = np.r_[idx[br], idx[-1]]
    return [slice(a, a + length) for s, e in zip(starts, ends) for a in range(s, e - length, length)]


def _level_var(t, y, a, b):
    """Level process variance per second in [a, b]: var(d) + 2·cov1(d) of detrended increments."""
    m = (t >= a) & (t <= b)
    if m.sum() < 6:
        return None
    d = np.diff(y[m])
    d = d - d.mean()
    dt = float(np.diff(t[m]).mean())
    return float(max((d * d).mean() + 2 * (d[1:] * d[:-1]).mean(), 0.0) / dt)


def _excursion(t, y, start, r0, limit, win):
    """Initial event rate after ``start`` relative to ``r0`` and its 1/e duration (until ``limit``)."""
    w = (t >= start) & (t <= start + win)
    e0 = _slope(t[w], y[w]) - r0
    k = 1
    while start + (k + 1) * win <= limit:
        w = (t >= start + k * win) & (t <= start + (k + 1) * win)
        if abs(_slope(t[w], y[w]) - r0) < abs(e0) / math.e:
            return e0, k * win + win / 2
        k += 1
    return e0, None


def _course(tt, lam, beta, level0, excess0):
    """Two-node exchange course after opening: approach to the mixed equilibrium."""
    k = lam + beta
    equilibrium = level0 - lam * excess0 / max(k, 1e-12)
    return equilibrium + (level0 - equilibrium) * np.exp(-k * tt)


def _golden(f, lo, hi, iterations=60):
    g = (math.sqrt(5) - 1) / 2
    a, b = lo, hi
    c, d = b - g * (b - a), a + g * (b - a)
    fc, fd = f(c), f(d)
    for _ in range(iterations):
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - g * (b - a)
            fc = f(c)
        else:
            a, c, fc = c, d, fd
            d = a + g * (b - a)
            fd = f(d)
    return (a + b) / 2


def _source_basis(tt, lam):
    """Basis des Feuchteverlaufs mit Feuchtequelle: L − außen = A·e^(−λt) + S·(1 − e^(−λt))/λ."""
    decay = np.exp(-lam * tt)
    return np.column_stack([decay, (1 - decay) / lam])


def fit_source_exchange(tt, y, outside):
    """λ und Quellstärke S einer Öffnung (Feuchte) mit den Standardfehlern von log λ und S.

    Anfangsabstand ``A`` und ``S`` sind für festes λ linear; λ folgt aus der
    Profil-Fehlerquadratsumme.  Die Standardfehler stammen aus der gemeinsamen
    Jacobi-Matrix (S und λ hängen über den Gleichgewichtsabstand S/λ zusammen).
    """
    tt = np.asarray(tt, float)
    excess = np.asarray(y, float) - outside

    def profile(log_lam):
        basis = _source_basis(tt, math.exp(log_lam))
        coef, *_ = np.linalg.lstsq(basis, excess, rcond=None)
        r = excess - basis @ coef
        return float(r @ r), coef

    log_lam = _golden(lambda z: profile(z)[0], math.log(1e-6), 0.0)
    sse, coef = profile(log_lam)
    variance = sse / max(len(tt) - 3, 1)
    h = 1e-4
    slope = (_source_basis(tt, math.exp(log_lam + h)) - _source_basis(tt, math.exp(log_lam - h))) @ coef / (2 * h)
    jac = np.column_stack([slope, _source_basis(tt, math.exp(log_lam))])
    info = jac.T @ jac
    if np.linalg.cond(info) > 1e12:
        return math.exp(log_lam), float(coef[1]), math.inf, math.inf
    errors = np.sqrt(np.maximum(np.diag(variance * np.linalg.inv(info)), 0.0))
    return math.exp(log_lam), float(coef[1]), float(errors[0]), float(errors[2])


def fit_decay(tt, y):
    """Abklingen als nachlassende Zusatzsteigung: ``y = a + r·τ + d0·(1 − e^(−κτ))/κ``.

    ``r`` ist die normale Steigung, ``d0`` die Zusatzsteigung am Gipfel, ``κ`` ihre
    Abnahmerate.  ``κ`` per Profil (Goldener Schnitt über log κ), ``a, r, d0``
    linear.  Liefert ``(d0, log κ)`` oder ``None``.
    """
    tt = np.asarray(tt, float)
    y = np.asarray(y, float)
    if len(tt) < 8 or tt[-1] <= 0:
        return None

    def solve(log_k):
        k = math.exp(log_k)
        basis = np.column_stack([np.ones_like(tt), tt, -np.expm1(-k * tt) / k])
        coef, *_ = np.linalg.lstsq(basis, y, rcond=None)
        return coef, float(((basis @ coef - y) ** 2).sum())

    lo, hi = math.log(1.0 / max(tt[-1], 1.0)), math.log(1.0 / max(float(np.median(np.diff(tt))), 1e-3))
    if hi <= lo:
        return None
    log_k = _golden(lambda v: solve(v)[1], lo, hi)
    coef, _ = solve(log_k)
    d0 = float(coef[2])
    if not d0 < 0:
        return None
    return d0, log_k


def fit_exchange(tt, y, excess0, beta=None):
    """Least-squares fit of the exchange coefficient λ (and β, if not given) for one opening."""
    level0 = y[0]

    def sse_lambda(log_lam, b):
        r = y - _course(tt, math.exp(log_lam), b, level0, excess0)
        return float(r @ r)

    if beta is not None:
        log_lam = _golden(lambda z: sse_lambda(z, beta), math.log(1e-6), 0.0)
        return math.exp(log_lam), beta

    def sse_beta(log_beta):
        b = math.exp(log_beta)
        z = _golden(lambda v: sse_lambda(v, b), math.log(1e-6), 0.0, 40)
        return sse_lambda(z, b)

    log_beta = _golden(sse_beta, math.log(1e-6), 0.0, 40)
    b = math.exp(log_beta)
    log_lam = _golden(lambda z: sse_lambda(z, b), math.log(1e-6), 0.0)
    return math.exp(log_lam), b


def exchange_uncertainty(tt, y, excess0, lam, beta) -> float:
    """Standardfehler von log λ einer Öffnung aus der Krümmung der Fehlerquadratsumme."""
    tt = np.asarray(tt, float)
    y = np.asarray(y, float)
    if len(tt) < 3 or lam <= 0:
        return math.inf
    level0 = y[0]
    step = 1e-4
    up = _course(tt, lam * math.exp(step), beta, level0, excess0)
    down = _course(tt, lam * math.exp(-step), beta, level0, excess0)
    jacobian = (up - down) / (2 * step)                        # d course / d log λ
    residual = y - _course(tt, lam, beta, level0, excess0)
    information = float(jacobian @ jacobian)
    if information <= 0:
        return math.inf
    variance = float(residual @ residual) / (len(tt) - 1)
    return math.sqrt(variance / information)


def population(values, errors=None):
    """Verteilung der Ereignisgrößen (Mittel, Streuung) aus Einzelwerten mit Messfehlern.

    Zufallseffektmodell ``θᵢ ~ N(μ, τ² + sᵢ²)`` mit Maximum-Likelihood-Schätzung
    (Fixpunktiteration); ohne Messfehler ist das Mittel und Standardabweichung.
    Einzelwerte mit großem Messfehler (kaum bestimmte Ereignisse) zählen
    entsprechend wenig.
    """
    v = np.asarray(values, float)
    s = np.zeros(len(v)) if errors is None else np.asarray(errors, float)
    ok = np.isfinite(v) & np.isfinite(s)
    v, s = v[ok], s[ok]
    if not len(v):
        return [0.0, 0.0]
    tau2 = float(v.var())
    mu = float(v.mean())
    if not s.any():
        return [mu, math.sqrt(tau2)]
    s = np.maximum(s, 1e-9 * (float(np.abs(v).max()) + 1.0))
    for _ in range(500):
        w = 1.0 / (tau2 + s * s)
        mu = float((w * v).sum() / w.sum())
        new = float(max((w * w * ((v - mu) ** 2 - s * s)).sum() / (w * w).sum(), 0.0))
        if abs(new - tau2) <= 1e-12 * max(tau2, 1e-12):
            tau2 = new
            break
        tau2 = new
    return [mu, math.sqrt(tau2)]


def compact(value, digits: int = 7):
    """Evidence for storage: floats to ``digits`` significant digits (deterministic, JSON-safe)."""
    if isinstance(value, float):
        return float(f"{value:.{digits}g}") if math.isfinite(value) else value
    if isinstance(value, dict):
        return {k: compact(v, digits) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [compact(v, digits) for v in value]
    return value


def empty_evidence(channels=CHANNELS) -> dict:
    c = len(channels)
    return {
        "channels": list(channels),
        "sessions": 0,
        "dt": [],
        "noise_cov1": [],
        "level_sum": [[0.0] * c for _ in MODES],
        "level_count": [0] * NM,
        "open_level_sum": [0.0] * c,
        "open_excess_sum": [0.0] * c,
        "open_count": 0,
        "turbulence": {"infusion": [], "decay": []},
        "prior": {"open": [], "infusion": [], "decay": [], "decay_slope": [], "recovery": []},
        "beta": [],
        "exchange": [],
        "source": [],
        "tau": {"infusion": [], "decay": [], "recovery": []},
        "jumps": [],
        "drift": [],
        "counts": [[0.0] * NM for _ in range(NM)],
        "occupancy": [0.0] * NM,
        "innovations": [[[0] * (len(INNOVATION_BINS)) for _ in range(c)] for _ in MODES],
        "kappa_grid": KAPPA_GRID.tolist(),
        "turbulence_profile": [],
        "source_q_grid": SOURCE_Q_GRID.tolist(),
        "source_profile": [],
        "strat_hist": [[0] * (len(STRAT_BINS) - 1) for _ in MODES],
        "strat_ac": [[0.0] * STRAT_LAGS for _ in MODES],  # Σ lagged products of deviations (lag 0, 1, …)
        "strat_ac_n": [[0] * STRAT_LAGS for _ in MODES],   # their counts
    }


def merge_evidence(*items: dict) -> dict:
    """Combine evidence of several sessions (same channel order)."""
    out = empty_evidence(items[0]["channels"])
    for ev in items:
        out["sessions"] += ev["sessions"]
        out["dt"] += ev["dt"]
        out["noise_cov1"] += ev["noise_cov1"]
        for j in range(NM):
            out["level_sum"][j] = [a + b for a, b in zip(out["level_sum"][j], ev["level_sum"][j])]
            out["level_count"][j] += ev["level_count"][j]
            out["counts"][j] = [a + b for a, b in zip(out["counts"][j], ev["counts"][j])]
            out["occupancy"][j] += ev["occupancy"][j]
            for i in range(len(out["channels"])):
                out["innovations"][j][i] = [a + b for a, b in zip(out["innovations"][j][i], ev["innovations"][j][i])]
        out["open_level_sum"] = [a + b for a, b in zip(out["open_level_sum"], ev["open_level_sum"])]
        out["open_excess_sum"] = [a + b for a, b in zip(out["open_excess_sum"], ev["open_excess_sum"])]
        out["open_count"] += ev["open_count"]
        for key in ("turbulence", "prior", "tau"):
            for name in out[key]:
                out[key][name] += ev[key].get(name, [])
        out["beta"] += ev["beta"]
        out["exchange"] += ev.get("exchange", [])
        out["source"] += ev.get("source", [])
        grid = ev.get("kappa_grid", [])
        if len(grid) == len(out["kappa_grid"]) and np.allclose(grid, out["kappa_grid"]):
            out["turbulence_profile"] += ev.get("turbulence_profile", [])   # profiles on another grid are dropped
        grid = ev.get("source_q_grid", [])
        if len(grid) == len(out["source_q_grid"]) and np.allclose(grid, out["source_q_grid"]):
            out["source_profile"] += ev.get("source_profile", [])
        out["jumps"] += ev["jumps"]
        out["drift"] += ev.get("drift", [])
        for j in range(NM):
            if "strat_hist" in ev:
                out["strat_hist"][j] = [a + b for a, b in zip(out["strat_hist"][j], ev["strat_hist"][j])]
                if len(ev["strat_ac"][j]) == STRAT_LAGS:
                    out["strat_ac"][j] = [a + b for a, b in zip(out["strat_ac"][j], ev["strat_ac"][j])]
                    out["strat_ac_n"][j] = [a + b for a, b in zip(out["strat_ac_n"][j], ev["strat_ac_n"][j])]
    return out


def _labels_to_modes(t, y, channels, labels, win, dt, normal_sd=None):
    """Mode sequence and per-event measurements from labelled events.

    ``normal_sd``: spread of the normal slopes of the closed cabin per channel; the decay
    lasts until its excess slope is within it (without: until the next event).
    """
    c = len(channels)
    mode = np.zeros(len(t), int)
    found = {"open": [], "doors": [], "recovery": [], "infusion": [], "decay": [], "decay_slope": [], "tau": {"infusion": [], "decay": [],
             "recovery": []}, "turbulence": {"infusion": [], "decay": []}, "segments": []}
    events = sorted([(d["open"], "o", d) for d in labels.get("doors", [])]
                    + [(x["t"] if isinstance(x, dict) else x, "i", x) for x in labels.get("infusions", [])],
                    key=lambda z: z[0])
    bounds = [z[0] for z in events] + [t[-1]]
    humid = [i for i, ch in enumerate(channels) if is_humidity(ch)]
    for n, (at, kind, item) in enumerate(events):
        nxt = bounds[n + 1]
        source = item.get("source", "") if isinstance(item, dict) else ""
        pre = (t >= at - 90 * dt) & (t < at - 5 * dt)
        r0 = np.array([_slope(t[pre], y[pre, i]) for i in range(c)])
        if kind == "o":
            close = item.get("close") or nxt
            mode[(t >= at) & (t < close)] = OPEN
            if item.get("close") and close - at >= 2 * win:
                m = (t >= at) & (t <= close)
                found["doors"].append((source, at, close, r0, m))
                found["segments"].append((OPEN, at, close, r0, source))
            if item.get("close"):
                recovery_end = nxt - close
                for i in range(c):
                    r_ref = r0[i] if is_humidity(channels[i]) else 0.0   # temperature: the recovery is the rate
                    e0, tau = _excursion(t, y[:, i], close, r_ref, nxt, win)
                    found["recovery"].append((i, e0, source, at))
                    if tau and i == 0:
                        found["tau"]["recovery"].append(tau)
                        recovery_end = tau
                mode[(t >= close) & (t < min(close + recovery_end, nxt))] = RECOVERY
        else:
            k0 = np.searchsorted(t, at)
            if k0 > 0 and mode[k0 - 1] == OPEN:
                continue
            post = (t >= at) & (t <= min(at + 60 * dt, nxt))
            if post.sum() < 5:
                continue
            for i in humid:
                base = y[pre, i][-10:].mean() if pre.sum() else y[min(k0, len(t) - 1), i]
                seg = y[post, i]
                ip = int(np.argmax(seg))
                tp = float(t[post][ip])
                amp = float(seg[ip] - base)
                if amp <= 0:
                    continue
                rise = max(tp - at, dt)
                found["infusion"].append((i, math.log(amp / rise), source, at))
                kap = _level_var(t, y[:, i], at, tp)
                if kap is not None:
                    found["turbulence"]["infusion"].append((i, kap / (amp / rise) ** 2, source))
                first = i == humid[0]
                if first:
                    found["tau"]["infusion"].append(rise)
                    mode[(t >= at) & (t < tp)] = INFUSION
                    found["segments"].append((INFUSION, at, tp, r0, source))
                after = (t >= tp) & (t <= nxt)
                fit = fit_decay(t[after] - tp, y[after, i])
                td = None
                if fit is not None:
                    d0, log_k = fit
                    found["decay"].append((i, log_k, source, at))
                    found["decay_slope"].append((i, d0, source, at))
                    kappa_rate = math.exp(log_k)
                    td = float(t[after][-1] - tp)
                    if normal_sd is not None and normal_sd[i] > 0 and abs(d0) > normal_sd[i]:
                        td = min(td, math.log(abs(d0) / normal_sd[i]) / kappa_rate)
                    kap = _level_var(t, y[:, i], tp, tp + td)
                    if kap is not None:
                        found["turbulence"]["decay"].append((i, kap / d0 ** 2, source))
                    if first:
                        found["tau"]["decay"].append(1.0 / kappa_rate)
                        found["segments"].append((DECAY, tp, tp + td, r0, source))
                if first:
                    mode[(t >= tp) & (t < (tp + td if td else nxt))] = DECAY
    return mode, found


def collect_evidence(times, values, channels, labels, *, outside=None, exclude=(), session="") -> dict:
    """Belege einer Sitzung aus Messwerten (Sekunden, Kanalwerte) und Ereignissen.

    ``labels`` = {"doors": [{"open", "close", "ventilated", "exchanged", "source"}],
    "infusions": [{"t", "source"} | float]} in Sekunden.  ``exclude`` sind weitere
    Ereigniszeiten, die ruhige Abschnitte begrenzen (z. B. Erkennungen, die in der
    Referenz fehlen).  ``session`` kennzeichnet die Sitzung; zusammen mit der
    Ereigniszeit ordnet es die Kanalwerte eines Ereignisses einander zu.
    """
    t = np.asarray(times, float)
    y = np.asarray(values, float)
    channels = list(channels)
    c = len(channels)
    ev = empty_evidence(channels)
    if len(t) < 300:
        return ev
    ev["sessions"] = 1
    out = np.asarray(outside if outside is not None else y[:30].mean(0), float)
    dt = float(np.median(np.diff(t)))
    ev["dt"].append(dt)
    win = 15 * dt
    mode, _ = _labels_to_modes(t, y, channels, labels, win, dt)
    quiet = []
    for a0 in np.arange(t[0], t[-1] - 60.0, 60.0):
        m = (t >= a0) & (t < a0 + 60.0)
        if m.sum() > 20 and (mode[m] == CLOSED).all():
            quiet.append([_slope(t[m], y[m, i]) for i in range(c)])
    normal_sd = np.std(quiet, axis=0) if len(quiet) >= 10 else None
    mode, found = _labels_to_modes(t, y, channels, labels, win, dt, normal_sd)
    # noise scale of every channel (humidity relative to saturation at the same sensor)
    partner = [channels.index(partner_temperature(ch)) if is_humidity(ch) else -1 for ch in channels]
    scale = np.ones_like(y)
    for i, j in enumerate(partner):
        if j >= 0:
            scale[:, i] = np.array([saturation_humidity(v) for v in y[:, j]]) / 100
    for s in _runs(mode == CLOSED, 120):
        d = np.diff(y[s], axis=0) / scale[s][1:]
        d = d - d.mean(0)
        ev["noise_cov1"].append((d[1:] * d[:-1]).mean(0).tolist())
    for j in range(NM):
        for s in _runs(mode == j, 6):
            sc = np.ones_like(y[s][1:]) if j == OPEN else scale[s][1:]
            d = np.diff(y[s], axis=0) / sc
            d = d - d.mean(0)
            v = ((d * d).mean(0) + 2 * (d[1:] * d[:-1]).mean(0)) / float(np.diff(t[s]).mean())
            if j == OPEN:
                ev["open_level_sum"] = (np.asarray(ev["open_level_sum"]) + v).tolist()
                ev["open_excess_sum"] = (np.asarray(ev["open_excess_sum"]) + ((y[s] - out) ** 2).mean(0)).tolist()
                ev["open_count"] += 1
            else:
                ev["level_sum"][j] = (np.asarray(ev["level_sum"][j]) + v).tolist()
                ev["level_count"][j] += 1
    # exchange of every opening: β from long openings now; λ once β is pooled over all sessions
    # (``fit_exchange_evidence``), from the compact course kept until then
    ev["_courses"] = []
    for source, at, close, r0, m in found["doors"]:
        key = f"{session}@{at:.1f}"
        for i in range(c):
            tt = t[m] - at
            yy = y[m, i] - r0[i] * tt
            excess0 = float(yy[0] - out[i])
            if abs(excess0) < 1e-9:
                continue
            if close - at >= 3 * win and not is_humidity(channels[i]):
                ev["beta"].append([i, float(fit_exchange(tt, yy, excess0)[1]), source])
            ev["_courses"].append([i, tt, yy, excess0, source, key])
    for name in ("recovery", "infusion", "decay", "decay_slope"):
        for i, value, source, at in found[name]:
            ev["prior"].setdefault(name, []).append([i, float(value), source, f"{session}@{at:.1f}"])
    for name in ("infusion", "decay"):
        for i, value, source in found["turbulence"][name]:
            ev["turbulence"][name].append([i, float(value), source])
    for name in ("infusion", "decay", "recovery"):
        ev["tau"][name] += [float(v) for v in found["tau"][name]]
    # heater changes: slope differences of consecutive quiet closed minutes, with distance to every event
    marks = sorted([d["open"] for d in labels.get("doors", [])] + [d["close"] for d in labels.get("doors", [])
                   if d.get("close")] + [x["t"] if isinstance(x, dict) else x for x in labels.get("infusions", [])]
                   + list(exclude))
    marks = np.array(marks, float)
    slopes, dist = [], []
    for a0 in np.arange(t[0], t[-1] - 60.0, 60.0):
        m = (t >= a0) & (t < a0 + 60.0)
        quiet = m.sum() > 20 and (mode[m] == CLOSED).all()
        slopes.append([_slope(t[m], y[m, i]) for i in range(c)] if quiet else [float("nan")] * c)
        dist.append(float(np.min(np.abs(marks - (a0 + 30.0)))) if len(marks) else float("inf"))
    slopes = np.array(slopes)
    if len(slopes) > 1:
        diff = np.diff(slopes, axis=0)
        d2 = np.minimum(np.array(dist)[1:], np.array(dist)[:-1])
        ok = np.isfinite(diff).all(1)
        ev["jumps"] += [row + [float(dd)] for row, dd in zip(diff[ok].tolist(), d2[ok])]
    # drift distribution of the closed cabin: slopes of quiet minutes (window index for the autocorrelation)
    for k, (row, dd) in enumerate(zip(slopes.tolist(), dist)):
        if all(math.isfinite(v) for v in row):
            ev["drift"].append([session, k] + row + [float(dd)])
    # stratification: humidity difference upper − lower per labelled state and its correlation time
    if "Au" in channels and "Al" in channels:
        diff = y[:, channels.index("Au")] - y[:, channels.index("Al")]
        for j in range(NM):
            hist, _ = np.histogram(diff[mode == j], bins=STRAT_BINS)
            ev["strat_hist"][j] = [a + int(b) for a, b in zip(ev["strat_hist"][j], hist)]
            if not (mode == j).any():
                continue
            centre = float(np.median(diff[mode == j]))       # deviation from the state's level, not the run mean
            for s in _runs(mode == j, 10):
                d = diff[s] - centre
                lags = min(STRAT_LAGS, len(d))
                prod = np.correlate(d, d, mode="full")[len(d) - 1:len(d) - 1 + lags]
                ev["strat_ac"][j] = [a + float(b) for a, b in zip(ev["strat_ac"][j], prod)] + ev["strat_ac"][j][lags:]
                ev["strat_ac_n"][j] = ([a + len(d) - k for k, a in enumerate(ev["strat_ac_n"][j][:lags])]
                                       + ev["strat_ac_n"][j][lags:])
    dts = np.diff(t)
    for j in range(NM):
        ev["occupancy"][j] += float(dts[mode[:-1] == j].sum())
    for k in np.where(mode[1:] != mode[:-1])[0]:
        ev["counts"][mode[k]][mode[k + 1]] += 1
    ev["_mode"] = mode            # transient: used by likelihood_evidence, removed before storing
    ev["_outside"] = out.tolist()
    ev["_segments"] = found["segments"]
    return ev


def pooled_beta(evidence: dict, *, base: dict, rejected=frozenset()) -> np.ndarray:
    """Nachschub aus Wand und Steinen je Kanal: Median über die langen Öffnungen."""
    c = len(evidence["channels"])
    beta = np.zeros(c)
    for i in range(c):
        v = [b for k, b, s in evidence["beta"] if k == i and not _rejected(s, rejected)]
        beta[i] = float(np.median(v)) if v else float(np.asarray(base["beta"])[i])
    return beta


def fit_exchange_evidence(evidence: dict, beta) -> None:
    """Austausch jeder Öffnung; ersetzt die Verläufe durch ``exchange``- und ``source``-Belege.

    Temperatur: λ mit festem β (Zweiknotenverlauf).  Feuchte: λ und Quellstärke S.
    Ein Beleg ist ``[Kanal, Wert, Standardfehler, Quelle, Ereignis]`` (``exchange``: log λ).
    """
    channels = evidence["channels"]
    for i, tt, yy, excess0, source, key in evidence.pop("_courses", []):
        tt, yy = np.asarray(tt, float), np.asarray(yy, float)
        if is_humidity(channels[i]):
            lam, strength, error, error_source = fit_source_exchange(tt, yy, float(yy[0]) - excess0)
            evidence["source"].append([i, strength, error_source if math.isfinite(error_source) else None,
                                       source, key])
        else:
            lam = fit_exchange(tt, yy, excess0, float(beta[i]))[0]
            error = exchange_uncertainty(tt, yy, excess0, lam, float(beta[i]))
        evidence["exchange"].append([i, math.log(max(lam, 1e-9)), error if math.isfinite(error) else None,
                                     source, key])


def labels_from_detections(detections, origin, source) -> dict:
    """Ereignisse einer Erkennung als Lernmarken (Sekunden seit ``origin``).

    ``source(detection)`` liefert die Kennung, unter der eine spätere
    Rückmeldung das Ereignis ablehnen kann.
    """
    doors, infusions = [], []
    current = None
    for detection in sorted(detections, key=lambda d: (d.detected_at, d.effective_at)):
        at = (detection.effective_at - origin).total_seconds()
        kind = Kind(detection.kind)
        if kind is Kind.DOOR_OPEN:
            current = {"open": at, "close": None, "ventilated": None, "exchanged": None,
                       "source": source(detection)}
            doors.append(current)
        elif kind is Kind.DOOR_CLOSE and current is not None:
            current["close"] = at
            current = None
        elif kind is Kind.VENTILATION and current is not None:
            current["ventilated"] = at
        elif kind is Kind.VENTILATION_COMPLETE and current is not None:
            current["exchanged"] = at
        elif kind is Kind.INFUSION:
            infusions.append({"t": at, "source": source(detection)})
    return {"doors": doors, "infusions": infusions}


def session_evidence(recognizer, params: dict, *, session: str, source) -> dict | None:
    """Belege einer abgeschlossenen Sitzung aus ihrer eigenen Erkennung (Nachlernen)."""
    record = recognizer.record
    if record is None or recognizer.outside is None or len(record.times) < 300:
        return None
    labels = labels_from_detections(recognizer.detections, recognizer.origin, source)
    exclude = [(u.end.effective_at - recognizer.origin).total_seconds() for u in getattr(recognizer, "updates", ())]
    ev = collect_evidence(record.times, record.values, recognizer.channels, labels,
                          outside=recognizer.outside, exclude=exclude, session=session)
    if not ev["sessions"]:
        return None
    fit_exchange_evidence(ev, np.asarray(params["beta"], float))
    # the turbulence profiles stay with the learning recordings (their computation is too costly per session)
    likelihood_evidence(ev, params, record.times, record.values, turbulence=False)
    return ev


def _window_loglik(model_parameters: ModelParameters, outside, t, y, window) -> float | None:
    """Log-Evidenz eines Fensters um ein Ereignis im vollständigen Modell (die Zustände entscheidet das Modell)."""
    start, end, r0 = window
    k0 = max(int(np.searchsorted(t, start)), 0)
    k1 = int(np.searchsorted(t, end, side="right"))
    if k1 - k0 < 3:
        return None
    model = StateModel(model_parameters, y[k0], outside)
    model.x[:, model.ir] = r0                      # drift before the window
    total = 0.0
    for k in range(k0 + 1, k1):
        dt = t[k] - t[k - 1]
        if dt <= 0:
            continue
        model.step(dt, y[k])
        total += model.log_evidence
    return total


def _with_turbulence(p: ModelParameters, mode: int, channel: int, kappa: float) -> ModelParameters:
    ql = p.ql.copy()
    ql[mode, channel] = kappa
    return replace(p, ql=ql)


def likelihood_evidence(evidence: dict, params: dict, times, values, *, turbulence: bool = True) -> None:
    """Belege, die das aktuelle Modell brauchen: Innovationen je Zustand und Turbulenzprofile.

    Innovationen: Histogramm der standardisierten Innovationen je bezeichnetem
    Zustand (für die Student-t-Schätzung).  Turbulenz (``turbulence``):
    Log-Evidenz eines Fensters um jeden bezeichneten Aufguss über ``KAPPA_GRID``
    je Zustand und Feuchtekanal; rechenaufwendig, darum nur beim Erstlernen.
    """
    mode = evidence.pop("_mode", None)
    outside = evidence.pop("_outside", None)
    segments = evidence.pop("_segments", [])
    if mode is None or not evidence["sessions"]:
        return
    channels = tuple(evidence["channels"])
    gaussian = dict(params)
    gaussian["nu"] = (np.full((NM, len(params["channels"])), 1e6)).tolist()
    gaussian["nus"] = (np.ones((NM, len(params["channels"])))).tolist()
    model = StateModel(ModelParameters.from_dict(gaussian, channels), values[0], outside)
    t = np.asarray(times, float)
    y = np.asarray(values, float)
    hist = np.asarray(evidence["innovations"])
    for k in range(1, len(t)):
        dt = t[k] - t[k - 1]
        if dt <= 0:
            continue
        model.step(dt, y[k])
        j = int(mode[k])
        z = model.last_standardised[j]
        idx = np.clip(np.searchsorted(INNOVATION_BINS, np.abs(z), side="right") - 1, 0, len(INNOVATION_BINS) - 1)
        for i, b in enumerate(idx):
            hist[j, i, b] += 1
    evidence["innovations"] = hist.astype(int).tolist()
    if not turbulence:
        return
    # steam turbulence: evidence of a window around every labelled infusion (the model itself decides
    # where rise and decay are), as a function of κ of one state and channel
    humid = [i for i, ch in enumerate(channels) if is_humidity(ch)]
    dt = float(np.median(np.diff(t)))
    p = ModelParameters.from_dict(params, channels)
    for kind, start, end, r0, source in segments:
        if kind == INFUSION:
            window = (start - 10 * dt, start + 60 * dt, r0)
            for j, name in ((INFUSION, "infusion"), (DECAY, "decay")):
                for i in humid:
                    profile = [_window_loglik(_with_turbulence(p, j, i, float(kappa)), outside, t, y, window)
                               for kappa in KAPPA_GRID]
                    if any(v is None or not math.isfinite(v) for v in profile):
                        continue
                    evidence["turbulence_profile"].append([name, i, profile, source])
        elif kind == OPEN and humid:
            # moisture source of the opening: evidence of the window around the door over its wandering
            window = (start - 10 * dt, end + 30 * dt, r0)
            profile = [_window_loglik(replace(p, source_q=float(q)), outside, t, y, window)
                       for q in SOURCE_Q_GRID]
            if any(v is None or not math.isfinite(v) for v in profile):
                continue
            evidence["source_profile"].append([profile, source])


def stratification(evidence: dict) -> list | None:
    """Verteilung der Feuchtedifferenz oben − unten je Zustand: Lage, Skala, ν und Korrelationszeit.

    Student-t per Maximum-Likelihood über das Histogramm (Lage Median, Skala per EM
    je ν).  Die Korrelationszeit folgt aus der Autokorrelation aufeinanderfolgender
    Schritte; sie gewichtet die Differenz im Modell (ein langsam veränderlicher Wert
    zählt nicht bei jedem Messschritt neu).
    """
    hists = np.asarray(evidence.get("strat_hist") or [], float)
    if not len(hists):
        return None
    centres = (STRAT_BINS[:-1] + STRAT_BINS[1:]) / 2
    dt = float(np.median(evidence["dt"])) if evidence["dt"] else 1.0
    out = []
    for j in range(NM):
        counts = hists[min(j, len(hists) - 1)] if j != SHIFT else hists[CLOSED]
        if counts.sum() < 200:
            out.append(None)
            continue
        cum = np.cumsum(counts) / counts.sum()
        loc = float(centres[np.searchsorted(cum, 0.5)])
        u = centres - loc
        best = None
        for nu in NU_GRID:
            s2 = float((counts * u ** 2).sum() / counts.sum()) or 1e-4
            for _ in range(50):
                w = (nu + 1) / (nu + u ** 2 / s2)
                s2 = max(float((counts * w * u ** 2).sum() / counts.sum()), 1e-6)
            ll = float((counts * (math.lgamma((nu + 1) / 2) - math.lgamma(nu / 2) - 0.5 * math.log(nu * math.pi * s2)
                                  - (nu + 1) / 2 * np.log1p(u ** 2 / (nu * s2)))).sum())
            if best is None or ll > best[0]:
                best = (ll, nu, math.sqrt(s2))
        # integrated autocorrelation time: dt·(1 + 2·Σ ρ_k) up to the first lag where ρ is no longer positive
        sums = np.asarray(evidence["strat_ac"][j if j != SHIFT else CLOSED], float)
        counts_k = np.asarray(evidence["strat_ac_n"][j if j != SHIFT else CLOSED], float)
        ok = counts_k > 0
        rho = np.where(ok, sums / np.maximum(counts_k, 1), 0.0)
        rho = rho / rho[0] if rho[0] > 0 else rho
        cut = int(np.argmax(rho[1:] <= 0)) + 1 if (rho[1:] <= 0).any() else len(rho)
        tau = dt * (1.0 + 2.0 * float(rho[1:cut].sum()))
        out.append([loc, best[2], best[1], tau])
    return out


def _fit_student(hist: np.ndarray):
    """ν and scale of a symmetric Student-t from a histogram of |z| (bin centres)."""
    centres = np.concatenate([(INNOVATION_BINS[:-1] + INNOVATION_BINS[1:]) / 2, [INNOVATION_BINS[-1]]])
    counts = np.asarray(hist, float)
    if counts.sum() < 200:
        return None
    best = None
    for nu in NU_GRID:
        s2 = float((counts * centres ** 2).sum() / counts.sum()) or 1.0
        for _ in range(50):                                   # EM for the scale at fixed ν
            w = (nu + 1) / (nu + centres ** 2 / s2)
            s2 = float((counts * w * centres ** 2).sum() / counts.sum())
        ll = float((counts * (math.lgamma((nu + 1) / 2) - math.lgamma(nu / 2) - 0.5 * math.log(nu * math.pi * s2)
                              - (nu + 1) / 2 * np.log1p(centres ** 2 / (nu * s2)))).sum())
        if best is None or ll > best[0]:
            best = (ll, nu, math.sqrt(s2))
    return best[1], best[2]


def base_parameters(channels=CHANNELS) -> dict:
    """Festlegungen, die nicht gelernt werden, und neutrale Startwerte für fehlende Belege."""
    c = len(channels)
    return {
        "version": 1,
        "channels": list(channels),
        "modes": list(MODES),
        # decision: probability threshold and confirmation horizon in measurement steps
        "decision": {"evidence_ratio": 150.0, "flat_min_samples": 20, "flat_rise_fraction": 0.05},
        # random-walk intensities chosen by model evidence on the training recordings
        "qd": [1e-6] * c,
        "qlam": 1e-3,
        "sv": [0.01] * c,
        "ql": [[0.0] * c for _ in MODES],
        "prior": [[[0.0, 0.0]] * c for _ in MODES],
        "beta": [0.0] * c,
        "qe": [[0.0] * c for _ in MODES],
        "rates": [[0.0] * NM for _ in MODES],
        "jump": [0.0] * c,
        "nu": [[1e6] * c for _ in MODES],
        "nus": [[1.0] * c for _ in MODES],
        "zcorr": [np.eye(c).tolist() for _ in MODES],
        "source": [[0.0, 0.0] for _ in range(c)],
        "source_corr": np.eye(c).tolist(),
        "source_q": 0.0,
    }


def _profile_maximum(total, grid_values=KAPPA_GRID) -> float:
    """Maximum einer Log-Likelihood über einem logarithmischen Gitter (Parabel um den besten Gitterpunkt)."""
    total = np.asarray(total, float)
    values = np.asarray(grid_values, float)
    k = int(np.argmax(total))
    grid = np.log(values)
    if 0 < k < len(grid) - 1:
        a, b, c = total[k - 1], total[k], total[k + 1]
        curvature = a - 2 * b + c
        if curvature < 0:
            shift = 0.5 * (a - c) / curvature
            return float(math.exp(grid[k] + shift * (grid[k + 1] - grid[k])))
    return float(values[k])


def drift_distribution(rows, c: int, margin: float):
    """Verteilung der Grunddrift (Mittel, Streuung, Rückkehrzeit) aus Steigungen ruhiger Minuten.

    Zeilen ``[Sitzung, Fensterindex, Steigung je Kanal…, Abstand zum nächsten
    Ereignis]``; verwendet werden Minuten jenseits ``margin``.  Die Rückkehrzeit
    folgt aus der Autokorrelation aufeinanderfolgender Minuten.
    """
    rows = [r for r in rows if r[2 + c] > margin]
    if len(rows) < 50:
        return None
    values = np.array([r[2:2 + c] for r in rows], float)
    index = {(r[0], r[1]): r[2:2 + c] for r in rows}
    pairs = np.array([(index[key], index[(key[0], key[1] + 1)]) for key in index if (key[0], key[1] + 1) in index],
                     float)
    tau = [None] * c                                   # None: no return (free random walk)
    if len(pairs) >= 30:
        for i in range(c):
            rho = float(np.corrcoef(pairs[:, 0, i], pairs[:, 1, i])[0, 1])
            if 0 < rho < 1:
                tau[i] = -60.0 / math.log(rho)
    return values.mean(0).tolist(), values.std(0).tolist(), tau


def _rejected(entry_source, rejected):
    return bool(entry_source) and entry_source in rejected


def _mad(values) -> float:
    v = np.asarray(values, float)
    return float(1.4826 * np.median(np.abs(v - np.median(v))))


def event_correlation(entries, c: int) -> np.ndarray:
    """Korrelation der Ereignisgrößen der Kanäle aus Werten je Ereignis.

    ``entries`` = [(Kanal, Wert, Ereignisschlüssel)].  Robuste Paarkorrelation
    (Gnanadesikan–Kettenring mit MAD-Skalen), danach die nächste positiv definite
    Korrelationsmatrix.  Kanalpaare mit weniger als acht gemeinsamen Ereignissen
    bleiben unkorreliert.
    """
    by_event: dict = {}
    for i, value, key in entries:
        if key is not None and math.isfinite(value):
            by_event.setdefault(key, {})[i] = value
    r = np.eye(c)
    for a in range(c):
        for b in range(a + 1, c):
            pairs = np.array([(d[a], d[b]) for d in by_event.values() if a in d and b in d])
            if len(pairs) < 8:
                continue
            u = (pairs[:, 0] - np.median(pairs[:, 0])) / max(_mad(pairs[:, 0]), 1e-12)
            w = (pairs[:, 1] - np.median(pairs[:, 1])) / max(_mad(pairs[:, 1]), 1e-12)
            plus, minus = _mad(u + w) ** 2, _mad(u - w) ** 2
            if plus + minus > 0:
                r[a, b] = r[b, a] = (plus - minus) / (plus + minus)
    values, vectors = np.linalg.eigh(r)
    r = vectors @ np.diag(np.maximum(values, 1e-3)) @ vectors.T
    d = np.sqrt(np.diag(r))
    return r / np.outer(d, d)


def estimate(evidence: dict, *, base: dict, rejected=frozenset()) -> dict:
    """Modellgrößen aus den Belegen; nicht gelernte Festlegungen kommen aus ``base``."""
    channels = evidence["channels"]
    c = len(channels)
    params = {k: v for k, v in base.items() if k != "evidence"}
    params["channels"] = list(channels)
    cov1 = np.asarray(evidence["noise_cov1"], float)
    params["sv"] = np.sqrt(np.maximum(-np.median(cov1, 0), 1e-12)).tolist() if len(cov1) else base["sv"]
    ql = []
    for j in range(NM):
        n = evidence["level_count"][j]
        ql.append((np.maximum(np.asarray(evidence["level_sum"][j]) / n, 0)).tolist() if n else [0.0] * c)
    if evidence["open_count"]:
        opened = (np.asarray(evidence["open_level_sum"]) - evidence["open_count"] * np.asarray(ql[CLOSED]))
        ql[OPEN] = np.maximum(opened / np.maximum(np.asarray(evidence["open_excess_sum"]), 1e-12), 0).tolist()
    for name, j in (("infusion", INFUSION), ("decay", DECAY)):
        for i in range(c):
            profiles = [ll for m, k, ll, s in evidence.get("turbulence_profile", [])
                        if m == name and k == i and not _rejected(s, rejected)]
            if profiles:
                ql[j][i] = _profile_maximum(np.sum(profiles, axis=0))
                continue
            v = [x for k, x, s in evidence["turbulence"][name] if k == i and not _rejected(s, rejected)]
            if v:
                ql[j][i] = float(np.median(v))
    params["ql"] = ql
    beta = pooled_beta(evidence, base=base, rejected=rejected)
    params["beta"] = beta.tolist()
    prior = np.zeros((NM, c, 2))
    source = np.asarray(base.get("source", [[0.0, 0.0]] * c), float).copy()
    sizes = {OPEN: [], INFUSION: [], DECAY: [], RECOVERY: []}      # (channel, value, event) for the correlation
    for i in range(c):
        lams, errors = [], []
        for k, log_lam, error, s, key in evidence["exchange"]:
            if k != i or _rejected(s, rejected) or error is None:
                continue
            lams.append(log_lam)
            errors.append(error)
            sizes[OPEN].append((i, log_lam, key))
        prior[OPEN, i] = population(lams, errors) if lams else np.asarray(base["prior"])[OPEN, i]
        strengths = [(x, e) for k, x, e, s, key in evidence.get("source", [])
                     if k == i and e is not None and not _rejected(s, rejected)]
        if strengths:
            source[i] = population([x for x, _ in strengths], [e for _, e in strengths])
        for name, j in (("infusion", INFUSION), ("decay", DECAY), ("recovery", RECOVERY)):
            v = []
            for k, x, s, key in evidence["prior"][name]:
                if k == i and not _rejected(s, rejected):
                    v.append(x)
                    sizes[j].append((i, x, key))
            if v:
                prior[j, i] = population(v)
    params["prior"] = prior.tolist()
    params["source"] = source.tolist()
    decay_slope = np.zeros((c, 2))
    for i in range(c):
        v = [x for k, x, s, key in evidence["prior"].get("decay_slope", []) if k == i and not _rejected(s, rejected)]
        if len(v) >= 3:
            decay_slope[i] = population(v)
    params["decay_slope"] = decay_slope.tolist()
    # the moisture source of an opening at both sensors (wood, stones: one cause); its wandering within it
    source_entries = [(k, x, key) for k, x, e, s, key in evidence.get("source", []) if not _rejected(s, rejected)]
    params["source_corr"] = event_correlation(source_entries, c).tolist()
    profiles = [ll for ll, s in evidence.get("source_profile", []) if not _rejected(s, rejected)]
    if profiles:
        params["source_q"] = _profile_maximum(np.sum(profiles, axis=0), SOURCE_Q_GRID)
    # one cause per event (one door, one steam source): correlation of the event sizes across channels
    zcorr = [np.eye(c) for _ in MODES]
    for j, entries in sizes.items():
        zcorr[j] = event_correlation(entries, c)
    params["zcorr"] = [m.tolist() for m in zcorr]
    qe = np.zeros((NM, c))
    for name, j in (("infusion", INFUSION), ("decay", DECAY), ("recovery", RECOVERY)):
        taus = evidence["tau"][name]
        if taus:
            log_rate = np.array([is_humidity(ch) and j in (INFUSION, DECAY) for ch in channels])   # log κ for the decay
            qe[j] = np.where(log_rate, 1.0, prior[j, :, 0] ** 2) / float(np.mean(taus))
    params["qe"] = qe.tolist()
    counts = np.asarray(evidence["counts"], float)
    occupancy = np.asarray(evidence["occupancy"], float)
    rates = np.zeros((NM, NM))
    for a, b in ALLOWED:
        rates[a, b] = counts[a, b] / occupancy[a] if occupancy[a] > 0 else 0.0
    for a, b in ALLOWED:
        if rates[a, b] == 0 and a != CLOSED:
            rates[a, b] = rates[CLOSED, b]
    # heater changes: two-component mixture of slope changes beyond the recovery after doors
    taus = evidence["tau"]["recovery"]
    margin = (float(np.mean(taus)) if taus else 0.0) + 90.0
    jumps = np.asarray([row[:c] for row in evidence["jumps"] if row[c] > margin], float)
    jump = np.asarray(base["jump"], float).copy()
    rate_jump = float(np.asarray(base["rates"])[CLOSED, SHIFT])
    if len(jumps) >= 50:
        weights = []
        for i in range(c):
            d = jumps[:, i]
            s1, s2, pi = float(np.median(np.abs(d))) or 1e-6, 5 * (float(np.median(np.abs(d))) or 1e-6), 0.1
            for _ in range(200):
                l1 = (1 - pi) * np.exp(-0.5 * d * d / s1 ** 2) / s1
                l2 = pi * np.exp(-0.5 * d * d / s2 ** 2) / s2
                g = l2 / (l1 + l2)
                pi = float(g.mean())
                s2 = float(np.sqrt((g * d * d).sum() / max(g.sum(), 1e-12)))
                s1 = float(np.sqrt(((1 - g) * d * d).sum() / max((1 - g).sum(), 1e-12)))
            jump[i] = s2
            weights.append(pi)
        rate_jump = float(np.mean(weights)) / 60.0
    params["jump"] = jump.tolist()
    rates[CLOSED, SHIFT] = rate_jump
    drift = drift_distribution(evidence["drift"], c, margin)
    if drift is not None:
        params["drift_mean"], params["drift_sd"], params["drift_tau"] = drift
    dts = evidence["dt"]
    rates[SHIFT, CLOSED] = 1.0 / (float(np.median(dts)) if dts else 1.0)
    params["rates"] = rates.tolist()
    params["strat"] = stratification(evidence)
    hist = np.asarray(evidence["innovations"], float)
    nu = np.asarray(base["nu"], float).copy()
    nus = np.asarray(base["nus"], float).copy()
    for j in range(NM):
        for i in range(c):
            fit = _fit_student(hist[j, i])
            if fit is not None:
                nu[j, i], nus[j, i] = fit
    params["nu"] = nu.tolist()
    params["nus"] = nus.tolist()
    return params
