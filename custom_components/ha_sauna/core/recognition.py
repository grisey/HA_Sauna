"""Ereigniserkennung aus Temperatur und Feuchte über das Zustandsmodell der Kabine.

Die Erkennung kennt weder Sitzung noch Gang oder Steuerung.  Sie erhält nur
Messwerte und liefert Ereignisse mit physikalischem Zeitpunkt (``effective_at``)
und Bestätigungszeitpunkt (``detected_at``):

* Tür auf, Tür zu, Aufguss: aus den Zustandswahrscheinlichkeiten des Modells
  (``recognition_model``).  Ein Kandidat beginnt, sobald die Wahrscheinlichkeit
  ihren Ruhebereich verlässt; bestätigt ist er, wenn sie nach dem Nachlauf die
  Entscheidungsschwelle ``q`` erreicht.  Der Nachlauf bezieht die folgenden
  Messwerte in die Wahrscheinlichkeit eines Zeitpunkts ein (Glättung über
  einen festen Abstand).
* Lüften und Durchgelüftet: Abflachen der relativen bzw. absoluten Feuchte
  während der offenen Tür, als Kurvenform mit Bayes-Knickmodell
  (``flattening_probability``), solange die Temperatur weiter fällt.
"""

from __future__ import annotations

import bisect
import math
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np

from .models import Measurement, Position, Quantity
from .moisture import absolute_humidity
from .recognition_model import (
    CHANNELS, CLOSED, DECAY, INFUSION, NM, OPEN, OUTSIDE_TYPICAL, RECOVERY, SHIFT, ModelParameters, StateModel,
    closing_kink, is_humidity, opening_evident,
)
from .timeline import DoorCloseEvidence, InfusionEnd, Kind, utc

CHANNEL_SOURCES = {
    "Tu": (Position.UPPER, "T"), "Tl": (Position.LOWER, "T"),
    "Au": (Position.UPPER, "A"), "Al": (Position.LOWER, "A"),
}


@dataclass(frozen=True)
class Detection:
    """Erkanntes Ereignis mit physikalischem Zeitpunkt und Bestätigung."""

    kind: Kind
    effective_at: datetime
    detected_at: datetime
    channels: tuple[str, ...]
    trace_at: datetime | None = None
    door_close_evidence: DoorCloseEvidence | None = None
    probability: float | None = None


def _start_drift(t: np.ndarray, y: np.ndarray):
    """Drift of every channel at the start: least-squares slope of the start samples and its variance."""
    x = t - t.mean()
    sxx = float(x @ x)
    if len(t) < 3 or sxx <= 0:
        return np.zeros(y.shape[1]), np.full(y.shape[1], 1e-4)
    slope = x @ (y - y.mean(0)) / sxx
    residual = y - y.mean(0) - np.outer(x, slope)
    variance = (residual ** 2).sum(0) / (len(t) - 2) / sxx
    return slope, np.maximum(variance, 1e-10)


ORIGINAL_MEMORY = 4096      # recent originals kept to recognise a redelivery


def _original_identity(measurement: Measurement):
    """Identity of one original reading: the physical sample or the archived/HA original."""
    if measurement.sample is not None:
        sample = measurement.sample
        return "sample", sample.sensor_id, sample.boot_id, sample.sample_id, measurement.quantity
    if measurement.original_id is not None:
        return "original", measurement.position, measurement.quantity, measurement.source, measurement.original_id
    return None


def recognition_event_id(session_id: str, kind, effective_at: datetime) -> str:
    """Feste Kennung eines erkannten Ereignisses (auch Quelle seiner Lernbelege)."""
    return f"recognition:{session_id}:{Kind(kind).value}:{utc(effective_at).isoformat()}"


@dataclass(frozen=True)
class RejectedOpening:
    """Episode von der Öffnungsform verworfener Türkandidaten mit ihrem Schluss (G9, G9b).

    Die Erkennung entscheidet unverändert; sie meldet die Episode nur
    zusätzlich.  Beginnt ein neuer Kandidat nach dem Schlusskandidaten ``c``
    eines verworfenen und spätestens im Schlussübergang ``c + T``, gehört er
    dazu; wird er bestätigt, entfällt die Meldung.  Ohne Folger endet die
    Episode bei ``ended_at`` = max(letzte Entscheidung, ``c + T``).  Öffnung
    ist der Beginn des ersten Kandidaten, Schluss die Schlusszeit wie für eine
    bestätigte Tür über die Episode.  Ob daraus ein Türvorgang wird,
    entscheidet das Gangmodell.
    """

    opened_at: datetime
    closed_at: datetime
    close_candidate_at: datetime        # c of the last candidate
    ended_at: datetime                  # E: confirmation of the closing
    decided_at: datetime                # measurement step of the report (at or after E)
    candidates: tuple[datetime, ...]
    channels: tuple[str, ...]
    door_close_evidence: DoorCloseEvidence


@dataclass
class _RejectedEpisode:
    first: int                          # start step of the first candidate
    close: int                          # closing candidate step c of the last candidate
    decided: datetime                   # decision of the last candidate
    starts: list = field(default_factory=list)
    follower: int | None = None         # start step of a candidate in c..c + T not yet decided


@dataclass(frozen=True)
class InfusionUpdate:
    """Ende des Dampfabschnitts zu einem bereits gelieferten Aufguss."""

    confirmed_detection: Detection
    end: InfusionEnd


@dataclass(frozen=True)
class DecisionParameters:
    """Nicht gelernte Festlegungen der Entscheidung.

    ``evidence_ratio``: Ein Zustand gilt als bestätigt, wenn er mindestens so
    viel wahrscheinlicher ist als die nächstbeste Erklärung (150:1, „sehr starke
    Evidenz“ nach Kass und Raftery).  Es gibt keine feste Wartezeit.
    """

    evidence_ratio: float = 150.0
    flat_min_samples: int = 20
    flat_rise_fraction: float = 0.05

    @classmethod
    def from_dict(cls, data: dict | None) -> DecisionParameters:
        data = data or {}
        return cls(**{k: type(getattr(cls, k))(v) for k, v in data.items() if hasattr(cls, k)})


