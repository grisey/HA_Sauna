"""Gelernte Vergleichsformen der Ereignisse (V07).

Eine Form beschreibt den Verlauf aller vier Kanäle (Temperatur und absolute
Feuchte, oben und unten) um ein Ereignis, relativ zu seinem Knick: Öffnung mit
Abflachen und Konvektion, Schluss mit Erholung, Aufguss mit Abklingen, Lüften,
Durchgelüftet und Störung.  Je Kanal ist der Verlauf eine Basis (Spalten:
Niveau, Steigungen, Größen) mit gelernten Raten ``(k, p)``.

Einmal gemeinsam über alle Ereignisse der Lernmenge gelernt und danach fest
sind:

* die Raten je Kanal und die Streuung eines gemeinsamen Ratenfaktors je
  Ereignis (eine Tür, eine Dampfquelle: alle Kanäle zugleich schneller oder
  langsamer),
* die Verteilung der Koeffizienten über alle Kanäle (Mittel und Kovarianz,
  Zufallseffektmodell mit den Schätzfehlern der Ereignisse, wahlweise relativ
  zum Abstand des Kanals von der Außenluft); das Niveau ist frei,
* das Rauschen um die Form je Kanal vor und nach dem Knick mit seiner
  Autokorrelation und die Verteilung eines Rauschfaktors je Ereignis.

Daraus folgt die Randlikelihood eines Abschnitts unter einer Form
(``log_evidence``): Niveau flach, Koeffizienten, Ratenfaktor und Rauschfaktor
über ihre gelernten Verteilungen integriert.  Das Verhältnis der
Randlikelihoods zweier Formen über denselben Abschnitt ist der Bayes-Faktor der
Erklärungen (V03).  Die Fenster ergeben sich aus den bezeichneten Ereignissen
und der gelernten mittleren Zeit bis zum nächsten Heizwechsel.  Untypisch heißt
ein Ereignis, dessen Ratenfaktor und Koeffizienten oder dessen Rauschfaktoren
mit mehr als dem Evidenzverhältnis außerhalb der gelernten Streuung liegen.
Das Modul enthält keine festgelegten Schwellen.
"""

from __future__ import annotations

import math

import numpy as np

from .recognition_model import CHANNELS, closing_basis, is_humidity, opening_basis

# rates (1/s) of the forms: from far below the inverse of the longest window (the
# form is linear there) to far above the inverse sampling interval (a step)
RATE_GRID = np.geomspace(1 / 200000, 200.0, 62)
RATE_STEP = math.log(RATE_GRID[1] / RATE_GRID[0])


def slope_basis(u, k: float = 0.0, p: float = 0.0) -> np.ndarray:
    """Steigungswechsel am Knick ohne eigene Raten.  Spalten: Niveau, Steigung davor, danach."""
    u = np.asarray(u, float)
    return np.column_stack([np.ones_like(u), np.minimum(u, 0), np.maximum(u, 0)])


def infusion_basis(u, k: float, p: float) -> np.ndarray:
    """Vergleichsform der Feuchte bei einem Aufguss zur Zeit ``u`` relativ zum Guss.

    Vor dem Guss läuft die geschlossene Kabine mit ihrer Steigung.  Ab dem Guss
    steigt die Feuchte mit der Rate ``k`` auf ein Plateau; dazu kommt die
    Dampfspitze, die steil ansteigt und mit der Rate ``p`` abklingt (Gammapuls
    ``p·v·e^(−p·v)``, Gipfel bei ``1/p``), und die Steigung der geschlossenen
    Kabine danach.  Weder das Niveau noch die Steigung vor dem Guss werden
    danach erwartet (V14).  Spalten: Niveau, Steigung davor, Steigung danach,
    Plateau, Dampfspitze.
    """
    u = np.asarray(u, float)
    v = np.maximum(u, 0)
    return np.column_stack(
        [
            np.ones_like(u),
            np.minimum(u, 0),
            v,
            -np.expm1(-k * v),
            p * v * np.exp(-p * v),
        ]
    )


RATELESS = (slope_basis,)

# basis and coefficient names (without the level) of every form, for temperature
# ("T") and absolute humidity ("A"); a pour shows no fall of the temperature (V14);
# a disturbance has no shape of its own, only a learned size of the apparent change
# of the slope and of the fluctuation (Verwirbelung, Wedeln, Rauschen)
FORMS = {
    "opening": {"T": (opening_basis, ("e", "s")), "A": (opening_basis, ("e", "s"))},
    "closing": {"T": (closing_basis, ("s", "e")), "A": (closing_basis, ("s", "e"))},
    "infusion": {
        "T": (slope_basis, ("e", "r")),
        "A": (infusion_basis, ("e", "r", "a", "g")),
    },
    "ventilation": {"T": (closing_basis, ("s", "e")), "A": (closing_basis, ("s", "e"))},
    "exchange": {"T": (closing_basis, ("s", "e")), "A": (closing_basis, ("s", "e"))},
    "disturbance": {"T": (slope_basis, ("e", "r")), "A": (slope_basis, ("e", "r"))},
}


