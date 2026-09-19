"""Kausaler laufender Tür-/Gangdetektor ohne HA, NumPy oder Recorderwissen."""
from collections import deque
from dataclasses import dataclass
from datetime import timedelta
from math import ceil, isfinite
from statistics import median

from .models import Measurement, Position, Quantity
from .timeline import Kind, utc


@dataclass(frozen=True)
class Detection:
    kind: Kind
    effective_at: object
    detected_at: object
    channels: tuple[str, ...]


def robust_slope(values, step=1):
    if len(values) < 2 or any(v is None for v in values):
        return None
    return median((values[j] - values[i]) / ((j - i) * step / 60)
                  for i in range(len(values)) for j in range(i + 1, len(values)))


class Detector:
    """Ein-Sekunden-Raster übernimmt nur bis dahin empfangene Originalwerte.

    Begrenzte Arbeitsfenster sind keine Archivverdichtung. Jedes Original wird
    separat archiviert. Fehlende/alte Messungen werden niemals interpoliert.
    """
    def __init__(self, parameters, origin, positions=(Position.UPPER, Position.LOWER), observer=None):
        self.p = parameters.values
        self.origin = utc(origin)
        self.positions = tuple(positions)
        self.index = -1
        self.latest = {}
        self.pending = deque()
        self.last_received = None
        self.capacity = int(max(self.p[k] for k in (
            "door_window_seconds", "door_humidity_seconds", "vent_baseline_seconds",
            "strong_window_seconds", "weak_window_seconds", "infusion_window_seconds"))
            + self.p["median_seconds"] + self.p["person_step_seconds"] + 1)
        self.frames = {p: deque(maxlen=self.capacity) for p in self.positions}
        self.open = False  # Anfangsannahme des Referenzkandidaten; kein Türereignis.
        self.opened_at = None
        self.baseline = {}
        self.ventilated = False
        self.context = False
        self.counts = {}
        self.levels = {}
        self.active_positions = ()
        self.faults = ()
        self.rejected = 0
        self.observer = observer
        self.diagnostic = None
        self.heating_since = None

    def report_heating(self, heating, at):
        """Zusatzregel nur bei durchgehend bestätigtem Heizbetrieb verwenden."""
        if heating is not True:
            self.heating_since = None
            self.counts["door_heating"] = 0
        elif self.heating_since is None:
            self.heating_since = utc(at)

    def accept(self, measurement: Measurement):
        if measurement.position not in self.positions:
            return False
        if ((self.last_received is not None and measurement.received_at < self.last_received)
                or (self.index >= 0 and measurement.received_at < self.origin + timedelta(seconds=self.index))):
            self.rejected += 1
            return False
        self.last_received = measurement.received_at
        self.pending.append(measurement)
        return True

    def _consume(self, now):
        while self.pending and self.pending[0].received_at <= now:
            m = self.pending.popleft()
            key = (m.position, m.quantity)
            old = self.latest.get(key)
            if (old and old.source == m.source and m.measured_at is not None
                    and old.measured_at is not None and m.measured_at < old.measured_at):
                self.rejected += 1
                continue
            if old and old.source != m.source:
                self.frames[m.position].clear()
                self.baseline.pop(m.position, None)
                self.counts.clear()
                self.levels.clear()
            self.latest[key] = m

    def _value(self, position, quantity, now):
        m = self.latest.get((position, quantity))
        timeout = self.p.get("sensor_timeout_seconds")
        if m is None or m.value is None or timeout is None:
            return None
        if (now - m.received_at).total_seconds() > timeout:
            return None
        if m.measured_at is not None and (now - m.measured_at).total_seconds() > timeout:
            return None
        if not isfinite(m.value):
            return None
        # Physikalische Plausibilität; keine erfundene sichere Heiztemperatur.
        if quantity == Quantity.HUMIDITY and not 0 <= m.value <= 100:
            return None
        if quantity == Quantity.TEMPERATURE and not -273.15 < m.value:
            return None
        return m.value

    def _series(self, position, key, window, step=1):
        frames = self.frames[position]
        need = int(window) + 1
        if len(frames) < need:
            return []
        return [frame[key] for frame in list(frames)[-need::int(step)]]

    def _slope(self, position, key, window, step=1):
        return robust_slope(self._series(position, key, window, step), step)

    def _difference(self, position, key, window):
        series = self._series(position, key, window)
        if not series or series[0] is None or series[-1] is None:
            return None
        return series[-1] - series[0]

    def _sustain(self, name, condition, seconds, step=1):
        self.counts[name] = self.counts.get(name, 0) + 1 if condition else 0
        return self.counts[name] >= ceil(seconds / step) + 1

    def _edge(self, name, condition):
        old = self.levels.get(name, False)
        self.levels[name] = condition
        return condition and not old

    def advance(self, at, *, enabled, allowed=None, on_detection=None):
        """Laufzeitkontext vor jeder Prüfung lesen; Ereignisse sofort zurückmelden.

        Ohne Kontext bleibt der reine Messvergleich zum Referenzkandidaten möglich.
        Im Betrieb liefert ausschließlich der Controller die Erkennungsfreigaben.
        """
        at = utc(at)
        final = int((at - self.origin).total_seconds())
        if final < self.index:
            raise ValueError("Detektoruhr darf nicht rückwärts laufen")
        output = []
        while self.index < final:
            self.index += 1
            now = self.origin + timedelta(seconds=self.index)
            self._consume(now)
            output.extend(self._sample(now, at, enabled, allowed, on_detection))
        return output

    def _sample(self, now, decision_at, enabled, allowed=None, on_detection=None):
        p = self.p
        available, faults = [], []
        for position in self.positions:
            frames = self.frames[position]
            raw = {"T": self._value(position, Quantity.TEMPERATURE, now),
                   "H": self._value(position, Quantity.HUMIDITY, now)}
            valid = all(v is not None for v in raw.values())
            frame = dict(raw)
            for key in ("T", "H"):
                last = [f[key] for f in list(frames)[-int(p["median_seconds"] - 1):]] if p["median_seconds"] > 1 else []
                values = last + [raw[key]]
                frame[key + "m"] = median(values) if len(values) == p["median_seconds"] and all(v is not None for v in values) else None
            frames.append(frame)
            if valid:
                available.append(position)
            else:
                faults.append(position.value + "_unavailable")
        channels = tuple(available)
        if channels != self.active_positions:
            # Kein Haltebeweis über eine Änderung der benutzten Quellen hinweg.
            self.counts.clear()
            self.levels.clear()
        self.active_positions, self.faults = channels, tuple(faults)
        trace = {"at": now, "channels": tuple(c.value for c in channels),
                 "metrics": {c.value: {} for c in channels}, "conditions": {}, "checks": {}}
        output = []
        def observe():
            trace.update(holds=dict(self.counts), signals=tuple(d.kind for d in output),
                         door_open=self.open, ventilation_context=self.context)
            self.diagnostic = trace
            if self.observer:
                self.observer(trace)
        def emit(kind):
            detection = Detection(kind, now, decision_at, tuple(c.value for c in channels))
            output.append(detection)
            if on_detection:
                on_detection(detection)
        if not channels:
            observe()
            return output
        opening, closing = (None, True) if self.open else (True, None)
        temperature_opening = bool(not self.open and enabled and self.heating_since is not None
            and (now - self.heating_since).total_seconds() >= p["door_window_seconds"] + p["median_seconds"]
            and set(channels) == {Position.UPPER, Position.LOWER})
        for c in channels:
            trend = self._slope(c, "Tm", p["door_window_seconds"])
            dh = self._difference(c, "Hm", p["door_humidity_seconds"]) if not self.open else None
            trace["metrics"][c.value].update(door_temperature_slope=trend, door_humidity_delta=dh)
            if self.open:
                closing &= trend is not None and trend > p["door_close_slope"]
            else:
                opening &= trend is not None and trend < p["door_open_slope"] and dh is not None and dh <= -p[f"door_open_humidity_{c.value}"]
            if not self.open:
                temperature_opening &= trend is not None and trend < p["door_heating_slope"]
                current = self.frames[c][-1]["Tm"]
                temperature_opening &= (p["door_heating_max_temperature_c"] > 0 and current is not None
                    and current < p["door_heating_max_temperature_c"])
        temperature_toggle = self._sustain("door_heating", temperature_opening and not self.open,
            p["door_heating_hold_seconds"])
        toggle = self._sustain("door", closing if self.open else opening,
            p["door_close_hold_seconds"] if self.open else p["door_open_hold_seconds"])
        trace["checks"].update(door_open=not self.open, door_close=self.open)
        trace["conditions"].update(door_open=opening, door_heating=temperature_opening if not self.open else None, door_close=closing)
        # Eine Lüftung kann am selben Rasterpunkt wie die Schließung belegt sein.
        # Dann wird zuerst ihr noch offener Kontext abgeschlossen.
        if self.open and not self.ventilated and (now - self.opened_at).total_seconds() >= p["vent_hold_seconds"]:
            if all(c in self.baseline and self.frames[c][-1]["Tm"] is not None
                   and self.baseline[c] - self.frames[c][-1]["Tm"] >= p[f"vent_drop_{c.value}"] for c in channels):
                self.ventilated = True
                emit(Kind.VENTILATION)
        if toggle or temperature_toggle:
            self.open = not self.open
            self.counts["door"] = 0
            self.counts["door_heating"] = 0
            if self.open:
                self.opened_at, self.context, self.ventilated = now, False, False
                self.baseline = {}
                for c in self.positions:
                    values = [f["Tm"] for f in list(self.frames[c])[-int(p["vent_baseline_seconds"]):]]
                    if len(values) == p["vent_baseline_seconds"] and all(v is not None for v in values):
                        self.baseline[c] = max(values)
                emit(Kind.DOOR_OPEN)
            else:
                self.context = self.ventilated
                emit(Kind.DOOR_CLOSE)
        eligible = bool(enabled and not self.open)
        infusion_check = eligible and (allowed is None or allowed(Kind.INFUSION))
        trace["checks"]["infusion"] = infusion_check
        infusion = infusion_check
        if infusion_check:
            for c in channels:
                dh = self._difference(c, "H", p["infusion_window_seconds"])
                dt = self._difference(c, "T", p["infusion_window_seconds"])
                trace["metrics"][c.value].update(infusion_humidity_delta=dh, infusion_temperature_delta=dt)
                infusion &= dh is not None and dh >= p["infusion_humidity"] and dt is not None and dt >= p["infusion_temperature"]
        sustained = self._sustain("infusion", infusion, p["infusion_hold_seconds"])
        trace["conditions"]["infusion"] = infusion if infusion_check else None
        if self._edge("infusion", sustained):
            emit(Kind.INFUSION)
        if self.index % p["person_step_seconds"] == 0:
            for route, kind in (("strong", Kind.PERSON_STRONG), ("weak", Kind.PERSON_WEAK)):
                checking = eligible and (route != "weak" or self.context) and (allowed is None or allowed(kind))
                trace["checks"][route] = checking
                condition = checking
                if checking:
                    for c in channels:
                        for key, quantity in (("Tm", "temperature"), ("Hm", "humidity")):
                            trend = self._slope(c, key, p[f"{route}_window_seconds"], p["person_step_seconds"])
                            trace["metrics"][c.value][f"{route}_{quantity}_slope"] = trend
                            condition &= trend is not None and trend >= p[f"{route}_{quantity}_{c.value}"]
                sustained = self._sustain(route, condition, p[f"{route}_hold_seconds"], p["person_step_seconds"])
                trace["conditions"][route] = condition if checking else None
                if self._edge(route, sustained):
                    emit(kind)
        observe()
        return output