# ---------------------------------------------------------------------------
# Student-t-Verteilungsfunktion (regularisierte unvollständige Betafunktion)


def _betacf(a: float, b: float, x: np.ndarray) -> np.ndarray:
    """Kettenbruch der unvollständigen Betafunktion (modifizierter Lentz)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = np.ones_like(x)
    d = 1.0 - qab * x / qap
    d = np.where(np.abs(d) < tiny, tiny, d)
    d = 1.0 / d
    h = d.copy()
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = np.where(np.abs(d) < tiny, tiny, d)
        c = 1.0 + aa / c
        c = np.where(np.abs(c) < tiny, tiny, c)
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = np.where(np.abs(d) < tiny, tiny, d)
        c = 1.0 + aa / c
        c = np.where(np.abs(c) < tiny, tiny, c)
        d = 1.0 / d
        delta = d * c
        h *= delta
        if np.all(np.abs(delta - 1.0) < 1e-12):
            break
    return h


def _betainc(a: float, b: float, x: np.ndarray) -> np.ndarray:
    x = np.clip(np.asarray(x, float), 0.0, 1.0)
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    with np.errstate(divide="ignore"):
        front = np.exp(lbeta + a * np.log(np.where(x > 0, x, 1)) + b * np.log(np.where(x < 1, 1 - x, 1)))
    front = np.where((x > 0) & (x < 1), front, 0.0)
    swap = x >= (a + 1) / (a + b + 2)
    direct = front * _betacf(a, b, np.where(swap, 0.5, x)) / a
    mirrored = 1.0 - front * _betacf(b, a, np.where(swap, 1 - x, 0.5)) / b
    out = np.where(swap, mirrored, direct)
    return np.where(x <= 0, 0.0, np.where(x >= 1, 1.0, out))


def t_cdf(t, dof: float) -> np.ndarray:
    """Verteilungsfunktion der Student-t-Verteilung."""
    t = np.asarray(t, float)
    tail = 0.5 * _betainc(dof / 2, 0.5, dof / (dof + t * t))
    return np.where(t > 0, 1 - tail, tail)


# ---------------------------------------------------------------------------
# Abflachen als Kurvenform


def flattening_probability(x, y, min_samples: int, rise_fraction: float):
    """Wahrscheinlichkeit, dass ``y`` nach einem Knick abgeflacht ist.

    Stetiges Zwei-Segment-Modell ``y = b0 + b1·(x − τ) + b2·max(x − τ, 0)`` mit
    unbekanntem Rauschen und flacher A-priori-Verteilung; über den Knick ``τ``
    wird mit seiner Evidenz gemittelt.  Abgeflacht heißt: vorher fallend
    (``s1 = b1 < 0``), danach ``s1/e < s2 < rise_fraction·|s1|`` mit
    ``s2 = b1 + b2`` – nicht mehr steil fallend und nicht wieder steigend.
    Liefert ``(Wahrscheinlichkeit, Knickzeit)``.
    """
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    n = len(x)
    m = int(min_samples)
    if n < 2 * m + 1:
        return 0.0, None
    x = x - x[0]
    # suffix sums over points right of each candidate kink (strictly increasing x)
    rx = np.concatenate([np.cumsum(x[::-1])[::-1][1:], [0.0]])
    rxx = np.concatenate([np.cumsum((x * x)[::-1])[::-1][1:], [0.0]])
    ry = np.concatenate([np.cumsum(y[::-1])[::-1][1:], [0.0]])
    rxy = np.concatenate([np.cumsum((x * y)[::-1])[::-1][1:], [0.0]])
    rn = np.arange(n - 1, -1, -1, dtype=float)
    k = np.arange(m, n - m)
    tau = x[k]
    sx, sxx, sy, sxy, syy = x.sum(), (x * x).sum(), y.sum(), (x * y).sum(), (y * y).sum()
    s1 = sx - n * tau
    s2 = sxx - 2 * tau * sx + n * tau * tau
    a = rx[k] - rn[k] * tau
    b = rxx[k] - 2 * tau * rx[k] + rn[k] * tau * tau
    xtx = np.empty((len(k), 3, 3))
    xtx[:, 0, 0] = n
    xtx[:, 0, 1] = xtx[:, 1, 0] = s1
    xtx[:, 0, 2] = xtx[:, 2, 0] = a
    xtx[:, 1, 1] = s2
    xtx[:, 1, 2] = xtx[:, 2, 1] = b
    xtx[:, 2, 2] = b
    xty = np.stack([np.full(len(k), sy), sxy - tau * sy, rxy[k] - tau * ry[k]], axis=1)
    det = np.linalg.det(xtx)
    ok = det > 1e-12 * np.abs(xtx).max(axis=(1, 2)) ** 3
    if not ok.any():
        return 0.0, None
    xtx, xty, tau, det = xtx[ok], xty[ok], tau[ok], det[ok]
    inv = np.linalg.inv(xtx)
    coef = np.einsum("kij,kj->ki", inv, xty)
    rss = syy - np.einsum("ki,ki->k", coef, xty)
    dof = n - 3
    good = rss > 0
    if not good.any():
        return 0.0, None
    inv, coef, rss, tau, det = inv[good], coef[good], rss[good], tau[good], det[good]
    logw = -0.5 * np.log(det) - 0.5 * dof * np.log(rss)
    s2v = rss / dof
    flat_low = np.array([0.0, 1 - 1 / math.e, 1.0])                 # s2 − s1/e > 0
    flat_high = np.array([0.0, -1 - rise_fraction, -1.0])           # rise·|s1| − s2 > 0  (s1 < 0)
    falling = np.array([0.0, -1.0, 0.0])                            # −s1 > 0

    def prob(vec):
        se = np.sqrt(s2v * np.einsum("i,kij,j->k", vec, inv, vec))
        return t_cdf(coef @ vec / se, dof)

    p_low, p_high, p_fall = prob(flat_low), prob(flat_high), prob(falling)
    p = np.maximum(p_low + p_high - 1, 0.0) * p_fall               # Fréchet lower bound of the joint
    w = np.exp(logw - logw.max())
    w /= w.sum()
    return float((w * p).sum()), float(tau[int(np.argmax(w))])


def falling_probability(x, y) -> float:
    """P(Steigung < 0) einer Geraden durch die Punkte (Student-t)."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    if len(x) < 4:
        return 0.0
    u = x - x.mean()
    suu = float((u * u).sum())
    if suu <= 0:
        return 0.0
    slope = float((u * (y - y.mean())).sum() / suu)
    res = y - y.mean() - slope * u
    dof = len(x) - 2
    se = math.sqrt(max(float(res @ res) / dof / suu, 1e-30))
    return float(t_cdf(np.array([-slope / se]), dof)[0])