def bases(form: str, channels=CHANNELS) -> list:
    """Basis und Koeffizientennamen je Kanal."""
    return [FORMS[form]["A" if is_humidity(c) else "T"] for c in channels]


def columns_needed(form: str, channels=CHANNELS) -> int:
    """Spalten der größten Basis (Mindestzahl der Werte je Seite des Knicks)."""
    return max(len(names) + 1 for _, names in bases(form, channels))


# -- windows --------------------------------------------------------------------------


def _marks(labels: dict) -> list[float]:
    doors = labels.get("doors", [])
    out = [d["open"] for d in doors]
    out += [
        d[key]
        for d in doors
        for key in ("close", "ventilated", "exchanged")
        if d.get(key) is not None
    ]
    out += [x["t"] if isinstance(x, dict) else x for x in labels.get("infusions", [])]
    return sorted(float(v) for v in out)


def event_windows(labels: dict, horizon: float, first: float, last: float) -> list:
    """Fenster ``(Form, Knick, Beginn, Ende)`` aller bezeichneten Ereignisse in Sekunden.

    Ein Fenster reicht vom vorigen bis zum nächsten bezeichneten Ereignis, in der
    geschlossenen Kabine höchstens ``horizon`` (gelernte mittlere Zeit bis zum
    nächsten Heizwechsel) vor und nach dem Knick.  Öffnung: bis zum Schluss, mit
    Lüften, Durchlüften und Konvektion.  Lüften: von der Öffnung bis zum
    Durchlüften bzw. Schluss; Durchgelüftet: vom Lüften bis zum Schluss.  Schluss:
    ab der letzten Phase der offenen Tür und danach ebenso lange (die Erholung
    bis zur Bestätigung, nicht der weitere Verlauf der geschlossenen Kabine).
    """
    marks = _marks(labels)

    def before(at):
        return max([m for m in marks if m < at] + [first])

    def after(at):
        return min([m for m in marks if m > at] + [last])

    out = []
    for door in labels.get("doors", []):
        opened, closed = door["open"], door.get("close")
        if closed is None:
            continue
        vent, exch = door.get("ventilated"), door.get("exchanged")
        vent = vent if vent is not None and opened < vent < closed else None
        exch = exch if exch is not None and opened < exch < closed else None
        out.append(("opening", opened, max(before(opened), opened - horizon), closed))
        start = max(
            max(v for v in (opened, vent, exch) if v is not None), closed - horizon
        )
        out.append(
            ("closing", closed, start, min(after(closed), closed + (closed - start)))
        )
        if vent is not None:
            end = exch if exch is not None and exch > vent else closed
            out.append(("ventilation", vent, max(opened, vent - horizon), end))
        if exch is not None:
            start = vent if vent is not None and vent < exch else opened
            out.append(("exchange", exch, max(start, exch - horizon), closed))
    for x in labels.get("infusions", []):
        at = float(x["t"] if isinstance(x, dict) else x)
        out.append(
            (
                "infusion",
                at,
                max(before(at), at - horizon),
                min(after(at), at + horizon),
            )
        )
    return out


def disturbance_windows(
    candidates, labels: dict, horizon: float, first: float, last: float
) -> list:
    """Fenster der Störungen: Öffnungskandidaten ohne bezeichnetes Ereignis.

    Ein Kandidat gehört zu einer bezeichneten Tür, wenn er vor ihrem Schluss und
    höchstens so lange vor ihrer Öffnung liegt, wie sie offen war; zu einem
    Aufguss, wenn er in dessen Abklingen bis zum nächsten Ereignis liegt.
    """
    marks = _marks(labels)
    pours = sorted(
        float(x["t"] if isinstance(x, dict) else x) for x in labels.get("infusions", [])
    )
    out = []
    for at in sorted(candidates):
        door = any(
            d["open"] - ((d.get("close") or d["open"]) - d["open"])
            <= at
            <= (d.get("close") or d["open"])
            for d in labels.get("doors", [])
        )
        steam = any(
            p <= at <= min([m for m in marks if m > p] + [p + horizon]) for p in pours
        )
        if door or steam:
            continue
        start = max([m for m in marks if m < at] + [first, at - horizon])
        end = min([m for m in marks if m > at] + [last, at + horizon])
        out.append(("disturbance", float(at), start, end))
    return out


def cut(t, y, anchor: float, start: float, end: float, columns: int):
    """Zeit relativ zum Knick und Werte eines Fensters; ``None`` ohne genug Werte je Seite.

    Je Seite des Knicks braucht es mindestens so viele Werte, wie die Basis Spalten hat.
    """
    t = np.asarray(t, float)
    m = (t >= start) & (t <= end)
    u = t[m] - anchor
    if min((u < 0).sum(), (u >= 0).sum()) < columns:
        return None
    return u, np.asarray(y, float)[m]


