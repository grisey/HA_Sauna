"""Physikalisches Zustandsmodell der Saunakabine für die Ereigniserkennung.

Schaltendes Zustandsraummodell (IMM-Filter).  Je Messkanal (Temperatur und
absolute Feuchte, oben und unten) gibt es den Zustand ``[L, r, z, W]``:

* ``L`` Pegel des Kanals; gemessen wird ``y = L + Rauschen``,
* ``r`` Grunddrift (Heizen oder Abkühlen bei geschlossener Tür),
* ``z`` Größe des laufenden Ereignisses (siehe unten),
* ``W`` bei offener Tür: Temperatur von Wand und Steinen beim Öffnen, bei der
  Feuchte die Feuchtequelle ``s`` (Holz, Steine, Personen); sie beginnt mit
  ihrer gelernten Verteilung und wandert während der Öffnung (``source_q``).

Die Kabine befindet sich in genau einem Zustand:

====================  =======================================================
zu                    ``L += r·dt``
offen                 Temperatur ``L += (r − λ·(L − außen) − β·(L − W))·dt``,
                      Feuchte ``L += (r − λ·(L − außen) + s)·dt``, ``λ = exp(z)``:
                      Luftaustausch mit außen, Nachschub aus Wand und Steinen
                      bzw. Feuchtequelle
Aufguss               Feuchte ``L += (r + exp(z))·dt``
Abklingen             Feuchte ``L += (r − exp(z))·dt``
Erholung              Temperatur ``L += e·dt`` mit ``e ≥ 0`` (sie steigt nach dem
                      Schließen), Feuchte ``L += (r + e)·dt``
Heizwechsel           wie zu, die Grunddrift der Temperatur springt
====================  =======================================================

Ein Ereignis hat eine Ursache für alle Kanäle (eine Tür, eine Dampfquelle).
Darum sind die Ereignisgrößen ``z`` der Kanäle beim Beginn des Ereignisses und
in ihrer Wanderung korreliert (gelernte Korrelation ``zcorr``); der Zustand
aller Kanäle wird gemeinsam geschätzt.  Ein Kanal, der dem Ereignis nicht
folgt, senkt so dessen Größe auch in den übrigen Kanälen.

Messrauschen ist Student-t-verteilt.  Die Feuchtesensoren messen relative
Feuchte, darum skaliert das Rauschen der absoluten Feuchte mit dem
Sättigungswert der Temperatur am selben Sensor.  Alle Größen stammen aus
Aufzeichnungen (``recognition_learning``); dieses Modul enthält keine
festgelegten Schwellen.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .moisture import KELVIN_OFFSET_C, WATER_VAPOR_GAS_CONSTANT, saturation_vapor_pressure

CHANNELS = ("Tu", "Tl", "Au", "Al")
MODES = ("closed", "open", "infusion", "decay", "recovery", "shift")
CLOSED, OPEN, INFUSION, DECAY, RECOVERY, SHIFT = range(len(MODES))
NM = len(MODES)
NX = 4
# physically possible mode changes; every other transition has rate zero
ALLOWED = (
    (CLOSED, SHIFT), (SHIFT, CLOSED), (CLOSED, OPEN), (CLOSED, INFUSION), (OPEN, RECOVERY),
    (INFUSION, DECAY), (DECAY, CLOSED), (DECAY, OPEN), (DECAY, INFUSION),
    (RECOVERY, CLOSED), (RECOVERY, OPEN), (RECOVERY, INFUSION),
)


def is_humidity(channel: str) -> bool:
    return channel.startswith("A")


def partner_temperature(channel: str) -> str:
    """Temperaturkanal desselben Sensors (für die Rauschskala der Feuchte)."""
    return "T" + channel[1]


def saturation_humidity(temperature_c: float) -> float:
    """Sättigungswert der absoluten Feuchte in g/m³ (ASHRAE, wie die Messwerte)."""
    pressure = saturation_vapor_pressure(min(max(temperature_c, -100.0), 200.0))
    return pressure * 1000 / (WATER_VAPOR_GAS_CONSTANT * (temperature_c + KELVIN_OFFSET_C))


def expm(a: np.ndarray) -> np.ndarray:
    """Matrixexponential über Skalieren und Quadrieren mit Taylorreihe."""
    norm = float(np.abs(a).sum(axis=0).max()) if a.size else 0.0
    squarings = max(0, int(math.ceil(math.log2(norm))) + 1) if norm > 0.5 else 0
    scaled = a / (2 ** squarings)
    result = np.eye(len(a))
    term = np.eye(len(a))
    for k in range(1, 14):
        term = term @ scaled / k
        result = result + term
    for _ in range(squarings):
        result = result @ result
    return result


@dataclass(frozen=True)
class ModelParameters:
    """Gelernte Größen des Zustandsmodells für eine Kanalauswahl."""

    channels: tuple[str, ...]
    sv: np.ndarray          # Messrauschen (Temperatur in K, Feuchte relativ zur Sättigung)
    ql: np.ndarray          # Pegelschwankung je Zustand und Kanal
    prior: np.ndarray       # Startverteilung (Mittel, Streuung) der Ereignisgröße z
    zcorr: np.ndarray       # Korrelation der Ereignisgrößen der Kanäle je Zustand
    beta: np.ndarray        # Nachschub aus Wand und Steinen bei offener Tür
    source: np.ndarray      # Feuchtequelle bei offener Tür: Startverteilung (Mittel, Streuung) je Kanal
    source_corr: np.ndarray  #   ihre Korrelation zwischen den Kanälen
    source_q: float         #   und ihre Wanderung innerhalb einer Öffnung ((g/m³/s)²/s); 0 = konstant
    qe: np.ndarray          # Wanderung der Ereignisgröße
    rates: np.ndarray       # Übergangsraten zwischen den Zuständen (1/s)
    jump: np.ndarray        # Sprung der Grunddrift beim Heizwechsel
    nu: np.ndarray          # Freiheitsgrade des Messrauschens je Zustand und Kanal
    nus: np.ndarray         # Skalenkorrektur des Messrauschens je Zustand und Kanal
    qd: np.ndarray          # Wanderung der Grunddrift (ohne gelernte Driftverteilung)
    drift_mean: np.ndarray  # Verteilung der Grunddrift bei geschlossener Tür: Mittel,
    drift_sd: np.ndarray    #   Streuung
    drift_tau: np.ndarray   #   und Rückkehrzeit (s); unendlich = freie Wanderung
    qlam: float             # Wanderung des Austauschkoeffizienten (log) bei offener Tür

    @classmethod
    def from_dict(cls, data: dict, channels: tuple[str, ...] = CHANNELS) -> ModelParameters:
        index = [list(data["channels"]).index(c) for c in channels]
        arr = lambda key: np.asarray(data[key], float)  # noqa: E731
        zcorr = (arr("zcorr")[:, index][:, :, index] if "zcorr" in data
                 else np.repeat(np.eye(len(index))[None], NM, 0))
        return cls(
            channels=tuple(channels),
            sv=arr("sv")[index],
            ql=arr("ql")[:, index],
            prior=arr("prior")[:, index],
            zcorr=zcorr,
            beta=arr("beta")[index],
            source=arr("source")[index] if "source" in data else np.zeros((len(index), 2)),
            source_corr=(arr("source_corr")[index][:, index] if "source_corr" in data
                         else np.eye(len(index))),
            source_q=float(data.get("source_q") or 0.0),
            qe=arr("qe")[:, index],
            rates=arr("rates"),
            jump=arr("jump")[index],
            nu=arr("nu")[:, index],
            nus=arr("nus")[:, index],
            qd=arr("qd")[index],
            drift_mean=arr("drift_mean")[index] if "drift_mean" in data else np.zeros(len(index)),
            drift_sd=arr("drift_sd")[index] if "drift_sd" in data else np.zeros(len(index)),
            drift_tau=(np.array([math.inf if v is None else float(v) for v in data["drift_tau"]])[index]
                       if "drift_tau" in data else np.full(len(index), math.inf)),
            qlam=float(data["qlam"]),
        )


class StateModel:
    """IMM-Filter: Zustandswahrscheinlichkeiten und gemeinsamer Kanalzustand Schritt für Schritt.

    Der Zustand je Modus ist ``[L, r, z, W]`` für jeden Kanal hintereinander;
    die Kovarianz umfasst alle Kanäle.  Die Messwerte werden Kanal für Kanal
    eingerechnet (sequenzielle skalare Aktualisierung).
    """

    def __init__(self, params: ModelParameters, y0, outside, *, drift=None, drift_var=None):
        p = params
        self.p = p
        channels = p.channels
        c = len(channels)
        n = NX * c
        self.channels = channels
        self.n = n
        self.humid = np.array([is_humidity(ch) for ch in channels])
        # temperature channel of the same sensor for the noise scale of each humidity channel
        self.partner = np.array([
            channels.index(partner_temperature(ch)) if is_humidity(ch) and partner_temperature(ch) in channels else -1
            for ch in channels
        ])
        base = NX * np.arange(c)
        self.iL, self.ir, self.iz, self.iW = base, base + 1, base + 2, base + 3
        sign = np.zeros((NM, c))
        sign[OPEN] = -1
        sign[INFUSION] = np.where(self.humid, 1, 0)
        sign[DECAY] = np.where(self.humid, -1, 0)
        sign[RECOVERY] = 1
        self.sign = sign
        constr = np.zeros((NM, c), bool)        # event size must stay ≥ 0 (physical direction)
        constr[RECOVERY] = ~self.humid
        self.constr = constr
        logm = np.zeros((NM, c), bool)          # infusion and decay carried as log rates
        logm[INFUSION] = self.humid
        logm[DECAY] = self.humid
        self.logm = logm
        self.sv2 = p.sv ** 2
        self.out = np.asarray(outside, float)
        self.beta = p.beta
        self.beta_open = np.where(self.humid, 0.0, p.beta)     # wall and stone node: temperature only
        # moisture source of an opening (humidity): joint start distribution of the channels (one cause);
        # within the opening it wanders locally (a person at one sensor), independently per channel
        hw = np.where(self.humid)[0]
        self.source_mean = p.source[hw, 0]
        spread = p.source[hw, 1]
        self.source_cov = spread[:, None] * spread[None, :] * p.source_corr[np.ix_(hw, hw)]
        self.nu = p.nu.copy()
        self.nu[SHIFT] = self.nu[CLOSED]
        self.nus2 = p.nus.copy() ** 2
        self.nus2[SHIFT] = self.nus2[CLOSED]
        self.tconst = np.array([[math.lgamma((v + 1) / 2) - math.lgamma(v / 2) - 0.5 * math.log(v * math.pi)
                                 for v in row] for row in self.nu])
        self.ql = p.ql.copy()
        self.ql[SHIFT] = self.ql[CLOSED]
        self.jump2 = np.where(self.humid, 0.0, p.jump) ** 2     # the heater changes the temperature drift only
        # start distribution of the event size on entering a mode: mean and joint covariance of the channels
        active = sign != 0
        self.z_mean = p.prior[:, :, 0] * active
        spread = p.prior[:, :, 1] * active
        self.z_cov = spread[:, :, None] * spread[:, None, :] * p.zcorr
        # process noise: drift and event size; the event sizes wander together (same correlation)
        q = np.zeros((NM, n, n))
        qe = np.array(p.qe, float)
        qe[OPEN] = p.qlam
        for j in range(NM):
            # drift without a learned distribution: free random walk, held while the door is open
            q[j, self.ir, self.ir] = np.where(np.isfinite(p.drift_tau), 0.0, 0.0 if j == OPEN else p.qd)
            q[j][np.ix_(self.iz, self.iz)] = np.sqrt(np.outer(qe[j], qe[j])) * p.zcorr[j]
        self.q = q
        # recovery → closed: the temperature recovery rate becomes the drift; event size and wall are reset
        a = np.eye(n)
        a[np.r_[self.iz, self.iW]] = 0
        for ch in np.where(~self.humid)[0]:
            a[self.ir[ch]] = 0
            a[self.ir[ch], self.iz[ch]] = 1
        self._recovery_to_closed = a
        g = np.array(p.rates, float).copy()
        np.fill_diagonal(g, 0)
        np.fill_diagonal(g, -g.sum(1))
        self.generator = g
        self._transition = {}
        y0 = np.asarray(y0, float)
        self.x = np.zeros((NM, n))
        self.x[:, self.iL] = y0
        self.P = np.zeros((NM, n, n))
        self.P[:, self.iL, self.iL] = self.sv2 * self.noise_scale(y0) ** 2
        self.P[:, self.ir, self.ir] = 1e-4
        if drift is not None:
            self.x[:, self.ir] = np.asarray(drift, float)
            self.P[:, self.ir, self.ir] = np.asarray(drift_var, float)
        self.mu = np.full(NM, 1e-6)
        self.mu[CLOSED] = 1 - (NM - 1) * 1e-6
        self.lik = np.zeros(NM)
        self.T = np.eye(NM)
        self.log_evidence = 0.0
        self.last_standardised = np.zeros((NM, c))

    def noise_scale(self, y) -> np.ndarray:
        """Humidity sensors measure RH: AH noise scales with the saturation value at the same sensor."""
        scale = np.ones(len(self.channels))
        for i, j in enumerate(self.partner):
            if j >= 0:
                scale[i] = saturation_humidity(float(y[j])) / 100
        return scale

    def transition(self, dt: float) -> np.ndarray:
        key = round(dt, 2)
        t = self._transition.get(key)
        if t is None:
            t = expm(self.generator * key)
            if len(self._transition) < 512:
                self._transition[key] = t
        return t

    def channel_state(self, mode: int) -> tuple[np.ndarray, np.ndarray]:
        """State ``[L, r, z, W]`` and its covariance per channel for one mode."""
        x = self.x[mode].reshape(len(self.channels), NX)
        cov = np.stack([self.P[mode][NX * k:NX * k + NX, NX * k:NX * k + NX] for k in range(len(self.channels))])
        return x, cov

    def _sources(self):
        """Source estimates as seen by each target mode (re-initialisation on entering it)."""
        x, P = self.x, self.P
        iL, ir, iz, iW = self.iL, self.ir, self.iz, self.iW
        X = np.repeat(x[None], NM, 0)                  # [target, source, n]
        PP = np.repeat(P[None], NM, 0)
        event = np.r_[iz, iW]
        X[:, :, event] = 0
        PP[:, :, event, :] = 0
        PP[:, :, :, event] = 0
        for j in range(1, NM):
            X[j][:, iz] = self.z_mean[j]
            PP[j][:, iz[:, None], iz[None, :]] = self.z_cov[j]
        # opening, temperature: the wall level starts at the current level (perfectly correlated);
        # humidity: the slot carries the moisture source (wood, stones, people) with its learned distribution
        X[OPEN][:, iW] = x[:, iL]
        PP[OPEN][:, iW, :] = PP[OPEN][:, iL, :]
        PP[OPEN][:, :, iW] = PP[OPEN][:, :, iL]
        hw = iW[self.humid]
        X[OPEN][:, hw] = self.source_mean
        PP[OPEN][:, hw, :] = 0
        PP[OPEN][:, :, hw] = 0
        PP[OPEN][:, hw[:, None], hw[None, :]] = self.source_cov
        a = self._recovery_to_closed
        for j in (CLOSED, SHIFT):
            X[j, RECOVERY] = a @ x[RECOVERY]
            PP[j, RECOVERY] = a @ P[RECOVERY] @ a.T
        for j in range(NM):
            X[j, j] = x[j]
            PP[j, j] = P[j]
        PP[SHIFT][:, ir, ir] += self.jump2             # the drift jumps on entering a heater change
        return X, PP

    def step(self, dt: float, y) -> np.ndarray:
        y = np.asarray(y, float)
        n = self.n
        iL, ir, iz, iW = self.iL, self.ir, self.iz, self.iW
        T = self.transition(dt)
        prior_mode = self.mu @ T
        weights = (T * self.mu[:, None]) / np.maximum(prior_mode[None, :], 1e-300)
        X, PP = self._sources()
        x0 = np.einsum("ij,jin->jn", weights, X)
        dx = X - x0[:, None]
        P0 = np.einsum("ij,jinm->jnm", weights, PP + dx[..., :, None] * dx[..., None, :])
        F = np.tile(np.eye(n), (NM, 1, 1))
        F[:, iL, ir] = dt
        F[:, iL, iz] = self.sign * dt
        F[:, iz, iz] = self.sign != 0
        F[RECOVERY, iL[~self.humid], ir[~self.humid]] = 0   # after closing the temperature rises with its own rate
        xp = np.einsum("jnm,jm->jn", F, x0)
        # log-rate channels: dL = (r ± exp(z))·dt (extended Kalman linearisation)
        jj, cc = np.where(self.logm)
        rate = np.exp(x0[jj, iz[cc]])
        xp[jj, iL[cc]] = x0[jj, iL[cc]] + (x0[jj, ir[cc]] + self.sign[jj, cc] * rate) * dt
        F[jj, iL[cc], iz[cc]] = self.sign[jj, cc] * rate * dt
        # open, temperature: dL = (r − λ·(L − außen) − β·(L − W))·dt; humidity: dL = (r − λ·(L − außen) + s)·dt
        level, drift, zlam, node = x0[OPEN, iL], x0[OPEN, ir], x0[OPEN, iz], x0[OPEN, iW]
        excess = level - self.out
        lam = np.exp(zlam)
        beta = self.beta_open
        supply = np.where(self.humid, node, -beta * (level - node))
        xp[OPEN, iL] = level + (drift - lam * excess + supply) * dt
        F[OPEN, iL, iL] = 1 - (lam + beta) * dt
        F[OPEN, iL, iz] = -lam * excess * dt
        F[OPEN, iL, iW] = np.where(self.humid, 1.0, beta) * dt
        # the drift returns to its learned distribution (Ornstein-Uhlenbeck): heating and cooling of the
        # closed cabin are bounded, a door-like fall is not a drift
        tau = self.p.drift_tau
        bounded = np.isfinite(tau)
        if bounded.any():
            decay = np.where(bounded, np.exp(-dt / np.where(bounded, tau, 1.0)), 1.0)
            F[:, ir, ir] = decay
            xp[:, ir] = decay * x0[:, ir] + (1 - decay) * self.p.drift_mean
        Pp = F @ P0 @ F.swapaxes(-1, -2) + self.q * dt
        if self.p.source_q > 0:                       # the moisture source wanders within the opening
            hw = iW[self.humid]
            Pp[OPEN, hw, hw] += self.p.source_q * dt
        if bounded.any():
            Pp[:, ir, ir] += np.where(bounded, self.p.drift_sd ** 2 * (1 - decay ** 2), 0.0)
        scale2 = self.noise_scale(y) ** 2
        qscale = np.repeat(scale2[None], NM, 0)
        qscale[OPEN] = excess ** 2                     # exchange turbulence ∝ (inside − outside)²
        for j in (INFUSION, DECAY):                    # steam turbulence ∝ (event rate)²
            qscale[j] = np.where(self.logm[j], np.exp(2 * x0[j, iz]), scale2)
        level_noise = self.ql * qscale * dt
        level_noise[OPEN] += self.ql[CLOSED] * scale2 * dt
        for j in (INFUSION, DECAY):
            level_noise[j] += np.where(self.logm[j], self.ql[CLOSED] * scale2, 0) * dt
        Pp[:, iL, iL] += level_noise
        x, cov = xp, Pp
        lik = np.zeros(NM)
        held = [OPEN, INFUSION, DECAY]
        eye = np.eye(n)
        for k in range(len(self.channels)):
            col = iL[k]
            pll = cov[:, col, col]
            S = (pll + self.sv2[k] * scale2[k]) * self.nus2[:, k]
            v = y[k] - x[:, col]
            z2 = v * v / S
            nu = self.nu[:, k]
            self.last_standardised[:, k] = v / np.sqrt(S)
            lik += self.tconst[:, k] - 0.5 * np.log(S) - (nu + 1) / 2 * np.log1p(z2 / nu)
            # robust update: the measurement part is re-weighted by the Student-t weight
            S = np.maximum(pll + (S - pll) * (nu + z2) / (nu + 1), pll + 1e-12)
            K = cov[:, :, col] / S[:, None]
            # during an event the heater drift is held (a 'consider' state) and not re-estimated from the event
            K[np.ix_(held, ir)] = 0
            x = x + K * v[:, None]
            ikh = np.repeat(eye[None], NM, 0)
            ikh[:, :, col] -= K
            rv = (S - pll)[:, None, None]
            cov = ikh @ cov @ ikh.swapaxes(-1, -2) + K[:, :, None] * K[:, None, :] * rv
            cov = 0.5 * (cov + cov.swapaxes(-1, -2))
        # physical direction (recovery: temperature rate ≥ 0): project the joint estimate onto z = 0
        for j, k in zip(*np.where(self.constr & ~self.logm)):
            e = iz[k]
            if x[j, e] >= 0 or cov[j, e, e] <= 1e-18:
                continue
            gain = cov[j, :, e] / cov[j, e, e]
            x[j] = x[j] - gain * x[j, e]
            cov[j] = cov[j] - np.outer(gain, cov[j, e, :])
            cov[j] = 0.5 * (cov[j] + cov[j].T)
        self.x, self.P = x, cov
        logw = np.log(np.maximum(prior_mode, 1e-300)) + lik
        top = logw.max()
        w = np.exp(logw - top)
        self.mu = w / w.sum()
        self.lik = lik
        self.T = T
        self.log_evidence = float(top + math.log(w.sum()))   # log p(y_k | y_1..k-1)
        return self.mu