MODE_NAMES = ("closed", "open", "infusion", "decay", "recovery", "shift")

# ---------------------------------------------------------------------------
# Episoden aus geglätteten Wahrscheinlichkeiten


@dataclass
class _Track:
    """Episode eines Zustands (oder einer Zustandsgruppe) über die geglätteten Wahrscheinlichkeiten.

    Kandidat: Der Zustand ist an einem Messschritt die wahrscheinlichste Erklärung.
    Bestätigt: An einem Schritt der Episode ist er mindestens ``ratio``-mal so
    wahrscheinlich wie die nächstbeste Erklärung.  Der Zeitpunkt ist der erste
    Schritt der Episode, bestimmt mit allen Messwerten bis zur Bestätigung.  Das
    Ende ebenso: Kandidat, sobald eine andere Erklärung die wahrscheinlichste ist,
    bestätigt bei ``ratio`` zugunsten der anderen.
    """

    states: tuple
    exits: tuple = ()              # the episode ends only into these states (empty: any other state)
    inside: bool = False
    start: int | None = None
    floor: int = 0                 # first step not yet decided
    candidate: bool = False        # an unconfirmed start or end is pending

    def scan(self, post: np.ndarray, base: int, ratio: float, more_exits=None) -> list:
        """``more_exits``: per step further states the episode may end into (or None)."""
        events = []
        mask = np.zeros(post.shape[1], bool)
        mask[list(self.states)] = True
        target = post[:, mask].sum(1)
        other = post[:, ~mask].max(1)
        n = len(post)
        i = max(self.floor - base, 0)
        self.candidate = False
        open_block = False             # an unconfirmed earlier candidate may still be confirmed later
        while i < n:
            if not self.inside:
                if target[i] > other[i]:
                    j = i
                    while j < n and target[j] > other[j]:
                        if target[j] >= ratio * other[j]:
                            break
                        j += 1
                    if j < n and target[j] > other[j]:
                        events.append(("start", base + i, base + j))
                        self.inside, self.start = True, base + i
                        i += 1
                        self.floor = base + i
                        continue
                    if j >= n:
                        self.candidate = True        # pending: more data decides
                        break
                    if any(other[k] < ratio * target[k] for k in range(i, j)):
                        open_block = True            # it ended unconfirmed but is not ruled out
                    i = j
                    continue
                if other[i] >= ratio * target[i] and not open_block:
                    self.floor = base + i + 1        # settled: no episode here and none before
                i += 1                               # an undecided step stays open, a later one may start
                continue
            top = int(np.argmax(post[i]))
            leaves = target[i] < other[i] and (not self.exits or top in self.exits
                                               or (more_exits is not None and top in more_exits[i]))
            if not leaves:
                i += 1
                self.floor = base + i
                continue
            j = i
            while j < n and target[j] < other[j]:
                if other[j] >= ratio * target[j]:
                    break
                j += 1
            if j < n and target[j] < other[j]:
                events.append(("end", base + i, base + j))
                self.inside = False
                i += 1
                self.floor = base + i
                continue
            if j >= n:
                self.candidate = True
                break
            i = j                                    # back in the state: no end
        return events


@dataclass
class _OpenDoor:
    start: int
    confirmed: int
    ventilated: tuple[int, int] | None = None
    exchanged: tuple[int, int] | None = None
    next_check: int = 0


@dataclass
class SessionRecord:
    """Session data kept for learning: grid, channel values, RH and decisions."""

    times: list = field(default_factory=list)
    values: list = field(default_factory=list)
    relative: list = field(default_factory=list)
    door_prob: list = field(default_factory=list)
    infusion_prob: list = field(default_factory=list)