# -- fitting --------------------------------------------------------------------------


def _columns(basis, u, k, p):
    """Basis an ``(k, p)``; ``None``, wo sie im Fenster nicht darstellbar ist.

    Die Schlussform wird vor dem Knick rückwärts mit der Rate ``p`` steiler; bei
    großem ``p`` und langem Vorlauf wächst eine Spalte weit über die Fensterlänge
    hinaus (keine Form, numerisch nicht bestimmbar).
    """
    if basis in RATELESS:
        return basis(u)
    with np.errstate(over="ignore", invalid="ignore"):
        b = basis(u, k, p)
    if not np.isfinite(b).all() or np.abs(b).max() > 1e3 * max(
        1.0, float(np.abs(u).max())
    ):
        return None
    return b


def _surfaces(u, values, channel_bases) -> np.ndarray:
    """Restquadratsummen je Rasterpunkt ``(k, p)`` und Kanal: ``(k, p, Kanal)``."""
    y = values - values.mean(0)  # the form has a level: centring keeps precision
    total = (y * y).sum(0)
    n = len(RATE_GRID)
    out = np.empty((n, n, y.shape[1]))
    groups: dict = {}
    for c, (basis, _) in enumerate(channel_bases):
        groups.setdefault(basis, []).append(c)
    for basis, chans in groups.items():
        yc = y[:, chans]
        if basis in RATELESS:
            b = basis(u)
            coef, *_ = np.linalg.lstsq(b, yc, rcond=None)
            out[:, :, chans] = ((yc - b @ coef) ** 2).sum(0)
            continue
        for a, k in enumerate(RATE_GRID):
            columns = [_columns(basis, u, k, p) for p in RATE_GRID]
            usable = np.array([b is not None for b in columns])
            out[a][:, chans] = total[chans]  # not representable: nothing explained
            if not usable.any():
                continue
            stack = np.stack([b for b in columns if b is not None])
            gram_inv = np.linalg.pinv(np.einsum("gni,gnj->gij", stack, stack))
            proj = np.einsum("gni,nc->gic", stack, yc)
            coef = np.einsum("gij,gjc->gic", gram_inv, proj)
            explained = np.einsum("gic,gic->gc", coef, proj)
            block = out[a][:, chans]
            block[usable] = total[chans][None] - explained
            out[a][:, chans] = block
    return np.maximum(out, 1e-12 * np.maximum(total, 1e-300))


def _lag_correlation(e) -> float:
    e = np.asarray(e, float)
    if len(e) < 4 or e.std() == 0:
        return 0.0
    return float(np.corrcoef(e[:-1], e[1:])[0, 1])


def _effective(n: int, rho: float) -> float:
    """Zahl unabhängiger Werte bei Autokorrelation ersten Grades (``rho`` < 1)."""
    rho = min(max(rho, 0.0), 1.0 - 1.0 / n)  # at least one independent value
    return n * (1 - rho) / (1 + rho)


def _fit(u, values, channel_bases, rates):
    """Kleinste Quadrate je Kanal: Koeffizienten, Kovarianz (ohne Rauschskala), Reste."""
    coefs, grams, residuals = [], [], []
    for c, ((basis, _), rate) in enumerate(zip(channel_bases, rates)):
        b = _columns(basis, u, *rate)
        if b is None:
            return None
        gram = np.linalg.pinv(b.T @ b)
        coef = gram @ b.T @ values[:, c]
        coefs.append(coef)
        grams.append(gram)
        residuals.append(values[:, c] - b @ coef)
    return coefs, grams, np.column_stack(residuals)


def _shifted(index: int, shift: int) -> int:
    """Rasterindex nach dem Ratenfaktor; am Rand bleibt die Rate auf ihrem Grenzwert.

    Das Raster reicht bis zu den Grenzwerten der Basis (linear bzw. Stufe), darüber
    hinaus ändert sie sich nicht mehr.
    """
    return min(max(index + shift, 0), len(RATE_GRID) - 1)


def _shift_rates(base, shift: int) -> list:
    return [
        (0.0, 0.0)
        if pair is None
        else (
            float(RATE_GRID[_shifted(pair[0], shift)]),
            float(RATE_GRID[_shifted(pair[1], shift)]),
        )
        for pair in base
    ]


def _allowed_shifts(base) -> range:
    """Verschiebungen, bei denen noch mindestens eine Rate nicht auf ihrem Grenzwert liegt."""
    rated = [pair for pair in base if pair is not None]
    if not rated:
        return range(1)
    n = len(RATE_GRID)
    return range(
        -max(max(pair) for pair in rated), n - min(min(pair) for pair in rated)
    )


