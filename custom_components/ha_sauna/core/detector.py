"""Kausaler laufender Tür-/Gangdetektor ohne HA, NumPy oder Recorderwissen."""

from collections import deque
from dataclasses import dataclass
from datetime import timedelta
from math import ceil, isclose, isfinite
from statistics import median

from .models import Measurement, Position, Quantity
from .moisture import absolute_humidity
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
    return median(
        (values[j] - values[i]) / ((j - i) * step / 60)
        for i in range(len(values))
        for j in range(i + 1, len(values))
    )


class Detector:
    """Ein-Sekunden-Raster übernimmt nur bis dahin empfangene Originalwerte.

    Begrenzte Arbeitsfenster sind keine Archivverdichtung. Jedes Original wird
    separat archiviert. Fehlende/alte Messungen werden niemals interpoliert.
    """

    def __init__(
        self,
        parameters,
        origin,
        positions=(Position.UPPER, Position.LOWER),
        observer=None,
    ):
        self.p = parameters.values
        self.origin = utc(origin)
        self.positions = tuple(positions)
        self.index = -1
        self.latest = {}
        self.pending = deque()
        self.last_received = None
        self.capacity = int(
            max(
                self.p[k]
                for k in (
                    "door_window_seconds",
                    "door_humidity_seconds",
                    "vent_baseline_seconds",
                    "strong_window_seconds",
                    "weak_window_seconds",
                    "infusion_window_seconds",
                )
            )
            + self.p["median_seconds"]
            + self.p["person_step_seconds"]
            + 1
        )
        self.frames = {p: deque(maxlen=self.capacity) for p in self.positions}
        self.open = False  # Anfangsannahme des Referenzkandidaten; kein Türereignis.
        self.opened_at = None
        self.closed_at = None
        self.baseline = {}
        self.ventilated = False
        self.ventilation_degraded = False
        self.ventilation_positions = ()
        self.ventilation_invalid = set()
        self.door_episode = None
        self.counts = {}
        self.levels = {}
        self.active_positions = ()
        self.faults = ()
        self.rejected = 0
        self.observer = observer
        self.diagnostic = None
        self.heating_since = None
        self.weak_anchor_at = None
        self.moisture_states = {
            "weak": {"active": False, "onset_at": None, "unknown": False}
        }

    def report_heating(self, heating, at):
        """Zusatzregel nur bei durchgehend bestätigtem Heizbetrieb verwenden."""
        if heating is not True:
            self.heating_since = None
            self.counts["door_heating"] = 0
            if self.door_episode:
                for hints in self.door_episode["hints"].values():
                    hints.pop("thermal", None)
        elif self.heating_since is None:
            self.heating_since = utc(at)

    def accept(self, measurement: Measurement):
        if measurement.position not in self.positions:
            return False
        if (
            self.last_received is not None
            and measurement.received_at < self.last_received
        ) or (
            self.index >= 0
            and measurement.received_at < self.origin + timedelta(seconds=self.index)
        ):
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
            if (
                old
                and old.source == m.source
                and m.measured_at is not None
                and old.measured_at is not None
                and m.measured_at < old.measured_at
            ):
                self.rejected += 1
                continue
            if old and old.source != m.source:
                self.frames[m.position].clear()
                if self.open and m.position in self.ventilation_positions:
                    self.ventilation_invalid.add(m.position)
                    self.ventilation_degraded = True
                else:
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
        if (
            m.measured_at is not None
            and (now - m.measured_at).total_seconds() > timeout
        ):
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
        return [frame[key] for frame in list(frames)[-need :: int(step)]]

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

    @staticmethod
    def _absolute_humidity(frame):
        """Wassergehalt nur aus einem gemeinsamen geglätteten Messrahmen."""
        temperature, humidity = frame.get("Tm"), frame.get("Hm")
        if temperature is None or humidity is None:
            return None
        try:
            return absolute_humidity(temperature, humidity)
        except ValueError:
            return None

    def _absolute_humidity_difference(self, position, window, step=1):
        """Nur ein vollständiger T/RH-Quellenzug darf Feuchteanstieg belegen."""
        frames = self.frames[position]
        need = int(window) + 1
        if len(frames) < need:
            return None
        selected = list(frames)[-need :: int(step)]
        sources = [(frame.get("Ts"), frame.get("Hs")) for frame in selected]
        values = [self._absolute_humidity(frame) for frame in selected]
        if (
            not selected
            or any(source[0] is None or source[1] is None for source in sources)
            or any(source != sources[0] for source in sources)
            or any(value is None for value in values)
        ):
            return None
        change = values[-1] - values[0]
        return 0.0 if isclose(change, 0.0, abs_tol=1e-9) else change

    def _moisture_rise(self, route, channels):
        """RH-Schwelle und AH-Ausgleich beschreiben denselben Feuchteweg."""
        condition = True
        for position in channels:
            if len(self.frames[position]) < int(self.p[f"{route}_window_seconds"]) + 1:
                return None
            humidity_slope = self._slope(
                position,
                "Hm",
                self.p[f"{route}_window_seconds"],
                self.p["person_step_seconds"],
            )
            absolute_change = self._absolute_humidity_difference(
                position,
                self.p[f"{route}_window_seconds"],
                self.p["person_step_seconds"],
            )
            if humidity_slope is None or absolute_change is None:
                return None
            condition &= (
                humidity_slope >= self.p[f"{route}_humidity_{position.value}"]
                and absolute_change > 0
            )
        return condition

    def _update_moisture_state(self, condition, now):
        """Nur eine beobachtete false→true-Flanke darf einen neuen Anstieg tragen."""
        state = self.moisture_states["weak"]
        if condition is None:
            state["unknown"] = True
            return state
        if not condition:
            state.update(active=False, onset_at=None, unknown=False)
        elif not state["active"]:
            state.update(active=True, onset_at=None if state["unknown"] else now)
        return state

    def _remember_ventilation_baseline(self, position):
        """Die letzte vollständige Vorherprobe als quellengebundene Referenz merken."""
        feature_window = int(
            max(self.p["door_window_seconds"], self.p["door_humidity_seconds"])
        )
        window = list(self.frames[position])[
            -int(self.p["vent_baseline_seconds"]) - feature_window - 1 : -feature_window
            - 1
        ]
        usable = [
            frame
            for frame in window
            if self._absolute_humidity(frame) is not None
            and frame.get("Ts") is not None
            and frame.get("Hs") is not None
            and (
                self.door_episode is None
                or (frame.get("Ts"), frame.get("Hs"))
                == self.door_episode["sources"][position]
            )
        ]
        if not usable:
            return
        reference = usable[-1]
        self.baseline[position] = {
            "temperature": reference["Tm"],
            "absolute_humidity": self._absolute_humidity(reference),
            "sources": (reference["Ts"], reference["Hs"]),
        }

    def _start_door_episode(self, channels, now):
        """Beim ersten Hinweis verfügbare Rollen und ihre Quellen festhalten."""
        self.door_episode = {
            "started_at": now,
            "positions": tuple(channels),
            "sources": {
                position: (
                    self.frames[position][-1].get("Ts"),
                    self.frames[position][-1].get("Hs"),
                )
                for position in channels
            },
            "hints": {position: {} for position in channels},
            "invalid": set(),
        }
        self.ventilation_positions = tuple(channels)
        self.baseline = {}
        self.ventilation_invalid = set()
        self.ventilation_degraded = False
        for position in channels:
            self._remember_ventilation_baseline(position)

    def _episode_frame(self, position, channels):
        """Nur die zu Beginn eingefrorene T/RH-Quellenpaarung darf weiter beweisen."""
        episode = self.door_episode
        if episode is None or position not in channels:
            return None
        frame = self.frames[position][-1]
        if (frame.get("Ts"), frame.get("Hs")) != episode["sources"][position]:
            episode["invalid"].add(position)
            self.ventilation_invalid.add(position)
            self.ventilation_degraded = True
            return None
        if position in episode["invalid"]:
            return None
        return frame

    def _remember_door_hint(self, position, name, condition, now):
        if condition:
            self.door_episode["hints"][position][name] = now

    def _door_hint(self, position, name, now, seconds):
        at = self.door_episode["hints"][position].get(name)
        if at is None:
            return False
        if (now - at).total_seconds() > seconds:
            self.door_episode["hints"][position].pop(name, None)
            return False
        return True

    def _episode_route(self, name, now):
        """Alle anfangs gültigen Rollen müssen dieselbe frische Route belegen."""
        episode = self.door_episode
        if episode is None:
            return False
        if any(position in episode["invalid"] for position in episode["positions"]):
            return False
        seconds = (
            self.p["door_window_seconds"]
            if name != "humidity"
            else self.p["door_humidity_seconds"]
        )
        timestamps = []
        for position in episode["positions"]:
            if not self._door_hint(position, name, now, seconds):
                return False
            timestamps.append(episode["hints"][position][name])
        return (max(timestamps) - min(timestamps)).total_seconds() <= seconds

    def _ventilation_proof(self, channels, now, trace):
        """Temperatur- und Wasserverlust gegen die beim Öffnen fixierte Referenz."""
        evidence = {}
        for position in self.ventilation_positions:
            if position not in channels:
                # Eine kurze Lücke pausiert nur diesen Vergleich; die beim
                # ersten Hinweis gemerkte Referenz bleibt für die Rückkehr.
                evidence[position] = None
                continue
            frame = self.frames[position][-1]
            baseline = self.baseline.get(position)
            current_water = self._absolute_humidity(frame)
            if (
                baseline is None
                or position in self.ventilation_invalid
                or current_water is None
                or (frame.get("Ts"), frame.get("Hs")) != baseline["sources"]
            ):
                if position in self.ventilation_invalid or (
                    baseline is not None
                    and (frame.get("Ts"), frame.get("Hs")) != baseline["sources"]
                ):
                    self.ventilation_degraded = True
                evidence[position] = None
                continue
            temperature_loss = baseline["temperature"] - frame["Tm"]
            water_loss = (
                (baseline["absolute_humidity"] - current_water)
                / baseline["absolute_humidity"]
                if baseline["absolute_humidity"] > 0
                else None
            )
            evidence[position] = (temperature_loss, water_loss)
            trace["metrics"][position.value].update(
                ventilation_temperature_loss=temperature_loss,
                ventilation_absolute_humidity_loss=water_loss,
            )

        dual_sensor = (
            len(self.ventilation_positions) == 2 and not self.ventilation_degraded
        )
        single_sensor = (
            len(self.ventilation_positions) == 1 and not self.ventilation_degraded
        )
        temperature_drop = all(
            evidence.get(position) is not None
            and evidence[position][0] >= self.p[f"vent_drop_{position.value}"]
            for position in self.ventilation_positions
        )
        water_loss = all(
            evidence.get(position) is not None
            and evidence[position][1] is not None
            and evidence[position][1]
            >= self.p["vent_absolute_humidity_loss_percent"] / 100
            for position in self.ventilation_positions
        )
        time_held = (now - self.opened_at).total_seconds() >= self.p[
            "vent_hold_seconds"
        ]
        proven = (
            temperature_drop
            and water_loss
            and (dual_sensor or (single_sensor and time_held))
        )
        trace["conditions"].update(
            ventilation_temperature_drop=temperature_drop,
            ventilation_water_loss=water_loss,
            ventilation_time_held=time_held if not dual_sensor else None,
            ventilation_sources_coherent=not self.ventilation_degraded,
            ventilation_proven=proven,
        )
        trace["checks"]["ventilation"] = True
        return proven

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
            raw = {
                "T": self._value(position, Quantity.TEMPERATURE, now),
                "H": self._value(position, Quantity.HUMIDITY, now),
            }
            valid = all(v is not None for v in raw.values())
            frame = dict(raw)
            frame["Ts"] = (
                self.latest.get((position, Quantity.TEMPERATURE)).source
                if raw["T"] is not None
                else None
            )
            frame["Hs"] = (
                self.latest.get((position, Quantity.HUMIDITY)).source
                if raw["H"] is not None
                else None
            )
            for key in ("T", "H"):
                last = (
                    [f[key] for f in list(frames)[-int(p["median_seconds"] - 1) :]]
                    if p["median_seconds"] > 1
                    else []
                )
                values = last + [raw[key]]
                frame[key + "m"] = (
                    median(values)
                    if len(values) == p["median_seconds"]
                    and all(v is not None for v in values)
                    else None
                )
            frames.append(frame)
            if valid:
                available.append(position)
            else:
                faults.append(position.value + "_unavailable")
        channels = tuple(available)
        if channels != self.active_positions:
            # Kein Haltebeweis über eine Änderung der benutzten Quellen hinweg.
            if self.active_positions:
                self._update_moisture_state(None, now)
            preserved = (
                {
                    name: self.counts[name]
                    for name in ("door", "door_heating")
                    if name in self.counts
                }
                if self.door_episode is not None
                else {}
            )
            self.counts.clear()
            self.counts.update(preserved)
            self.levels.clear()
        self.active_positions, self.faults = channels, tuple(faults)
        trace = {
            "at": now,
            "channels": tuple(c.value for c in channels),
            "metrics": {c.value: {} for c in channels},
            "conditions": {},
            "checks": {},
        }
        output = []

        def observe():
            trace.update(
                holds=dict(self.counts),
                signals=tuple(d.kind for d in output),
                door_open=self.open,
                ventilation_context=self.ventilated,
            )
            self.diagnostic = trace
            if self.observer:
                self.observer(trace)

        def emit(kind, effective_at=None, event_channels=None):
            detection = Detection(
                kind,
                effective_at or now,
                decision_at,
                tuple(c.value for c in (event_channels or channels)),
            )
            output.append(detection)
            if on_detection:
                on_detection(detection)

        hints = {}
        for c in channels:
            trend = self._slope(c, "Tm", p["door_window_seconds"])
            dh = (
                self._difference(c, "Hm", p["door_humidity_seconds"])
                if not self.open
                else None
            )
            trace["metrics"][c.value].update(
                door_temperature_slope=trend, door_humidity_delta=dh
            )
            hints[c] = {
                "temperature": trend is not None and trend < p["door_open_slope"],
                "humidity": dh is not None
                and dh <= -p[f"door_open_humidity_{c.value}"],
                "thermal": bool(
                    enabled
                    and self.heating_since is not None
                    and (now - self.heating_since).total_seconds()
                    >= p["door_window_seconds"] + p["median_seconds"]
                    and trend is not None
                    and trend < p["door_heating_slope"]
                ),
            }
        if (
            not self.open
            and self.door_episode is None
            and any(
                position_hints[name]
                for position_hints in hints.values()
                for name in ("temperature", "humidity", "thermal")
            )
        ):
            self._start_door_episode(channels, now)

        episode = self.door_episode
        complete_episode = bool(
            episode and all(position in channels for position in episode["positions"])
        )
        if episode:
            for position in episode["positions"]:
                frame = self._episode_frame(position, channels)
                if frame is None or position not in hints:
                    continue
                for name in ("temperature", "humidity", "thermal"):
                    self._remember_door_hint(position, name, hints[position][name], now)

        mixed_opening = thermal_opening = closing = None
        closing_positions = ()
        if episode and complete_episode:
            mixed_opening = self._episode_route(
                "temperature", now
            ) and self._episode_route("humidity", now)
            thermal_opening = self._episode_route("thermal", now)
        if episode and self.open:
            closing_slopes = {
                position: self._slope(position, "Tm", p["door_window_seconds"])
                for position in episode["positions"]
            }
            closing_positions = tuple(
                position
                for position, slope in closing_slopes.items()
                if slope is not None
            )
            for position, slope in closing_slopes.items():
                if slope is not None:
                    trace["metrics"].setdefault(position.value, {})[
                        "door_temperature_slope"
                    ] = slope
            closing = bool(closing_positions) and all(
                closing_slopes[position] > p["door_close_slope"]
                for position in closing_positions
            )
        mixed_toggle = (
            self._sustain("door", mixed_opening, p["door_open_hold_seconds"])
            if mixed_opening is not None and not self.open
            else False
        )
        thermal_toggle = (
            self._sustain(
                "door_heating", thermal_opening, p["door_heating_hold_seconds"]
            )
            if thermal_opening is not None and not self.open
            else False
        )
        close_toggle = (
            self._sustain("door", bool(closing), p["door_close_hold_seconds"])
            if self.open
            else False
        )
        trace["checks"].update(door_open=not self.open, door_close=self.open)
        trace["conditions"].update(
            door_open=mixed_opening if not self.open else None,
            door_heating=thermal_opening if not self.open else None,
            door_close=closing if self.open else None,
        )
        # Erholung beendet zuerst die kurze Öffnung. Ein gerade am selben Raster
        # sichtbarer Verlust darf sie nicht nachträglich zur Lüftung machen.
        if self.open and close_toggle:
            self.open = False
            self.closed_at = now
            self.weak_anchor_at = now
            self.counts["door"] = 0
            self.counts["door_heating"] = 0
            emit(Kind.DOOR_CLOSE, now, closing_positions)
            self.baseline = {}
            self.ventilation_degraded = False
            self.ventilation_positions = ()
            self.ventilation_invalid = set()
            self.door_episode = None
        elif self.open and closing:
            # Die frische beidseitige Erholung widerlegt eine noch unbestätigte
            # Lüftung, auch wenn ihre Schließhaltezeit noch nicht erreicht ist.
            trace["conditions"]["ventilation_recovery"] = True
        elif self.open and not self.ventilated:
            if self._ventilation_proof(channels, now, trace):
                self.ventilated = True
                emit(Kind.VENTILATION)
        elif not self.open and (mixed_toggle or thermal_toggle):
            self.open = True
            self.weak_anchor_at = None
            self.counts["door"] = 0
            self.counts["door_heating"] = 0
            self.opened_at, self.closed_at, self.ventilated = (
                episode["started_at"],
                None,
                False,
            )
            self.counts["weak"] = 0
            self.levels["weak"] = False
            emit(Kind.DOOR_OPEN, episode["started_at"], episode["positions"])
        elif not self.open and episode:
            expiry = max(p["door_window_seconds"], p["door_humidity_seconds"])
            if (now - episode["started_at"]).total_seconds() > expiry:
                self.door_episode = None
                self.baseline = {}
                self.ventilation_positions = ()
                self.ventilation_invalid = set()
        if not channels:
            self._update_moisture_state(None, now)
            observe()
            return output
        eligible = bool(enabled and not self.open)
        infusion_check = eligible and (allowed is None or allowed(Kind.INFUSION))
        trace["checks"]["infusion"] = infusion_check
        infusion = infusion_check
        if infusion_check:
            for c in channels:
                dh = self._difference(c, "H", p["infusion_window_seconds"])
                dt = self._difference(c, "T", p["infusion_window_seconds"])
                da = self._absolute_humidity_difference(c, p["infusion_window_seconds"])
                trace["metrics"][c.value].update(
                    infusion_humidity_delta=dh,
                    infusion_temperature_delta=dt,
                    infusion_absolute_humidity_delta=da,
                )
                infusion &= (
                    dh is not None
                    and dh >= p["infusion_humidity"]
                    and da is not None
                    and da > 0
                )
        sustained = self._sustain("infusion", infusion, p["infusion_hold_seconds"])
        trace["conditions"]["infusion"] = infusion if infusion_check else None
        if self._edge("infusion", sustained):
            emit(Kind.INFUSION)
        weak_opportunity = (
            self.weak_anchor_at is not None
            and now
            <= self.weak_anchor_at + timedelta(minutes=p["confirmation_minutes"])
        )
        if not weak_opportunity:
            self.counts["weak"] = 0
            self.levels["weak"] = False
        if self.index % p["person_step_seconds"] == 0:
            for route, kind in (
                ("strong", Kind.PERSON_STRONG),
                ("weak", Kind.PERSON_WEAK),
            ):
                checking = (
                    eligible
                    and (route != "weak" or weak_opportunity)
                    and (allowed is None or allowed(kind))
                )
                trace["checks"][route] = checking
                # Schwache Signale behalten ihre Feuchteflanke auch dann, wenn
                # der Kontext ihre Ausgabe gerade sperrt. Starke Signale und
                # deren Diagnose werden nur für tatsächlich prüfbare Routen
                # berechnet.
                moisture_rise = (
                    self._moisture_rise(route, channels)
                    if checking or route == "weak"
                    else None
                )
                moisture_state = (
                    self._update_moisture_state(moisture_rise, now)
                    if route == "weak"
                    else None
                )
                if checking:
                    for c in channels:
                        for key, quantity in (
                            ("Tm", "temperature"),
                            ("Hm", "humidity"),
                        ):
                            trace["metrics"][c.value][f"{route}_{quantity}_slope"] = (
                                self._slope(
                                    c,
                                    key,
                                    p[f"{route}_window_seconds"],
                                    p["person_step_seconds"],
                                )
                            )
                        trace["metrics"][c.value][
                            f"{route}_absolute_humidity_delta"
                        ] = self._absolute_humidity_difference(
                            c, p[f"{route}_window_seconds"], p["person_step_seconds"]
                        )
                fresh_weak = route != "weak" or (
                    moisture_state["onset_at"] is not None
                    and self.opened_at is not None
                    and moisture_state["onset_at"] >= self.opened_at
                )
                condition = bool(
                    checking
                    and moisture_rise
                    and (route != "weak" or moisture_state["onset_at"] is not None)
                    and fresh_weak
                )
                sustained = self._sustain(
                    route,
                    condition,
                    p[f"{route}_hold_seconds"],
                    p["person_step_seconds"],
                )
                trace["conditions"][route] = condition if checking else None
                if self._edge(route, sustained):
                    emit(kind)
        observe()
        return output