class ClimateRecognizer:
    """Messwerte annehmen, Zustandsmodell fortschreiben, Ereignisse liefern."""

    def __init__(self, parameters: dict, origin: datetime, *, positions=(Position.UPPER, Position.LOWER),
                 observer=None, keep_record: bool = True, evaluate: bool = True, outside=None):
        self.origin = utc(origin)
        self.positions = tuple(Position(p) for p in positions)
        channels = tuple(c for c in CHANNELS if CHANNEL_SOURCES[c][0] in self.positions)
        if not channels:
            raise ValueError("Erkennung benötigt mindestens eine Messposition")
        self.channels = channels
        self.model_parameters = ModelParameters.from_dict(parameters, channels)
        self.decision = DecisionParameters.from_dict(parameters.get("decision"))
        self.observer = observer
        self.evaluate = evaluate
        self.clock_position = Position.UPPER if Position.UPPER in self.positions else Position.LOWER
        self._latest: dict[tuple[Position, Quantity], tuple[datetime, float]] = {}
        self._pending: deque = deque()
        self._clocks: dict = {}                                # physical sensor clock → (time, monotonic ns)
        self._seen: OrderedDict = OrderedDict()               # recent originals → value (redelivery)
        self._model: StateModel | None = None
        self._start: list = []
        self._start_t: list = []
        # surrounding air of the cabin (outside air of the sauna): {"temperature", "absolute_humidity"} from the
        # configured weather source or sensors; missing values: the typical value of the parameters
        typical = {**OUTSIDE_TYPICAL, **(parameters.get("outside_typical") or {})}
        given = {k: v for k, v in (outside or {}).items() if v is not None}
        air = {**typical, **given}
        self.outside_source = {k: "configured" if k in given else "typical" for k in typical}
        self.outside = np.array([air["absolute_humidity"] if is_humidity(c) else air["temperature"]
                                 for c in channels], float)
        self._last_time: datetime | None = None
        self._steps = 0
        self._buffer: deque = deque()                         # (step, filtered mu, lik, T) of undecided steps
        self._door = _Track((OPEN,))                       # a closing: any state of the closed cabin
        self._infusion = _Track((INFUSION,))
        self._steam = _Track((INFUSION, DECAY))                # infusion rise and decay together
        self._pour: int | None = None                         # start step of the latest pour
        self._separations: deque = deque(maxlen=64)           # steps that separate pours (flattened decay, door)
        self._steepest: tuple | None = None                   # steepest humidity slope of the running decay
        self._post: np.ndarray | None = None                  # smoothed probabilities of the buffered steps
        self._post_base = 0
        self._steam_infusions: list[Detection] = []           # delivered infusions of the current steam episode
        self._held: list[Detection] = []                      # infusions confirmed while the door is still open
        self._held_end: InfusionEnd | None = None
        self.updates: list[InfusionUpdate] = []
        self.rejected_openings: list[RejectedOpening] = []
        self._episode: _RejectedEpisode | None = None
        self._open: _OpenDoor | None = None
        self._unconfirmed: tuple | None = None    # (step, probability) of an opening the form does not yet show
        self._grid_t: list = []          # seconds since origin for every model step
        self._grid_y: list = []          # channel values of every model step
        self._grid_rh: list = []         # relative humidity of the clock position
        self._grid_t_temp: list = []     # temperature of the clock position
        self._grid_ah: list = []         # absolute humidity of the clock position
        self._grid_open: list = []       # filtered probability of the open door
        self.record = SessionRecord() if keep_record else None
        self.detections: list[Detection] = []
        self.decisions: list[dict] = []                     # why and when every event was decided
        self._heating: list = []                            # (seconds, heater on) reported by the runtime
        self.diagnostic: dict | None = None

    # -- inputs -----------------------------------------------------------------

    @property
    def active_positions(self) -> tuple[Position, ...]:
        return tuple(p for p in self.positions if (p, Quantity.TEMPERATURE) in self._latest
                     and (p, Quantity.HUMIDITY) in self._latest)

    def accept(self, measurement: Measurement) -> bool:
        """Einen Messwert übernehmen; die Auswertung folgt in ``advance``.

        Zeitachse ist die Messzeit: bei physischen Proben die monotone Uhr des
        Sensors ab seinem ersten Wert (Korrekturen der Sensoruhrzeit verschieben
        die Kurve nicht), sonst die Messzeit, wenn sie nicht nach dem Empfang
        liegt, sonst der Empfang.  Werte von vor dem Beginn (der aktuelle Stand
        beim Sitzungsstart) gelten ab dem Beginn.  Ein erneut geliefertes
        Original mit demselben Wert zählt nicht noch einmal.
        """
        if measurement.position not in self.positions or measurement.value is None:
            return False
        identity = _original_identity(measurement)
        if identity is not None:
            if identity in self._seen and self._seen[identity] == measurement.value:
                return False
            self._seen[identity] = measurement.value
            self._seen.move_to_end(identity)
            while len(self._seen) > ORIGINAL_MEMORY:
                self._seen.popitem(last=False)
        at = max(self._coordinate(measurement), self.origin)
        self._pending.append((at, measurement.position, measurement.quantity, float(measurement.value)))
        return True

    def _coordinate(self, measurement: Measurement) -> datetime:
        received = utc(measurement.received_at)
        measured = utc(measurement.measured_at) if measurement.measured_at is not None else None
        sample = measurement.sample
        if sample is not None:
            clock = sample.sensor_id, sample.boot_id
            anchor_at, anchor_ns = self._clocks.setdefault(
                clock, (min(measured or received, received), sample.monotonic_ns))
            return anchor_at + timedelta(microseconds=(sample.monotonic_ns - anchor_ns) / 1000)
        return measured if measured is not None and measured <= received else received

    # -- processing ----------------------------------------------------------------

    @property
    def processed_until(self) -> datetime | None:
        """Zeit des zuletzt ausgewerteten Messschritts."""
        return self._last_time

    def advance(self, through: datetime, *, on_detection=None, on_infusion_update=None,
                on_rejected_opening=None, **_unused) -> list[Detection]:
        """Alle bis ``through`` vorliegenden Messwerte auswerten.

        Ereignisse, Aufgussenden und verworfene Öffnungen werden in ihrer
        Entstehungsreihenfolge zurückgemeldet, jedes zu seinem Messschritt.
        Verworfene Öffnungen sind keine Erkennungen (``detections``).
        """
        through = utc(through)
        pending = sorted((p for p in self._pending if p[0] <= through), key=lambda p: p[0])
        self._pending = deque(p for p in self._pending if p[0] > through)
        out: list[Detection] = []
        index = 0
        while index < len(pending):
            # all values of one time first (one physical sample: temperature and humidity), then one step
            at = pending[index][0]
            clock = False
            while index < len(pending) and pending[index][0] == at:
                _, position, quantity, value = pending[index]
                index += 1
                previous = self._latest.get((position, quantity))
                if previous is not None and at < previous[0]:
                    continue      # a delayed original older than the current value does not replace it
                self._latest[(position, quantity)] = (at, value)
                clock = clock or (position is self.clock_position and quantity is Quantity.TEMPERATURE)
            if not clock:
                continue
            for item in self._step(at):
                if isinstance(item, InfusionUpdate):
                    self.updates.append(item)
                    if on_infusion_update is not None:
                        on_infusion_update(item)
                    continue
                if isinstance(item, RejectedOpening):
                    self.rejected_openings.append(item)
                    if on_rejected_opening is not None:
                        on_rejected_opening(item)
                    continue
                out.append(item)
                self.detections.append(item)
                if on_detection is not None:
                    on_detection(item)
        return out

    def _observation(self):
        values, rh = [], None
        for channel in self.channels:
            position, kind = CHANNEL_SOURCES[channel]
            temperature = self._latest.get((position, Quantity.TEMPERATURE))
            humidity = self._latest.get((position, Quantity.HUMIDITY))
            if temperature is None or (kind == "A" and humidity is None):
                return None, None
            if kind == "T":
                values.append(temperature[1])
            else:
                try:
                    values.append(absolute_humidity(temperature[1], min(max(humidity[1], 0.0), 100.0)))
                except ValueError:
                    return None, None
        humidity = self._latest.get((self.clock_position, Quantity.HUMIDITY))
        rh = humidity[1] if humidity else None
        return np.array(values), rh

    def _step(self, at: datetime) -> list[Detection]:
        if self._last_time is not None and at <= self._last_time:
            return []             # same reading time (a repeated or corrected value): no new step
        y, rh = self._observation()
        if y is None or rh is None:
            return []
        sec = (at - self.origin).total_seconds()
        if self.record is not None:
            self.record.times.append(sec)
            self.record.values.append([float(v) for v in y])
            self.record.relative.append(float(rh))
        if not self.evaluate:
            self._last_time = at
            return []
        if self._model is None:
            # the drift at the start: slope of the first 30 samples; the state starts at the newest values
            self._start.append(y)
            self._start_t.append(sec)
            if len(self._start) < 30:
                self._last_time = at
                return []
            self.decisions.append({"event": "outside_air", "decided_at": at.isoformat(),
                                   "values": {c: round(float(v), 2) for c, v in zip(self.channels, self.outside)},
                                   "source": dict(self.outside_source)})
            drift, drift_var = _start_drift(np.asarray(self._start_t), np.asarray(self._start))
            self._model = StateModel(self.model_parameters, y, self.outside, drift=drift, drift_var=drift_var)
            self._model.infusion_possible = False      # an infusion needs someone inside: only after an opening
            self._last_time = at
            return []
        dt = (at - self._last_time).total_seconds()
        self._last_time = at
        mu = self._model.step(dt, y).copy()
        self._buffer.append((self._steps, mu, self._model.lik.copy(), self._model.T.copy(),
                             self._model.last_strat.copy()))
        self._grid_t.append(sec)
        self._grid_y.append(y)
        self._grid_open.append(float(mu[OPEN]))
        self._grid_rh.append(math.log(max(rh, 0.1)))
        clock_t = self.channels.index("Tu" if self.clock_position is Position.UPPER else "Tl")
        clock_a = self.channels.index("Au" if self.clock_position is Position.UPPER else "Al")
        self._grid_t_temp.append(float(y[clock_t]))
        self._grid_ah.append(math.log(max(float(y[clock_a]), 0.1)))
        self._steps += 1
        self._watch_decay(mu, at)
        out = []
        self._smooth()
        out.extend(self._decide_door(at))
        out.extend(self._decide_infusion(at))
        self._trim()
        out.extend(self._flattening(at))
        self.diagnostic = {
            "at": at.isoformat(),
            "state_probabilities": {name: round(float(v), 4) for name, v in zip(
                ("closed", "open", "infusion", "decay", "recovery", "shift"), mu)},
        }
        if self.observer is not None:
            self.observer(self.diagnostic)
        return out

    def _smooth(self) -> None:
        """P(state at every buffered step | all data so far): backward pass from the newest step."""
        items = list(self._buffer)
        n = len(items)
        post = np.empty((n, NM))
        beta = np.ones(NM)
        post[-1] = items[-1][1]
        for k in range(n - 1, 0, -1):
            _, _, lik, T, _ = items[k]
            emission = np.exp(lik - lik.max())
            beta = T @ (emission * beta)
            beta /= beta.sum()
            row = items[k - 1][1] * beta
            post[k - 1] = row / row.sum()
        self._post = post
        self._post_base = items[0][0]

    def _trim(self) -> None:
        """Drop steps that every episode has decided."""
        floor = min(self._door.floor, self._infusion.floor, self._steam.floor)
        if self._open is not None:
            floor = min(floor, self._open.start)
        if self._unconfirmed is not None:                 # an opening the form may still confirm
            floor = min(floor, self._unconfirmed[0])
        while self._buffer and self._buffer[0][0] < floor and len(self._buffer) > 1:
            self._buffer.popleft()

    def _explain(self, kind, index: int, confirmed: int, states: tuple, at: datetime, entering: bool) -> None:
        """Record why and when an event was decided: competing explanation, ratio, contribution of the
        humidity difference upper − lower over the episode."""
        post = self._posterior(confirmed)
        mask = np.zeros(NM, bool)
        mask[list(states)] = True
        p_target = float(post[mask].sum())
        others = np.where(~mask)[0]
        rival = int(others[np.argmax(post[others])])
        p_rival = float(post[rival])
        winner, loser = (states[0], rival) if entering else (rival, states[0])
        items = list(self._buffer)
        lo, hi = min(index, confirmed), max(index, confirmed)
        strat = sum(float(e[4][winner] - e[4][loser]) for e in items if lo <= e[0] <= hi)
        self.decisions.append({
            "event": Kind(kind).value,
            "effective_at": self._index_time(index).isoformat(),
            "decided_at": at.isoformat(),
            "state": MODE_NAMES[winner],
            "against": MODE_NAMES[loser],
            "ratio": round((p_target / max(p_rival, 1e-300)) if entering else (p_rival / max(p_target, 1e-300)), 1),
            "steps_to_decision": confirmed - index,
            "difference_factor": round(math.exp(strat), 2),
        })

    def _posterior(self, index: int) -> np.ndarray | None:
        k = index - self._post_base
        if self._post is None or not 0 <= k < len(self._post):
            return None
        return self._post[k]

    def _index_time(self, index: int) -> datetime:
        return self.origin + timedelta(seconds=self._grid_t[index])

    def report_heating(self, at: datetime, on: bool | None) -> None:
        """Heizzustand (Rückmeldung) ab ``at``.  Ohne laufende Heizung gibt es keine Erholung nach dem
        Schließen; eine Öffnung darf dann auch in die geschlossene, abkühlende Kabine enden."""
        sec = (utc(at) - self.origin).total_seconds()
        if self._heating and self._heating[-1][1] == on:
            return
        if self._heating and sec <= self._heating[-1][0]:
            self._heating[-1] = (self._heating[-1][0], on)
            return
        self._heating.append((sec, on))

    def _heater_off(self, steps) -> list:
        """Per buffered step: True when the heater is off long enough for the cabin to cool.

        After switching off, walls and stones keep heating the cabin; the heater counts as off only once
        it has been reported off for the learned return time of the temperature drift (the time the
        heating effect persists in the closed cabin).
        """
        if not self._heating:
            return [False] * len(steps)
        p = self.model_parameters
        temperature = [i for i, c in enumerate(self.channels) if not is_humidity(c)]
        taus = [float(p.drift_tau[i]) for i in temperature if math.isfinite(float(p.drift_tau[i]))]
        persist = max(taus) if taus else 0.0
        times = [h[0] for h in self._heating]
        out = []
        for step in steps:
            at = self._grid_t[step]
            k = int(np.searchsorted(times, at, side="right")) - 1
            out.append(k >= 0 and self._heating[k][1] is False and at - self._heating[k][0] >= persist)
        return out

    def _door_pending(self) -> bool:
        """The door episode is open or its start or end is not yet decided."""
        return self._door.inside or self._door.candidate

    def _decide_door(self, at: datetime) -> list:
        ratio = self.decision.evidence_ratio
        if self.record is not None:
            self.record.door_prob.append((self._steps - 1, float(self._post[-1][OPEN])))
        out: list = []
        for what, index, confirmed in self._door.scan(self._post, self._post_base, ratio):
            self._explain(Kind.DOOR_OPEN if what == "start" else Kind.DOOR_CLOSE, index, confirmed, (OPEN,), at,
                          what == "start")
            p_open = float(self._posterior(index)[OPEN])
            if what == "start":
                out.extend(self._follow(index, at))
                self._unconfirmed = (index, p_open)
                out.extend(self._confirm_opening(at, self._steps - 1))
                continue
            if self._unconfirmed is not None:
                out.extend(self._confirm_opening(at, index))   # the form up to the closing decides
            if self._unconfirmed is not None:
                self.decisions.append({"event": "door_open_rejected", "effective_at":
                                       self._index_time(self._unconfirmed[0]).isoformat(),
                                       "decided_at": at.isoformat(), "reason": "opening_form"})
                out.extend(self._reject(index, at))
                self._unconfirmed = None
                continue
            effective = min(self._closing_time(self._open, index), at)
            self._open = None
            evidence = DoorCloseEvidence(effective, at)
            out.append(Detection(Kind.DOOR_CLOSE, effective, at, self.channels, trace_at=at,
                                 door_close_evidence=evidence, probability=1 - p_open))
            out.extend(self._release_held(effective, at))
        if self._unconfirmed is not None and self._door.inside:
            out.extend(self._confirm_opening(at, self._steps - 1))
        if self._held and not self._door_pending():
            out.extend(self._release_held(None, at))      # the opening was not confirmed
        out.extend(self._episode_due(at))
        return out

    def _closing_transition(self) -> float:
        """Schlussübergang T in Sekunden: 1/k der gelernten Schlussform der Temperaturkanäle, der größere
        Wert; ohne gelernte Schlussform 0 (G9b)."""
        form = self.model_parameters.close_form
        if not form:
            return 0.0
        rates = [float(form[i][0]) for i, c in enumerate(self.channels)
                 if not is_humidity(c) and form[i] is not None and float(form[i][0]) > 0]
        return max(1.0 / k for k in rates) if rates else 0.0

    def _follow(self, index: int, at: datetime) -> list:
        """A new opening candidate: it joins the pending episode when it begins by c + T, else ends it."""
        episode = self._episode
        if episode is None:
            return []
        if self._grid_t[index] <= self._grid_t[episode.close] + self._closing_transition():
            episode.follower = index
            return []
        return self._finish_episode(at)

    def _reject(self, index: int, at: datetime) -> list:
        """The opening form rejected the candidate whose closing candidate is ``index``."""
        start = self._unconfirmed[0]
        episode = self._episode
        if episode is not None and episode.follower == start:
            episode.starts.append(start)
            episode.close, episode.decided, episode.follower = index, at, None
            return []
        out = self._finish_episode(at) if episode is not None else []
        self._episode = _RejectedEpisode(first=start, close=index, decided=at, starts=[start])
        return out

    def _episode_due(self, at: datetime) -> list:
        """Ohne Folger endet die Episode, sobald ``c + T`` erreicht ist und bis dahin kein Kandidat mehr
        beginnen kann (die Schritte bis ``c + T`` sind entschieden)."""
        episode = self._episode
        if episode is None or episode.follower is not None:
            return []
        until = self._grid_t[episode.close] + self._closing_transition()
        if (at - self.origin).total_seconds() < until:
            return []
        last = bisect.bisect_right(self._grid_t, until) - 1
        if not self._door.inside and self._door.floor <= last:
            return []
        return self._finish_episode(at)

    def _finish_episode(self, at: datetime) -> list:
        episode, self._episode = self._episode, None
        candidate = self._index_time(episode.close)
        ended = min(max(episode.decided, candidate + timedelta(seconds=self._closing_transition())), at)
        closed = min(self._closing_time(_OpenDoor(start=episode.first, confirmed=self._steps - 1),
                                        episode.close), ended)
        return [RejectedOpening(min(self._index_time(episode.first), at), closed, candidate, ended, at,
                                tuple(self._index_time(s) for s in episode.starts), self.channels,
                                DoorCloseEvidence(closed, ended))]

    def _confirm_opening(self, at: datetime, through: int) -> list:
        """Bestätigung eines Öffnungskandidaten durch die gelernte Öffnungsform.

        Der Kandidat (Zeitpunkt aus dem Zustandsmodell) gilt als Öffnung, sobald der
        Temperaturabfall nach ihm mit dem Evidenzverhältnis steiler ist als davor
        (``opening_evident``), verglichen über die Messschritte bis ``through``.
        Davor zählt die Kabine höchstens so lange, wie im Mittel kein Heizwechsel
        kommt (gelernte Rate zu → Heizwechsel), und nicht vor dem letzten Ereignis.
        Ohne gelernte Form gilt der Kandidat sofort.
        """
        index, p_open = self._unconfirmed
        form = self.model_parameters.open_form
        columns = [i for i, c in enumerate(self.channels) if not is_humidity(c) and form and form[i] is not None]
        if columns:
            kink = self._grid_t[index]
            rate = float(self.model_parameters.rates[CLOSED, SHIFT])
            start = kink - (1.0 / rate if rate > 0 else math.inf)
            for detection in reversed(self.detections):  # not before the previous event
                previous = (detection.effective_at - self.origin).total_seconds()
                if previous < kink:
                    start = max(start, previous)
                    break
            first = bisect.bisect_left(self._grid_t, start)
            t = np.asarray(self._grid_t[first:through + 1])
            y = np.asarray(self._grid_y[first:through + 1])[:, columns]
            if not opening_evident(t, y, kink, [form[i] for i in columns], self.decision.evidence_ratio):
                return []
            self.decisions.append({"event": "door_open_form", "effective_at": self._index_time(index).isoformat(),
                                   "decided_at": at.isoformat(), "steps_to_decision": through - index})
        self._unconfirmed = None
        if self._episode is not None and self._episode.follower == index:
            self._episode = None                          # a candidate of the episode is the door (G9b-2)
        self._separations.append(index)                   # an opening also separates infusions
        self._open = _OpenDoor(start=index, confirmed=self._steps - 1, next_check=index)
        if self._model is not None:
            self._model.infusion_possible = True
        effective = min(self._index_time(index), at)
        return [Detection(Kind.DOOR_OPEN, effective, at, self.channels, trace_at=at, probability=p_open)]

    def _closing_time(self, door: _OpenDoor | None, index: int) -> datetime:
        """Zeitpunkt des Schließens: gemeinsamer Knick aller Kanäle mit der gelernten Vergleichsform.

        Die Bestätigung (Beginn des Übergangs bei ``index``) bleibt die Entscheidung;
        ihr Zeitpunkt ist der Knick (``closing_kink``).  Der Vergleich beginnt beim
        Öffnen, bei einer gelüfteten Öffnung beim letzten bestätigten Lüften bzw.
        Durchlüften, und endet beim aktuellen Schritt oder am Beginn einer neuen
        Öffnung oder eines Aufgusses.  Eine Erholung wird erwartet, außer der Ofen
        war während der ganzen Öffnung als aus gemeldet.  Ohne gelernte Form oder
        ohne passenden Knick gilt ``index``.
        """
        fallback = self._index_time(index)
        form = self.model_parameters.close_form
        columns = [i for i in range(len(self.channels)) if form and form[i] is not None]
        if door is None or door.start >= index or not columns:
            return fallback
        phases = [p[0] for p in (door.ventilated, door.exchanged) if p is not None and p[0] < index]
        start = max(phases) if phases else door.start
        end = self._steps - 1
        for step in range(index + 1, self._steps):        # a new opening or an infusion ends the comparison
            post = self._posterior(step)
            if post is not None and int(np.argmax(post)) in (OPEN, INFUSION):
                end = step
                break
        recovery = not all(self._heater_off(range(door.start, index + 1)))
        t = np.asarray(self._grid_t[start:end + 1])
        y = np.asarray(self._grid_y[start:end + 1])[:, columns]
        kink = closing_kink(t, y, t[t <= self._grid_t[index]], [form[i] for i in columns],
                            [not is_humidity(self.channels[i]) for i in columns], recovery=recovery)
        return fallback if kink is None else self.origin + timedelta(seconds=kink)

    def _release_held(self, closed_at: datetime | None, at: datetime) -> list:
        """Infusions held while a door episode was undecided; after a closing they follow it."""
        out: list = []
        for held in self._held:
            effective = held.effective_at if closed_at is None else max(held.effective_at, closed_at)
            detection = Detection(Kind.INFUSION, min(effective, at), at, held.channels,
                                  trace_at=at, probability=held.probability)
            out.append(detection)
            self._steam_infusions.append(detection)
        self._held = []
        if self._held_end is not None:
            out.extend(self._end_steam(self._held_end))
            self._held_end = None
        return out

    def _decide_infusion(self, at: datetime) -> list:
        ratio = self.decision.evidence_ratio
        if self.record is not None:
            self.record.infusion_prob.append((self._steps - 1, float(self._post[-1][INFUSION])))
        out: list = []
        steam_events = self._steam.scan(self._post, self._post_base, ratio)
        for what, index, _confirmed in steam_events:
            if what == "end":
                self._separations.append(index)       # the steam has decayed completely
        for what, index, confirmed in self._infusion.scan(self._post, self._post_base, ratio):
            if what == "start":
                # several pours are one infusion unless the decay has evidently begun to flatten in between
                # (or the steam ended, or the door opened)
                merged = self._pour is not None and not any(self._pour < k <= index for k in self._separations)
                self._pour = index
                if merged:
                    self.decisions.append({"event": "infusion_pour", "effective_at": self._index_time(index).isoformat(),
                                           "decided_at": at.isoformat(), "joined": "the decay of the previous pour "
                                           "had not evidently flattened"})
                    continue
                self._explain(Kind.INFUSION, index, confirmed, (INFUSION,), at, True)
                effective = min(self._index_time(index), at)
                detection = Detection(Kind.INFUSION, effective, at, self.channels, trace_at=at,
                                      probability=float(self._posterior(index)[INFUSION]))
                out.extend(self._deliver_infusion(detection))
        # steam episode (rise and decay): its end completes the infusions delivered in it
        for what, index, _confirmed in steam_events:
            if what != "end":
                continue
            post = self._posterior(index)
            end_at = min(self._index_time(index), at)
            # the steam ends into an opened door or decays in the closed cabin
            opened = post[OPEN] > post[CLOSED] + post[SHIFT] + post[RECOVERY]
            end = InfusionEnd(end_at, at, at, "door_open" if opened else "steam_decayed", "physical_effective")
            if self._held:
                self._held_end = end
            else:
                out.extend(self._end_steam(end))
        return out

    def _watch_decay(self, mu: np.ndarray, at: datetime) -> None:
        """Abflachen des Abklingens: Die Feuchtesteigung (Summe der Feuchtekanäle im Zustand Abklingen) ist
        mit dem Evidenzverhältnis weniger steil als ihr bisher steilster Wert in diesem Abklingen.  Ab dann
        ist ein weiterer Anstieg ein neuer Aufguss."""
        top = int(np.argmax(mu))
        if top == INFUSION:
            self._steepest = None                 # a rise: the decay starts anew after it
            return
        if top != DECAY or self._pour is None:
            return
        step = self._steps - 1
        if any(k > self._pour for k in self._separations):
            return                                # already separated
        slope, var = self._model.humidity_decay_slope()
        if self._steepest is None or slope < self._steepest[0]:
            self._steepest = (slope, var, step)
            return
        steepest, var0, at_steepest = self._steepest
        z = (slope - steepest) / math.sqrt(max(var + var0, 1e-30))
        less_steep = 0.5 * math.erfc(-z / math.sqrt(2))
        ratio = self.decision.evidence_ratio
        if less_steep < ratio * (1 - less_steep):
            return
        self._separations.append(step)
        self.decisions.append({
            "event": "infusion_decay_flattened",
            "effective_at": self._index_time(step).isoformat(),
            "decided_at": at.isoformat(),
            "steepest_at": self._index_time(at_steepest).isoformat(),
            "slope": round(slope, 5),
            "steepest": round(steepest, 5),
            "ratio": round(less_steep / max(1 - less_steep, 1e-300), 1),
        })

    def _deliver_infusion(self, detection: Detection) -> list:
        """A recognized infusion: held while a door episode is undecided, otherwise delivered."""
        if self._door_pending():
            self._held.append(detection)
            return []
        self._steam_infusions.append(detection)
        return [detection]

    def _end_steam(self, end: InfusionEnd) -> list:
        out = []
        for detection in self._steam_infusions:
            if detection.effective_at <= end.effective_at:
                out.append(InfusionUpdate(detection, end))
        self._steam_infusions = []
        return out

    def _flattening(self, at: datetime) -> list[Detection]:
        """Lüften (relative Feuchte flach) und Durchgelüftet (absolute Feuchte flach) bei offener Tür."""
        door = self._open
        if door is None or door.exchanged is not None:
            return []
        out = []
        newest = self._steps - 1
        d = self.decision
        while door.next_check <= newest and door.exchanged is None:
            j = door.next_check
            door.next_check += 2
            if door.ventilated is None:
                series, begin = self._grid_rh, door.start
            else:
                series, begin = self._grid_ah, door.ventilated[0]
            found = self._flat_stage(series, begin, j)
            if found is None:
                continue
            p, kink = found
            effective = self._index_time(kink)
            if door.ventilated is None:
                door.ventilated = (kink, j)
                out.append(Detection(Kind.VENTILATION, effective, at, self.channels, trace_at=at, probability=p))
            else:
                door.exchanged = (kink, j)
                out.append(Detection(Kind.VENTILATION_COMPLETE, effective, at, self.channels, trace_at=at,
                                     probability=p))
        return out

    def _flat_stage(self, series: list, begin: int, j: int):
        """(probability, kink step) when ``series[begin:j + 1]`` has flattened during the open door."""
        d = self.decision
        times = self._grid_t
        x = np.asarray(times[begin:j + 1])
        if len(x) < 2 * d.flat_min_samples + 1:
            return None
        p, tau = flattening_probability(x, np.asarray(series[begin:j + 1]), d.flat_min_samples,
                                        d.flat_rise_fraction)
        sure = d.evidence_ratio / (1.0 + d.evidence_ratio)       # probability at the evidence ratio
        if p < sure or tau is None:
            return None
        kink = int(np.searchsorted(times, x[0] + tau))
        # the flattening only counts while the door is still open after the kink (a closing door also
        # stops the fall) and the temperature keeps falling
        seg = slice(kink, j + 1)
        if j + 1 - kink < 4:
            return None
        for step in range(kink, j + 1):                  # the door stays the most probable explanation
            post = self._posterior(step)
            if post is None or post[OPEN] < post.max():
                return None
        if falling_probability(times[seg], self._grid_t_temp[seg]) < sure:
            return None
        return p, kink