def common_rates(profiles: list, rated: list, iterations: int = 20):
    """Raten je Kanal mit einem gemeinsamen Ratenfaktor je Ereignis.

    ``profiles`` je Ereignis ``(Kanal, k, p)``: negative Log-Likelihood mit
    eigenem Rauschen (½·n_eff·ln Restquadratsumme).  Abwechselnd wird je Ereignis
    der Faktor (gleiche Verschiebung im logarithmischen Raster für alle Kanäle)
    und je Kanal die Grundrate gewählt.  Liefert Rasterindizes (``None`` für
    Kanäle ohne Raten) und Verschiebungen, zentriert auf deren Median.
    """
    n = len(RATE_GRID)
    base = []
    for c, has in enumerate(rated):
        total = sum(pr[c] for pr in profiles)
        base.append(
            tuple(int(v) for v in np.unravel_index(int(np.argmin(total)), total.shape))
            if has
            else None
        )
    shifts = np.zeros(len(profiles), int)
    for _ in range(iterations):
        allowed = _allowed_shifts(base)
        for e, pr in enumerate(profiles):
            values = [
                sum(
                    pr[c][_shifted(pair[0], j), _shifted(pair[1], j)]
                    for c, pair in enumerate(base)
                    if pair is not None
                )
                for j in allowed
            ]
            shifts[e] = allowed[int(np.argmin(values))]
        new = []
        for c, has in enumerate(rated):
            if not has:
                new.append(None)
                continue
            total = np.zeros((n, n))
            for e, pr in enumerate(profiles):
                index = np.clip(np.arange(n) + int(shifts[e]), 0, n - 1)
                total += pr[c][np.ix_(index, index)]
            new.append(
                tuple(
                    int(v) for v in np.unravel_index(int(np.argmin(total)), total.shape)
                )
            )
        if new == base:
            break
        base = new
    centre = int(np.median(shifts))
    moved = [
        None if pair is None else (pair[0] + centre, pair[1] + centre) for pair in base
    ]
    if all(0 <= v < n for pair in moved if pair is not None for v in pair):
        base, shifts = moved, shifts - centre
    return base, shifts


def population(estimates, errors, iterations: int = 500):
    """Mittel und Kovarianz wahrer Koeffizienten aus Schätzwerten mit Fehlerkovarianzen (EM)."""
    x = np.asarray(estimates, float)
    s = np.asarray(errors, float)
    mu = x.mean(0)
    cov = np.atleast_2d(np.cov(x.T, bias=True)) if len(x) > 1 else np.eye(x.shape[1])
    floor = 1e-12 * np.maximum(np.diag(cov), 1e-300)
    for _ in range(iterations):
        means, covs = [], []
        for xi, si in zip(x, s):
            gain = cov @ np.linalg.pinv(cov + si)
            means.append(mu + gain @ (xi - mu))
            covs.append(cov - gain @ cov)
        means = np.array(means)
        new_mu = means.mean(0)
        d = means - new_mu
        new_cov = (d.T @ d + np.sum(covs, axis=0)) / len(x)
        new_cov = 0.5 * (new_cov + new_cov.T) + np.diag(floor)
        done = np.allclose(new_mu, mu, rtol=1e-9, atol=1e-12) and np.allclose(
            new_cov, cov, rtol=1e-7, atol=1e-15
        )
        mu, cov = new_mu, new_cov
        if done:
            break
    return mu, cov


def _gaussian_loglik(x, mu, cov) -> float:
    d = x - mu
    sign, logdet = np.linalg.slogdet(cov)
    if sign <= 0:
        return -math.inf
    return float(
        -0.5 * (d @ np.linalg.solve(cov, d) + logdet + len(x) * math.log(2 * math.pi))
    )


# -- distributions ----------------------------------------------------------------------


def _gamma_q(a: float, x: float) -> float:
    """Regularisierte obere unvollständige Gammafunktion Q(a, x)."""
    if x <= 0:
        return 1.0
    gln = math.lgamma(a)
    if x < a + 1:
        term = total = 1.0 / a
        ap = a
        for _ in range(1000):
            ap += 1
            term *= x / ap
            total += term
            if abs(term) < abs(total) * 1e-15:
                break
        return max(0.0, 1.0 - total * math.exp(-x + a * math.log(x) - gln))
    b = x + 1 - a
    c = 1.0 / 1e-300
    d = 1.0 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = 1e-300 if abs(d) < 1e-300 else d
        c = b + an / c
        c = 1e-300 if abs(c) < 1e-300 else c
        d = 1.0 / d
        h *= d * c
        if abs(d * c - 1) < 1e-15:
            break
    return min(1.0, math.exp(-x + a * math.log(x) - gln) * h)


def chi2_sf(x: float, dof: float) -> float:
    """Wahrscheinlichkeit, dass eine χ²-verteilte Größe mit ``dof`` Freiheitsgraden ``x`` übersteigt."""
    return _gamma_q(0.5 * dof, 0.5 * x)


# -- learning -------------------------------------------------------------------------


def _scales(u, values, outside, relative: bool) -> np.ndarray:
    """Bezugsgröße je Kanal: Abstand von der Außenluft beim Knick (relativ) oder 1."""
    if not relative:
        return np.ones(values.shape[1])
    last = values[u < 0][-1]
    return np.where(np.abs(last - outside) > 0, last - outside, 1.0)


def _event_fit(u, values, channel_bases, rates, scale):
    """Koeffizienten ohne Niveau (alle Kanäle), ihre Fehlerkovarianz und die Reste."""
    fitted = _fit(u, values, channel_bases, rates)
    if fitted is None:
        raise ValueError("Form im Fenster nicht darstellbar")
    coefs, grams, residuals = fitted
    theta, blocks = [], []
    for c, (coef, gram) in enumerate(zip(coefs, grams)):
        e = residuals[:, c]
        var = float(e @ e) / max(len(e) - len(coef), 1)
        var *= len(e) / _effective(len(e), _lag_correlation(e))
        theta.append(coef[1:] / scale[c])
        blocks.append(var * gram[1:, 1:] / scale[c] ** 2)
    size = sum(len(b) for b in blocks)
    cov = np.zeros((size, size))
    i = 0
    for b in blocks:
        cov[i : i + len(b), i : i + len(b)] = b
        i += len(b)
    return np.concatenate(theta), cov, residuals


def _noise(events, fits) -> dict:
    """Rauschen je Kanal vor und nach dem Knick und seine Autokorrelation (gepoolt)."""
    channels = fits[0][2].shape[1]
    before = np.zeros(channels)
    after = np.zeros(channels)
    nb = na = 0
    for (u, _), (_, _, e) in zip(events, fits):
        before += (e[u < 0] ** 2).sum(0)
        after += (e[u >= 0] ** 2).sum(0)
        nb += int((u < 0).sum())
        na += int((u >= 0).sum())
    sd_before = np.sqrt(before / max(nb, 1))
    sd_after = np.sqrt(after / max(na, 1))
    num = np.zeros(channels)
    den = np.zeros(channels)
    for (u, _), (_, _, e) in zip(events, fits):
        z = e / np.where(u[:, None] < 0, sd_before, sd_after)
        num += (z[1:] * z[:-1]).sum(0)
        den += (z * z).sum(0)
    rho = num / np.maximum(den, 1e-300)
    return {
        "sd_before": sd_before.tolist(),
        "sd_after": sd_after.tolist(),
        "rho": rho.tolist(),
    }


def _noise_scale(u, residuals, noise: dict):
    """Logarithmus der Restvarianz je Kanal relativ zum gelernten Rauschen und sein Schätzfehler."""
    out, errors = [], []
    for c in range(residuals.shape[1]):
        e = residuals[:, c]
        sd = np.where(u < 0, noise["sd_before"][c], noise["sd_after"][c])
        n = _effective(len(e), _lag_correlation(e))
        out.append(math.log(max(float(np.mean((e / sd) ** 2)), 1e-300)))
        errors.append(2.0 / n)  # variance of a log variance from n independent values
    return np.array(out), np.diag(errors)


def _profile(u, values, channel_bases, base):
    """Negative Log-Likelihood mit eigenem Rauschen je Verschiebung des Ratenfaktors.

    Die Zahl unabhängiger Werte je Kanal gilt für alle Verschiebungen gleich (bei
    den Grundraten bestimmt); sonst sähe ein schlecht passender, glatter Rest wie
    wenige Werte aus und würde bevorzugt.
    """
    fitted = _fit(u, values, channel_bases, _shift_rates(base, 0))
    e = fitted[2] if fitted is not None else values - values.mean(0)
    n = [_effective(len(e), _lag_correlation(e[:, c])) for c in range(values.shape[1])]
    shifts, scores = [], []
    for j in _allowed_shifts(base):
        fitted = _fit(u, values, channel_bases, _shift_rates(base, j))
        if fitted is None:
            continue
        e = fitted[2]
        score = sum(
            0.5 * n[c] * math.log(max(float(e[:, c] @ e[:, c]), 1e-300))
            for c in range(values.shape[1])
        )
        shifts.append(j)
        scores.append(score)
    return np.array(shifts), np.array(scores)


def _factor_estimate(shifts, scores) -> tuple[int, float]:
    """Bester Ratenfaktor (Verschiebung) und seine Unsicherheit (ln) aus dem Profil.

    Die Unsicherheit ist die halbe Breite des Bereichs, in dem das Profil um
    höchstens ½ über seinem Minimum liegt, mindestens das Raster.
    """
    best = int(np.argmin(scores))
    inside = shifts[scores <= scores[best] + 0.5]
    half = 0.5 * (inside.max() - inside.min()) * RATE_STEP
    return int(shifts[best]), max(half, RATE_STEP / math.sqrt(12))


def _edges(profiles, base, shifts) -> list:
    """Raten auf dem Rasterrand und der Anstieg des Profils zum Inneren.

    Ein Anstieg unter ½ (eine Standardabweichung des Profils) über die letzten
    drei Rasterschritte heißt: Die Basis hat dort ihren Grenzwert (linear bzw.
    Stufe) erreicht, das Raster reicht darüber hinaus.
    """
    n = len(RATE_GRID)
    out = []
    for c, pair in enumerate(base):
        if pair is None:
            continue
        total = np.zeros((n, n))
        for pr, j in zip(profiles, shifts):
            index = np.clip(np.arange(n) + int(j), 0, n - 1)
            total += pr[c][np.ix_(index, index)]
        for axis, name in ((0, "k"), (1, "p")):
            v = pair[axis]
            if v not in (0, n - 1):
                continue
            inner = list(pair)
            inner[axis] = 3 if v == 0 else n - 4
            rise = float(total[tuple(inner)] - total[pair])
            limit = "linear" if v == 0 else "Stufe"  # rate towards zero or infinity
            out.append(
                {
                    "channel": c,
                    "rate": name,
                    "rise": rise,
                    "limit": limit if rise < 0.5 else None,
                }
            )
    return out


def learn_form(
    name: str, events: list, ratio: float, *, keys=None, channels=CHANNELS
) -> dict:
    """Eine Form aus ihren Ereignissen ``[(u, Werte, Außenluft)]`` lernen.

    Untypisch nach Raten (heutiges Maß): mit eigenen Raten je Kanal um mehr als
    ``ratio`` besser erklärt als mit der gemeinsamen Form (ohne bzw. mit
    Ratenfaktor).  Untypisch nach Streuung: ``typicality`` mit der gelernten Form.
    """
    channel_bases = bases(name, channels)
    rated = [basis not in RATELESS for basis, _ in channel_bases]
    keys = list(keys) if keys is not None else [str(i) for i in range(len(events))]
    profiles = []
    for u, values, _ in events:
        s = _surfaces(u, values, channel_bases)
        n = []
        for c in range(values.shape[1]):
            a, b = np.unravel_index(int(np.argmin(s[:, :, c])), s.shape[:2])
            rate = (float(RATE_GRID[a]), float(RATE_GRID[b]))
            fitted = _fit(u, values[:, [c]], [channel_bases[c]], [rate])
            e = (
                fitted[2][:, 0]
                if fitted is not None
                else values[:, c] - values[:, c].mean()
            )
            n.append(_effective(len(e), _lag_correlation(e)))
        profiles.append(0.5 * np.array(n)[:, None, None] * np.log(np.moveaxis(s, 2, 0)))
    base, shifts = common_rates(profiles, rated)
    fixed = []
    for c, has in enumerate(rated):
        total = sum(pr[c] for pr in profiles)
        fixed.append(
            np.unravel_index(int(np.argmin(total)), total.shape) if has else (0, 0)
        )
    log_ratio = math.log(ratio)
    atypical_fixed, atypical_factor = [], []
    for key, pr, j in zip(keys, profiles, shifts):
        own = sum(float(pr[c].min()) for c in range(len(base)))
        if sum(float(pr[c][a, b]) for c, (a, b) in enumerate(fixed)) - own > log_ratio:
            atypical_fixed.append(key)
        common = sum(
            float(pr[c][_shifted(pair[0], j), _shifted(pair[1], j)])
            if pair
            else float(pr[c][0, 0])
            for c, pair in enumerate(base)
        )
        if common - own > log_ratio:
            atypical_factor.append(key)
    edges = _edges(profiles, base, shifts)
    factors = [
        _factor_estimate(*_profile(u, v, channel_bases, base)) for u, v, _ in events
    ]
    shifts = np.array([j for j, _ in factors])
    omega_mu, omega_cov = population(
        (shifts * RATE_STEP)[:, None], [np.array([[s * s]]) for _, s in factors]
    )
    sizes = np.array([len(names) for _, names in channel_bases])
    choice = {}
    for relative in (False, True):
        fits, thetas, errors = [], [], []
        for (u, values, outside), j in zip(events, shifts):
            scale = _scales(u, values, np.asarray(outside), relative)
            theta, cov, residuals = _event_fit(
                u, values, channel_bases, _shift_rates(base, int(j)), scale
            )
            fits.append((theta, cov, residuals))
            thetas.append(theta)
            errors.append(cov)
        mu, cov = population(thetas, errors)
        loglik = 0.0
        for (u, values, outside), theta, err in zip(events, thetas, errors):
            scale = _scales(u, values, np.asarray(outside), relative)
            loglik += _gaussian_loglik(theta, mu, cov + err)
            loglik -= float(np.log(np.abs(np.repeat(scale, sizes))).sum())  # Jacobian
        choice[relative] = (loglik, mu, cov, fits)
    relative = choice[True][0] > choice[False][0]
    _, mu, cov, fits = choice[relative]
    noise = _noise([(u, v) for u, v, _ in events], fits)
    scales = [_noise_scale(u, fit[2], noise) for (u, _, _), fit in zip(events, fits)]
    scale_mu, scale_cov = population([x for x, _ in scales], [e for _, e in scales])
    noise["log_scale_mean"] = scale_mu.tolist()
    noise["log_scale_cov"] = scale_cov.tolist()
    form = {
        "basis": name,
        "channels": list(channels),
        "coefficients": [list(names) for _, names in channel_bases],
        "rates": [
            None
            if pair is None
            else [float(RATE_GRID[pair[0]]), float(RATE_GRID[pair[1]])]
            for pair in base
        ],
        "rate_index": [
            None if pair is None else [int(pair[0]), int(pair[1])] for pair in base
        ],
        "rate_factor": {
            "mean": float(omega_mu[0]),
            "sd": max(math.sqrt(float(omega_cov[0, 0])), RATE_STEP / math.sqrt(12)),
            "step": RATE_STEP,
        },
        "relative": bool(relative),
        "mean": mu.tolist(),
        "cov": cov.tolist(),
        "noise": noise,
        "events": len(events),
        "grid_edge": edges,
        "population_loglik": {
            "absolute": choice[False][0],
            "relative": choice[True][0],
        },
        "atypical_rates": atypical_fixed,
        "atypical_rates_factor": atypical_factor,
    }
    checks = [typicality(form, u, v, o, ratio) for u, v, o in events]
    form["atypical"] = [k for k, ch in zip(keys, checks) if ch["atypical"]]
    form["typicality"] = {k: ch for k, ch in zip(keys, checks)}
    return form


# -- use of a learned form ------------------------------------------------------------------


def _form_bases(form: dict):
    channel_bases = bases(form["basis"], form["channels"])
    base = [None if pair is None else tuple(pair) for pair in form["rate_index"]]
    return channel_bases, base


def typicality(form: dict, u, values, outside, ratio: float) -> dict:
    """Liegt ein Ereignis innerhalb der gelernten Streuung seiner Form?

    Bestimmt werden der beste Ratenfaktor (Raster, mit Unsicherheit aus dem
    Profil), die Koeffizienten und der Rauschfaktor je Kanal.  Ratenfaktor und
    Koeffizienten zusammen: Mahalanobis-Abstand zu ihrer gelernten Verteilung
    (Streuung plus Schätzfehler), χ²-verteilt.  Rauschfaktoren ebenso gegen ihre
    gelernte Verteilung.  Untypisch ist das Ereignis, wenn eine der beiden
    Wahrscheinlichkeiten unter ``1 / (1 + ratio)`` liegt.
    """
    channel_bases, base = _form_bases(form)
    values = np.asarray(values, float)
    u = np.asarray(u, float)
    j, spread = _factor_estimate(*_profile(u, values, channel_bases, base))
    scale = _scales(u, values, np.asarray(outside, float), form["relative"])
    theta, err, residuals = _event_fit(
        u, values, channel_bases, _shift_rates(base, j), scale
    )
    d = theta - np.asarray(form["mean"])
    distance = float(d @ np.linalg.solve(np.asarray(form["cov"]) + err, d))
    factor = form["rate_factor"]
    z = (j * factor["step"] - factor["mean"]) / math.sqrt(factor["sd"] ** 2 + spread**2)
    distance += z * z
    p_param = chi2_sf(distance, len(theta) + 1)
    noise = form["noise"]
    log_scale, scale_err = _noise_scale(u, residuals, noise)
    g = log_scale - np.asarray(noise["log_scale_mean"])
    noise_distance = float(
        g @ np.linalg.solve(np.asarray(noise["log_scale_cov"]) + scale_err, g)
    )
    p_noise = chi2_sf(noise_distance, len(g))
    limit = 1.0 / (1.0 + ratio)
    sizes = [len(names) for _, names in channel_bases]
    labels = [f"{n}{c}" for c, (_, names) in enumerate(channel_bases) for n in names]
    return {
        "shift": int(j),
        "p_param": p_param,
        "p_noise": p_noise,
        "atypical": bool(p_param < limit or p_noise < limit),
        "distance": distance,
        "factor_z": float(z),
        "noise_distance": noise_distance,
        "log_noise_scale": log_scale.tolist(),
        "coefficients": dict(zip(labels, (theta * np.repeat(scale, sizes)).tolist())),
    }


def _ar1_precision(sd, rho: float):
    """Präzisionsmatrix (dicht) und log-Determinante der Kovarianz eines AR(1)-Rauschens."""
    n = len(sd)
    rho = min(max(rho, 0.0), 1.0 - 1e-9)
    q = np.zeros((n, n))
    idx = np.arange(n)
    q[idx, idx] = 1 + rho * rho
    q[0, 0] = q[-1, -1] = 1.0
    q[idx[:-1], idx[1:]] = q[idx[1:], idx[:-1]] = -rho
    q /= 1 - rho * rho
    inv = 1.0 / np.asarray(sd, float)
    logdet = 2 * float(np.log(sd).sum()) + (n - 1) * math.log(1 - rho * rho)
    return q * inv[:, None] * inv[None, :], logdet


# Gauss-Hermite nodes and weights (standard normal) for the common noise factor
_NODES, _WEIGHTS = np.polynomial.hermite_e.hermegauss(9)
_LOG_WEIGHTS = np.log(_WEIGHTS / _WEIGHTS.sum())


def log_evidence(form: dict, u, values, outside) -> float:
    """Randlikelihood eines Abschnitts unter der Form (Knick bei ``u`` = 0).

    Niveau je Kanal flach (gemeinsame Konstante aller Formen über denselben
    Abschnitt), Koeffizienten aller Kanäle nach ihrer gelernten Verteilung,
    Ratenfaktor über seine gelernte Verteilung (Raster) integriert, Rauschen
    je Kanal mit Autokorrelation ersten Grades und gelerntem Rauschfaktor:
    Kanalmittel fest, der allen Kanälen gemeinsame Anteil seiner Streuung
    integriert (Gauß-Hermite).
    """
    channel_bases, base = _form_bases(form)
    u = np.asarray(u, float)
    values = np.asarray(values, float)
    channels = values.shape[1]
    scale = _scales(u, values, np.asarray(outside, float), form["relative"])
    mu = np.asarray(form["mean"])
    cov = np.asarray(form["cov"])
    cov_inv = np.linalg.inv(cov)
    _, cov_logdet = np.linalg.slogdet(cov)
    noise = form["noise"]
    offset = np.asarray(noise["log_scale_mean"])
    common = math.sqrt(max(float(np.mean(noise["log_scale_cov"])), 0.0))
    precisions, logdets = [], []
    for c in range(channels):
        sd = np.where(u < 0, noise["sd_before"][c], noise["sd_after"][c])
        q, logdet = _ar1_precision(sd, noise["rho"][c])
        precisions.append(q)
        logdets.append(logdet)
    factor = form["rate_factor"]
    allowed = list(_allowed_shifts(base))
    weights = np.array(
        [
            -0.5 * ((j * factor["step"] - factor["mean"]) / factor["sd"]) ** 2
            for j in allowed
        ]
    )
    weights -= np.logaddexp.reduce(weights)
    widths = [len(names) + 1 for _, names in channel_bases]
    starts = np.concatenate([[0], np.cumsum(widths)[:-1]]).astype(int)
    size = int(sum(widths))
    event = [int(s) + 1 + i for s, w in zip(starts, widths) for i in range(w - 1)]
    n = len(u)
    terms = []
    for j, w in zip(allowed, weights):
        if w < weights.max() - 50:  # below numerical precision of the sum
            continue
        rates = _shift_rates(base, j)
        columns = [
            _columns(basis, u, *rate) for (basis, _), rate in zip(channel_bases, rates)
        ]
        if any(x is None for x in columns):
            continue
        blocks = []
        for c, x in enumerate(columns):
            x = x.copy()
            x[:, 1:] *= scale[c]
            q = precisions[c]
            y = values[:, c]
            blocks.append((x.T @ q @ x, x.T @ q @ y, float(y @ q @ y)))
        for node, log_weight in zip(_NODES, _LOG_WEIGHTS):
            g = offset + common * node  # log noise factor per channel
            a = np.zeros((size, size))
            b = np.zeros(size)
            quad = float(mu @ cov_inv @ mu)
            noise_logdet = 0.0
            for c, (xqx, xqy, yqy) in enumerate(blocks):
                f = math.exp(-g[c])
                sl = slice(int(starts[c]), int(starts[c]) + widths[c])
                a[sl, sl] = f * xqx
                b[sl] = f * xqy
                quad += f * yqy
                noise_logdet += logdets[c] + n * g[c]
            a[np.ix_(event, event)] += cov_inv
            b[event] += cov_inv @ mu
            sign, a_logdet = np.linalg.slogdet(a)
            if sign <= 0:
                continue
            fit = float(b @ np.linalg.solve(a, b))
            loglik = -0.5 * (quad - fit) - 0.5 * (noise_logdet + cov_logdet + a_logdet)
            loglik -= 0.5 * (values.size - channels) * math.log(2 * math.pi)
            terms.append(w + log_weight + loglik)
    return float(np.logaddexp.reduce(terms)) if terms else -math.inf
