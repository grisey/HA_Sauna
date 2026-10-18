"""Kausaler laufender Tür-/Gangdetektor ohne HA, NumPy oder Recorderwissen."""

from bisect import bisect_left, bisect_right
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import timedelta
from hashlib import sha256
from math import ceil, isclose, isfinite
from statistics import median

from .curve_change import (
    CurveFeatures,
    CurveFormFeatures,
    CurvePolicy,
    OriginalPositionWindows,
    curve_change,
    curve_form,
    curve_level,
    curve_recovery,
    curve_trend,
    original_contrasts,
    original_decline_evidence,
    original_edge_change,
    original_flattening,
    original_followup,
    original_humidity_contrasts,
    original_kink_tolerance,
    original_thermal_contrasts,
    original_time_support,
    original_water_pair,
    original_water_pair_support,
    original_water_window,
    original_window,
    original_window_changes,
)
from .models import Measurement, Position, Quantity
from .moisture import KELVIN_OFFSET_C, absolute_humidity
from .parameters import BY_KEY
from .timeline import DoorCloseEvidence, InfusionEnd, Kind, utc


@dataclass(frozen=True)
class Detection:
    kind: Kind
    effective_at: object
    detected_at: object
    channels: tuple[str, ...]
    trace_at: object | None = None
    door_close_evidence: DoorCloseEvidence | None = None


@dataclass(frozen=True)
class InfusionEpisodeUpdate:
    """Endbeleg oder ausdrücklicher Widerruf zur ursprünglichen Bestätigung."""

    confirmed_detection: Detection
    end: InfusionEnd | None


@dataclass(frozen=True)
class InfusionEpisodeSnapshot:
    """Unveränderlicher Fachstand der aktuell geführten Aufgussepisode.

    ``bound_at`` ist die Rasterzeit der ersten Pending-Bindung. Sie bleibt bei
    Fortsetzungen und Korrekturen erhalten und bezeichnet die Episode nur
    innerhalb derselben Detector-Instanz mit fortschreitendem Raster; sie ist
    weder eine globale Ereignis-ID noch eine reale Empfangszeit.
    ``onset_at`` enthält zunächst den gebundenen Form-/Anstiegsbezug und nach
    der Bestätigung die zulässige wirksame Zuordnung. Ohne gesonderten früheren
    Beginnbeleg bleiben Formbindungs-/Bestätigungszeiten Zuordnungsbezüge,
    keine behaupteten exakten physischen Anstiegsbeginne. ``confirmed_at`` übernimmt
    die Erkennungsuhr der Bestätigung, ``confirmed_detection`` genau deren
    ausgegebenes Objekt. ``level_end_at`` ist die Rasterzeit des Endbelegs,
    kein rückdatierter physischer Endzeitpunkt. ``last_rise_at`` enthält die
    zuletzt gebundenen Form-/Anstiegsbezüge je Position; ``interrupted_positions``
    nennt Positionen mit unterbrochenem Nachlaufbeleg.
    """

    phase: str
    positions: tuple[Position, ...]
    onset_at: object
    bound_at: object
    confirmed_at: object | None
    confirmed_detection: Detection | None
    level_end_at: object | None
    end_basis: str | None
    last_rise_at: tuple[tuple[Position, object], ...]
    interrupted_positions: tuple[Position, ...]


@dataclass(frozen=True)
class _CurveCandidate:
    """Vorläufiger Kurvenbeginn bis zur bestehenden Ereignisbestätigung."""

    candidate_id: str
    onset_at: object
    first_observed_at: object
    context: tuple
    features: CurveFeatures | CurveFormFeatures
    original_points: tuple
    first_newest_at: object
    fresh_newest_at: object | None = None
    fresh_observed_at: object | None = None


@dataclass(frozen=True)
class _CurveProposal:
    """Ein erstmals zugleich belegter Satz im vorhandenen Ereigniskontext."""

    context: tuple
    candidates: tuple
    first_observed_at: object
    ambiguous: bool = False
    evidence_bound_at: object | None = None


@dataclass(frozen=True)
class _OriginalPrefix:
    """Ein bereits empfangener Originalstand, noch ohne Ereignisentscheidung."""

    received_at: object
    original: bool
    context: tuple
    points: tuple
    windows: tuple
    temperatures: tuple
    humidity_kinks: tuple = ()
    humidities: tuple = ()
    time_supports: tuple = ()
    changed_position: Position | None = None
    input_time: tuple | None = None
    changed_quantity: Quantity | None = None
    window_diagnostics: tuple = ()


def robust_slope(values, step=BY_KEY["grid_seconds"].default):
    if len(values) < 2 or any(v is None for v in values):
        return None
    slopes = [
        (values[j] - values[i]) / ((j - i) * step / 60)
        for i in range(len(values))
        for j in range(i + 1, len(values))
    ]
    if not all(isfinite(slope) for slope in slopes):
        return None
    result = median(slopes)
    return result if isfinite(result) else None


def _gate(result, *, value=None, operator=None, threshold=None, unit=None,
          reference=None, reason=None):
    """Den verwendeten Befund beschreiben; der Aufrufer besitzt die Entscheidung.

    Ein positiver Abstand liegt auf der erfüllenden Seite der genannten Grenze.
    None bezeichnet einen nicht ausgewerteten oder nicht auswertbaren Vergleich. Scores
    und Abstände sind keine Ereigniswahrscheinlichkeiten.
    """
    record = {"result": result}
    if operator is not None:
        record.update(value=value, operator=operator, threshold=threshold)
        if result is not None and value is not None and threshold is not None:
            if operator in (">", ">=", "<", "<="):
                margin = value - threshold if operator in (">", ">=") else threshold - value
                if isinstance(margin, timedelta):
                    margin = margin.total_seconds()
                if isfinite(margin):
                    record["margin"] = margin
        if unit is not None:
            record["unit"] = unit
        if reference is not None:
            record["reference"] = reference
    if reason is not None:
        record["reason"] = reason
    return record


def _decision(trace, route, result, gates, *, evaluated=True, reason=None):
    """Die am Entscheidungsort verwendeten Gates vor Zustandswechseln sichern."""
    record = {"evaluated": evaluated, "result": result, "gates": gates}
    if reason is not None:
        record["reason"] = reason
    trace["decisions"][route] = record
    return result


class Detector:
    """Das konfigurierte Raster übernimmt nur bis dahin empfangene Originalwerte.

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
        self.grid_seconds = self.p["grid_seconds"]
        self.origin = utc(origin)
        self.positions = tuple(positions)
        self.index = -1
        self.latest = {}
        self.pending = deque()
        self.last_received = None
        self.capacity = int(
            max(
                self.p["median_seconds"] / self.grid_seconds,
                (self.p["infusion_window_seconds"] + self.p["median_seconds"])
                / self.grid_seconds + 1,
                (self.p["vent_baseline_seconds"]
                 + max(self.p["door_window_seconds"], self.p["door_humidity_seconds"]))
                / self.grid_seconds + 1,
                *(self.p[k] / self.grid_seconds + 1 for k in (
                    "door_window_seconds",
                    "door_humidity_seconds",
                    "strong_window_seconds",
                    "weak_window_seconds",
                    "infusion_window_seconds",
                )),
            )
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
        self.curve_policy = CurvePolicy(
            min_side_samples=self.p["curve_min_side_samples"],
            max_points=self.p["curve_max_points"],
            max_candidates=self.p["curve_max_candidates"],
            robust_iterations=self.p["curve_robust_iterations"],
            huber_delta=self.p["curve_huber_delta"],
        )
        self._curve_series = {
            (position, quantity): {
                "points": deque(maxlen=self.p["curve_max_points"]),
                "source": None,
                "time_basis": None,
                "started_at": None,
                "received_at": None,
                "version": 0,
                "last_original_version": None,
                "last_original_received_at": None,
                "epoch": 0,
            }
            for position in self.positions
            for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY)
        }
        self._curve_candidates = {}
        self._curve_evaluated = {}
        self._curve_status = {}
        self._curve_proposals = {}
        self._curve_proposal_status = {}
        self._curve_proposal_checks = {}
        self._curve_fit_cache = {}
        self._curve_recovery_evidence = {}
        self._curve_door_context = 0
        self._curve_closed_since = self.origin
        self._curve_open_effective_at = None
        self._curve_close_effective_at = None
        self._curve_ventilation_at = None
        self._curve_infusion_since = None
        self._infusion_episode = None
        self._infusion_updates = deque()
        self._infusion_segment_start = {}
        self._original_prefixes = deque(maxlen=self.p["curve_max_points"] * 2 * len(self.positions))
        self._original_prefix_overflow = False
        self._original_prefix_overflow_positions = set()
        self._original_window_cache = {}
        self._original_points_cache = {}
        self._original_kink_cache = {}
        self._original_time_support_cache = {}
        self._original_temperature_background_memo = {}
        self._original_humidity_connection_memo = {}
        self._original_infusion_candidate = None
        self._position_evidence = {}
        self._door_owner = None
        self._door_decision_owners = {}
        self._infusion_owner = None
        self._door_published_stages = set()
        self._person_owner = {}

    def _position_state(self, position):
        """Eigene Originalbelege; Ereignisse besitzt weiterhin der Detector."""
        return self._position_evidence.setdefault(position, {
            "door": None, "infusion": None, "infusion_candidate": None,
            "infusion_updates": [],
            "person": {},
        })

    def _clear_position_person(self, position):
        """Eine entwertete eigene Folge setzt auch ihre Ausgabespiegel zurück."""
        self._position_state(position)["person"].clear()
        for route, owner in tuple(self._person_owner.items()):
            if owner == position:
                self.counts[route] = 0
                self.levels[route] = False
                self._person_owner.pop(route)

    def _preferred_position(self, available):
        return next((position for position in (Position.UPPER, Position.LOWER)
                     if position in self.positions and position in available), None)

    def _position_available(self, position, quantities, now):
        """Sensorverfügbarkeit folgt den Messwerten, nicht der Fensterfrische."""
        return all(self._value(position, quantity, now) is not None
                   for quantity in quantities)

    def _refresh_position_door(self, position, prefix, now):
        state = self._position_state(position)
        episode = state["door"]
        if not self.open:
            return
        if episode is None:
            episode = state["door"] = self._new_door_episode(
                (position,), now, self._curve_open_effective_at or self.opened_at or now,
            )
        quantities = (Quantity.TEMPERATURE, Quantity.HUMIDITY)
        sources = tuple(self._curve_series[position, quantity]["source"] for quantity in quantities)
        epochs = tuple(self._curve_series[position, quantity]["epoch"] for quantity in quantities)
        if (all(self._value(position, quantity, now) is not None for quantity in quantities)
                and (episode["sources"][position] != sources or episode["epochs"][position] != epochs)):
            # Quellenabschnitt neu binden. T-Schlussstützen wurden bereits nur
            # beim Verlust ihrer eigenen T-Epoche entwertet.
            self._reset_original_ventilation(episode, "replacement_source_segment")
            episode["sources"][position] = sources
            episode["epochs"][position] = epochs
            episode["invalid"].discard(position)
            episode["followup_received_at"] = prefix.received_at
            episode["original_ventilation_phase"] = prefix.received_at
            episode.pop("original_humidity_minima", None)

    def _position_trace(self, trace, *, infusion=False):
        return {
            "metrics": {}, "conditions": {}, "checks": {},
            "decisions": ({"infusion": {**trace["decisions"]["infusion"],
                            "gates": dict(trace["decisions"]["infusion"]["gates"])}}
                          if infusion else {}),
        }

    @staticmethod
    def _record_consulted_evidence(trace, route, records):
        selection = trace.get("position_selection", {}).get(route, {})
        if selection.get("role") == "clarification":
            # Der ausgewählte vollständige Beleg steht bereits im Haupttrace.
            position = Position(selection["leading"])
            trace.setdefault("position_evidence", {})[route] = {
                position.value: records[position],
            }

    @staticmethod
    def _copy_position_trace(trace, local):
        for key, value in local.items():
            if key in ("decisions", "conditions", "checks"):
                trace[key].update(value)
            elif key == "metrics":
                for position, metrics in value.items():
                    trace[key].setdefault(position, {}).update(metrics)
            else:
                trace[key] = value

    @staticmethod
    def _bound_position_span(candidate):
        if candidate is None:
            return None
        windows = candidate.get("binding_windows", candidate.get("windows", ()))
        spans = []
        for _, window in windows:
            for item in ((window.temperature, window.humidity)
                         if isinstance(window, OriginalPositionWindows) else (window,)):
                spans.append((item.start_at, item.newest_at))
        return (min(start for start, _ in spans), max(end for _, end in spans)) if spans else None

    @staticmethod
    def _close_position_binding(episode):
        if episode is None:
            return None
        if episode.get("original_close") is not None:
            return episode["original_close"]
        for position, fall in episode.get("original_temperature_falls", {}).items():
            if fall.get("braking_window") is not None and fall.get("recovery_received_at") is not None:
                return {"received_at": fall["recovery_received_at"],
                        "windows": ((position.value, fall["braking_window"]),)}
        return None

    def _position_source_context(self, position):
        return {
            "context": self._curve_door_context,
            "sources": tuple(self._curve_series[position, quantity]["source"]
                             for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY)),
            "epochs": tuple(self._curve_series[position, quantity]["epoch"]
                            for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY)),
            "time_bases": tuple(self._curve_series[position, quantity]["time_basis"]
                                for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY)),
        }

    def _position_result(self, position, *, available, confirmed=False,
                         pending=False, refuted=False, candidate=None, reason=None):
        binding = (candidate.get("position_bindings", {}).get(position)
                   if candidate is not None else None)
        return {
            "position": position,
            "status": ("unavailable" if not available else "confirmed" if confirmed else
                       "refuted" if refuted else "pending" if pending else "idle"),
            "span": self._bound_position_span(candidate),
            "bound_at": candidate.get("received_at") if candidate is not None else None,
            "reason": reason,
            **(binding if binding is not None else self._position_source_context(position)),
        }

    def _select_position(self, route, results, owner, trace):
        """Klare Eigenbelege entscheiden; Zweitbelege klären gebundene Muster."""
        first = owner if owner in results else self._preferred_position(results)
        if first is None:
            return None
        leading = results[first]
        selected, role = first, "leading"
        if leading["status"] == "unavailable":
            replacement = self._preferred_position({
                position for position, value in results.items()
                if value["status"] != "unavailable"
            })
            if replacement is not None:
                selected, role = replacement, "replacement"
        elif leading["status"] == "pending":
            # Derselbe Ownerkontext plus überlappende eigene Bindestützen.
            # Ein bloß zeitnaher, ungebundener Verlauf reicht nicht aus.
            eligible = []
            for position, value in results.items():
                if position == first or value["status"] not in ("confirmed", "refuted"):
                    continue
                left, right = leading["span"], value["span"]
                if (left is None or right is None or leading["context"] != value["context"]
                        or max(left[0], right[0]) > min(left[1], right[1])):
                    continue
                coherence = (self.p[f"{route}_window_seconds"] if route in ("strong", "weak")
                             else self._curve_coherence_window(route))
                if abs((leading["bound_at"] - value["bound_at"]).total_seconds()) > coherence:
                    continue
                eligible.append(position)
            if len(eligible) == 1:
                selected, role = eligible[0], "clarification"
        trace.setdefault("position_selection", {})[route] = {
            "leading": first.value, "selected": selected.value, "role": role,
            "results": {position.value: {key: value for key, value in result.items()
                                         if key != "position"}
                        for position, result in results.items()},
        }
        return selected

    @staticmethod
    def _door_decision_identity(route, result):
        if result["bound_at"] is None:
            return None
        # A T-only close is independent of an unrelated RH source change.
        roles = slice(0, 1) if route == "door_close" else slice(None)
        return (result["context"], result["bound_at"],
                result["sources"][roles], result["epochs"][roles],
                result["time_bases"][roles])

    def _door_decision_owner(self, route, results):
        held = self._door_decision_owners.get(route)
        if held is None:
            return None
        position, identity = held
        if (position not in results
                or self._door_decision_identity(route, results[position]) != identity):
            self._door_decision_owners.pop(route, None)
            return None
        return position

    def _remember_door_decision_owner(self, route, selected, results):
        result = results.get(selected)
        if result is not None and result["status"] == "unavailable":
            return  # Missing measurements do not rewrite an existing own binding.
        identity = self._door_decision_identity(route, result) if result is not None else None
        if result is not None and result["status"] in ("pending", "confirmed") and identity is not None:
            self._door_decision_owners[route] = (selected, identity)
        else:
            self._door_decision_owners.pop(route, None)

    def _adopt_door_position(self, position, now):
        episode = self._position_state(position)["door"]
        if episode is None:
            return
        previous = self._door_owner
        if self.open and self.door_episode is not None and self.door_episode is not episode:
            # Nur Ereignisidentität übertragen; alle Fenster bleiben lokal.
            identity = self.door_episode.get("event_identity", {
                "effective_at": self._curve_open_effective_at,
                "positions": self.door_episode["positions"],
            })
            episode["event_identity"] = identity
            episode["followup_received_at"] = now
        self.door_episode = episode
        self._door_owner = position
        self.ventilation_positions = (position,)
        self.ventilation_invalid = set(episode["invalid"])
        self.ventilation_degraded = bool(self.ventilation_invalid)
        if previous != position:
            self.baseline = {}
            self._remember_ventilation_baseline(position)

    def _start_door_episode(self, channels, now, beginning):
        position = self._preferred_position(channels)
        if position is not None:
            self._position_state(position)["door"] = self._new_door_episode((position,), now, beginning)
            self._adopt_door_position(position, now)

    def _reset_position_doors(self):
        for state in self._position_evidence.values():
            state["door"] = None
        self._door_owner = None
        self._door_decision_owners.clear()
        self._door_published_stages.clear()

    def _bind_opening_identity(self, owner, effective_at):
        bound = self._bound_position_span(owner.get("original_open", owner.get("original_heating")))
        for state in self._position_evidence.values():
            episode = state["door"]
            if episode is None:
                continue
            if episode is not owner:
                own = self._bound_position_span(episode.get("original_open", episode.get("original_heating")))
                if bound is None or own is None or max(bound[0], own[0]) > min(bound[1], own[1]):
                    state["door"] = None
                    continue
            episode["event_identity"] = owner["event_identity"]
            episode["started_at"] = effective_at

    @staticmethod
    def _original_input_group_end(prefixes, index):
        """Nur bereits eingelesene gleiche Empfangs- und Originalzeiten bündeln."""
        current = prefixes[index]
        return (
            index + 1 == len(prefixes)
            or current.input_time is None
            or current.received_at != prefixes[index + 1].received_at
            or current.input_time != prefixes[index + 1].input_time
        )

    def _original_door_evidence(self, prefixes, channels, now, decision_at, trace, emit):
        result = (False, False, False, ())
        route = "door_close" if self.open else "door_open"
        group_original_roles = set()
        for index, prefix in enumerate(prefixes):
            # Retain both positions even when their native form is idle. The
            # records were captured at input consumption, not reconstructed here.
            trace["original_window_diagnostics"] = {
                position.value: {
                    quantity: record for name, quantity, record in prefix.window_diagnostics
                    if name == position.value
                } for position in self.positions
            }
            stage_confirmation_allowed = self._original_input_group_end(prefixes, index)
            if prefix.changed_position is not None and prefix.changed_quantity is not None:
                role = prefix.changed_position, prefix.changed_quantity
                if prefix.original:
                    group_original_roles.add(role)
                else:
                    group_original_roles.discard(role)
            stage_original_roles = frozenset(group_original_roles)
            if stage_confirmation_allowed:
                group_original_roles.clear()
            result = (False, False, False, ())
            outcomes, records, local_results, previous_stages = {}, {}, {}, {}
            for position in self.positions:
                state = self._position_state(position)
                self._refresh_position_door(position, prefix, now)
                before = state["door"]
                previous_stages[position] = {key: before.get(key) if before else None
                                             for key in ("original_ventilation_rh", "original_ventilation")}
                before_candidate = (self._close_position_binding(before) if self.open else
                                    before.get("original_open") if before else None)
                local = self._position_trace(trace)
                local_results[position] = self._original_door_position_evidence(
                    (prefix,), (position,), now, decision_at, local, state,
                    stage_confirmation_allowed=stage_confirmation_allowed,
                    stage_original_roles=stage_original_roles,
                )
                episode = state["door"]
                candidate = (self._close_position_binding(episode) if self.open else
                             episode.get("original_open") if episode else None)
                forms = local.get("original_forms", {})
                reason = forms.get("closing_candidate_reset_reason" if self.open else "opening_candidate_reset_reason")
                refuted = bool(before_candidate is not None and (
                    reason in ("temperature_counterform", "humidity_branch_recovery")
                    or forms.get("resolved_opening_counterform", False)
                    or local.get("temperature_fall_support", {}).get(position.value, {}).get("reset_reason")
                    == "renewed_temperature_fall"
                ))
                quantities = (Quantity.TEMPERATURE,) if self.open else (Quantity.TEMPERATURE, Quantity.HUMIDITY)
                available = self._position_available(position, quantities, now)
                outcomes[position] = self._position_result(
                    position, available=available,
                    confirmed=local_results[position][2 if self.open else 0],
                    pending=candidate is not None, refuted=refuted,
                    candidate=candidate or before_candidate, reason=reason,
                )
                records[position] = local
            selected = self._select_position(
                route, outcomes, self._door_decision_owner(route, outcomes), trace,
            )
            self._remember_door_decision_owner(route, selected, outcomes)
            if selected is None:
                continue
            choice = outcomes[selected]
            self._copy_position_trace(trace, records[selected])
            self._record_consulted_evidence(trace, route, records)
            if choice["status"] in ("pending", "confirmed"):
                self._adopt_door_position(selected, prefix.received_at)
                result = local_results[selected]
            elif not self.open and choice["status"] in ("idle", "refuted"):
                self.door_episode = None
                self._door_owner = None
                self.baseline = {}
                self.ventilation_positions = ()
                if choice["status"] == "refuted":
                    # Die Widerlegung betrifft den konsultierten Primärkandidaten.
                    for position in outcomes:
                        self._position_state(position)["door"] = None
            elif self.open and choice["status"] == "refuted":
                leading = Position(trace["position_selection"][route]["leading"])
                self._reject_close_candidate(leading, prefix)
            if result[0] or result[2]:
                self._original_prefixes.extend(prefixes[index + 1:])
                break
            if not self.open or not stage_confirmation_allowed:
                continue
            for stage, key, kind in (
                ("ventilation", "original_ventilation_rh_confirmed", Kind.VENTILATION),
                ("ventilation_complete", "original_ventilation_complete", Kind.VENTILATION_COMPLETE),
            ):
                if kind in self._door_published_stages:
                    continue
                candidates = {}
                for position in self.positions:
                    episode = self._position_state(position)["door"]
                    pending_key = "original_ventilation_rh" if kind == Kind.VENTILATION else "original_ventilation"
                    candidate = episode.get(pending_key) if episode else None
                    completed = episode.get(key) if episode else None
                    former = previous_stages[position][pending_key]
                    candidate_info = ({"received_at": completed["candidate_at"],
                                       "windows": completed["windows"]} if completed else candidate or former)
                    if candidate_info is not None and "humidity_windows" in candidate_info:
                        candidate_info = {**candidate_info, "windows": candidate_info["humidity_windows"]}
                    stage_name = "relative_humidity" if kind == Kind.VENTILATION else "absolute_humidity"
                    local_stage = records[position].get("original_ventilation", {}).get(stage_name, {})
                    candidates[position] = self._position_result(
                        position,
                        available=self._position_available(position,
                                  (Quantity.TEMPERATURE, Quantity.HUMIDITY), now),
                        confirmed=completed is not None and not completed.get("connection_unknown"),
                        pending=candidate is not None,
                        refuted=former is not None and local_stage.get("counterform", False),
                        candidate=candidate_info,
                    )
                carrier = self._select_position(
                    stage, candidates, self._door_decision_owner(stage, candidates), trace,
                )
                self._remember_door_decision_owner(stage, carrier, candidates)
                if carrier is not None and candidates[carrier]["status"] == "refuted":
                    for state in self._position_evidence.values():
                        if state["door"] is not None:
                            state["door"].pop(pending_key, None)
                if carrier is None or candidates[carrier]["status"] != "confirmed":
                    continue
                if kind == Kind.VENTILATION_COMPLETE and not self.ventilated:
                    continue
                self._adopt_door_position(carrier, prefix.received_at)
                self.door_episode[key].update(published=True, published_at=now)
                self._door_published_stages.add(kind)
                self._copy_position_trace(trace, records[carrier])
                self._record_consulted_evidence(trace, stage, records)
                if kind == Kind.VENTILATION:
                    self.ventilated = True
                    self._curve_ventilation_at = now
                    self._clear_curve_route("door_close", keep_status=True,
                                            reason="ventilation_after_onset")
                emit(kind, event_channels=(carrier,))
        return result

    def _reject_close_candidate(self, position, prefix):
        episode = self._position_state(position)["door"]
        window = dict(prefix.temperatures).get(position.value)
        if episode is None or window is None:
            return
        episode.pop("original_close", None)
        episode.pop("closing_onset_witnesses", None)
        episode.pop("closing_evidence", None)
        episode["rejected_close_after"] = window.newest_at
        fall = episode.get("original_temperature_falls", {}).get(position)
        if fall is not None:
            for key in ("braking_form", "braking_window", "braking_background",
                        "recovery_window", "recovery_received_at"):
                fall.pop(key, None)
        self._clear_curve_route("door_close", position=position,
                                reason="bound_close_counterform")

    def _start_infusion_episode(self, positions, beginning, now, *, rise_key, water, onsets=None):
        self._infusion_episode = self._new_infusion_episode(
            positions, beginning, now, rise_key=rise_key, water=water, onsets=onsets,
        )
        if len(positions) == 1:
            self._infusion_owner = positions[0]
            self._position_state(positions[0])["infusion"] = self._infusion_episode

    @staticmethod
    def _pause_position_infusion(state):
        state["infusion_candidate"] = None
        episode = state["infusion"]
        if episode is not None and episode["confirmed_detection"] is None:
            state["infusion"] = None
        elif episode is not None and episode["phase"] == "active":
            episode["interrupted"].update(episode["positions"])
            episode["context"] = None
            for key in ("decays", "levels", "level_status", "backgrounds", "level_evaluated"):
                episode[key].clear()
            episode["original_background"] = None

    def _adopt_infusion_position(self, position, now):
        state = self._position_state(position)
        episode = state["infusion"]
        if episode is None:
            return False
        old = self._infusion_episode
        if (old is not None and old["phase"] == "ended" and old.get("confirmed_detection") is not None
                and episode is not old
                and episode.get("confirmed_detection") is old["confirmed_detection"]):
            return False
        if (old is not None and old is not episode and old.get("confirmed_detection") is not None
                and old["phase"] != "ended"):
            # Ersatz hat eigenen Peak/Fall. Nur die gebuchte Identität bleibt.
            for key in ("confirmed_detection", "confirmed_at", "onset_at", "observed_at", "reported_end"):
                episode[key] = old[key]
            if episode["phase"] == "pending":
                episode["phase"] = "active"
            episode["followup_received_at"] = now
            episode["event_identity"] = old.get("event_identity", {
                "positions": old["positions"], "onset_at": old["onset_at"],
            })
        self._infusion_episode = episode
        self._original_infusion_candidate = state["infusion_candidate"]
        self._infusion_owner = position
        return True

    @staticmethod
    def _infusion_start_component(episode):
        return (episode.get("original_start_component") or episode.get("original_component")
                if episode is not None else None)

    def _infusion_position_result(self, position, state, previous, form, *, available):
        episode, candidate = state["infusion"], state["infusion_candidate"]
        ready = bool(episode is not None and episode.get("confirmation_ready")
                     and episode["confirmed_detection"] is None)
        # Die Bestätigung gehört zum Veröffentlichungsbeginn. Ein späterer
        # ungeprüfter Anstieg liefert weder dessen Bindung noch Anschlussstatus.
        binding = self._infusion_start_component(episode) if ready else candidate or previous
        own_context = binding.get("position_bindings", {}).get(position) if binding else None
        confirmed = bool(
            ready and binding is not None and binding.get("qualified")
            and not binding.get("connection_unknown")
            and own_context == self._position_source_context(position)
        )
        return self._position_result(
            position, available=available, confirmed=confirmed,
            pending=not ready and candidate is not None
                    and (episode is None or episode["phase"] == "pending"),
            refuted=not ready and previous is not None and form.get("terminal_negative", False),
            candidate=binding,
            reason="nonpositive_complete_water"
                   if not ready and form.get("terminal_negative") else None,
        )

    def _original_infusion_evidence(
        self, prefixes, channels, now, decision_at, floor, trace, onset_allowed, emit, allowed,
    ):
        for prefix in prefixes:
            results, records = {}, {}
            for position in self.positions:
                state = self._position_state(position)
                previous = state["infusion_candidate"]
                state["infusion_updates"].clear()
                local = self._position_trace(trace, infusion=True)
                self._original_infusion_position_evidence(
                    (prefix,), (position,), now, decision_at,
                    floor, local, onset_allowed, allowed, state,
                )
                episode = state["infusion"]
                # Ein eigenes Ende benötigt nur RH; der Beginn weiterhin T/RH.
                quantities = ((Quantity.HUMIDITY,) if episode is not None
                              and episode["phase"] in ("active", "ended") else
                              (Quantity.TEMPERATURE, Quantity.HUMIDITY))
                available = self._position_available(position, quantities, now)
                form = local.get("original_infusion", {})
                results[position] = self._infusion_position_result(
                    position, state, previous, form, available=available,
                )
                records[position] = local
            # Das Ende gehört noch dem bisherigen Besitzer, bevor die
            # Positionswahl für die nächste Episode wieder frei wird.
            ended_owner = self._infusion_episode
            if (ended_owner is not None and ended_owner["phase"] == "ended"
                    and ended_owner.get("evidence_end") is not None):
                self._update_infusion_end(ended_owner, ended_owner["evidence_end"])
            owner = (None if self._infusion_episode is not None
                     and self._infusion_episode["phase"] == "ended" else self._infusion_owner)
            selected = self._select_position("infusion", results, owner, trace)
            if selected is None:
                continue
            state = self._position_state(selected)
            self._copy_position_trace(trace, records[selected])
            self._record_consulted_evidence(trace, "infusion", records)
            active = (self._infusion_episode is not None
                      and self._infusion_episode.get("confirmed_detection") is not None
                      and self._infusion_episode["phase"] != "ended")
            if results[selected]["status"] == "refuted" and not active:
                for local in self._position_evidence.values():
                    local["infusion"] = local["infusion_candidate"] = None
                self._infusion_episode = self._original_infusion_candidate = None
                self._infusion_owner = None
                continue
            if selected != owner and active:
                # Nur Ausfall erlaubt einen neuen Folgeabschnitt einer Buchung.
                if owner is not None and results[owner]["status"] != "unavailable":
                    selected, state = owner, self._position_state(owner)
                else:
                    own = state["infusion"]
                    original = own.get("original_component") if own else None
                    reference = self._infusion_episode.get("original_component")
                    left, right = self._bound_position_span(original), self._bound_position_span(reference)
                    if (original is None or not original.get("qualified") or left is None or right is None
                            or dict(original["context"])[selected][1]
                            != self._curve_series[selected, Quantity.HUMIDITY]["epoch"]
                            or max(left[0], right[0]) > min(left[1], right[1])):
                        continue
            if not self._adopt_infusion_position(selected, prefix.received_at):
                if selected == owner and not active:
                    self._infusion_episode = self._original_infusion_candidate = None
                    self._infusion_owner = None
                continue
            episode = self._infusion_episode
            if (results[selected]["status"] == "confirmed"
                    and episode["confirmed_detection"] is None):
                ended = episode["phase"] == "ended"
                self._original_confirm_infusion(
                    episode, now, decision_at, floor, onset_allowed, emit,
                )
                if ended:
                    episode["phase"] = "ended"
                    episode["evidence_end"] = replace(
                        episode["evidence_end"], confirmed_at=decision_at,
                    )
            self._associate_infusion_followups(episode)
            for updated, end in state["infusion_updates"]:
                if updated is episode:
                    self._update_infusion_end(episode, end)
            if episode["phase"] == "ended" and episode.get("evidence_end") is not None:
                self._update_infusion_end(episode, episode["evidence_end"])

    def _associate_infusion_followups(self, owner):
        """Eine Buchung zuordnen, ausschließlich zu bereits eigenen Peakbelegen."""
        detection = owner.get("confirmed_detection")
        span = self._bound_position_span(owner.get("original_component"))
        if detection is None or span is None:
            return
        for state in self._position_evidence.values():
            episode = state["infusion"]
            component = episode.get("original_component") if episode else None
            own = self._bound_position_span(component)
            if (episode is owner or component is None or not component.get("qualified")
                    or own is None or max(span[0], own[0]) > min(span[1], own[1])
                    or episode.get("confirmed_detection") is not None):
                continue
            if any(dict(component["context"])[position][1]
                   != self._curve_series[position, Quantity.HUMIDITY]["epoch"]
                   for position in episode["positions"]):
                continue
            episode["confirmed_detection"] = detection
            episode["confirmed_at"] = owner["confirmed_at"]
            episode["event_identity"] = {"positions": owner["positions"],
                                         "onset_at": owner["onset_at"]}
            if owner["phase"] == "ended" and owner.get("reported_end") is not None:
                episode["booked_end_at"] = owner["reported_end"].effective_at
            if episode["phase"] == "pending":
                episode["phase"] = "active"

    def _correct_original_infusion(self, position, quantity, corrected_at, value):
        state = self._position_state(position)
        previous = state["infusion"]
        state["infusion_updates"].clear()
        self._correct_position_infusion(position, quantity, corrected_at, value, state)
        if previous is self._infusion_episode:
            for episode, end in state["infusion_updates"]:
                self._update_infusion_end(episode, end)
            self._infusion_episode = state["infusion"]
            self._original_infusion_candidate = state["infusion_candidate"]
        state["infusion_updates"].clear()

    def _person_position_evidence(self, route, features, checking, now, trace):
        results, evaluated = {}, {}
        for position in self.positions:
            state = self._position_state(position)["person"].setdefault(route, {
                "active": False, "onset_at": None, "unknown": False, "count": 0,
                "refuted_flank": False,
            })
            previous_active = state["active"]
            previous_onset = state["onset_at"]
            gates = {}
            rise = (self._moisture_rise(route, {position: features[position]}, gates=gates)
                    if position in features else None)
            if rise is None:
                state["unknown"] = True
            elif not rise:
                state.update(active=False, onset_at=None, unknown=False, refuted_flank=False)
            elif not state["active"] and not state.get("refuted_flank", False):
                state.update(active=True, onset_at=None if route == "weak" and state["unknown"] else now)
            fresh = route != "weak" or (state["onset_at"] is not None
                    and self.opened_at is not None and state["onset_at"] >= self.opened_at)
            condition = bool(checking and rise and fresh and not state.get("refuted_flank", False) and (
                route != "weak" or state["onset_at"] is not None
            ))
            state["count"] = state["count"] + 1 if condition else 0
            gates["own_flank_after_counterform"] = _gate(
                not state.get("refuted_flank", False),
                reason="waiting_for_own_observed_false" if state.get("refuted_flank", False) else None,
            )
            sustained = state["count"] >= ceil(self.p[f"{route}_hold_seconds"] / self.p["person_step_seconds"]) + 1
            # Nur ein tatsächlich ausgewerteter gegenteiliger Wasserverlauf
            # widerlegt eine gebundene positive Feuchtefolge.
            water = features.get(position, (None, None))[1]
            refuted = previous_active and water is not None and water <= 0
            anchor = state["onset_at"] or previous_onset
            result = self._position_result(
                position, available=rise is not None, confirmed=sustained,
                pending=condition and not sustained, refuted=refuted,
                candidate={"received_at": anchor} if anchor is not None else None,
                reason="nonpositive_complete_water" if refuted else None,
            )
            if anchor is not None:
                result["span"] = (anchor - timedelta(seconds=self.p[f"{route}_window_seconds"]), now)
            results[position] = result
            evaluated[position] = (rise, state, fresh, condition, sustained, gates)
        selected = self._select_position(route, results, self._person_owner.get(route), trace)
        if selected is None:
            self.counts[route] = 0
            return None, None, {"onset_at": None}, False, False, False, self._edge(route, False), {}
        rise, state, fresh, condition, sustained, gates = evaluated[selected]
        selection = trace["position_selection"][route]
        if results[selected]["status"] == "refuted" and selection["role"] == "clarification":
            leading = Position(selection["leading"])
            rejected = self._position_state(leading)["person"][route]
            selection["rejected_binding"] = {
                "position": leading.value, "onset_at": rejected["onset_at"],
                "count": rejected["count"], "reason": "bound_person_counterform",
            }
            # Dieselbe positive Folge darf nicht als neue Flanke wiederkehren.
            # Erst ein eigener ausgewerteter negativer Verlauf gibt sie frei.
            rejected.update(active=False, onset_at=None, count=0, refuted_flank=True)
        if results[selected]["status"] in ("idle", "refuted", "unavailable"):
            self._person_owner.pop(route, None)
        else:
            self._person_owner[route] = selected
        self.counts[route] = state["count"]
        if route == "weak":
            self.moisture_states["weak"] = state
        return selected, rise, state, fresh, condition, sustained, self._edge(route, sustained), gates

    def _heating_rejection_floor(self, position):
        state = self._position_state(position)
        rejected = state.get("heating_rejection")
        if rejected is None:
            return None
        context = (self._curve_door_context, self.heating_since,
                   self._curve_series[position, Quantity.TEMPERATURE]["epoch"])
        if rejected["context"] != context:
            state.pop("heating_rejection")
            return None
        return rejected["through"]

    def _reject_heating_position(self, position, prefix, trace):
        state = self._position_state(position)
        episode = state["door"]
        window = dict(prefix.temperatures).get(position.value) if prefix else None
        candidate = self._curve_candidates.get(("door_open_heating", position, Quantity.TEMPERATURE))
        originals = ([window.newest_at] if window is not None else [])
        if candidate is not None:
            originals.append(candidate.original_points[-1][0])
        if episode is None or not originals:
            return
        bound = episode.pop("original_heating", None)
        state["heating_rejection"] = {
            "context": (self._curve_door_context, self.heating_since,
                        self._curve_series[position, Quantity.TEMPERATURE]["epoch"]),
            "through": max(originals),
        }
        episode["retained_heating_only"] = False
        self._clear_curve_route("door_open_heating", position=position,
                                reason="bound_heating_counterform")
        trace["position_selection"]["door_open_heating"]["rejected_binding"] = {
            "position": position.value,
            "candidate_id": candidate.candidate_id if candidate is not None else None,
            "bound_at": bound["received_at"] if bound is not None else None,
            "originals_through": max(originals), "reason": "bound_heating_counterform",
        }
        if self.door_episode is episode and episode.get("original_open") is None:
            self.door_episode = None
            self._door_owner = None
            self.baseline = {}
            self.ventilation_positions = ()

    def _heating_position_evidence(self, prefixes, now, decision_at, enabled,
                                   curve_floor, mixed_toggle, trace):
        if self.open or not enabled or self.heating_since is None or mixed_toggle:
            return False, False
        results, records, flags = {}, {}, {}
        prefix = prefixes[-1] if prefixes else None
        for position in self.positions:
            state = self._position_state(position)
            episode = state["door"]
            key = ("door_open_heating", position, Quantity.TEMPERATURE)
            candidate = self._curve_candidates.get(key)
            supported = candidate is not None and self._curve_status.get(key, {}).get("status") == "supported"
            rejected_through = self._heating_rejection_floor(position)
            own_new_support = (rejected_through is None or candidate is not None
                               and candidate.original_points[0][0] >= rejected_through
                               and candidate.onset_at > rejected_through)
            humidity_available = self._original_humidity_available((position,), now)
            if episode is None and supported and own_new_support and not humidity_available:
                episode = state["door"] = self._new_door_episode((position,), now, candidate.onset_at)
            native = episode.get("original_open") if episode else None
            coherent = bool(episode is not None and self._value(position, Quantity.TEMPERATURE, now) is not None
                            and self._curve_series[position, Quantity.TEMPERATURE]["source"] == episode["sources"][position][0]
                            and self._curve_series[position, Quantity.TEMPERATURE]["epoch"] == episode["epochs"][position][0])
            opening = bool(coherent and supported and own_new_support and (not humidity_available or native is not None)
                           and candidate.onset_at >= self.heating_since
                           and candidate.original_points[0][0] >= self.heating_since)
            confirmation = False
            bound = episode.get("original_heating") if episode else None
            window = dict(prefix.temperatures).get(position.value) if prefix else None
            if opening and bound is None and window is not None:
                bound = episode["original_heating"] = {
                    "received_at": prefix.received_at, "windows": ((position.value, window),),
                }
            counterform = None
            if bound is not None and window is not None:
                reference = dict(bound["windows"])[position.value]
                form = original_followup(window, reference)
                significance = self.p["curve_original_significance"]
                counterform = bool(form is not None
                                   and form.fresh_current == self.p["curve_original_samples"]
                                   and form.current_direction > significance
                                   and form.current_change > significance)
            if episode is not None:
                episode["retained_heating_only"] = bool(coherent and supported and not humidity_available)
            if opening and not counterform:
                floor = max(curve_floor, self.heating_since, rejected_through or self.heating_since)
                self._remember_curve_proposal(
                    "door_open_heating", (position,), (Quantity.TEMPERATURE,), floor=floor,
                    context=(self._curve_door_context, episode["started_at"], floor, self.heating_since),
                    observed_at=decision_at,
                )
                confirmation = self._curve_confirmation(
                    "door_open_heating", (position,), (Quantity.TEMPERATURE,), floor=floor,
                )
                if humidity_available:
                    confirmation = False  # Native RH-Bestätigung hat oben Vorrang.
            results[position] = self._position_result(
                position, available=self._value(position, Quantity.TEMPERATURE, now) is not None,
                confirmed=confirmation, pending=opening and not counterform,
                refuted=bound is not None and counterform is True, candidate=bound,
                reason="resolved_temperature_recovery" if counterform else None,
            )
            local = self._position_trace(trace)
            _decision(local, "door_open_heating", confirmation, {
                "operation_enabled": _gate(bool(enabled)),
                "heating_context": _gate(self.heating_since is not None),
                "episode_temperature_sources": _gate(coherent),
                "heating_shape_and_original_support": _gate(opening),
                "humidity_candidate_required": _gate(humidity_available),
                "bound_humidity_candidate": _gate(native is not None if humidity_available else None),
                "fresh_bound_original_confirmation": _gate(False if humidity_available else None),
                "fixed_original_confirmation": _gate(confirmation if opening else None),
                "temperature_recovery_counterform": _gate(counterform),
                "own_support_after_counterform": _gate(own_new_support),
                **{f"{position.value}:{name}_since_heating": _gate(
                    value >= self.heating_since if value is not None else None,
                    value=value, operator=">=", threshold=self.heating_since, unit="s",
                    reference="heating_started_at",
                ) for name, value in (
                    ("onset", candidate.onset_at if candidate else None),
                    ("first_original", candidate.original_points[0][0] if candidate else None),
                )},
            }, reason="retained_heating_only")
            records[position] = local
            flags[position] = (opening, confirmation)
        selected = self._select_position(
            "door_open_heating", results,
            self._door_decision_owner("door_open_heating", results), trace,
        )
        self._remember_door_decision_owner("door_open_heating", selected, results)
        if selected is None:
            return False, False
        self._copy_position_trace(trace, records[selected])
        self._record_consulted_evidence(trace, "door_open_heating", records)
        if results[selected]["status"] == "refuted":
            leading = Position(trace["position_selection"]["door_open_heating"]["leading"])
            self._reject_heating_position(leading, prefix, trace)
        if results[selected]["status"] in ("pending", "confirmed"):
            self._adopt_door_position(selected, now)
            return flags[selected]
        return False, False


    @property
    def infusion_episode(self) -> InfusionEpisodeSnapshot | None:
        """Den Ownerstand ohne Auswertung kopieren; None ist kein Endbeleg.

        Ein verworfener oder zurückgesetzter Kontext führt keine Episode mehr.
        Alle Positionsfolgen behalten die Reihenfolge der gebundenen Episode.
        """
        episode = self._infusion_episode
        if episode is None:
            return None
        return InfusionEpisodeSnapshot(
            phase=episode["phase"],
            positions=tuple(episode["positions"]),
            onset_at=episode["onset_at"],
            bound_at=episode["observed_at"],
            confirmed_at=episode["confirmed_at"],
            confirmed_detection=episode["confirmed_detection"],
            level_end_at=episode["level_end_at"],
            end_basis=episode["end_basis"],
            last_rise_at=tuple(
                (position, episode["last_rise_at"][position])
                for position in episode["positions"]
            ),
            interrupted_positions=tuple(
                position for position in episode["positions"]
                if position in episode["interrupted"]
            ),
        )

    def _clear_curve_series(self, position, quantity):
        series = self._curve_series[position, quantity]
        series["points"].clear()
        series.update(source=None, time_basis=None, started_at=None, received_at=None)
        series["version"] += 1
        series["last_original_version"] = None
        series["last_original_received_at"] = None
        series["epoch"] += 1
        self._original_time_support_cache.pop((position, quantity), None)
        position_state = self._position_state(position)
        local_door = position_state["door"]
        if local_door is not None and position in local_door["positions"]:
            self._reset_original_ventilation(local_door, "original_epoch_changed")
            if not self.open:
                local_door.pop("original_open", None)
                local_door.pop("original_ventilation_phase", None)
            if quantity == Quantity.TEMPERATURE:
                local_door.get("original_temperature_falls", {}).pop(position, None)
                local_door.get(
                    "original_temperature_confirmation_after", {},
                ).pop(position, None)
                local_door.pop("original_close", None)
        self._curve_recovery_evidence.pop((position, quantity), None)
        if not self.open:
            position_state["door"] = None
        elif local_door is not None:
            local_door["invalid"].add(position)
        self._clear_position_person(position)
        episode = position_state["infusion"]
        if (
            episode is not None and episode["confirmed_detection"] is None
            and position in episode["positions"]
        ):
            position_state["infusion"] = position_state["infusion_candidate"] = None
            if self._infusion_episode is episode:
                self._infusion_episode = self._original_infusion_candidate = None
            episode = None
        if (
            episode is not None and episode["phase"] == "active"
            and position in episode["positions"]
        ):
            episode["interrupted"].update(episode["positions"])
            episode["context"] = None
            episode["decays"].clear()
            episode["levels"].clear()
            episode["level_status"].clear()
            episode["backgrounds"].clear()
            episode["level_evaluated"].clear()
            episode["original_background"] = None
        position_state["infusion_candidate"] = None
        if self._infusion_owner == position:
            self._original_infusion_candidate = None
        for mapping in (
            self._curve_candidates, self._curve_evaluated, self._curve_status
        ):
            for key in tuple(mapping):
                if key[1] == position and (
                    key[2] == quantity or key[0].startswith("infusion")
                ):
                    mapping.pop(key, None)
        for key, proposal in tuple(self._curve_proposals.items()):
            if any(
                candidate_key[1] == position
                and (candidate_key[2] == quantity or key[0].startswith("infusion"))
                for candidate_key, _ in proposal.candidates
            ):
                self._invalidate_curve_proposal(key, "source_context")

    def _correct_smooth_close_evidence(self, position, quantity, corrected_at):
        """Benutzte Originale vor einem folgenden Append synchron entwerten."""
        if quantity != Quantity.TEMPERATURE:
            return
        record = self._curve_recovery_evidence.get((position, quantity), {})
        form = record.get("smooth_form")
        if form is None:
            return
        if any(stamp == corrected_at for stamp, _ in form.get("original_points", ())):
            record.pop("smooth_form")
            return
        current = form.get("current_recovery")
        if current is not None and any(
            stamp == corrected_at for stamp, _ in current["points"]
        ):
            form["current_recovery"] = {
                **current, "original_supported": False, "fresh_observed_at": None,
            }

    def _record_curve_sample(self, measurement, now):
        """Originale einmal zählen; Geräte- und Empfangsuhr nicht vermischen."""
        position, quantity = measurement.position, measurement.quantity
        measured = measurement.measured_at
        time_basis = (
            "measured" if measured is not None and measured <= measurement.received_at
            else "received"
        )
        point_at = measured if time_basis == "measured" else measurement.received_at
        series = self._curve_series[position, quantity]
        value, unavailable = self._value_state(position, quantity, now)
        if unavailable == "invalid":
            self._correct_curve_proposals(position, quantity, point_at, measurement.value)
            self._correct_infusion_tail(position, quantity, point_at, measurement.value)
            self._clear_curve_series(position, quantity)
            self._record_original_prefix(
                measurement.received_at, original=False, changed_position=position,
                input_time=(time_basis, point_at), changed_quantity=quantity,
            )
            return
        if series["points"] and (
            series["source"] != measurement.source
            or series["time_basis"] != time_basis
            or point_at < series["points"][-1][0]
        ):
            self._clear_curve_series(position, quantity)
        points = series["points"]
        corrected = bool(points and points[-1][0] == point_at)
        if value is None and not (corrected and points[-1][1] != measurement.value):
            # Alter allein erzeugt keinen neuen Stützpunkt. Eine echte
            # Korrektur derselben gespeicherten Probe durchläuft dagegen
            # die bestehenden Entwertungen auch nach dem Sensortimeout.
            self._record_original_prefix(
                measurement.received_at, original=False, changed_position=position,
                input_time=(time_basis, point_at), changed_quantity=quantity,
            )
            return
        if corrected:
            # Eine Korrektur derselben Originalprobe ersetzt ihren Wert. Sie
            # erzeugt weder einen weiteren Stützpunkt noch zusätzliche Zeit.
            if points[-1][1] == measurement.value:
                series["received_at"] = measurement.received_at
                return
            points[-1] = (point_at, measurement.value)
        else:
            points.append((point_at, measurement.value))
        if series["started_at"] is None:
            series["started_at"] = point_at
        series.update(
            source=measurement.source,
            time_basis=time_basis,
            received_at=measurement.received_at,
        )
        series["version"] += 1
        if corrected:
            local_door = self._position_state(position)["door"]
            if local_door is not None and position in local_door["positions"]:
                local_door.pop("original_open", None)
                if not self.open:
                    local_door.pop("original_ventilation_phase", None)
                if quantity == Quantity.TEMPERATURE:
                    local_door.pop("original_close", None)
                    falls = local_door.get("original_temperature_falls", {})
                    fall = falls.get(position)
                    references = tuple(
                        window for key in ("window", "braking_window", "recovery_window")
                        if fall is not None and (window := fall.get(key)) is not None
                    )
                    form = fall.get("braking_form") if fall is not None else None
                    if any(
                        stamp == point_at for window in references
                        for stamp, _ in window.previous + window.current
                    ):
                        falls.pop(position, None)
                    elif form is not None and any(
                        stamp == point_at for stamp, _ in form.original_points
                    ):
                        # Nur die tatsächlich berührte Formstütze berichtigen.
                        # Ihre spätere Fortsetzung muss erneut tragen; die
                        # unberührten Fall-/Bremsfenster bleiben dieselben.
                        fall["braking_form"] = replace(
                            form,
                            original_points=tuple(
                                (stamp, measurement.value if stamp == point_at else value)
                                for stamp, value in form.original_points
                            ),
                            fresh_newest_at=None,
                            fresh_observed_at=None,
                        )
                    # Eine unberührte historische Form bleibt derselbe Beleg.
                    # Ihr aktueller Anschluss braucht nach der Korrektur aber
                    # wieder zwei eigene neue Originalblöcke dieser T-Rolle.
                    local_door.setdefault("original_temperature_confirmation_after", {})[
                        position
                    ] = point_at
                self._reset_original_ventilation(local_door, "original_corrected")
                if quantity == Quantity.HUMIDITY:
                    # Das laufende Mittelminimum hat ohne seine korrigierte
                    # Stütze keinen weiterhin belastbaren RH-Erholungsbeleg.
                    local_door.pop("original_humidity_minima", None)
            self._correct_smooth_close_evidence(position, quantity, point_at)
            self._correct_curve_proposals(position, quantity, point_at, measurement.value)
            self._correct_infusion_tail(position, quantity, point_at, measurement.value)
        else:
            series["last_original_version"] = series["version"]
            series["last_original_received_at"] = measurement.received_at
        self._record_original_prefix(
            measurement.received_at, original=not corrected, changed_position=position,
            input_time=(time_basis, point_at), changed_quantity=quantity,
        )

    def _original_context(self):
        return tuple((position, tuple(
            self._curve_series[position, quantity]["epoch"]
            for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY)
        )) for position in self.positions)

    def _original_time_support(self, position, quantity, samples, at, *, diagnostic=None):
        """Eine kausale Zeitstütze je Rolle/Epoche, nur Append neu bewerten."""
        series = self._curve_series[position, quantity]
        key = position, quantity
        cached = self._original_time_support_cache.get(key)
        same_epoch = cached is not None and cached[0] == series["epoch"]
        if same_epoch and cached[1] == series["version"]:
            if diagnostic is not None:
                diagnostic.update(cached[3], cache_hit=True, evaluated_at=cached[4])
            return cached[2]
        previous = cached[2] if same_epoch else None
        support_diagnostic = {}
        support = original_time_support(
            samples, count=self.p["curve_original_samples"],
            max_gap_seconds=self.p[f"curve_original_gap_{quantity.value}_{position.value}_seconds"],
            at=at, previous=previous, diagnostic=support_diagnostic,
        )
        if diagnostic is not None:
            diagnostic.update(support_diagnostic, cache_hit=False, evaluated_at=at)
        # Ein unerklärter Verlust des Append-Ankers darf nicht im nächsten
        # Präfix durch ein stilles Neu-Anlernen alte Verbindungen heilen.
        if support is not None or previous is None:
            self._original_time_support_cache[key] = (
                series["epoch"], series["version"], support, support_diagnostic, at,
            )
        return support

    @staticmethod
    def _original_prefix_time_support(prefix, position, quantity):
        return next((support for name, role, support in prefix.time_supports
                     if name == position.value and role == quantity), None)

    def _record_original_prefix(
        self, received_at, *, original, changed_position=None, input_time=None, changed_quantity=None,
    ):
        """Jedes angenommene Präfix erhalten; ein Raster ist kein neues Original."""
        points, windows, temperatures, humidity_kinks, humidities = [], [], [], [], []
        time_supports = []
        window_diagnostics = []
        for position in self.positions:
            streams = []
            for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY):
                series = self._curve_series[position, quantity]
                cached = self._original_points_cache.get((position, quantity))
                if cached is None or cached[0] != series["version"]:
                    cached = (series["version"], tuple(series["points"]))
                    self._original_points_cache[position, quantity] = cached
                streams.append(cached[1])
            points.append((position, *streams))
            features = []
            for quantity, samples in zip(
                (Quantity.TEMPERATURE, Quantity.HUMIDITY), streams, strict=True,
            ):
                series = self._curve_series[position, quantity]
                gap = self.p[f"curve_original_gap_{quantity.value}_{position.value}_seconds"]
                support_record = {}
                support = self._original_time_support(
                    position, quantity, samples, received_at, diagnostic=support_record,
                )
                time_supports.append((position.value, quantity, support))
                cached = self._original_window_cache.get((position, quantity))
                feature = cached[1] if cached is not None and cached[0] == series["version"] else None
                window_record = (
                    {**cached[2], "cache_hit": True, "evaluated_at": cached[3]}
                    if feature is not None else {}
                )
                if support is None:
                    feature = None
                    window_record = {"reason": "time_support_unavailable", "evaluated_at": received_at}
                elif feature is None:
                    feature = original_window(
                        samples, count=self.p["curve_original_samples"],
                        scale=self.p[f"curve_original_noise_{quantity.value}_{position.value}"],
                        max_gap_seconds=gap, at=received_at, time_support=support,
                        diagnostic=window_record,
                    )
                    self._original_window_cache[position, quantity] = (
                        series["version"], feature, dict(window_record), received_at,
                    )
                    window_record.update(cache_hit=False, evaluated_at=received_at)
                window_diagnostics.append((position.value, quantity.value, {
                    "source": series["source"], "epoch": series["epoch"],
                    "version": series["version"], "time_basis": series["time_basis"],
                    "source_started_at": series["started_at"],
                    "received_at": received_at, "available_count": len(samples),
                    "window_ready": feature is not None,
                    "time_support": support_record, "window": window_record,
                }))
                features.append(feature)
                if quantity == Quantity.HUMIDITY:
                    cached = self._original_kink_cache.get(position)
                    if cached is None or cached[0] != series["version"]:
                        kink = (original_kink_tolerance(
                            samples, feature,
                            factor=self.p["curve_original_kink_factor"],
                            min_history_windows=self.p["curve_original_kink_history_windows"],
                            max_gap_seconds=gap, time_support=support,
                        ) if feature is not None else None)
                        cached = (series["version"], kink)
                        self._original_kink_cache[position] = cached
                    humidity_kinks.append((
                        position.value, cached[1] if feature is not None else None,
                    ))
            if features[0] is not None:
                temperatures.append((position.value, features[0]))
            if features[1] is not None:
                humidities.append((position.value, features[1]))
            if all(feature is not None for feature in features):
                windows.append((position.value, OriginalPositionWindows(*features)))
        if len(self._original_prefixes) == self._original_prefixes.maxlen:
            self._original_prefix_overflow = True
            discarded = self._original_prefixes[0].changed_position
            self._original_prefix_overflow_positions.update(
                (discarded,) if discarded is not None else self.positions,
            )
        self._original_prefixes.append(_OriginalPrefix(
            received_at, original, self._original_context(), tuple(points), tuple(windows),
            tuple(temperatures), tuple(humidity_kinks), tuple(humidities), tuple(time_supports),
            changed_position, input_time, changed_quantity, tuple(window_diagnostics),
        ))

    @staticmethod
    def _original_humidity_kinks(prefix, positions):
        """Nur die vor der Entscheidung empfangenen RH-Toleranzen verwenden."""
        kinks = dict(prefix.humidity_kinks)
        return tuple((position.value, kinks.get(position.value)) for position in positions)

    def _original_humidity_kink_gate(self, kinks, *, rising):
        """Jede gebundene RH-Rolle muss den gerichteten Knick selbst belegen."""
        control_mode = self.p["curve_original_kink_factor"] == 0
        positions = {}
        for position, kink in kinks:
            passed = None
            if kink is not None:
                passed = kink.effective_change > 0 if rising else kink.effective_change < 0
            threshold = None
            if kink is not None:
                threshold = kink.tolerance if rising else -kink.tolerance
            record = _gate(
                passed, value=kink.delta if kink is not None else None,
                operator=">" if rising else "<",
                threshold=threshold, unit="%/s",
                reason="invalid_original_kink_support" if kink is None else None,
            )
            record["valid"] = kink is not None
            record["status"] = kink.status if kink is not None else "invalid"
            if kink is not None:
                record.update(
                    factor=kink.factor, catalog_scale=kink.catalog_scale,
                    history_scale=kink.history_scale, used_scale=kink.used_scale,
                    tolerance=kink.tolerance, delta=kink.delta,
                    effective_change=kink.effective_change,
                    scale_source=("catalog_history_max" if kink.status == "history"
                                  else "catalog_fallback"),
                    history_count=kink.history_count, history_start=kink.history_start,
                    history_end=kink.history_end, start_at=kink.window.start_at,
                    newest_at=kink.window.newest_at,
                )
            positions[position] = record
        return {
            # Faktor null ist der unveränderte rohe Kontrollpfad, auch wenn
            # zusätzliche historische Toleranzstützen nicht auswertbar sind.
            "result": bool(positions) and (control_mode or all(
                record["result"] is True for record in positions.values()
            )),
            "direction": "rise" if rising else "fall", "positions": positions,
            "factor": self.p["curve_original_kink_factor"],
            "applied": not control_mode, "control_mode": control_mode,
        }

    @staticmethod
    def _original_bound_context(context, positions):
        return tuple(item for item in context if item[0] in positions)

    def _original_prefix_valid(self, prefix, positions=None, quantities=None):
        positions = self.positions if positions is None else positions
        quantities = ((Quantity.TEMPERATURE, Quantity.HUMIDITY)
                      if quantities is None else quantities)
        indices = tuple(0 if quantity == Quantity.TEMPERATURE else 1 for quantity in quantities)
        previous, current = dict(prefix.context), dict(self._original_context())
        if any(tuple(previous[position][index] for index in indices)
               != tuple(current[position][index] for index in indices) for position in positions):
            return False
        for position, temperature, humidity in prefix.points:
            if position not in positions:
                continue
            for quantity, points in (
                (Quantity.TEMPERATURE, temperature), (Quantity.HUMIDITY, humidity),
            ):
                if quantity not in quantities:
                    continue
                current = dict(self._curve_series[position, quantity]["points"])
                if any(stamp in current and current[stamp] != value
                       for stamp, value in points):
                    return False
        return True

    @staticmethod
    def _original_newest(contrasts):
        return tuple((position, pair.temperature.newest_at, pair.humidity.newest_at)
                     for position, pair in contrasts.positions)

    @staticmethod
    def _original_support(windows):
        return {
            position: {
                f"{name}_{anchor}_at": getattr(window, anchor + "_at")
                for name, window in (("temperature", pair.temperature), ("humidity", pair.humidity))
                for anchor in ("start", "newest")
            }
            for position, pair in windows
        }

    @staticmethod
    def _original_newer(contrasts, reference):
        previous = {position: (temperature, humidity)
                    for position, temperature, humidity in reference}
        return all(
            position in previous
            and pair.temperature.newest_at > previous[position][0]
            and pair.humidity.newest_at > previous[position][1]
            for position, pair in contrasts.positions
        )

    @staticmethod
    def _original_role_windows(windows, quantities):
        """Die Rollen eines gespeicherten Positionsbelegs unverändert lesen."""
        return tuple(
            (name, quantity, getattr(pair, quantity.value))
            for name, pair in windows for quantity in quantities
        )

    def _original_contiguous(self, prefix, references):
        """Jedes Original seit der letzten Stütze muss anschließen können."""
        streams = {
            (position.value, quantity): samples
            for position, temperature, humidity in prefix.points
            for quantity, samples in (
                (Quantity.TEMPERATURE, temperature), (Quantity.HUMIDITY, humidity),
            )
        }
        for name, quantity, stamp in references:
            points = streams.get((name, quantity), ())
            start = bisect_left(points, stamp, key=lambda point: point[0])
            if start == len(points) or points[start][0] != stamp:
                return False
            support = self._original_prefix_time_support(prefix, Position(name), quantity)
            if support is None or not support.covers(stamp, points[-1][0]):
                return False
        return bool(references)

    def _retain_interrupted_evidence(self, position, route, evidence, at):
        """Beobachtete Referenzen bleiben begrenzt beim bisherigen Besitzer."""
        if evidence is None:
            return
        held = self._position_state(position).setdefault(
            "interrupted_evidence", deque(maxlen=self.p["curve_max_candidates"]),
        )
        if not any(item["evidence"] is evidence for item in held):
            held.append({
                "route": route, "retained_at": at, "evidence": evidence,
                "source_context": tuple((quantity, self._curve_series[position, quantity]["source"],
                                         self._curve_series[position, quantity]["epoch"])
                                        for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY)),
            })

    def _mark_original_unknown(self, record, prefix, positions, quantities):
        """Der neue beobachtete Abschnitt ersetzt keinen unbekannten Verlauf."""
        starts = []
        for position in positions:
            for quantity in quantities:
                support = self._original_prefix_time_support(prefix, position, quantity)
                if support is not None:
                    # A real missing anchor/context needs subsequent evidence;
                    # a mere report interval no longer reaches this path.
                    starts.append(support.newest_at)
        if starts:
            previous = record.get("unknown_after")
            record["unknown_after"] = max(starts + ([previous] if previous is not None else []))
        record["connection_unknown"] = True

    def _original_segment_points(self, prefix, position, quantity):
        support = self._original_prefix_time_support(prefix, position, quantity)
        if support is None:
            return ()
        start = support.start_at
        samples = next((temperature if quantity == Quantity.TEMPERATURE else humidity
                        for role, temperature, humidity in prefix.points if role == position), ())
        return tuple(point for point in samples if point[0] >= start)

    def _original_resume_ready(self, prefix, positions, quantities, record):
        after = record.get("unknown_after")
        if after is None or not self._original_prefix_valid(prefix, positions, quantities):
            return False
        windows = {Quantity.TEMPERATURE: dict(prefix.temperatures),
                   Quantity.HUMIDITY: dict(prefix.humidities)}
        return all(
            (window := windows[quantity].get(position.value)) is not None
            and window.start_at >= after and window.newest_at > after
            and (support := self._original_prefix_time_support(prefix, position, quantity)) is not None
            and support.covers(window.start_at, window.newest_at)
            for position in positions for quantity in quantities
        )

    def _original_followups(self, prefix, references, *, continuity=None):
        """Neue Stützblöcke nur im lückenlosen Ast des gebundenen Belegs."""
        anchors = tuple(
            (name, quantity, reference.newest_at)
            for name, quantity, reference in references
        ) if continuity is None else continuity
        if not self._original_contiguous(prefix, anchors):
            return None
        temperatures, positions = dict(prefix.temperatures), dict(prefix.windows)
        result = {}
        for name, quantity, reference in references:
            window = (temperatures.get(name) if quantity == Quantity.TEMPERATURE else
                      positions[name].humidity if name in positions else None)
            followup = original_followup(window, reference)
            if followup is None:
                return None
            result[name, quantity] = followup
        return result or None

    @staticmethod
    def _original_followup_trace(followups):
        """Normierte Formabstände beschreiben keine Wahrscheinlichkeit."""
        result = {}
        for (name, quantity), followup in (followups or {}).items():
            result.setdefault(name, {})[quantity.value] = {
                "reference_newest_at": followup.reference.newest_at,
                "current_start_at": followup.window.current[0][0],
                "newest_at": followup.window.newest_at,
                **{key: getattr(followup, key) for key in (
                    "fresh_previous", "fresh_current", "current_direction", "previous_direction",
                    "current_change", "previous_change", "level_change", "residual",
                    "reference_level_loss",
                    "current_change_from_reference_current",
                    "previous_change_from_reference_current",
                )},
            }
        return result

    def _original_temperature_braking(self, followups):
        """Zwei neue T-Blöcke bestätigen die Bremse desselben echten Fallastes."""
        count = self.p["curve_original_samples"]
        significance = self.p["curve_original_significance"]
        return bool(followups) and all(
            form.fresh_previous == count and form.fresh_current == count
            and form.reference.b < -significance * form.reference.e_minus
            and form.current_change > significance
            and form.previous_change > significance
            for form in followups.values()
        )

    def _original_temperature_braking_form(self, episode, prefix, position, fall):
        """Den gebundenen Bremsknick durch spätere eigene Originale nachprüfen."""
        if (episode.get("rejected_close_after") is not None
                and dict(prefix.temperatures)[position.value].current[0][0]
                <= episode["rejected_close_after"]):
            return
        held = fall.get("braking_form")
        floor = max(episode["started_at"], fall["window"].newest_at)
        samples = next(temperature for role, temperature, _ in prefix.points if role == position)
        support = self._original_prefix_time_support(prefix, position, Quantity.TEMPERATURE)
        key = ("door_close", position, Quantity.TEMPERATURE)
        source = self._curve_series[position, Quantity.TEMPERATURE]["source"]
        if held is not None:
            last_at = held.original_points[-1][0]
            if not samples or samples[-1][0] <= last_at:
                return
            if support is None or not support.covers(last_at, samples[-1][0]):
                fall["braking_form"] = replace(
                    held, fresh_newest_at=None, fresh_observed_at=None,
                )
                return
            # Die ursprüngliche Stütze gehört zum festen Beginn. Ein rollendes
            # Suchfenster darf sie nicht nachträglich abschneiden. Nur kausal
            # anschließende eigene Originale erweitern denselben Formbeleg.
            points = held.original_points + tuple(
                point for point in samples if point[0] > last_at
            )
            features = self._fit_candidate_form(
                "door_close", points, floor, Quantity.TEMPERATURE,
                candidate=held,
                fit_context=("original_temperature_braking", position, source, fall["epoch"]),
            )
            if features is None or features.evidence == "unresolved":
                fall["braking_form"] = replace(
                    held, original_points=points,
                    fresh_newest_at=None, fresh_observed_at=None,
                )
            elif self._curve_supports(
                "door_close", features, floor, Quantity.TEMPERATURE,
            ):
                fall["braking_form"] = replace(
                    held, features=features, original_points=points,
                    fresh_newest_at=features.newest_at,
                    fresh_observed_at=prefix.received_at,
                )
            else:
                fall.pop("braking_form")
            return
        start = prefix.received_at - timedelta(seconds=self.p["curve_window_seconds"])
        points = tuple(point for point in samples if start <= point[0] <= prefix.received_at)
        if not points or support is None or not support.covers(points[0][0], points[-1][0]):
            return
        candidate = self._curve_candidates.get(key)
        status = self._curve_status.get(key, {})
        originals = dict(points)
        if (candidate is None or candidate.features.hinge_order != 1
                or status.get("status") != "supported"
                or status.get("source") != source
                or not self._curve_supports(
                    "door_close", candidate.features, floor, Quantity.TEMPERATURE,
                )
                or candidate.features.newest_at != points[-1][0]
                or candidate.first_observed_at > prefix.received_at
                or candidate.fresh_observed_at is not None
                and candidate.fresh_observed_at > prefix.received_at
                or not all(originals.get(stamp) == value
                           for stamp, value in candidate.original_points)):
            fit_key = ("original_temperature_braking", self.curve_policy, points, floor)
            if fit_key not in self._curve_fit_cache:
                self._curve_fit_cache[fit_key] = curve_change(
                    points, policy=self.curve_policy, direction=1, onset_not_before=floor,
                )
            features = self._curve_fit_cache[fit_key]
            if not self._curve_supports("door_close", features, floor, Quantity.TEMPERATURE):
                return
            candidate = _CurveCandidate(
                f"door_close:{position.value}:temperature:{source}:"
                f"{fall['epoch']}:original_brake:{features.onset_at.isoformat()}:"
                f"{prefix.received_at.isoformat()}",
                features.onset_at, prefix.received_at, (fall["epoch"], floor), features,
                points, features.newest_at,
            )
        fall["braking_form"] = candidate

    @staticmethod
    def _original_temperature_braking_forms(episode, positions):
        falls = episode.get("original_temperature_falls", {})
        return bool(positions) and all(
            position in falls and not falls[position].get("connection_unknown")
            and (witness := falls[position].get("braking_form")) is not None
            and witness.fresh_newest_at is not None
            and witness.fresh_newest_at > witness.first_newest_at
            for position in positions
        )

    @staticmethod
    def _original_close_confirmation_fresh(episode, prefix, positions):
        corrected = episode.get("original_temperature_confirmation_after", {})
        windows = dict(prefix.temperatures)
        return all(
            position not in corrected or all(
                stamp > corrected[position]
                for stamp, _ in windows[position.value].previous + windows[position.value].current
            )
            for position in positions
        )

    def _original_temperature_counterform(self, window, recovery):
        """Ein neuer vollständiger T-Block widerlegt die gehaltene Bremse."""
        form = original_followup(window, recovery)
        significance = self.p["curve_original_significance"]
        return bool(
            form is not None
            and form.fresh_current == self.p["curve_original_samples"]
            and form.current_direction < -significance
            and form.current_change_from_reference_current < -significance
        )

    def _original_temperature_backgrounds(self, prefix, windows):
        """Den beobachteten T-Hintergrund am gebundenen Originalfenster halten."""
        points = {position.value: temperature
                  for position, temperature, _ in prefix.points}
        result = []
        for name, window in windows:
            support = self._original_prefix_time_support(prefix, Position(name), Quantity.TEMPERATURE)
            if support is None or not support.covers(window.start_at, window.newest_at):
                # Das gebundene historische Fenster bleibt gültig, auch wenn
                # seine Originale inzwischen den Rollenring verlassen haben.
                # Der Ereignisbesitzer prüft den fortgeschriebenen Anschluss.
                support = window.time_support
            factor = self.p["curve_original_significance"]
            history = self.p["curve_original_kink_history_windows"]
            gap = self.p[f"curve_original_gap_temperature_{name}_seconds"]
            arguments = (points[name], window, factor, history, gap, support)
            cached = self._original_temperature_background_memo.get(name)
            # Das Ergebnis hält genau dieses Fensterobjekt. Gleiche Werte
            # eines anderen Fensters rechtfertigen keine alte Objektbindung.
            if (cached is None or cached[0][1] is not window
                    or cached[0] != arguments):
                cached = (arguments, original_kink_tolerance(
                    points[name], window, factor=factor,
                    min_history_windows=history, max_gap_seconds=gap,
                    time_support=support,
                ))
                self._original_temperature_background_memo[name] = cached
            result.append((name, cached[1]))
        return tuple(result)

    def _original_infusion_return_evidence(self, component, prefix):
        """Den ersten vollständig eigenen RH-Rücklauf beim Aufguss halten."""
        if (component is None or not (component.get("strong") or component.get("qualified"))
                or not prefix.original
                or component["received_at"] > prefix.received_at
                or component["context"] != self._original_bound_context(
                    prefix.context, component["positions"],
                )
                or not self._original_prefix_valid(
                    prefix, component["positions"], (Quantity.HUMIDITY,),
                )):
            return
        significance = self.p["curve_original_significance"]
        returns = component.setdefault("humidity_returns", {})
        windows = dict(prefix.humidities)
        for position in component["positions"]:
            name = position.value
            window = windows.get(name)
            peak = component["peaks"].get(position)
            held = returns.get(name)
            if (window is None or peak is None or window.current[0][0] <= peak[0]
                    or held is not None and held["peak"] == peak):
                continue
            if window.v < -significance * window.e:
                # Unveränderlicher erster Fall; ein späterer, steilerer Ast
                # darf diese Referenz nicht fortlaufend ersetzen.
                returns[name] = {
                    "peak": peak, "window": window, "received_at": prefix.received_at,
                }

    @staticmethod
    def _original_opening_return_reference(candidate, position):
        """Den jüngsten zugehörigen Aufgussbesitzer gemeinsam konsumieren."""
        applicable = tuple(
            (received_at, reference["component"])
            for received_at, reference in candidate.get("infusion_references", {}).items()
            if position in reference["component"]["positions"]
            and (reference["component"].get("strong") or reference["component"].get("qualified"))
        )
        return max(applicable, key=lambda item: item[0]) if applicable else None

    def _original_opening_return_evidence(self, candidate, prefix, positions):
        """Einen späteren RH-Knick mit eigenem Vorlauf und Folgeblock belegen."""
        witnesses = dict(candidate["onset_witnesses"])
        current = dict(prefix.humidities)
        significance = self.p["curve_original_significance"]
        threshold = self.p["curve_original_open_contrast"]
        count = self.p["curve_original_samples"]
        roles = {}
        for position in positions:
            reference = self._original_opening_return_reference(candidate, position)
            if reference is None:
                continue
            received_at, component = reference
            name = position.value
            held = component.get("humidity_returns", {}).get(name)
            witness = witnesses.get(("door_open", position, Quantity.HUMIDITY))
            native = None
            if (held is not None and held["peak"] == component["peaks"].get(position)
                    and witness is not None):
                for intervals in (
                    candidate.get("local_humidity_onset_intervals", {}),
                    candidate["humidity_fall_transition_intervals"],
                    candidate["humidity_onset_intervals"],
                ):
                    proposed = intervals.get(name)
                    if proposed is None:
                        continue
                    window = proposed["window"]
                    if (window.start_at > held["window"].newest_at
                            and window.previous[-1][0] <= witness.onset_at <= window.newest_at
                            and proposed["kink"].effective_change < 0):
                        native = proposed
                        break
            form = (original_followup(current[name], native["window"])
                    if native is not None and name in current else None)
            supported = bool(
                form is not None and form.fresh_current == count
                and form.current_direction < -threshold
                and form.current_change < -significance
                and form.reference_level_loss > threshold
            )
            roles[name] = {
                "result": supported, "component_received_at": received_at,
                "peak": component["peaks"].get(position),
                "return_newest_at": held["window"].newest_at if held else None,
                "native_start_at": native["window"].start_at if native else None,
                "native_newest_at": native["window"].newest_at if native else None,
                "onset_at": witness.onset_at if witness is not None else None,
                "followup": self._original_followup_trace(
                    {(name, Quantity.HUMIDITY): form} if form is not None else None,
                ),
            }
        return {"result": all(role["result"] for role in roles.values()), "positions": roles}

    def _original_opening_infusion_guard(self, candidate, prefix, positions, state):
        """Aufgussreferenzen und ergänzende lokale T-Fallbelege bereitstellen.

        Die RH-Astzuordnung wird nach der Aktualisierung der Türzeugen gelesen.
        Ein lokaler T-Fall allein macht die erste Aufgussrückkehr nicht zur Tür.
        """
        context = self._original_bound_context(prefix.context, positions)
        episode = state["infusion"]
        components = [state["infusion_candidate"]]
        if episode is not None and episode["phase"] in ("pending", "active"):
            components.extend((episode.get("original_start_component"),
                               episode.get("original_component")))
        references = candidate.setdefault("infusion_references", {})
        for identity, reference in tuple(references.items()):
            owner = reference["component"]
            if not (owner.get("strong") or owner.get("qualified")):
                references.pop(identity)
        for component in components:
            if (component is None or not (component.get("strong") or component.get("qualified"))
                    or component["received_at"] > prefix.received_at
                    or self._original_bound_context(component["context"], positions) != context
                    or not all(position in component["positions"] for position in positions)):
                continue
            self._original_infusion_return_evidence(component, prefix)
            identity = component["received_at"]
            if identity not in references:
                windows = tuple((name, pair) for name, pair in component["binding_windows"]
                                if Position(name) in positions)
                references[identity] = {
                    "component": component,
                    "windows": windows,
                    "temperature_backgrounds": self._original_temperature_backgrounds(
                        prefix, tuple((name, pair.temperature) for name, pair in windows),
                    ),
                    "onsets": tuple(component["onsets"].items()),
                }
        current = dict(prefix.temperatures)
        threshold = self.p["curve_original_open_contrast"]
        count = self.p["curve_original_samples"]
        diagnostics = []
        independent = True
        significance = self.p["curve_original_significance"]
        # Der Türbesitzer hat diesen fortgeschriebenen Anschluss schon seit
        # seiner Bindung geprüft; eine alte Aufgussbaseline braucht keinen
        # dauerhaft im Ring gespeicherten Zeitanker.
        contiguous = self._original_contiguous(prefix, candidate["continuity"])
        temperature_witnesses = candidate.setdefault("temperature_fall_witnesses", {})
        temperature_backgrounds = candidate.setdefault("temperature_fall_backgrounds", {})
        current_backgrounds = dict(self._original_temperature_backgrounds(
            prefix, tuple((name, window) for name, window in prefix.temperatures
                          if Position(name) in positions),
        )) if references else {}
        local_roles = {}
        for position in positions if references else ():
            name = position.value
            window = current.get(name)
            background = current_backgrounds.get(name)
            if (name not in temperature_witnesses and window is not None
                    and background is not None and background.effective_change < 0
                    and window.v < -significance * window.e):
                temperature_witnesses[name] = window
                temperature_backgrounds[name] = background
            reference = temperature_witnesses.get(name)
            form = original_followup(window, reference) if window and reference else None
            supported = bool(
                contiguous and form is not None
                and form.fresh_current == count
                and form.current_direction < -threshold
                and form.current_change < -significance
            )
            local_roles[name] = {
                "supported": supported,
                "reference_newest_at": reference.newest_at if reference is not None else None,
                # Die feste Knickreferenz bleibt der Maßstab. Nur ein aktuell
                # unterstützter eigener Fall erweitert ihre zeitliche Zuordnung
                # bis zum jüngsten tatsächlich ausgewerteten T-Original.
                "attribution_bounds": (
                    (reference.start_at, window.newest_at if supported else reference.newest_at)
                    if reference is not None else None
                ),
                "followup": self._original_followup_trace({(name, Quantity.TEMPERATURE): form}
                                                          if form is not None else None),
            }
        new_local_fall = bool(local_roles) and all(role["supported"] for role in local_roles.values())
        for received_at, reference in references.items():
            backgrounds = dict(reference["temperature_backgrounds"])
            roles = {}
            for name, pair in reference["windows"]:
                window = current.get(name)
                followup = original_followup(window, pair.temperature) if window else None
                proof = (original_decline_evidence(
                    window.current, pair.temperature, background=backgrounds[name],
                ) if window is not None and backgrounds[name] is not None else None)
                supported = bool(
                    contiguous and followup is not None and followup.fresh_current == count
                    and proof is not None and len(proof.points) == count
                    and proof.direction < -threshold and proof.level_loss > threshold
                )
                independent &= supported
                roles[name] = {
                    "independent_temperature_loss": _gate(supported),
                    "reference_newest_at": pair.temperature.newest_at,
                    "newest_at": window.newest_at if window is not None else None,
                    "direction": proof.direction if proof else None,
                    "level_loss": proof.level_loss if proof else None,
                    "level_background_center": proof.level_center if proof else None,
                    "level_difference_scale": proof.level_scale if proof else None,
                }
            diagnostics.append({
                "rise_received_at": received_at, "humidity_onsets": reference["onsets"],
                "support": self._original_support(reference["windows"]),
                "contiguous_support": _gate(contiguous), "positions": roles,
            })
        admitted = not references or new_local_fall
        return admitted, {
            "result": admitted, "native_infusion_references": tuple(diagnostics),
            "baseline_temperature_loss": _gate(independent if references else None),
            "new_local_temperature_fall": {"result": new_local_fall, "positions": local_roles},
            "reason": ("new_local_temperature_fall" if new_local_fall else
                       "infusion_return_without_new_temperature_fall" if references else
                       "no_bound_original_infusion"),
        }

    def _original_opening_humidity_connected(self, samples, witness, window, name, time_support):
        """Keinen alten RH-Knick über eine vollständig belegte Erholung tragen."""
        count = self.p["curve_original_samples"]
        significance = self.p["curve_original_significance"]
        # Die alte Zeugenstütze darf den Ring verlassen; der Aufrufer prüft
        # den fortgeschriebenen Kandidatenanschluss mit _original_contiguous.
        if time_support is None:
            return None
        samples = tuple(samples)
        gap = self.p[f"curve_original_gap_humidity_{name}_seconds"]
        arguments = (samples, witness.onset_at, window, count, significance, gap, time_support)
        cached = self._original_humidity_connection_memo.get(name)
        if cached is not None and cached[0] == arguments:
            return cached[1]
        connected = True
        for end in range(2 * count, len(samples) + 1):
            block = samples[end - 2 * count:end]
            if block[count][0] <= witness.onset_at:
                continue
            native = original_window(
                block, count=count, scale=window.scale,
                max_gap_seconds=gap, at=block[-1][0], time_support=time_support,
            )
            if native is not None and native.v > significance * native.e:
                connected = False
                break
        self._original_humidity_connection_memo[name] = (arguments, connected)
        return connected

    def _original_local_fall_intervals(self, prefix, windows, *, kinks=None):
        """Den ersten Originalschritt eines eigenen belegten RH-Knicks halten."""
        result = {}
        significance = self.p["curve_original_significance"]
        kinks = dict(prefix.humidity_kinks) if kinks is None else kinks
        for name, pair in windows:
            kink = kinks.get(name)
            if kink is None or kink.window is not pair.humidity:
                continue
            # Der Gesamtknick qualifiziert die Bindung; den eigenen Beginn
            # löst hier die Sekante mit derselben Knicktoleranz auf.
            changes = original_window_changes(pair.humidity, tolerance=kink.tolerance)
            for change in changes or ():
                if max(change.direction, change.change) >= -significance:
                    continue
                result[name] = {
                    "interval": change, "window": pair.humidity, "kink": kink,
                    "received_at": prefix.received_at,
                }
                break
        return result

    def _original_opening_local_fall_intervals(self, candidate, prefix, windows):
        """Den späteren RH-Ast mit seinem eigenen Originalvorlauf bewerten."""
        kinks, boundaries = {}, {}
        transitions = candidate.get("temperature_fall_witnesses", {})
        held_intervals = candidate.get("local_humidity_onset_intervals", {})
        for name, pair in windows:
            if name not in transitions or name in held_intervals:
                continue
            position = Position(name)
            reference = self._original_opening_return_reference(candidate, position)
            if reference is None:
                continue
            received_at, component = reference
            held = component.get("humidity_returns", {}).get(name)
            if (held is None or held["peak"] != component["peaks"].get(position)
                    or pair.humidity.start_at <= held["window"].newest_at):
                continue
            after = max(held["window"].newest_at, transitions[name].start_at)
            samples = tuple(point for point in self._original_segment_points(
                prefix, position, Quantity.HUMIDITY,
            ) if point[0] >= after)
            support = self._original_prefix_time_support(prefix, position, Quantity.HUMIDITY)
            # Der gehaltene lokale T-Fall begrenzt schon die zulässige
            # RH-Sekante. Sein eigener Vorlauf begrenzt auch deren Hintergrund;
            # der erste Aufgussrücklauf bleibt die früheste mögliche Grenze.
            # Instrumentmaßstab, Historienminimum und Faktor bleiben gleich.
            kinks[name] = original_kink_tolerance(
                samples, pair.humidity, factor=self.p["curve_original_kink_factor"],
                min_history_windows=self.p["curve_original_kink_history_windows"],
                max_gap_seconds=self.p[f"curve_original_gap_humidity_{name}_seconds"],
                time_support=support,
            ) if support is not None else None
            boundaries[name] = {
                "component_received_at": received_at, "background_after": after,
                "return_newest_at": held["window"].newest_at,
                "temperature_reference_start_at": transitions[name].start_at,
            }
        intervals = self._original_local_fall_intervals(prefix, windows, kinks=kinks)
        for name, interval in intervals.items():
            interval.update(boundaries[name])
        diagnostic = self._original_humidity_kink_gate(tuple(kinks.items()), rising=False)
        for name, boundary in boundaries.items():
            diagnostic["positions"][name].update(boundary)
        return intervals, diagnostic

    def _original_opening_onset_witnesses(
        self, episode, prefix, windows, *, quantities=(Quantity.HUMIDITY,),
        transitions=None, onset_intervals=None, diagnostics=None,
    ):
        """RH-Beginne desselben fortlaufenden Quellenastes kausal zusammenführen."""
        points = {position: tuple(self._original_segment_points(prefix, position, quantity)
                                  for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY))
                  for position, _, _ in prefix.points}
        epochs = dict(prefix.context)
        witnesses = []
        for name, pair in windows:
            position = Position(name)
            interval = (onset_intervals.get(name)
                        if onset_intervals is not None else None)
            if transitions is not None and (interval is None or name not in transitions):
                if diagnostics is not None:
                    diagnostics.append({
                        "position": name, "received_at": prefix.received_at,
                        "reason": "missing_local_interval" if interval is None
                        else "missing_temperature_attribution",
                    })
                continue
            bounds = transitions[name] if transitions is not None else None
            for index, quantity in enumerate((Quantity.TEMPERATURE, Quantity.HUMIDITY)):
                if quantity not in quantities:
                    continue
                key = ("door_open", position, quantity)
                candidate = self._curve_candidates.get(key)
                status = self._curve_status.get(key, {})
                source = episode["sources"][position][index]
                series = self._curve_series[position, quantity]
                diagnostic = {
                    "position": name, "quantity": quantity.value,
                    "received_at": prefix.received_at, "source": source,
                    "epoch": epochs[position][index], "time_basis": series["time_basis"],
                    "attribution_bounds": bounds, "accepted": False,
                }
                if diagnostics is not None:
                    diagnostics.append(diagnostic)
                if series["source"] != source or series["epoch"] != epochs[position][index]:
                    diagnostic["reason"] = "source_or_epoch_changed"
                    continue
                floor = max(
                    self._curve_closed_since,
                    series["started_at"] or self._curve_closed_since,
                )
                context = (
                    (self._curve_door_context, self._curve_closed_since),
                    (epochs[position][index],), floor, None,
                )
                diagnostic["context_floor"] = floor
                window = getattr(pair, quantity.value)
                originals = dict(points[position][index])
                current_candidate = bool(
                    candidate is not None and status.get("status") == "supported"
                    and status.get("source") == source and candidate.context == context
                    and all(originals.get(stamp) == value
                            for stamp, value in candidate.original_points)
                )
                native = interval
                if (native is not None and transitions is None and current_candidate
                        and not native["window"].previous[-1][0] <= candidate.onset_at
                        <= native["window"].newest_at):
                    # Nur die wirklich aufgelösten Sekanten lokalisieren
                    # diesen Knick. Frühere eigene RH-Formbelege bleiben frei.
                    diagnostic["native_interval_used"] = False
                    diagnostic["native_interval_reason"] = "supported_form_outside_native_interval"
                    native = None
                native_onset = native["interval"].previous[0] if native is not None else None
                diagnostic.update(
                    current_supported_candidate=current_candidate,
                    current_candidate_onset=candidate.onset_at if candidate is not None else None,
                    native_onset=native_onset,
                    native_support=(native["window"].start_at, native["window"].newest_at)
                    if native is not None else None,
                )
                if native is not None and not all(
                    originals.get(stamp) == value
                    for stamp, value in native["window"].previous + native["window"].current
                ):
                    diagnostic["reason"] = "native_original_changed"
                    continue
                local_native = native is not None and transitions is not None
                current_native_branch = bool(
                    current_candidate and native is not None
                    and (bounds is None or bounds[0] <= candidate.onset_at <= bounds[1])
                    and native["window"].previous[-1][0] <= candidate.onset_at
                    <= native["window"].newest_at
                    and candidate.features.onset_at == candidate.onset_at
                )
                if (local_native or not current_candidate
                        or native_onset is not None and (
                            candidate.onset_at != native_onset
                            or candidate.features.onset_at != native_onset
                        )
                        or bounds is not None and not bounds[0] <= candidate.onset_at <= bounds[1]):
                    # Das Raster kann bereits spätere Präfixe enthalten.
                    # Sein Fit ist dann kein Zeuge dieser ersten Bindung.
                    start = prefix.received_at - timedelta(seconds=self.p["curve_window_seconds"])
                    if native is not None:
                        # Die lokale Ersatzprüfung gehört nur zum gebundenen
                        # nativen Vorlauf und seinen echten Folgeoriginalen.
                        # Die allgemeine Suche behält ihren bisherigen Vorlauf
                        # und nimmt zusätzlich die vollständige native Stütze.
                        start = (native["window"].start_at if local_native else
                                 min(start, native["window"].start_at))
                    support = tuple(point for point in points[position][index]
                                    if start <= point[0] <= prefix.received_at)
                    fit_context = position, quantity, source, series["time_basis"], context
                    diagnostic["fit_support"] = {
                        "start_at": support[0][0] if support else None,
                        "newest_at": support[-1][0] if support else None,
                        "original_count": len(support),
                        "before_or_at_native_onset": sum(stamp <= native_onset for stamp, _ in support)
                        if native_onset is not None else None,
                        "after_native_onset": sum(stamp > native_onset for stamp, _ in support)
                        if native_onset is not None else None,
                        "min_side_samples": self.curve_policy.min_side_samples,
                    }
                    features = None
                    previous_form = None
                    previous_supported = False
                    if native_onset is not None and transitions is None:
                        previous_form = self._fit_candidate_form(
                            "door_open", support, floor, quantity, fit_context=fit_context,
                        )
                        previous_gates = (self._curve_support_gates(
                            "door_open", previous_form, floor, quantity,
                        ) if previous_form is not None else {})
                        previous_supported = bool(previous_form is not None and all(
                            gate["result"] for gate in previous_gates.values()
                        ))
                        diagnostic["free_form"] = {
                            "available": previous_form is not None,
                            "supported": previous_supported, "gates": previous_gates,
                            "onset_at": previous_form.onset_at if previous_form is not None else None,
                            "features": self._curve_feature_diagnostic(previous_form)
                            if previous_form is not None else None,
                        }
                        if (previous_supported
                                and not native["window"].previous[-1][0] <= previous_form.onset_at
                                <= native["window"].newest_at):
                            features = previous_form
                            native_onset = None
                            diagnostic["fit_method"] = "supported_form_outside_native_interval"
                    if native_onset is not None:
                        diagnostic["fit_method"] = "fixed_native_onset"
                        hinge_order = candidate.features.hinge_order if candidate is not None else 1
                        fit_key = (
                            "original_opening_change", fit_context, self.curve_policy,
                            support, native_onset, -1, floor, bounds, hinge_order,
                        )
                        if fit_key not in self._curve_fit_cache:
                            self._curve_fit_cache[fit_key] = curve_change(
                                support, policy=self.curve_policy, direction=-1,
                                onset_at=native_onset, onset_not_before=floor,
                                onset_bounds=bounds, hinge_order=hinge_order,
                            )
                        features = self._curve_fit_cache[fit_key]
                    elif features is None and bounds is None:
                        diagnostic["fit_method"] = "searched_form"
                        features = self._fit_candidate_form(
                            "door_open", support, floor, quantity, fit_context=fit_context,
                        )
                    elif features is None:
                        diagnostic["fit_method"] = "searched_form_in_attribution"
                        features = curve_change(
                            support, policy=self.curve_policy, direction=-1,
                            onset_not_before=floor, onset_bounds=bounds,
                        )
                    gates = (self._curve_support_gates("door_open", features, floor, quantity)
                             if features is not None else {})
                    supported = bool(features is not None and all(
                        gate["result"] for gate in gates.values()
                    ))
                    if (
                        not supported and not local_native and native_onset is not None
                        and features is not None and features.evidence == "rejects"
                        and previous_supported
                        and native_onset < previous_form.onset_at <= native["window"].newest_at
                    ):
                        # Ein abgelehnter Ansatz bindet keinen gültigen Beginn.
                        # Der bereits gestützte spätere RH-Fit behält seinen
                        # eigenen Beginn; Anschluss und Gegenformen folgen unten.
                        diagnostic["rejected_native_form"] = {
                            "onset_at": features.onset_at, "gates": gates,
                            "features": self._curve_feature_diagnostic(features),
                        }
                        features, gates, supported = previous_form, previous_gates, True
                        diagnostic["fit_method"] = "supported_later_own_form"
                    diagnostic["selected_form"] = {
                        "available": features is not None, "supported": supported,
                        "onset_at": features.onset_at if features is not None else None,
                        "gates": gates,
                        "features": self._curve_feature_diagnostic(features)
                        if features is not None else None,
                    }
                    if supported:
                        candidate = _CurveCandidate(
                            f"door_open:{position.value}:{quantity.value}:{source}:"
                            f"{context[1]}:{features.onset_at.isoformat()}:{prefix.received_at.isoformat()}",
                            features.onset_at, prefix.received_at, context, features,
                            support, features.newest_at,
                        )
                    elif local_native or not current_native_branch:
                        diagnostic["reason"] = "no_evaluable_form" if features is None else "form_not_supported"
                        continue
                    else:
                        diagnostic["retained_current_form"] = True
                    # Nur die allgemeine Zuordnung darf eine vorhandene eigene
                    # Form weitertragen. Lokal braucht die eigene Stütze ihren
                    # Formbeleg; ein breiterer alter Fit ersetzt ihn nicht.
                # Die erste gemeinsame Bindung ist kein Zeitfenster für den
                # physischen RH-Beginn jeder Rolle. Der eigene unterstützte
                # Originalfit darf früher oder erst später einsetzen; Quelle,
                # Epoche und der lückenlose Fallast müssen weiterhin passen.
                if candidate.features.newest_at > prefix.received_at:
                    diagnostic["reason"] = "form_after_current_prefix"
                    continue
                if candidate.onset_at > window.newest_at:
                    diagnostic["reason"] = "onset_after_current_original"
                    continue
                if not self._original_contiguous(prefix, (
                    (name, quantity, candidate.features.newest_at),
                )):
                    diagnostic["reason"] = "original_connection_unresolved"
                    continue
                if quantity == Quantity.HUMIDITY:
                    connected = self._original_opening_humidity_connected(
                        points[position][index], candidate, window, name,
                        self._original_prefix_time_support(prefix, position, quantity),
                    )
                    diagnostic["humidity_connected"] = connected
                    if not connected:
                        diagnostic["reason"] = "humidity_recovery" if connected is False else "humidity_connection_unresolved"
                        continue
                diagnostic.update(accepted=True, reason="own_form_bound", onset_at=candidate.onset_at)
                witnesses.append((key, candidate))
        return tuple(witnesses)

    def _localize_original_opening(self, episode, trace):
        """Den eigenen kausalen RH-Knick jeder Rolle bei der Bestätigung erhalten."""
        opening = episode["original_open"]
        localized = []
        diagnostics = {}
        for key, witness in opening["onset_witnesses"]:
            if key[2] != Quantity.HUMIDITY:
                localized.append((key, witness))
                continue
            bounds = (witness.original_points[0][0], witness.features.newest_at)
            native = next((intervals[key[1].value] for intervals in (
                opening.get("local_humidity_onset_intervals", {}),
                opening["humidity_onset_intervals"],
            ) if key[1].value in intervals
                and intervals[key[1].value]["interval"].previous[0] == witness.onset_at), None)
            features = curve_change(
                witness.original_points, policy=self.curve_policy, direction=-1,
                onset_not_before=witness.context[2], onset_at=witness.onset_at,
                hinge_order=witness.features.hinge_order,
            )
            supported = self._curve_supports(
                "door_open", features, witness.context[2], Quantity.HUMIDITY,
            )
            selected = witness
            if supported:
                identity = witness.candidate_id
                if features.onset_at != witness.onset_at:
                    identity = f"{identity}:localized:{features.onset_at.isoformat()}"
                selected = replace(
                    witness, candidate_id=identity,
                    onset_at=features.onset_at, features=features,
                )
            diagnostics[key[1].value] = {
                "before": witness.onset_at, "after": selected.onset_at,
                "search_bounds": bounds,
                "first_original_at": witness.original_points[0][0],
                "newest_original_at": witness.original_points[-1][0],
                "original_count": len(witness.original_points),
                "supported": supported,
                "native_interval": ({
                    "previous": native["interval"].previous,
                    "current": native["interval"].current,
                    "direction": native["interval"].direction,
                    "change": native["interval"].change,
                    "received_at": native["received_at"],
                    "window_start_at": native["window"].start_at,
                    "window_newest_at": native["window"].newest_at,
                    "background_tolerance": native["kink"].tolerance,
                    "background_effective_change": native["kink"].effective_change,
                } if native is not None else None),
                "reason": ("own_native_original_humidity_transition" if supported and native is not None
                           else "own_original_humidity_transition" if supported
                           else "retained_original_witness"),
            }
            localized.append((key, selected))
        trace["door_open_localization"] = diagnostics
        opening["onset_witnesses"] = tuple(localized)
        return opening["onset_witnesses"]

    def _original_temperature_fall_evidence(self, episode, prefix, trace):
        """Echte Fallfenster bis zur ersten Erholung im Türbestand halten."""
        falls = episode.setdefault("original_temperature_falls", {})
        temperatures, context = dict(prefix.temperatures), dict(prefix.context)
        significance = self.p["curve_original_significance"]
        records = {}
        for position in episode["positions"]:
            name = position.value
            window = temperatures.get(name)
            fall = falls.get(position)
            new_original = bool(
                prefix.original and (fall is None or window is not None
                                     and window.newest_at > fall["continuity_at"])
            )
            reason = None
            valid = self._original_prefix_valid(
                prefix, (position,), (Quantity.TEMPERATURE,),
            )
            ready = valid and window is not None
            if fall is not None and (not valid or fall["epoch"] != context[position][0]):
                reason = "temperature_source_invalidated"
            elif fall is not None and not self._original_contiguous(
                prefix, ((name, Quantity.TEMPERATURE, fall["continuity_at"]),),
            ):
                self._mark_original_unknown(fall, prefix, (position,), (Quantity.TEMPERATURE,))
            if fall is not None and fall.get("connection_unknown") and reason is None:
                if (ready and prefix.original
                        and self._original_resume_ready(prefix, (position,), (Quantity.TEMPERATURE,), fall)
                        and window.v < -significance * window.e):
                    recovery = fall.get("recovery_window")
                    if recovery is not None and self._original_temperature_counterform(window, recovery):
                        reason = "renewed_temperature_fall"
                    else:
                        self._retain_interrupted_evidence(position, "temperature_fall", fall, prefix.received_at)
                        falls.pop(position)
                        fall = None
                else:
                    ready = False
            recovery = fall.get("recovery_window") if fall is not None else None
            # Ein fremdes RH-Präfix kann den letzten T-Gegenbeleg nicht lösen.
            if (ready and fall is not None and recovery is not None and (
                window.newest_at > fall["continuity_at"]
                or window.current[-2:] != fall.get("edge_originals", window.current[-2:])
            )):
                edge = original_edge_change(window, recovery)
                # Die letzte Kante bleibt Diagnose. Ein vollständig neuer
                # Block muss negativ geneigt sein und gegenüber der gehaltenen
                # Bremse signifikant erneut fallen. Ein älterer Vorgängerblock
                # darf diesen Gegenbeleg nicht bis nach der Schlussfreigabe sperren.
                renewed = self._original_temperature_counterform(window, recovery)
                fall["close_counterform"] = renewed
                fall["last_edge_counterform"] = bool(
                    edge is not None and edge.direction < -significance
                    and edge.change < -significance
                )
                if renewed:
                    episode.pop("original_close", None)
                    fall.pop("recovery_received_at", None)
                if renewed:
                    reason = "renewed_temperature_fall"
            if reason is not None:
                falls.pop(position)
                episode.pop("original_close", None)
                fall = None
            if ready and prefix.original:
                if fall is None and window.v < -significance * window.e:
                    fall = {
                        "window": window, "received_at": prefix.received_at,
                        "epoch": context[position][0], "continuity_at": window.newest_at,
                        "edge_originals": window.current[-2:],
                    }
                    falls[position] = fall
                elif fall is not None:
                    reference = fall["window"]
                    if (fall.get("recovery_window") is None
                            and window.newest_at > fall["continuity_at"]
                            and window.v < -significance * window.e
                            and window.v + significance * window.e
                            < reference.v + significance * reference.e):
                        # Der ganze beobachtete Fallblock trägt die spätere
                        # Bremse; dessen echte Zeit begrenzt auch die Frische.
                        fall["window"] = window
                        fall["received_at"] = prefix.received_at
                        fall.pop("braking_form", None)
                    fall["continuity_at"] = window.newest_at
                if self.open and new_original and fall is not None:
                    self._original_temperature_braking_form(episode, prefix, position, fall)
                # Ein später schwächerer Fall allein ist kein Schlussknick.
                # Die lokale native Bremse muss sich auch vom beobachteten
                # Hintergrund abheben; jede Rolle bindet ihren eigenen Beginn.
                braking = (dict(self._original_temperature_backgrounds(
                    prefix, ((name, window),),
                ))[name] if fall is not None and fall.get("braking_window") is None
                    and window.b < -significance * window.e_minus else None)
                if (fall is not None and not fall.get("close_counterform", False)
                        and (episode.get("rejected_close_after") is None
                             or window.current[0][0] > episode["rejected_close_after"])
                        and braking is not None and braking.effective_change > 0):
                    fall["recovery_received_at"] = prefix.received_at
                    fall["braking_window"] = window
                    fall["braking_background"] = braking
                    fall["recovery_window"] = window
                elif fall is not None and fall.get("recovery_window") is not None:
                    if window.v > fall["recovery_window"].v:
                        fall["recovery_window"] = window
            if ready and fall is not None:
                # Eine Wertkorrektur prüft die Kante erneut, liefert aber
                # weder einen neuen Originalstempel noch einen neuen Fall.
                fall["edge_originals"] = window.current[-2:]
            records[name] = {
                "received_at": fall["received_at"] if fall is not None else None,
                "reference_newest_at": fall["window"].newest_at if fall is not None else None,
                "continuity_at": fall["continuity_at"] if fall is not None else None,
                "epoch": fall["epoch"] if fall is not None else None,
                "close_counterform": fall.get("close_counterform", False) if fall is not None else False,
                "braking_form_onset_at": (
                    fall["braking_form"].onset_at
                    if fall is not None and fall.get("braking_form") else None
                ),
                "braking_form_received_at": (
                    fall["braking_form"].first_observed_at
                    if fall is not None and fall.get("braking_form") else None
                ),
                "reset_reason": reason,
                "connection_unknown": bool(fall is not None and fall.get("connection_unknown")),
            }
        trace["temperature_fall_support"] = records

    def _original_temperature_fall_followups(self, episode, prefix, positions):
        falls = episode.get("original_temperature_falls", {})
        if not positions or any(
            position not in falls or falls[position].get("connection_unknown")
            or falls[position].get("braking_window") is None
            or falls[position].get("close_counterform", False)
            for position in positions
        ):
            return None
        return self._original_followups(
            prefix, tuple((position.value, Quantity.TEMPERATURE, falls[position]["braking_window"])
                          for position in positions),
            continuity=tuple((position.value, Quantity.TEMPERATURE, falls[position]["continuity_at"])
                             for position in positions),
        )

    def _original_humidity_available(self, positions, now):
        """Gültige RH-Originalfenster begrenzen den vorhandenen Heiz-Sonderweg."""
        return bool(positions) and all(
            self._value(position, Quantity.HUMIDITY, now) is not None
            and (cached := self._original_window_cache.get((position, Quantity.HUMIDITY))) is not None
            and cached[0] == self._curve_series[position, Quantity.HUMIDITY]["version"]
            and cached[1] is not None
            and (support := self._original_time_support(
                position, Quantity.HUMIDITY,
                self._original_points_cache[position, Quantity.HUMIDITY][1], now,
            )) is not None and support.covers(cached[1].start_at, cached[1].newest_at)
            for position in positions
        )

    @staticmethod
    def _reset_original_ventilation(episode, reason, *, water=True, humidity=True, completed=True):
        episode.pop("original_ventilation", None)
        if water and humidity:
            episode.pop("original_ventilation_continuity", None)
        if humidity:
            episode.pop("original_ventilation_rh", None)
            relative = episode.get("original_ventilation_rh_confirmed")
            if completed and relative is not None and not relative.get("published", True):
                episode.pop("original_ventilation_rh_confirmed")
        if water:
            episode.pop("original_ventilation_water", None)
        complete = episode.get("original_ventilation_complete")
        if completed and complete is not None and not complete.get("published", True):
            episode.pop("original_ventilation_complete")
        episode["original_ventilation_reset_reason"] = reason

    def _original_ventilation_contiguous(self, episode, prefix):
        """Neue Originale müssen an die noch offenen eigenen Belege anschließen."""
        references = episode.get("original_ventilation_continuity")
        if references is None:
            references = []
            for pending, completed in (
                ("original_ventilation_rh", "original_ventilation_rh_confirmed"),
                ("original_ventilation", "original_ventilation_complete"),
            ):
                stage = episode.get(pending)
                if (stage is None or stage.get("connection_unknown")
                        or episode.get(completed, {}).get("published", False)):
                    continue
                references.extend(
                    (name, quantity, stamp)
                    for name, temperature, humidity in stage["newest"]
                    for quantity, stamp in ((Quantity.TEMPERATURE, temperature),
                                            (Quantity.HUMIDITY, humidity))
                )
            for position, state in episode.get("original_ventilation_water", {}).items():
                if state["last_rh_at"] is not None:
                    references.append((position.value, Quantity.HUMIDITY, state["last_rh_at"]))
                if state["pairs"]:
                    references.append((position.value, Quantity.TEMPERATURE,
                                       state["pairs"][-1].temperature_after[0]))
        if references and not self._original_contiguous(prefix, references):
            return False
        # Nur beobachtete, bereits auf Anschluss geprüfte Originale fortführen.
        # Warten verschiebt keinen Anker; Pufferrotation verliert keinen Beleg.
        episode["original_ventilation_continuity"] = tuple(
            (position.value, quantity, samples[-1][0])
            for position, temperature, humidity in prefix.points
            if position in episode["positions"]
            for quantity, samples in ((Quantity.TEMPERATURE, temperature),
                                      (Quantity.HUMIDITY, humidity))
            if samples
        )
        return True

    def _original_ventilation_windows(self, episode, prefix):
        """Nur neue RH-Originale kausal paaren; offene rechte Partner abwarten."""
        positions = episode["positions"]
        context = self._original_bound_context(prefix.context, positions)
        candidate = episode.get("original_open")
        phase = candidate["received_at"] if not self.open and candidate is not None else episode.get(
            "original_ventilation_phase",
        )
        if (episode.get("original_ventilation_context") != context
                or episode.get("original_ventilation_phase") != phase):
            self._reset_original_ventilation(episode, "original_epoch_changed")
            episode["original_ventilation_context"] = context
            episode["original_ventilation_phase"] = phase
            opening_floor = phase or self._curve_open_effective_at or self.opened_at or self.origin
            episode["original_ventilation_floor"] = max(
                opening_floor, *(self._curve_series[position, quantity]["started_at"] or opening_floor
                                 for position in positions
                                 for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY)),
            )
        floor = episode["original_ventilation_floor"]
        states = episode.setdefault("original_ventilation_water", {})
        points = {position: (temperature, humidity)
                  for position, temperature, humidity in prefix.points}
        features, support = {}, {}
        waiting = False
        count = self.p["curve_original_samples"]
        limit = self.p["curve_max_points"]
        for position in positions:
            temperatures, humidities = points[position]
            state = states.setdefault(position, {
                "pending": deque(), "pairs": deque(maxlen=2 * count),
                "last_rh_at": None, "features": None, "window_status": None,
            })
            if state["last_rh_at"] is None:
                first = max(
                    len(humidities) - 2 * count,
                    bisect_left(humidities, floor, key=lambda point: point[0]),
                )
            else:
                first = bisect_right(humidities, state["last_rh_at"], key=lambda point: point[0])
            humidity_gap = self.p[f"curve_original_gap_humidity_{position.value}_seconds"]
            humidity_support = self._original_prefix_time_support(prefix, position, Quantity.HUMIDITY)
            temperature_support = self._original_prefix_time_support(prefix, position, Quantity.TEMPERATURE)
            if humidity_support is None or temperature_support is None:
                return "invalid", {}, {"reason": "original_time_support_unknown", "position": position.value}
            for point in humidities[first:]:
                if (state["last_rh_at"] is not None
                        and not humidity_support.pairing_covers(state["last_rh_at"], point[0])):
                    return "unresolved", {}, {"reason": "humidity_pairing_unresolved", "position": position.value}
                if len(state["pending"]) >= limit:
                    return "invalid", {}, {"reason": "water_pair_pending_overflow", "position": position.value}
                state["pending"].append(point)
                state["last_rh_at"] = point[0]
            changed = False
            paired_counterform = False
            while state["pending"]:
                point = state["pending"][0]
                right = bisect_left(temperatures, point[0], key=lambda sample: sample[0])
                if right < len(temperatures) and temperatures[right][0] == point[0]:
                    bracket = temperatures[right:right + 1]
                elif right == 0:
                    bracket = temperatures[:1]
                elif right == len(temperatures):
                    bracket = temperatures[-1:]
                else:
                    bracket = temperatures[right - 1:right + 1]
                paired = original_water_pair_support(
                    point, bracket,
                    temperature_scale=self.p[f"curve_original_noise_temperature_{position.value}"],
                    humidity_scale=self.p[f"curve_original_noise_humidity_{position.value}"],
                    max_gap_seconds=self.p[f"curve_original_gap_temperature_{position.value}_seconds"],
                    at=prefix.received_at, time_support=temperature_support,
                )
                if paired.status == "pending":
                    waiting = True
                    break
                if paired.status != "ready" or paired.pair.temperature_before[0] < floor:
                    status = "unresolved" if paired.reason == "temperature_pairing_unresolved" else "invalid"
                    return status, {}, {
                        "reason": paired.reason or "water_support_before_opening_segment",
                        "position": position.value,
                    }
                state["pending"].popleft()
                state["pairs"].append(paired.pair)
                changed = True
                window = original_water_window(
                    tuple(state["pairs"]), count=count,
                    max_gap_seconds=humidity_gap, at=prefix.received_at,
                    time_support=humidity_support,
                )
                state["window_status"], state["features"] = window.status, window.features
                if window.status == "invalid":
                    status = "unresolved" if window.reason == "humidity_pairing_unresolved" else "invalid"
                    return status, {}, {"reason": window.reason, "position": position.value}
                pending_stage = episode.get("original_ventilation")
                if window.features is not None and pending_stage is not None:
                    significance = self.p["curve_original_significance"]
                    reference = dict(pending_stage["humidity_windows"]).get(position)
                    temperature = dict(prefix.temperatures).get(position.value)
                    form = original_flattening(
                        window.features, temperature, significance=significance,
                        reference_humidity=reference,
                    )
                    if (form is not None and not form.abating
                            and not pending_stage.get("connection_unknown")):
                        # Ein neuer T-Partner kann mehrere wartende RH-Paare
                        # zugleich liefern. Auch innere Gegenformen verwenden
                        # die gebundene Abflachung, nicht eine Nullsteigung.
                        paired_counterform = True
                        self._reset_original_ventilation(
                            episode, "paired_water_counterform", water=False, humidity=False,
                            completed=False,
                        )
            if not changed and state["window_status"] is None:
                state["window_status"] = "insufficient"
            water = state["features"]
            if water is not None:
                features[position] = water
                last = water.current[-1]
                support[position.value] = {
                    "rh_window_start_at": water.start_at, "rh_newest_at": water.newest_at,
                    "partner_available_at": water.available_at,
                    "left_temperature_at": last.temperature_before[0],
                    "right_temperature_at": last.temperature_after[0],
                    "pending_count": len(state["pending"]),
                    "pending_rh_at": state["pending"][0][0] if state["pending"] else None,
                    "paired_count": len(state["pairs"]),
                    "paired_counterform_seen": paired_counterform,
                }
            else:
                support[position.value] = {
                    "paired_count": len(state["pairs"]), "pending_count": len(state["pending"]),
                    "pending_rh_at": state["pending"][0][0] if state["pending"] else None,
                }
        status = "pending" if waiting else "ready" if len(features) == len(positions) else "insufficient"
        return status, features, support

    def _original_ventilation_evidence(
        self, prefix, channels, now, decision_at, trace, episode, *, closing, close_value=None,
        stage_confirmation_allowed=True, stage_original_roles=(),
    ):
        """RH-Lüften und danach vollständiges AH-Lüften in derselben Tür."""
        candidate = episode.get("original_open") if episode is not None else None
        if episode is None or not self.open and not (
            candidate is not None and candidate.get("lowering_witness") is not None
        ):
            return
        positions = episode["positions"]
        record = {
            "prefix_received_at": prefix.received_at, "decision_at": decision_at, "raster_at": now,
            "positions": tuple(position.value for position in positions),
            "candidate_at": None, "water_status": "unknown",
            "close_same_prefix": closing, "close_contrast": close_value,
            "publication_allowed": self.open and stage_confirmation_allowed,
            "input_group_complete": stage_confirmation_allowed,
        }
        for name, key in (("relative_humidity", "original_ventilation_rh"),
                          ("absolute_humidity", "original_ventilation")):
            pending_stage = episode.get(key)
            record[name] = {
                "evaluated": False, "proven": False,
                "candidate_at": pending_stage["received_at"] if pending_stage is not None else None,
                "candidate_originals": pending_stage["newest"] if pending_stage is not None else (),
                "bound_support": pending_stage["support"] if pending_stage is not None else {},
            }
        trace["original_ventilation"] = record
        trace["checks"]["ventilation"] = True
        trace["checks"]["ventilation_complete"] = True
        for name, key in (("relative_humidity", "original_ventilation_rh_confirmed"),
                          ("absolute_humidity", "original_ventilation_complete")):
            confirmed_stage = episode.get(key)
            if confirmed_stage is not None:
                record[name] = {
                    **{field: value for field, value in confirmed_stage.items()
                       if field not in ("windows", "temperature_windows")},
                    "proven": confirmed_stage.get("published", True),
                }
        complete = episode.get("original_ventilation_complete")
        if complete is not None and complete.get("published", True):
            return
        if not all(self._position_available(
            position, (Quantity.TEMPERATURE, Quantity.HUMIDITY), now,
        ) for position in positions):
            _decision(trace, "ventilation", None, {}, evaluated=False,
                      reason="waiting_for_current_values")
            return
        sources_coherent = all(
            position in channels and position not in episode["invalid"]
            and tuple(self._curve_series[position, quantity]["source"]
                      for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY))
            == episode["sources"][position]
            for position in positions
        )
        if not sources_coherent or not self._original_prefix_valid(prefix, positions):
            self._reset_original_ventilation(episode, "opening_sources_or_originals_unavailable")
            record["reset_reason"] = episode["original_ventilation_reset_reason"]
            _decision(trace, "ventilation", None, {
                "opening_sources_coherent": _gate(sources_coherent),
            }, evaluated=False, reason="opening_sources_or_originals_unavailable")
            return
        # Eine fremde letzte Meldung erzeugt kein eigenes Original. Sie
        # darf aber die bereits vorliegenden eigenen Originalrollen derselben
        # Gruppe nicht verdecken. Eigene Korrekturen behalten den Rücksprung;
        # die native Frischeprüfung unten verlangt weiterhin neue T- UND RH-Stützen.
        own_group_original = (
            stage_confirmation_allowed
            and prefix.changed_position is not None
            and prefix.changed_position not in positions
            and all(any((position, quantity) in stage_original_roles
                        for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY))
                    for position in positions)
        )
        record["own_group_original"] = own_group_original
        record["group_original_roles"] = tuple(
            (position.value, quantity.value)
            for position in positions
            for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY)
            if (position, quantity) in stage_original_roles
        )
        if not prefix.original and not own_group_original:
            # Gebundene Korrekturen/Lücken wurden bereits beim Eingang entwertet.
            candidate = episode.get("original_ventilation")
            record.update(
                candidate_at=candidate["received_at"] if candidate is not None else None,
                candidate_originals=candidate["newest"] if candidate is not None else (),
            )
            _decision(trace, "ventilation", None, {
                "new_original_prefix": _gate(False),
            }, evaluated=False, reason="no_new_original_prefix")
            return
        if not self._original_ventilation_contiguous(episode, prefix):
            self._mark_original_unknown(
                episode, prefix, positions, (Quantity.TEMPERATURE, Quantity.HUMIDITY),
            )
            for key in ("original_ventilation_rh", "original_ventilation",
                        "original_ventilation_rh_confirmed", "original_ventilation_complete"):
                stage = episode.get(key)
                if stage is not None and not stage.get("published", False):
                    self._mark_original_unknown(
                        stage, prefix, positions, (Quantity.TEMPERATURE, Quantity.HUMIDITY),
                    )
            water = episode.pop("original_ventilation_water", None)
            for position in positions:
                self._retain_interrupted_evidence(position, "ventilation_pairs", water, prefix.received_at)
            episode.pop("original_ventilation_continuity", None)
            if episode.get("unknown_after") is not None:
                episode["original_ventilation_floor"] = max(
                    episode.get("original_ventilation_floor", episode["unknown_after"]),
                    episode["unknown_after"],
                )
            record["connection_unknown"] = True
            _decision(trace, "ventilation", None, {}, evaluated=False,
                      reason="unobserved_original_interval")
            return
        windows = dict(prefix.windows)
        if any(position.value not in windows for position in positions):
            # Warten liefert weder eine Gegenform noch eine neue Bestätigung.
            _decision(trace, "ventilation", None, {}, evaluated=False,
                      reason="waiting_for_original_windows")
            return
        status, water_windows, support = self._original_ventilation_windows(episode, prefix)
        record.update(
            water_status=status, support=support, paired_support=support,
            support_floor=episode["original_ventilation_floor"],
            epochs=tuple((position.value, epochs) for position, epochs
                         in episode["original_ventilation_context"]),
            reset_reason=episode.get("original_ventilation_reset_reason"),
        )
        if any(windows[position.value].temperature.current[0][0]
               < episode["original_ventilation_floor"] for position in positions):
            # Die aktuelle Stütze ist noch nicht lokal vollständig.
            # Bereits gebundene Fenster bleiben bis zu einer Gegenform erhalten.
            _decision(trace, "ventilation", None, {}, evaluated=False,
                      reason="temperature_support_before_opening_segment")
            return
        significance = self.p["curve_original_significance"]

        def stage_evidence(name, key, humidity_windows, *, paired):
            gates = {"opening_sources_coherent": _gate(sources_coherent)}
            initial = maintained = True
            counterform = unresolved = False
            pending_stage = episode.get(key)
            interrupted = bool(pending_stage is not None and pending_stage.get("connection_unknown"))
            newest, anchors = [], {}
            for position in positions:
                humidity = humidity_windows[position]
                temperature = windows[position.value].temperature
                pending = episode.get(key)
                reference = (dict(pending["humidity_windows"]).get(position)
                             if pending is not None else None)
                form = original_flattening(
                    humidity, temperature, significance=significance,
                    reference_humidity=None if interrupted else reference,
                )
                unresolved |= form is None
                if pending is not None:
                    temperature_reference = dict(pending["temperature_windows"]).get(position)
                    # Auch AH-Paare bleiben auf der Zeitachse ihrer RH-Originale.
                    humidity_current_at = (humidity.current[0].humidity_sample[0]
                                           if paired else humidity.current[0][0])
                    counterform |= bool(
                        reference is not None and humidity_current_at > reference.newest_at
                        and humidity.v > significance * humidity.e
                        or temperature_reference is not None
                        and temperature.current[0][0] > temperature_reference.newest_at
                        and temperature.v > significance * temperature.e
                    )
                initial &= form is not None and form.abating
                maintained &= form is not None and form.abating
                if form is not None:
                    for comparison in ("previous_fall", "current_nonrising", "braking",
                                       "temperature_falling", "temperature_not_braking"):
                        contrast = getattr(form, comparison)
                        gates[f"{position.value}:{comparison}"] = _gate(
                            contrast.result, value=contrast.value, operator=contrast.operator,
                            threshold=contrast.threshold,
                            unit="°C/s" if comparison.startswith("temperature") else
                            "g/m³/s" if paired else "%/s",
                        )
                newest.append((position.value, temperature.newest_at, humidity.newest_at))
                anchors[position.value] = {
                    "humidity_start_at": humidity.start_at,
                    "humidity_newest_at": humidity.newest_at,
                    "temperature_start_at": temperature.start_at,
                    "temperature_newest_at": temperature.newest_at,
                }
                trace["metrics"].setdefault(position.value, {}).update({
                    f"ventilation_{name}_slope": humidity.v,
                    f"ventilation_{name}_previous_slope": humidity.b,
                    f"ventilation_{name}_slope_error": humidity.e,
                    f"ventilation_{name}_previous_slope_error": humidity.e_minus,
                    "ventilation_temperature_slope": temperature.v,
                    "ventilation_temperature_slope_error": temperature.e,
                })
                if paired:
                    lower, upper = humidity.constant_bounds(significance)
                    trace["metrics"][position.value].update(
                        ventilation_water_slope=humidity.v,
                        ventilation_previous_water_slope=humidity.b,
                        ventilation_water_slope_error=humidity.e,
                        ventilation_previous_water_slope_error=humidity.e_minus,
                        ventilation_water_level_deviation=humidity.normalized_level_deviation(),
                        ventilation_water_constant_lower=lower,
                        ventilation_water_constant_upper=upper,
                    )
            completed_key = ("original_ventilation_complete" if paired
                             else "original_ventilation_rh_confirmed")
            if interrupted:
                fresh_segment = self._original_resume_ready(
                    prefix, positions, (Quantity.TEMPERATURE, Quantity.HUMIDITY), pending_stage,
                ) and all(window.start_at >= pending_stage["unknown_after"]
                          for window in humidity_windows.values())
                if not (fresh_segment and initial):
                    if fresh_segment and counterform:
                        episode.pop(key, None)
                    stage = {
                        "evaluated": not unresolved, "counterform": bool(fresh_segment and counterform),
                        "initial_form": initial, "maintained_form": False,
                        "candidate_at": pending_stage["received_at"],
                        "candidate_originals": pending_stage["newest"],
                        "bound_support": pending_stage["support"], "support": anchors,
                        "confirmable": False, "proven": False, "evidence_confirmed": False,
                        "retained_completed_evidence": False, "close_conflict": False,
                        "connection_unknown": True,
                    }
                    record[name] = stage
                    return stage, gates
                for position in positions:
                    self._retain_interrupted_evidence(position, key, pending_stage, prefix.received_at)
                episode.pop(key, None)
                completed = episode.get(completed_key)
                if completed is not None and not completed.get("published", False):
                    for position in positions:
                        self._retain_interrupted_evidence(position, completed_key, completed, prefix.received_at)
                    episode.pop(completed_key, None)
            elif not maintained and not unresolved:
                episode.pop(key, None)
            candidate = episode.get(key)
            confirmed = False
            completed = episode.get(completed_key)
            if candidate is None and completed is not None and completed.get("connection_unknown"):
                initial &= self._original_resume_ready(
                    prefix, positions, (Quantity.TEMPERATURE, Quantity.HUMIDITY), completed,
                ) and all(window.start_at >= completed["unknown_after"]
                          for window in humidity_windows.values())
                if initial:
                    for position in positions:
                        self._retain_interrupted_evidence(position, completed_key, completed, prefix.received_at)
                    episode.pop(completed_key)
            if candidate is None and initial:
                candidate = {
                    "received_at": prefix.received_at, "newest": tuple(newest),
                    "context": episode["original_ventilation_context"],
                    "humidity_windows": tuple(humidity_windows.items()),
                    "temperature_windows": tuple((position, windows[position.value].temperature)
                                                 for position in positions),
                    "support": anchors,
                }
                if paired:
                    candidate["water_windows"] = candidate["humidity_windows"]
                episode[key] = candidate
            elif candidate is not None:
                previous = {position: (temperature, humidity)
                            for position, temperature, humidity in candidate["newest"]}
                fresh = all(temperature > previous[position][0] and humidity > previous[position][1]
                            for position, temperature, humidity in newest)
                confirmed = maintained and fresh
                gates["later_originals_for_all_roles"] = _gate(fresh)
            evidence_confirmed = confirmed and not closing and stage_confirmation_allowed
            completed_key = ("original_ventilation_complete" if paired
                             else "original_ventilation_rh_confirmed")
            completed_stage = episode.get(completed_key)
            retained = (completed_stage is not None and not completed_stage.get("published", True)
                        and not completed_stage.get("connection_unknown"))
            proven = (self.open and not closing and stage_confirmation_allowed
                      and (evidence_confirmed or retained))
            stage = {
                "evaluated": True, "counterform": counterform,
                "initial_form": initial, "maintained_form": maintained,
                "candidate_at": candidate["received_at"] if candidate is not None else None,
                "candidate_originals": candidate["newest"] if candidate is not None else (),
                "bound_support": candidate["support"] if candidate is not None else {},
                "support": anchors, "confirmable": confirmed, "proven": proven,
                "evidence_confirmed": evidence_confirmed,
                "retained_completed_evidence": retained,
                "close_conflict": (close_value is not None and close_value > significance
                                   and (initial or confirmed)),
            }
            gates.update(
                bound_initial_transition=_gate(candidate is not None),
                same_prefix_close_has_priority=_gate(not closing),
                input_group_complete=_gate(stage_confirmation_allowed),
                confirmed_door_open=_gate(self.open),
            )
            if evidence_confirmed:
                stage.update(confirmation_received_at=prefix.received_at,
                             confirmation_originals=tuple(newest))
            if retained:
                # Die abgeschlossene Originalfolge bleibt ein vergangener
                # Beleg. Neue Formkandidaten werden weiterhin separat geprüft.
                stage.update(
                    evidence_confirmed=True,
                    confirmation_received_at=completed_stage["confirmation_received_at"],
                    confirmation_originals=completed_stage["confirmation_originals"],
                    completed_candidate_at=completed_stage["candidate_at"],
                    completed_candidate_originals=completed_stage["candidate_originals"],
                    completed_bound_support=completed_stage["bound_support"],
                    completed_support=completed_stage["support"],
                )
            gates["retained_completed_original_evidence"] = _gate(retained)
            record[name] = stage
            return stage, gates

        if not episode.get("original_ventilation_rh_confirmed", {}).get("published", False):
            rh_windows = {position: windows[position.value].humidity for position in positions}
            if any(window.current[0][0] < episode["original_ventilation_floor"]
                   for window in rh_windows.values()):
                _decision(trace, "ventilation", None, {}, evaluated=False,
                          reason="humidity_support_before_opening_segment")
            else:
                relative, gates = stage_evidence(
                    "relative_humidity", "original_ventilation_rh", rh_windows, paired=False,
                )
                _decision(trace, "ventilation", relative["proven"], gates)
                trace["conditions"].update(
                    ventilation_sources_coherent=sources_coherent,
                    ventilation_proven=relative["proven"],
                    ventilation_close_conflict=relative["close_conflict"],
                )
                if (relative["evidence_confirmed"]
                        and "original_ventilation_rh_confirmed" not in episode):
                    episode["original_ventilation_rh_confirmed"] = {
                        **relative, "windows": episode["original_ventilation_rh"]["humidity_windows"],
                        "temperature_windows": episode["original_ventilation_rh"]["temperature_windows"],
                        "published": False,
                    }
        if status in ("invalid", "unresolved"):
            # Die begrenzte AH-Zuordnung entwertet keine native RH-/T-Stütze.
            # Fehlende Paarstützen widerlegen keine historische AH-Form.
            # Ein neuer Versuch beginnt mit echten aktuellen Paaren; die
            # alten gebundenen Fenster werden nicht mit ihnen vermischt.
            for key in ("original_ventilation", "original_ventilation_complete"):
                stage = episode.get(key)
                if stage is not None and not stage.get("published", False):
                    self._mark_original_unknown(
                        stage, prefix, positions, (Quantity.TEMPERATURE, Quantity.HUMIDITY),
                    )
            water = episode.pop("original_ventilation_water", None)
            for position in positions:
                self._retain_interrupted_evidence(position, "ventilation_pairs", water, prefix.received_at)
            record["connection_unknown"] = True
            _decision(trace, "ventilation_complete", None, {}, evaluated=False,
                      reason=support.get("reason", "unknown_water_support"))
            return
        relative = episode.get("original_ventilation_rh_confirmed")
        relative_confirmed = relative is not None and not relative.get("connection_unknown")
        if not relative_confirmed or len(water_windows) != len(positions):
            _decision(trace, "ventilation_complete", None, {
                "relative_humidity_confirmed": _gate(relative_confirmed),
            }, evaluated=False, reason=(
                "relative_humidity_unconfirmed" if not relative_confirmed else
                "pending_water_pair" if status == "pending" else "insufficient_water_originals"
            ))
            return
        absolute, gates = stage_evidence(
            "absolute_humidity", "original_ventilation", water_windows, paired=True,
        )
        absolute["paired_support"] = support
        gates["relative_humidity_confirmed"] = _gate(relative_confirmed)
        gates["paired_windows_complete"] = _gate(True)
        record.update(absolute)
        _decision(trace, "ventilation_complete", absolute["proven"], gates)
        trace["conditions"]["ventilation_complete_proven"] = absolute["proven"]
        if absolute["evidence_confirmed"] and "original_ventilation_complete" not in episode:
            episode["original_ventilation_complete"] = {
                **absolute, "windows": episode["original_ventilation"]["humidity_windows"],
                "temperature_windows": episode["original_ventilation"]["temperature_windows"],
                "published": False,
            }

    def _original_door_position_evidence(
        self, prefixes, channels, now, decision_at, trace, state, *,
        stage_confirmation_allowed=True, stage_original_roles=(),
    ):
        """Türformen und Lüftung kausal im einzigen Türbestand fortschreiben."""
        opening = closing = close_candidate = False
        level_close = False
        closing_positions = ()
        opening_value = closing_value = None
        significance = self.p["curve_original_significance"]
        for prefix in prefixes:
            episode = state["door"]
            positions = episode["positions"] if episode is not None else channels
            quantities = ((Quantity.TEMPERATURE,) if self.open else
                          (Quantity.TEMPERATURE, Quantity.HUMIDITY))
            if not all(self._position_available(position, quantities, now) for position in positions):
                trace["original_forms"] = {"evaluable": False, "waiting_for_current_values": True}
                continue
            if episode is not None:
                self._original_temperature_fall_evidence(episode, prefix, trace)
            context = self._original_bound_context(prefix.context, positions)
            complete_prefix = self._original_prefix_valid(prefix, positions)
            if self.open and episode is not None:
                closing_positions = tuple(
                    position for position in episode["positions"]
                    if self._value(position, Quantity.TEMPERATURE, now) is not None
                )
                if not self._original_prefix_valid(
                    prefix, closing_positions, (Quantity.TEMPERATURE,),
                ):
                    self._original_ventilation_evidence(
                        prefix, channels, now, decision_at, trace, episode, closing=False,
                        stage_confirmation_allowed=stage_confirmation_allowed,
                        stage_original_roles=stage_original_roles,
                    )
                    continue
            elif not complete_prefix:
                continue
            if episode is not None and not self.open and episode.get("original_context") not in (None, context):
                state["door"] = episode = None
                positions = channels
                context = self._original_bound_context(prefix.context, positions)
            if episode is not None and episode.get("original_evaluation_context") != context:
                if not self.open:
                    episode.pop("original_close", None)
                episode.pop("original_humidity_minima", None)
                episode["original_evaluation_context"] = context
            expected = tuple(position.value for position in positions)
            windows = dict(prefix.windows)
            same_opening_sources = bool(episode) and all(
                episode["epochs"].get(position) == epochs
                for position, epochs in context
            )
            minima = episode.get("original_humidity_minima") if self.open and episode else None
            if self.open and not same_opening_sources:
                minima = None
            if minima is not None:
                for position in expected:
                    if position in windows:
                        humidity = windows[position].humidity
                        minima[position] = min(minima.get(position, humidity.mean), humidity.mean)
            contrasts = (original_contrasts(
                windows, expected_positions=expected, humidity_minima=minima,
            ) if complete_prefix else None)
            forms = {
                "received_at": prefix.received_at, "positions": expected,
                "evaluable": contrasts is not None, "new_original": prefix.original,
            }
            trace["original_forms"] = forms
            if contrasts is not None:
                forms.update(
                    opening=contrasts.opening, rise=contrasts.rise, fall=contrasts.fall,
                    humidity_fall_onset=contrasts.humidity_fall_onset,
                    opening_support=contrasts.opening_support,
                    background_slope=contrasts.background_slope,
                    background_residual=contrasts.background_residual,
                )
            if not self.open:
                opening_value = None
                current_opening = contrasts.opening_support if contrasts is not None else None
                forms["current_opening_support"] = _gate(
                    current_opening > self.p["curve_original_open_contrast"]
                    if current_opening is not None else None,
                    value=current_opening, operator=">",
                    threshold=self.p["curve_original_open_contrast"], unit="contrast",
                )
                if not all(position in channels for position in positions):
                    if episode is not None:
                        episode.pop("original_open", None)
                        self._reset_original_ventilation(episode, "opening_sources_unavailable")
                        episode.pop("original_ventilation_phase", None)
                    continue
                candidate = episode.get("original_open") if episode is not None else None
                if candidate is not None:
                    if not self._original_contiguous(prefix, candidate["continuity"]):
                        self._mark_original_unknown(candidate, prefix, positions, quantities)
                        forms["support_continuity"] = _gate(None, reason="unobserved_original_interval")
                    # Bereits geprüfte Anschlussoriginale bleiben erhalten,
                    # auch wenn das andere Rollenfenster noch aussteht.
                    newest_originals = {
                        (position.value, quantity): samples[-1][0]
                        for position, temperature, humidity in prefix.points
                        if position in positions
                        for quantity, samples in ((Quantity.TEMPERATURE, temperature),
                                                  (Quantity.HUMIDITY, humidity))
                        if samples
                    }
                    if not candidate.get("connection_unknown"):
                        candidate["continuity"] = tuple(
                            (name, quantity, newest_originals[name, quantity])
                            for name, quantity, _ in candidate["continuity"]
                        )
                if contrasts is None:
                    forms["opening_confirmation"] = _gate(
                        None, reason="waiting_for_original_windows",
                    )
                    continue
                humidity_kinks = self._original_humidity_kinks(prefix, positions)
                kink_gate = self._original_humidity_kink_gate(humidity_kinks, rising=False)
                raw_binding = bool(
                    prefix.original and contrasts.humidity_fall_onset > significance
                )
                binding = bool(raw_binding and kink_gate["result"])
                forms.update(
                    binding_humidity_fall_onset=_gate(
                        raw_binding, value=contrasts.humidity_fall_onset, operator=">",
                        threshold=significance, unit="contrast",
                    ),
                    binding_humidity_fall_kink=kink_gate,
                    binding_humidity_fall=_gate(binding),
                )
                if candidate is not None and candidate.get("connection_unknown"):
                    if not self._original_resume_ready(prefix, positions, quantities, candidate):
                        continue
                    if not binding:
                        points = {position.value: humidity for position, _, humidity in prefix.points}
                        refuted = any(
                            self._original_opening_humidity_connected(
                                points[key[1].value], onset, windows[key[1].value].humidity,
                                key[1].value, self._original_prefix_time_support(prefix, key[1], key[2]),
                            ) is False for key, onset in candidate["onset_witnesses"]
                            if key[2] == Quantity.HUMIDITY
                        )
                        if refuted:
                            forms["opening_candidate_reset_reason"] = "humidity_branch_recovery"
                            episode.pop("original_open", None)
                            self._reset_original_ventilation(episode, "humidity_branch_recovery")
                            episode.pop("original_ventilation_phase", None)
                        continue
                    for position in positions:
                        self._retain_interrupted_evidence(position, "door_open", episode, prefix.received_at)
                    state["door"] = episode = None
                if episode is None and binding:
                    state["door"] = episode = self._new_door_episode(positions, now, now)
                    episode["original_context"] = context
                    episode["original_evaluation_context"] = context
                    self._original_temperature_fall_evidence(episode, prefix, trace)
                candidate = episode.get("original_open") if episode is not None else None
                if candidate is None and binding:
                    onset_intervals = self._original_local_fall_intervals(
                        prefix, contrasts.positions,
                    )
                    candidate = {
                        "received_at": prefix.received_at,
                        "newest": self._original_newest(contrasts),
                        "context": context, "windows": contrasts.positions,
                        "continuity": tuple(
                            (name, quantity, window.newest_at)
                            for name, quantity, window in self._original_role_windows(
                                contrasts.positions, (Quantity.TEMPERATURE, Quantity.HUMIDITY),
                            )
                        ),
                        "humidity_fall_onset": contrasts.humidity_fall_onset,
                        "humidity_kinks": humidity_kinks,
                        "humidity_onset_intervals": onset_intervals,
                        "humidity_fall_transition_intervals": dict(onset_intervals),
                        "temperature_backgrounds": self._original_temperature_backgrounds(
                            prefix, tuple((name, pair.temperature)
                                          for name, pair in contrasts.positions),
                        ),
                        "onset_witnesses": self._original_opening_onset_witnesses(
                            episode, prefix, contrasts.positions,
                            onset_intervals=onset_intervals,
                            diagnostics=forms.setdefault("opening_onset_diagnostics", {}).setdefault("initial", []),
                        ),
                        "falling_temperatures": {
                            name for name, pair in contrasts.positions
                            if pair.temperature.v < -significance * pair.temperature.e
                        },
                    }
                    episode["original_open"] = candidate
                followups = (self._original_followups(
                    prefix, self._original_role_windows(
                        candidate["windows"], (Quantity.TEMPERATURE, Quantity.HUMIDITY),
                    ),
                    continuity=candidate["continuity"],
                ) if candidate is not None else None)
                fresh_block = bool(followups) and all(
                    form.fresh_current == self.p["curve_original_samples"]
                    for form in followups.values()
                )
                opening_value = (min(form.reference_level_loss for form in followups.values())
                                 if followups else None)
                lower_level = bool(followups) and all(
                    form.reference_level_loss > self.p["curve_original_open_contrast"]
                    for form in followups.values()
                )
                forms.update(
                    followup=self._original_followup_trace(followups),
                    complete_fresh_original_block=_gate(fresh_block),
                    contiguous_candidate_support=_gate(
                        followups is not None if candidate is not None else None,
                    ),
                    lower_level=_gate(
                        lower_level, value=opening_value, operator=">",
                        threshold=self.p["curve_original_open_contrast"], unit="contrast",
                        reference="bound_previous_original_mean",
                    ),
                )
                forms["opening_confirmation"] = _gate(False, reason="joint_original_evidence_pending")
                if candidate is not None and followups is None:
                    self._mark_original_unknown(candidate, prefix, positions, quantities)
                    forms["support_continuity"] = _gate(None, reason="unobserved_original_interval")
                    continue
                infusion_independent, infusion_guard = (
                    self._original_opening_infusion_guard(candidate, prefix, positions, state)
                    if candidate is not None else (False, None)
                )
                if candidate is not None and prefix.original:
                    candidate["continuity"] = tuple(
                        (name, quantity, form.window.newest_at)
                        for (name, quantity), form in followups.items()
                    )
                    # Jede Rolle liefert ihren eigenen kausalen RH-Knick.
                    # Ein später reagierender Ast nutzt seine aktuelle Stütze;
                    # der laufende Kandidat und seine Gegenformen bleiben eins.
                    bound = dict(candidate["onset_witnesses"])
                    onset_intervals = candidate["humidity_onset_intervals"]
                    current_intervals = self._original_local_fall_intervals(prefix, contrasts.positions)
                    active_intervals = candidate["humidity_fall_transition_intervals"]
                    current_kinks = dict(humidity_kinks)
                    for name, pair in contrasts.positions:
                        kink = current_kinks.get(name)
                        if name in current_intervals:
                            # Der erste eigene RH-Schritt bleibt auch ohne
                            # T-Zuordnung derselbe. Ein nichtnegativer Gesamtknick
                            # beendet ihn erst ohne eigene aufgelöste Sekante.
                            active_intervals.setdefault(name, current_intervals[name])
                        elif (kink is not None and kink.window is pair.humidity
                                and kink.effective_change >= 0):
                            active_intervals.pop(name, None)
                    for name, interval in active_intervals.items():
                        onset_intervals.setdefault(name, interval)
                    changed = False
                    if len(bound) < len(positions):
                        for key, onset in self._original_opening_onset_witnesses(
                            episode, prefix, tuple(
                                (name, pair) for name, pair in contrasts.positions
                                if ("door_open", Position(name), Quantity.HUMIDITY) not in bound
                            ),
                            onset_intervals=onset_intervals,
                            diagnostics=forms.setdefault("opening_onset_diagnostics", {}).setdefault("continued", []),
                        ):
                            if key not in bound:
                                bound[key] = onset
                                changed = True
                    # Speicherprüfung und Formzuordnung konsumieren denselben
                    # vom T-Fallbesitzer abgeleiteten Originalzeitbereich.
                    transitions = {
                        name: role["attribution_bounds"]
                        for name, role in infusion_guard["new_local_temperature_fall"]["positions"].items()
                        if role["attribution_bounds"] is not None
                    }
                    local_intervals = candidate.setdefault("local_humidity_onset_intervals", {})
                    local_candidates, local_background = self._original_opening_local_fall_intervals(
                        candidate, prefix, contrasts.positions,
                    )
                    forms["local_humidity_fall_kink"] = local_background
                    for name, interval in local_candidates.items():
                        # Der spätere lokale Ast gehört zur aktuellen eigenen
                        # RH-Stütze, nicht zur ersten Sekante des alten Falls.
                        # Erst der aufgelöste Gesamtknick wird fest gehalten;
                        # eine frühere Einzel-Sekante mit Nullkontrast darf ihn
                        # nicht verbrauchen. Folgeoriginale prüfen dieses feste
                        # Fenster, statt den Bezug bei jedem Präfix zu verschieben.
                        held = local_intervals.get(name)
                        if (name in transitions and transitions[name][0]
                                <= interval["interval"].previous[0] <= transitions[name][1]
                                and interval["kink"].effective_change < 0
                                and (held is None or held["kink"].effective_change >= 0)):
                            local_intervals[name] = interval
                    if infusion_guard["new_local_temperature_fall"]["result"]:
                        # T regt nur die Zuordnung an. Ersetzen darf allein
                        # ein eigener späterer RH-Knick mit seinem festen
                        # Originalanker und erneut gestützter eigener Form.
                        for key, onset in self._original_opening_onset_witnesses(
                            episode, prefix, tuple(
                                (name, pair) for name, pair in contrasts.positions
                                if name in local_intervals and (
                                    ("door_open", Position(name), Quantity.HUMIDITY) not in bound
                                    or local_intervals[name]["interval"].previous[0]
                                    > bound["door_open", Position(name), Quantity.HUMIDITY].onset_at
                                )
                            ),
                            transitions=transitions, onset_intervals=local_intervals,
                            diagnostics=forms.setdefault("opening_onset_diagnostics", {}).setdefault("local", []),
                        ):
                            if key not in bound or onset.onset_at > bound[key].onset_at:
                                bound[key] = onset
                                changed = True
                    candidate["onset_witnesses"] = tuple(bound.items())
                    if changed:
                        candidate["onset_witness_received_at"] = prefix.received_at
                if candidate is not None:
                    return_evidence = self._original_opening_return_evidence(candidate, prefix, positions)
                    infusion_independent &= return_evidence["result"]
                    infusion_guard["independent_humidity_branch"] = return_evidence
                    infusion_guard["result"] = infusion_independent
                    if not return_evidence["result"]:
                        infusion_guard["reason"] = "own_humidity_branch_not_separated"
                points = {position.value: tuple(self._original_segment_points(prefix, position, quantity)
                                                for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY))
                          for position, _, _ in prefix.points}
                evidence = {}
                if candidate is not None and followups:
                    backgrounds = dict(candidate["humidity_kinks"])
                    temperature_backgrounds = dict(candidate["temperature_backgrounds"])
                    local_falls = infusion_guard["new_local_temperature_fall"]["positions"]
                    for name, pair in candidate["windows"]:
                        for index, quantity in enumerate((Quantity.TEMPERATURE, Quantity.HUMIDITY)):
                            local_temperature = bool(
                                quantity == Quantity.TEMPERATURE
                                and local_falls.get(name, {}).get("supported", False)
                            )
                            reference = (candidate["temperature_fall_witnesses"][name]
                                         if local_temperature else getattr(pair, quantity.value))
                            background = (backgrounds[name] if quantity == Quantity.HUMIDITY
                                          else candidate["temperature_fall_backgrounds"][name]
                                          if local_temperature else temperature_backgrounds[name])
                            # Der eigene lokale T-Knick hält sein aktuelles
                            # K-Niveau. Sein früherer Vorlauf kann noch zum
                            # Aufgussanstieg gehören und liegt dann zu niedrig.
                            evidence[name, quantity] = original_decline_evidence(
                                points[name][index], reference, background=background,
                                reference_current=local_temperature,
                            )
                resolved_fall = fresh_block and bool(evidence) and all(
                    proof is not None
                    and (quantity == Quantity.HUMIDITY
                         or proof.direction < -self.p["curve_original_open_contrast"])
                    and proof.level_loss > self.p["curve_original_open_contrast"]
                    for (_, quantity), proof in evidence.items()
                )
                # Auch schon gebundene Zeugen müssen im aktuellen Präfix
                # noch demselben RH-Ast angehören. Eine vollständige Erholung
                # darf nicht unter dem alten Vorlaufniveau verborgen bleiben,
                # wenn später die zweite Rolle einen neuen Fall bindet.
                humidity_branch_counterform = bool(candidate) and any(
                    self._original_opening_humidity_connected(
                        points[key[1].value][1], onset,
                        windows[key[1].value].humidity, key[1].value,
                        self._original_prefix_time_support(prefix, key[1], key[2]),
                    ) is False
                    for key, onset in candidate["onset_witnesses"]
                    if key[2] == Quantity.HUMIDITY
                )
                forms["humidity_branch_counterform"] = _gate(humidity_branch_counterform)
                if humidity_branch_counterform:
                    forms["opening_candidate_reset_reason"] = "humidity_branch_recovery"
                    episode.pop("original_open", None)
                    self._reset_original_ventilation(episode, "humidity_branch_recovery")
                    episode.pop("original_ventilation_phase", None)
                    continue
                humidity_onsets = bool(candidate) and {
                    key[1] for key, _ in candidate["onset_witnesses"]
                } == set(positions)
                witness = candidate.get("lowering_witness") if candidate is not None else None
                if witness is None and resolved_fall and prefix.original:
                    witness = {
                        "received_at": prefix.received_at, "windows": contrasts.positions,
                        "newest": self._original_newest(contrasts),
                    }
                    candidate["lowering_witness"] = witness
                preserved_lowering = lower_level or witness is not None
                forms.update(
                    lowering_witness_received_at=witness["received_at"] if witness is not None else None,
                    lowering_witness_originals=witness["newest"] if witness is not None else (),
                    lowering_witness_support=(self._original_support(witness["windows"])
                                              if witness is not None else {}),
                    original_fall_evidence={
                        f"{name}:{quantity.value}": {
                            "available": proof is not None,
                            "first_at": proof.points[0][0] if proof else None,
                            "newest_at": proof.points[-1][0] if proof else None,
                            "original_count": len(proof.points) if proof else 0,
                            "direction_required": quantity == Quantity.TEMPERATURE,
                            "reference_level": proof.reference_level if proof else None,
                            "reference_current": proof.reference_current if proof else None,
                            "level_background_center": proof.level_center if proof else None,
                            "level_difference_scale": proof.level_scale if proof else None,
                            "observed_level_loss": (proof.reference_level - proof.mean
                                                    if proof else None),
                            "direction": _gate(
                                proof.direction < -self.p["curve_original_open_contrast"]
                                if proof else None,
                                value=proof.direction if proof else None, operator="<",
                                threshold=-self.p["curve_original_open_contrast"], unit="contrast",
                            ),
                            "level_loss": _gate(
                                proof.level_loss > self.p["curve_original_open_contrast"]
                                if proof else None,
                                value=proof.level_loss if proof else None, operator=">",
                                threshold=self.p["curve_original_open_contrast"], unit="contrast",
                            ),
                        } for (name, quantity), proof in evidence.items()
                    },
                    resolved_joint_fall=_gate(resolved_fall),
                    bound_humidity_onsets=_gate(humidity_onsets),
                    native_humidity_onset_intervals={
                        group: {
                            name: {
                                "previous": native["interval"].previous,
                                "current": native["interval"].current,
                                "received_at": native["received_at"],
                                "window_start_at": native["window"].start_at,
                                "window_newest_at": native["window"].newest_at,
                                "direction": native["interval"].direction,
                                "change": native["interval"].change,
                                "background_tolerance": native["kink"].tolerance,
                                "background_effective_change": native["kink"].effective_change,
                                "background_after": native.get("background_after"),
                                "background_component_received_at": native.get("component_received_at"),
                                "background_return_newest_at": native.get("return_newest_at"),
                                "background_temperature_reference_start_at": native.get("temperature_reference_start_at"),
                                "background_history_start": native["kink"].history_start,
                                "background_history_end": native["kink"].history_end,
                                "background_history_count": native["kink"].history_count,
                                "background_status": native["kink"].status,
                                "background_catalog_scale": native["kink"].catalog_scale,
                                "background_used_scale": native["kink"].used_scale,
                            } for name, native in intervals.items()
                        } for group, intervals in (
                            ("binding", candidate["humidity_onset_intervals"]),
                            ("transition", candidate["humidity_fall_transition_intervals"]),
                            ("local", candidate.get("local_humidity_onset_intervals", {})),
                        )
                    } if candidate is not None else {},
                )
                fall_ends = {
                    name: original_flattening(pair.humidity, pair.temperature,
                                              significance=significance)
                    for name, pair in contrasts.positions
                }
                fall_ended = any(
                    form is not None and form.previous_fall.result
                    and form.current_flat.result and form.braking.result
                    for form in fall_ends.values()
                )
                temperature_recovery = temperature_fall_ended = False
                if candidate is not None and prefix.original:
                    falling = candidate["falling_temperatures"]
                    for name, pair in contrasts.positions:
                        temperature = pair.temperature
                        ended = original_flattening(
                            temperature, pair.humidity, significance=significance,
                        )
                        temperature_recovery |= bool(
                            name in falling and temperature.v > significance * temperature.e
                        )
                        temperature_fall_ended |= bool(
                            ended is not None and ended.previous_fall.result
                            and ended.current_flat.result and ended.braking.result
                        )
                        if temperature.v < -significance * temperature.e:
                            falling.add(name)
                forms["humidity_fall_ended"] = _gate(fall_ended)
                humidity_recovery = any(pair.humidity.v > significance * pair.humidity.e
                                        for _, pair in contrasts.positions)
                temperature_counterform = bool(
                    temperature_recovery or temperature_fall_ended and not preserved_lowering
                )
                forms["resolved_opening_counterform"] = bool(
                    candidate is not None and followups and any(
                        form.fresh_current == self.p["curve_original_samples"]
                        and form.current_direction > significance
                        and form.current_change > significance
                        for form in followups.values()
                    )
                )
                forms["temperature_fall_counterform"] = _gate(temperature_counterform)
                forms["temperature_recovery_counterform"] = _gate(temperature_recovery)
                forms["temperature_fall_ended"] = _gate(temperature_fall_ended)
                forms["humidity_recovery"] = _gate(humidity_recovery)
                forms["ended_fall_retained_by_level"] = _gate(
                    preserved_lowering and (fall_ended or humidity_recovery or temperature_fall_ended),
                )
                forms["humidity_fall_end_contrasts"] = {
                    name: {
                        comparison: _gate(
                            contrast.result, value=contrast.value, operator=contrast.operator,
                            threshold=contrast.threshold, unit="%/s",
                        )
                        for comparison in ("previous_fall", "current_flat", "braking")
                        if (contrast := getattr(form, comparison)) is not None
                    }
                    for name, form in fall_ends.items() if form is not None
                }
                if (temperature_counterform
                        or (fall_ended or humidity_recovery) and not preserved_lowering):
                    forms["opening_candidate_reset_reason"] = (
                        "temperature_recovery" if temperature_recovery else
                        "temperature_fall_ended_without_level" if temperature_fall_ended else
                        "humidity_fall_ended_without_level" if fall_ended else
                        "humidity_recovery_without_level"
                    )
                    if episode is not None and episode.get("retained_heating_only"):
                        episode.pop("original_open", None)
                        self._reset_original_ventilation(episode, "opening_counterform")
                        episode.pop("original_ventilation_phase", None)
                        continue
                    if episode is not None:
                        state["door"] = None
                    continue
                if episode is None:
                    continue
                forms["infusion_decay_guard"] = infusion_guard
                # Ein gemischtes K-Fenster kann noch deutlich fallen,
                # obwohl die jüngsten Originale bereits zurückkehren. Ein
                # zuvor vollständig frisch belegter T-Fall gehört weiterhin
                # derselben Öffnung; deren komplette Gegenformen wurden oben
                # unabhängig von diesem Rollenbeleg geprüft.
                temperature_edges = {
                    name: original_edge_change(form.window, form.reference)
                    for (name, quantity), form in (followups or {}).items()
                    if quantity == Quantity.TEMPERATURE
                }
                temperature_falls = (candidate.setdefault("proven_temperature_falls", {})
                                     if candidate is not None else {})
                if prefix.original:
                    for name, edge in temperature_edges.items():
                        form = followups[name, Quantity.TEMPERATURE]
                        proof = evidence.get((name, Quantity.TEMPERATURE))
                        retained = temperature_falls.get(name)
                        if (retained is not None and proof is not None
                                and (retained["evidence"].reference is not proof.reference
                                     or retained["evidence"].reference_current != proof.reference_current)):
                            temperature_falls.pop(name)
                        if (name not in temperature_falls
                                and form.fresh_current == self.p["curve_original_samples"]
                                and proof is not None
                                and len(proof.points) >= self.p["curve_original_samples"]
                                and proof.direction < -self.p["curve_original_open_contrast"]
                                and proof.level_loss > self.p["curve_original_open_contrast"]
                                and edge is not None and edge.direction <= significance):
                            temperature_falls[name] = {
                                "received_at": prefix.received_at,
                                "window": form.window, "evidence": proof, "edge": edge,
                            }
                retained_temperature_fall = set(temperature_falls) == set(expected)
                temperature_not_recovering = retained_temperature_fall or (
                    bool(temperature_edges) and all(
                        edge is not None and edge.direction <= significance
                        for edge in temperature_edges.values()
                    )
                )
                forms["retained_temperature_fall"] = {
                    "result": retained_temperature_fall,
                    "positions": {
                        name: {
                            "received_at": retained["received_at"],
                            "start_at": retained["window"].start_at,
                            "newest_at": retained["window"].newest_at,
                            "reference_start_at": retained["evidence"].reference.start_at,
                            "reference_newest_at": retained["evidence"].reference.newest_at,
                            "direction": retained["evidence"].direction,
                            "level_loss": retained["evidence"].level_loss,
                            "edge_direction": retained["edge"].direction,
                        } for name, retained in temperature_falls.items()
                    },
                }
                forms["current_temperature_recovery"] = {
                    name: _gate(
                        edge.direction > significance if edge is not None else None,
                        value=edge.direction if edge is not None else None,
                        operator=">", threshold=significance, unit="contrast",
                    )
                    for name, edge in temperature_edges.items()
                }
                opening = bool(
                    candidate is not None and prefix.original
                    and resolved_fall and humidity_onsets and infusion_independent
                    and temperature_not_recovering
                )
                forms["confirmation_branch"] = (
                    "resolved_joint_original_fall" if opening else None
                )
                forms["opening_confirmation"] = _gate(
                    opening, reason=forms["confirmation_branch"] or "joint_original_evidence_pending",
                )
                forms.update(
                    bound_humidity_fall_onset=(candidate["humidity_fall_onset"]
                                              if candidate is not None else None),
                    bound_humidity_fall_kink=(self._original_humidity_kink_gate(
                        candidate["humidity_kinks"], rising=False,
                    ) if candidate is not None else None),
                    candidate_received_at=(candidate["received_at"] if candidate is not None else None),
                    candidate_originals=candidate["newest"] if candidate is not None else (),
                    binding_onsets=tuple({
                        "position": key[1].value, "quantity": key[2].value,
                        "onset_at": witness.onset_at, "candidate_id": witness.candidate_id,
                        "first_at": witness.original_points[0][0],
                        "newest_at": witness.features.newest_at,
                    } for key, witness in candidate["onset_witnesses"])
                    if candidate is not None else (),
                    onset_witness_received_at=(candidate.get("onset_witness_received_at",
                                                           candidate["received_at"])
                                              if candidate is not None
                                              and candidate["onset_witnesses"] else None),
                    binding_support=(self._original_support(candidate["windows"])
                                     if candidate is not None else {}),
                    continued_support=self._original_support(contrasts.positions),
                    bound_sources={position.value: sources
                                   for position, sources in episode["sources"].items()},
                    continued_humidity_fall=_gate(
                        contrasts.fall > significance, value=contrasts.fall, operator=">",
                        threshold=significance, unit="contrast",
                    ),
                    later_original_confirmation=_gate(opening),
                )
                if witness is not None:
                    self._original_ventilation_evidence(
                        prefix, channels, now, decision_at, trace, episode, closing=False,
                        stage_confirmation_allowed=stage_confirmation_allowed,
                        stage_original_roles=stage_original_roles,
                    )
                if opening:
                    episode["original_humidity_minima"] = {
                        position: pair.humidity.mean for position, pair in contrasts.positions
                    }
                    forms["confirmation_received_at"] = prefix.received_at
                    forms["confirmation_originals"] = self._original_newest(contrasts)
                    forms["confirmation_support"] = self._original_support(contrasts.positions)
                    candidate["confirmation_windows"] = contrasts.positions
                    candidate["confirmation_received_at"] = prefix.received_at
                    # Die äußere Aktion bestätigt dieselbe Öffnung. Spätere
                    # bereits empfangene Präfixe danach nicht verschlucken.
                    break
                continue
            if episode is None:
                continue
            # Der bestehende Türvertrag nutzt die noch verfügbaren T-Rollen
            # der gebundenen Öffnung; RH-Ausfall sperrt diesen Zweig nicht.
            thermal_context = tuple(
                (position, dict(prefix.context)[position][0]) for position in episode["positions"]
            )
            if episode.get("original_thermal_context") != thermal_context:
                episode.pop("original_close", None)
                episode["original_thermal_context"] = thermal_context
            candidate = episode.get("original_close")
            if candidate is not None and not self._original_contiguous(
                prefix, candidate["continuity"],
            ):
                self._mark_original_unknown(candidate, prefix, closing_positions, (Quantity.TEMPERATURE,))
                forms["support_continuity"] = _gate(None, reason="unobserved_original_interval")
            thermal = original_thermal_contrasts(
                dict(prefix.temperatures),
                expected_positions=tuple(position.value for position in closing_positions),
            )
            if thermal is None:
                closing_value = None
                self._original_ventilation_evidence(
                    prefix, channels, now, decision_at, trace, episode, closing=False,
                    stage_confirmation_allowed=stage_confirmation_allowed,
                    stage_original_roles=stage_original_roles,
                )
                continue
            if minima is None and same_opening_sources and contrasts is not None:
                episode["original_humidity_minima"] = {
                    position: pair.humidity.mean for position, pair in contrasts.positions
                }
            combined = bool(
                contrasts is not None and same_opening_sources
                and minima is not None and closing_positions == episode["positions"]
            )
            thermal_backgrounds = dict(self._original_temperature_backgrounds(
                prefix, tuple((name, window) for name, window in prefix.temperatures
                              if Position(name) in closing_positions),
            ))
            local_braking = bool(closing_positions) and all(
                (background := thermal_backgrounds.get(position.value)) is not None
                and background.effective_change > 0
                and background.window.b < -significance * background.window.e_minus
                for position in closing_positions
            )
            own_new_support = (episode.get("rejected_close_after") is None or all(
                dict(prefix.temperatures)[position.value].current[0][0]
                > episode["rejected_close_after"] for position in closing_positions
            ))
            close_candidate = own_new_support and (local_braking or thermal.warming_min > significance)
            newest = tuple((position.value, dict(prefix.temperatures)[position.value].newest_at)
                           for position in closing_positions)
            candidate = episode.get("original_close")
            if candidate is not None and candidate.get("connection_unknown"):
                resumed = self._original_resume_ready(
                    prefix, closing_positions, (Quantity.TEMPERATURE,), candidate,
                )
                renewed = resumed and any(
                    self._original_temperature_counterform(
                        dict(prefix.temperatures)[name], reference,
                    ) for name, reference in candidate["recovery_windows"]
                )
                new_level = resumed and self._original_temperature_braking_forms(
                    episode, closing_positions,
                ) and self._original_temperature_braking(
                    self._original_temperature_fall_followups(episode, prefix, closing_positions),
                )
                if renewed:
                    forms["closing_candidate_reset_reason"] = "temperature_counterform"
                    episode.pop("original_close", None)
                    candidate = None
                elif resumed and (close_candidate or new_level):
                    for position in closing_positions:
                        self._retain_interrupted_evidence(position, "door_close", candidate, prefix.received_at)
                    episode.pop("original_close", None)
                    candidate = None
                else:
                    self._original_ventilation_evidence(
                        prefix, channels, now, decision_at, trace, episode, closing=False,
                        stage_confirmation_allowed=stage_confirmation_allowed,
                        stage_original_roles=stage_original_roles,
                    )
                    continue
            if candidate is not None and tuple(name for name, _ in candidate["newest"]) != tuple(
                name for name, _ in newest
            ):
                candidate = None
                episode.pop("original_close", None)
            if candidate is None and close_candidate and prefix.original:
                candidate = {
                    "received_at": prefix.received_at, "newest": newest,
                    "windows": tuple((position.value, dict(prefix.temperatures)[position.value])
                                     for position in closing_positions),
                    "context": thermal_context,
                    "continuity": tuple((name, Quantity.TEMPERATURE, stamp)
                                        for name, stamp in newest),
                }
                candidate["recovery_windows"] = candidate["windows"]
                episode["original_close"] = candidate
            followups = (self._original_followups(
                prefix, tuple((name, Quantity.TEMPERATURE, window)
                              for name, window in candidate["windows"]),
                continuity=candidate["continuity"],
            ) if candidate is not None else None)
            counterform = False
            edges = {}
            if followups is not None:
                recovery = dict(candidate["recovery_windows"])
                for (name, _), form in followups.items():
                    supported = recovery[name]
                    edge = original_edge_change(form.window, supported)
                    edges[name] = {
                        "previous_original_at": form.window.current[-2][0],
                        "newest_original_at": form.window.newest_at,
                        "reference_newest_at": supported.newest_at,
                        "direction": _gate(
                            edge.direction < -significance if edge is not None else None,
                            value=edge.direction if edge is not None else None,
                            operator="<", threshold=-significance, unit="contrast",
                        ),
                        "change": _gate(
                            edge.change < -significance if edge is not None else None,
                            value=edge.change if edge is not None else None,
                            operator="<", threshold=-significance, unit="contrast",
                        ),
                    }
                    # Derselbe neue Block schützt auch den gemeinsamen
                    # Schlusskandidaten; die Endkante allein löst ihn nicht.
                    renewed = self._original_temperature_counterform(
                        form.window, supported,
                    )
                    counterform |= renewed
                    if renewed:
                        episode.get("original_temperature_falls", {}).pop(Position(name), None)
                    if form.window.v > supported.v:
                        recovery[name] = form.window
                if prefix.original:
                    candidate["recovery_windows"] = tuple(recovery.items())
                    candidate["continuity"] = tuple(
                        (name, quantity, form.window.newest_at)
                        for (name, quantity), form in followups.items()
                    )
            if candidate is not None and counterform:
                forms["closing_candidate_reset_reason"] = "temperature_counterform"
                episode.pop("original_close", None)
                candidate = None
                followups = None
            elif candidate is not None and followups is None:
                self._mark_original_unknown(candidate, prefix, closing_positions, (Quantity.TEMPERATURE,))
                forms["support_continuity"] = _gate(None, reason="unobserved_original_interval")
            count = self.p["curve_original_samples"]
            fresh_block = bool(followups) and all(
                form.fresh_previous == count and form.fresh_current == count
                for form in followups.values()
            )
            bound_braking = (min(form.current_change for form in followups.values())
                             if followups else None)
            discrete_braking = self._original_temperature_braking_forms(episode, closing_positions)
            closing_value = contrasts.closing if combined else thermal.closing
            sustained_braking = None
            warming_close = humidity_braking_close = False
            if bound_braking is not None:
                branches = [min(bound_braking, thermal.warming_max), thermal.warming_min]
                warming_close = max(branches) > significance
                # RH kann sich auch in der weiter offenen, abkühlenden Kabine
                # vom lokalen Minimum erholen. Dieser Zweig benötigt deshalb
                # denselben lokalen T-Formbeleg wie die reine Bremse; eine
                # weiterhin negative T-Steigung bleibt dabei zulässig.
                if combined and discrete_braking:
                    humidity_braking = min(bound_braking, contrasts.humidity_recovery)
                    branches.append(humidity_braking)
                    humidity_braking_close = humidity_braking > significance
                sustained_braking = min(
                    min(form.current_change, form.previous_change)
                    if form.reference.b < -significance * form.reference.e_minus
                    else -significance
                    for form in followups.values()
                )
                if discrete_braking:
                    branches.append(sustained_braking)
                closing_value = max(branches)
            # Jede Rolle bindet ihren lokalen Bremsknick am echten Fallast.
            # Zwei danach neue Blöcke bestätigen die zeitversetzten Bremsen,
            # auch wenn die Temperatur weiterhin deutlich negativ geneigt ist.
            fall_followups = self._original_temperature_fall_followups(
                episode, prefix, closing_positions,
            )
            continued_braking = self._original_temperature_braking(fall_followups)
            # Two complete new flat T blocks after the held fall establish
            # the settled level directly. A rolling polynomial's loss of its
            # old kink does not erase these own original level witnesses.
            plateau_close = continued_braking and all(
                abs(form.previous_direction) <= significance
                and abs(form.current_direction) <= significance
                for form in fall_followups.values()
            )
            level_close = own_new_support and continued_braking and (discrete_braking or plateau_close)
            close_candidate = candidate is not None or level_close
            correction_fresh = self._original_close_confirmation_fresh(
                episode, prefix, closing_positions,
            )
            closing = bool(
                prefix.original and correction_fresh and (
                    candidate is not None and fresh_block and closing_value > significance
                    or level_close
                )
            )
            forms.update(
                thermal_evaluable=True,
                local_temperature_braking=_gate(local_braking),
                closing=closing_value, closing_positions=tuple(p.value for p in closing_positions),
                humidity_recovery_used=combined and discrete_braking and bound_braking is not None,
                closing_confirmation=_gate(
                    closing_value > significance, value=closing_value,
                    operator=">", threshold=significance, unit="contrast",
                ),
                followup=self._original_followup_trace(followups),
                complete_fresh_original_block=_gate(fresh_block),
                temperature_counterform=_gate(counterform),
                temperature_edge_counterform=edges,
                bound_temperature_braking=_gate(
                    bound_braking > significance if bound_braking is not None else None,
                    value=bound_braking, operator=">", threshold=significance, unit="contrast",
                ),
                new_temperature_level=_gate(level_close),
                sustained_temperature_braking=_gate(level_close),
                discrete_temperature_braking=_gate(discrete_braking),
                temperature_close_terms={
                    "warming_min": thermal.warming_min,
                    "warming_max": thermal.warming_max,
                    "bound_braking": bound_braking,
                    "humidity_recovery": contrasts.humidity_recovery if combined else None,
                    "warming_close": warming_close,
                    "humidity_braking_close": humidity_braking_close,
                    "sustained_braking": sustained_braking,
                    "plateau_close": plateau_close,
                    "discrete_braking": discrete_braking,
                },
                temperature_braking_witnesses={
                    position.value: {
                        "candidate_id": witness.candidate_id,
                        "onset_at": witness.onset_at,
                        "first_observed_at": witness.first_observed_at,
                        "first_newest_at": witness.first_newest_at,
                        "fresh_newest_at": witness.fresh_newest_at,
                        "fresh_observed_at": witness.fresh_observed_at,
                        "newest_at": witness.features.newest_at,
                        "hinge_order": witness.features.hinge_order,
                        "slope_before": witness.features.slope_before,
                        "slope_after": witness.features.slope_after,
                        "score_gain": witness.features.score_gain,
                    }
                    for position in closing_positions
                    if (witness := episode.get("original_temperature_falls", {}).get(
                        position, {},
                    ).get("braking_form")) is not None
                },
                fresh_temperature_support_after_correction=_gate(correction_fresh),
                temperature_level_followup=self._original_followup_trace(fall_followups),
                candidate_received_at=candidate["received_at"] if candidate is not None else None,
                candidate_originals=candidate["newest"] if candidate is not None else (),
                later_original_confirmation=_gate(closing),
            )
            if closing:
                if discrete_braking and (
                    level_close or candidate is not None and fresh_block
                    and (
                        sustained_braking is not None and sustained_braking > significance
                        or humidity_braking_close and not warming_close
                    )
                ):
                    episode["closing_onset_witnesses"] = tuple(
                        (("door_close", position, Quantity.TEMPERATURE),
                         episode["original_temperature_falls"][position]["braking_form"])
                        for position in closing_positions
                    )
                forms["confirmation_received_at"] = prefix.received_at
                forms["confirmation_originals"] = newest
                candidate_at = (candidate["received_at"] if candidate is not None else max(
                    episode["original_temperature_falls"][position]["recovery_received_at"]
                    for position in closing_positions
                ))
                episode["closing_evidence"] = DoorCloseEvidence(
                    candidate_at, prefix.received_at,
                )
                if candidate is not None:
                    candidate["confirmation_windows"] = tuple(
                        (position.value, dict(prefix.temperatures)[position.value])
                        for position in closing_positions
                    )
                    candidate["confirmation_received_at"] = prefix.received_at
            self._original_ventilation_evidence(
                prefix, channels, now, decision_at, trace, episode, closing=closing,
                close_value=closing_value, stage_confirmation_allowed=stage_confirmation_allowed,
                stage_original_roles=stage_original_roles,
            )
            if closing:
                break
        _decision(trace, "door_open", opening if not self.open else None, {
            "fixed_lowering_witness": _gate(bool(
                trace.get("original_forms", {}).get("lowering_witness_received_at") is not None
            ) if not self.open else None),
            **{name: trace.get("original_forms", {}).get(name, _gate(None)) for name in (
                "resolved_joint_fall", "bound_humidity_onsets",
            )},
            "later_original_confirmation": _gate(opening if not self.open else None),
        }, evaluated=not self.open)
        _decision(trace, "door_close_temperature", closing if self.open else None, {
            "original_combined_recovery": _gate(
                closing_value > significance if closing_value is not None and self.open else None,
                value=closing_value, operator=">", threshold=significance, unit="contrast",
            ),
            "new_temperature_level": _gate(level_close if self.open else None),
            "later_original_confirmation": _gate(closing if self.open else None),
        }, evaluated=self.open)
        return opening, close_candidate, closing, closing_positions

    def _original_rise_witness(self, position, humidity, floor, received_at, branch_floor, time_support):
        """Ein Zeitzeuge muss Originale des gebundenen RH-Astes tragen."""
        if time_support is None:
            return None
        key = ("infusion", position, Quantity.HUMIDITY)
        candidate = self._curve_candidates.get(key)
        if (candidate is None or self._curve_status.get(key, {}).get("status") != "supported"
                or not max(floor, branch_floor) <= candidate.onset_at <= received_at):
            return None
        originals = dict(candidate.original_points)
        branch = tuple(point for point in humidity.current if point[0] > candidate.onset_at)
        if (len(branch) != len(humidity.current)
                or any(originals.get(stamp) != value for stamp, value in branch)):
            return None
        # Ein langes altes Fitfenster kann auch die Originale eines späteren
        # Anstiegs enthalten. Seine ersten rechten RH-Originale müssen bereits
        # diesen Anstieg tragen; ein dazwischenliegendes Plateau gehört nicht
        # nachträglich zu dem neuen Ast.
        count = self.p["curve_original_samples"]
        right = bisect_right(candidate.original_points, candidate.onset_at,
                             key=lambda point: point[0])
        if right < count or len(candidate.original_points) - right < count:
            return None
        onset_samples = candidate.original_points[right - count:right + count]
        onset_window = original_window(
            onset_samples, count=count,
            scale=self.p[f"curve_original_noise_humidity_{position.value}"],
            max_gap_seconds=self.p[f"curve_original_gap_humidity_{position.value}_seconds"],
            at=onset_samples[-1][0], time_support=time_support,
        )
        if (onset_window is None
                or onset_window.v <= self.p["curve_original_significance"] * onset_window.e):
            return None
        return candidate

    def _original_rise_onsets(self, contrasts, floor, received_at, branch_floors, prefix):
        """Frühere Zuordnung nur mit RH-Originalen desselben neuen Astes."""
        onsets = {}
        for name, pair in contrasts.positions:
            position = Position(name)
            candidate = self._original_rise_witness(
                position, pair.humidity, floor, received_at, branch_floors[position],
                self._original_prefix_time_support(prefix, position, Quantity.HUMIDITY),
            )
            if candidate is not None:
                onsets[position] = candidate.onset_at
        return onsets

    def _original_local_rise_intervals(self, windows, kinks, branch_floors):
        """Retain the first supported rise interval inside each bound RH block."""
        result = {}
        significance = self.p["curve_original_significance"]
        kinks = dict(kinks)
        for name, pair in windows:
            kink = kinks.get(name)
            if kink is None:
                continue
            changes = original_window_changes(pair.humidity, tolerance=kink.tolerance)
            for change in changes or ():
                if (change.previous[0] >= branch_floors[Position(name)]
                        and min(change.direction, change.change) > significance):
                    result[Position(name)] = change
                    break
        return result

    @staticmethod
    def _original_infusion_assignment(episode, windows, onsets, intervals=None):
        """Native RH-Trennung und zulässige frühere Astzeugen gemeinsam zuordnen."""
        if episode is None:
            return "new"
        end_at = episode.get("booked_end_at", episode["level_end_at"])
        if episode["phase"] != "ended" and end_at is None:
            return "continuation"
        onsets = tuple(onsets)
        if any(onset <= end_at for onset in onsets):
            return "continuation"
        if intervals and len(intervals) == len(windows):
            first_edges = tuple(intervals.values())
            if any(edge.current[0] <= end_at for edge in first_edges):
                return "continuation"
            if all(edge.previous[0] > end_at for edge in first_edges):
                return "new"
            return "unknown"
        if (len(onsets) == len(windows)
                or all(pair.humidity.current[0][0] > end_at for _, pair in windows)):
            return "new"
        return "unknown"

    def _original_water(self, component, prefix):
        """Positive Zeugen behalten; nur ungepaarte Originale begrenzt halten."""
        result = {}
        limit = self.p["curve_max_points"]
        for position, temperatures, humidities in prefix.points:
            if position not in component["bases"]:
                continue
            component["points"][position] = (temperatures, humidities)
            temperature_support = self._original_prefix_time_support(prefix, position, Quantity.TEMPERATURE)
            base = component["bases"][position]
            end = component["water_end"].get(position, component["support_end"].get(position))
            state = component["water_state"].setdefault(position, {
                "base": None, "maximum": None, "pending": {},
                "seen_through": None, "overflow": False,
            })
            for point in humidities:
                if point[0] < base[0] or end is not None and point[0] > end:
                    continue
                if state["seen_through"] is None or point[0] > state["seen_through"]:
                    state["pending"][point[0]] = point
                    state["seen_through"] = point[0]
            if len(state["pending"]) > limit:
                # Ein verlorener Stützpunkt darf niemals Vollständigkeit oder
                # negative Evidenz vortäuschen. Dies ist kein Quellenwechsel.
                state["overflow"] = True
                state["pending"] = dict(tuple(state["pending"].items())[-limit:])
            for stamp, point in tuple(state["pending"].items()):
                if temperature_support is None:
                    continue
                pair = original_water_pair(
                    point, temperatures,
                    temperature_scale=self.p[f"curve_original_noise_temperature_{position.value}"],
                    humidity_scale=self.p[f"curve_original_noise_humidity_{position.value}"],
                    max_gap_seconds=self.p[f"curve_original_gap_temperature_{position.value}_seconds"],
                    at=prefix.received_at, time_support=temperature_support,
                )
                if pair is None:
                    continue
                if stamp == base[0]:
                    state["base"] = pair
                if state["maximum"] is None or pair.lower_density > state["maximum"].lower_density:
                    state["maximum"] = pair
                state["pending"].pop(stamp)
            base_pair, maximum = state["base"], state["maximum"]
            change = (maximum.lower_density - base_pair.upper_density
                      if base_pair is not None and maximum is not None else None)
            complete = bool(
                end is not None and state["seen_through"] is not None
                and state["seen_through"] >= end and not state["pending"]
                and not state["overflow"] and base_pair is not None
            )
            result[position] = {
                "change": change, "complete": complete,
                "pairs": tuple(pair for pair in (base_pair, maximum) if pair is not None),
                "support_overflow": state["overflow"],
            }
        component["water"] = result
        return result

    def _original_confirm_infusion(self, episode, now, decision_at, floor, onset_allowed, emit):
        """Die einmalige Bestätigung am vorhandenen Callback buchen."""
        component = self._infusion_start_component(episode)
        witnesses = component.get("onset_witnesses", {})
        self._remember_curve_proposal(
            "infusion", episode["positions"], (Quantity.HUMIDITY,), floor=floor,
            context=(self._curve_door_context, floor), observed_at=decision_at,
        )
        proposal_key = ("infusion", episode["positions"], (Quantity.HUMIDITY,))
        proposal = self._curve_proposals.get(proposal_key)
        if proposal is not None and any(
            candidate_key[1] not in witnesses
            or candidate.candidate_id != witnesses[candidate_key[1]].candidate_id
            or candidate.onset_at != witnesses[candidate_key[1]].onset_at
            for candidate_key, candidate in proposal.candidates
        ):
            self._invalidate_curve_proposal(proposal_key, "outside_native_humidity_branch")
        effective = self._curve_beginning(
            "infusion", episode["positions"], (Quantity.HUMIDITY,), now,
            floor=floor, kind=Kind.INFUSION, now=now, decision_at=decision_at,
            onset_allowed=onset_allowed,
        )
        if effective == now:
            beginnings = {
                position: witnesses[position].onset_at if position in witnesses else
                component["onset_intervals"][position].previous[0]
                for position in episode["positions"]
                if position in witnesses or position in component.get("onset_intervals", {})
            }
            if len(beginnings) == len(episode["positions"]):
                beginning = min(beginnings.values())
                if (floor <= beginning <= now and (
                    onset_allowed is None or onset_allowed(Kind.INFUSION, beginning, now)
                )):
                    effective = beginning
        episode["phase"] = "active"
        episode["onset_at"] = effective
        episode["confirmed_at"] = decision_at
        # Nach der Ausgabe besitzt die Detection den Beginn. Bis dahin bleibt
        # der erste Ast auch bei einer Fortsetzung korrigierbarer Originalbeleg.
        episode["original_start_component"] = None
        self._clear_curve_route("infusion", keep_status=True)
        emit(Kind.INFUSION, effective, episode["positions"])

    def _original_infusion_position_evidence(
        self, prefixes, channels, now, decision_at, floor, trace, onset_allowed, allowed, state,
    ):
        """Originalformen im einzigen Aufgussbestand fortschreiben."""
        significance = self.p["curve_original_significance"]
        early_threshold = self.p["curve_original_new_infusion_contrast"]
        for prefix in prefixes:
            episode = state["infusion"]
            component = state["infusion_candidate"]
            positions = (
                episode["positions"] if episode is not None and episode["phase"] != "ended"
                else component["positions"] if component is not None else channels
            )
            expected = tuple(position.value for position in positions)
            context = self._original_bound_context(prefix.context, positions)
            quantities = ((Quantity.HUMIDITY,) if episode is not None
                          and episode["phase"] in ("active", "ended") else
                          (Quantity.TEMPERATURE, Quantity.HUMIDITY))
            if not all(self._position_available(position, quantities, now) for position in positions):
                continue
            if prefix.received_at < floor:
                continue
            if not self._original_prefix_valid(prefix, positions):
                self._original_infusion_background(prefix, now, decision_at, trace, state=state)
                continue
            if component is not None and component["context"] == context and not self._original_contiguous(
                prefix, component["continuity"],
            ):
                self._mark_original_unknown(
                    component, prefix, positions, (Quantity.TEMPERATURE, Quantity.HUMIDITY),
                )
                if episode is not None and episode["confirmed_detection"] is not None:
                    episode["interrupted"].update(positions)
            contrasts = original_contrasts(dict(prefix.windows), expected_positions=expected)
            if contrasts is None:
                self._original_infusion_background(prefix, now, decision_at, trace, state=state)
                continue
            humidity_kinks = self._original_humidity_kinks(prefix, positions)
            kink_gate = self._original_humidity_kink_gate(humidity_kinks, rising=True)
            tolerated_onset = bool(
                contrasts.humidity_rise_onset > significance
                and kink_gate["result"]
            )
            binding = bool(prefix.original and tolerated_onset)
            trace["original_infusion"] = {
                "received_at": prefix.received_at,
                "positions": expected,
                "rise": contrasts.rise,
                "humidity_rise_onset": contrasts.humidity_rise_onset,
                "humidity_rise_change": contrasts.humidity_rise_change,
                "fall": contrasts.fall,
                "confirmation_basis": (
                    episode.get("confirmation_basis") if episode is not None else None
                ),
                "background_slope": contrasts.background_slope,
                "background_residual": contrasts.background_residual,
                "rise_candidate": _gate(
                    contrasts.humidity_rise_onset > significance,
                    value=contrasts.humidity_rise_onset,
                    operator=">", threshold=significance, unit="contrast",
                ),
                "binding_humidity_rise_kink": kink_gate,
                "binding_humidity_rise": _gate(binding),
                "fall_contrast": _gate(
                    contrasts.fall > significance, value=contrasts.fall,
                    operator=">", threshold=significance, unit="contrast",
                ),
            }
            if not positions or not all(position in channels for position in positions):
                self._pause_position_infusion(state)
                continue
            if allowed is not None and not allowed(Kind.INFUSION):
                self._pause_position_infusion(state)
                break
            component = state["infusion_candidate"]
            if component is not None and component["context"] != context:
                state["infusion_candidate"] = component = None
            if component is not None and not self._original_contiguous(
                prefix, component["continuity"],
            ):
                # Zeitabstand oder ausgelagerte Ringstütze sind kein Gegenbeleg.
                # Die gebundenen Originale bleiben beim selben Besitzer. Neue
                # Bestätigungen brauchen weiterhin neue auswertbare RH-Blöcke,
                # den gebundenen Formnachweis und positive Wasserbelege.
                trace["original_infusion"]["support_continuity"] = _gate(
                    None, reason="unobserved_original_interval",
                )
                self._mark_original_unknown(
                    component, prefix, positions, (Quantity.TEMPERATURE, Quantity.HUMIDITY),
                )
                if episode is not None and episode["confirmed_detection"] is not None:
                    episode["interrupted"].update(positions)
            if component is not None and component.get("connection_unknown"):
                if not (binding and self._original_resume_ready(
                    prefix, positions, (Quantity.TEMPERATURE, Quantity.HUMIDITY), component,
                )):
                    self._original_infusion_background(
                        prefix, now, decision_at, trace, contrasts, state=state,
                    )
                    continue
                for position in positions:
                    self._retain_interrupted_evidence(position, "infusion_rise", component, prefix.received_at)
                state["infusion_candidate"] = component = None
                if episode is not None and episode["confirmed_detection"] is None:
                    for position in positions:
                        self._retain_interrupted_evidence(position, "infusion_pending", episode, prefix.received_at)
                    state["infusion"] = episode = None
            if component is not None and prefix.original:
                component["continuity"] = tuple(
                    (name, quantity, window.newest_at)
                    for name, quantity, window in self._original_role_windows(
                        contrasts.positions, (Quantity.TEMPERATURE, Quantity.HUMIDITY),
                    )
                )
            # Fehlende spätere Messungen widerlegen keinen gebundenen Anstieg.
            # Ein belegter Hintergrund oder Gegenbeleg beendet Pending-Evidenz.
            if component is not None and prefix.original:
                end_windows = dict(component["rise_end_windows"])
                for name, pair in contrasts.positions:
                    position = Position(name)
                    if (position not in component["water_end"]
                            and pair.humidity.v <= significance * pair.humidity.e):
                        # Jede Rolle begrenzt ihre RH-Stützmenge am eigenen
                        # ersten nativen Endbeleg. Späte T-Partner zu diesen
                        # RH-Originalen bleiben zulässig; neue Plateau-RH nicht.
                        component["water_end"][position] = pair.humidity.newest_at
                        end_windows[name] = pair
                component["rise_end_windows"] = tuple(end_windows.items())
                if all(pair.humidity.v <= significance * pair.humidity.e
                       for _, pair in contrasts.positions):
                    component["rise_ended"] = True
            if (component is not None and (component["fall_confirmed"] or component["rise_ended"])
                    and tolerated_onset):
                state["infusion_candidate"] = component = None
            if component is None and binding:
                previous_component = (episode.get("original_component")
                                      if episode is not None else None)
                branch_floors = {
                    position: max(
                        floor, self._infusion_segment_start.get(position, floor),
                        previous_component["peaks"].get(position, (floor,))[0]
                        if previous_component is not None else floor,
                    ) for position in positions
                }
                valid_onsets = self._original_rise_onsets(
                    contrasts, floor, prefix.received_at, branch_floors, prefix,
                )
                intervals = self._original_local_rise_intervals(
                    contrasts.positions, humidity_kinks, branch_floors,
                )
                onsets = {position: valid_onsets.get(position,
                          intervals[position].previous[0] if position in intervals
                          else prefix.received_at)
                          for position in positions}
                assignment = self._original_infusion_assignment(
                    episode, contrasts.positions, valid_onsets.values(), intervals,
                )
                component = {
                    "context": context, "positions": tuple(positions),
                    "position_bindings": {position: self._position_source_context(position)
                                          for position in positions},
                    "received_at": prefix.received_at,
                    "newest": self._original_newest(contrasts),
                    "binding_windows": contrasts.positions,
                    "continuity": tuple(
                        (name, quantity, window.newest_at)
                        for name, quantity, window in self._original_role_windows(
                            contrasts.positions, (Quantity.TEMPERATURE, Quantity.HUMIDITY),
                        )
                    ),
                    "branch_floors": branch_floors,
                    "humidity_rise_onset": contrasts.humidity_rise_onset,
                    "humidity_kinks": humidity_kinks,
                    "humidity_kink_originals": tuple(
                        (position.value, humidity)
                        for position, _, humidity in prefix.points if position in positions
                    ),
                    "onsets": onsets, "assignment": assignment,
                    "continuing": assignment == "continuation", "rise_score": None,
                    "strong": False, "qualified": False, "fall_confirmed": False,
                    "rise_ended": False,
                    "fall_ready": False,
                    "bases": {Position(name): min(pair.humidity.current,
                                                  key=lambda point: (point[1], point[0]))
                              for name, pair in contrasts.positions},
                    "peaks": {}, "points": {}, "support_end": {}, "water_end": {}, "water": {},
                    "rise_end_windows": (),
                    "water_state": {},
                    "onset_witnesses": {},
                    "onset_intervals": intervals,
                    "initial_historical_onsets": valid_onsets,
                }
                state["infusion_candidate"] = component
            if component is not None and not component["fall_confirmed"]:
                if not component["strong"] and contrasts.rise <= significance:
                    state["infusion_candidate"] = component = None
                else:
                    for name, pair in contrasts.positions:
                        position = Position(name)
                        candidate = self._original_rise_witness(
                            position, pair.humidity, floor, prefix.received_at,
                            component["branch_floors"][position],
                            self._original_prefix_time_support(prefix, position, Quantity.HUMIDITY),
                        )
                        if candidate is not None:
                            interval = component["onset_intervals"].get(position)
                            if (interval is not None
                                    and position not in component["initial_historical_onsets"]
                                    and not interval.previous[0] <= candidate.onset_at
                                    <= interval.current[0]):
                                # The native interval was already the first
                                # onset proof. A later broad fit cannot move it
                                # into another portion of the humidity curve.
                                continue
                            original = component["onset_witnesses"].get(position)
                            # Der zuerst belegte Beginn gehört zu diesem Anstieg.
                            # Ein späterer Fit mit Spitze und Fall darf ihn nicht
                            # durch einen früheren, anderen Knick ersetzen.
                            # Originalkorrekturen prüfen den gebundenen Zeugen
                            # weiterhin in _correct_original_infusion.
                            if original is None:
                                component["onset_witnesses"][position] = candidate
                                component["onsets"][position] = candidate.onset_at
                    # Ein früherer Zeuge darf Fortsetzung belegen. Vollständig
                    # spätere native RH-Fenster brauchen keinen optionalen
                    # historischen Beginn, um eine neue Episode zu bestätigen.
                    if episode is not None and (episode["phase"] == "ended" or episode.get("booked_end_at") is not None):
                        valid_onsets = tuple(candidate.onset_at
                                             for candidate in component["onset_witnesses"].values())
                        component["assignment"] = self._original_infusion_assignment(
                            episode, component["binding_windows"], valid_onsets,
                            component["onset_intervals"],
                        )
                    component["continuing"] = component["assignment"] == "continuation"
                    threshold = self.p[
                        "curve_original_new_infusion_contrast" if component["assignment"] == "new"
                        else "curve_original_continuation_contrast"
                    ]
                    later_original = bool(
                        prefix.original and self._original_newer(contrasts, component["newest"])
                    )
                    followups = self._original_followups(
                        prefix, self._original_role_windows(
                            component["binding_windows"], (Quantity.HUMIDITY,),
                        ),
                        # Die Originalfolge wird oben an jedem Präfix geprüft.
                        # Ihr eingefrorener Beginn muss bei einem langen
                        # Anstieg nicht dauerhaft im Ringspeicher liegen.
                        continuity=component["continuity"],
                    )
                    fresh_block = bool(followups) and all(
                        form.fresh_current == self.p["curve_original_samples"]
                        for form in followups.values()
                    )
                    # Eine unterbrochene Bindung erreicht diesen Pfad erst
                    # wieder als eigener, vollständig lokal beobachteter Anstieg.
                    # Die beobachtete Aststärke ist noch keine Bestätigung.
                    # Ein kurzer klarer Puls darf seinen starken Anstieg
                    # behalten und später durch den ganzen Fallblock belegen.
                    if later_original:
                        if component["rise_score"] is None or contrasts.rise > component["rise_score"]:
                            component["rise_score"] = contrasts.rise
                            component["rise_windows"] = contrasts.positions
                    component["threshold"] = threshold
                    component["strong"] = (
                        component["rise_score"] is not None and component["rise_score"] > threshold
                    )
                    new_peak = False
                    for name, pair in contrasts.positions:
                        position = Position(name)
                        peak = component["peaks"].get(position)
                        for point in pair.humidity.current:
                            if point[0] < component["bases"][position][0]:
                                continue
                            if peak is None or point[1] > peak[1]:
                                new_peak |= peak is not None
                                peak = point
                        component["peaks"][position] = peak
                    if new_peak and component["support_end"]:
                        # Der erste Fall schließt nur seinen bisherigen Peak.
                        # Ein höherer Peak vor der Terminalentscheidung öffnet
                        # die endliche Stützmenge bis zum nächsten Fall erneut.
                        component["support_end"].clear()
                        component["fall_ready"] = False
                        component.pop("fall_windows", None)
                    fallen = bool(
                        component["strong"] and prefix.original
                        and contrasts.fall > significance
                        and all(pair.humidity.current[0][0] > component["peaks"][Position(name)][0]
                                for name, pair in contrasts.positions)
                    )
                    if fallen:
                        component["fall_ready"] = True
                        component["fall_windows"] = contrasts.positions
                    if fallen and not component["support_end"]:
                        component["support_end"] = {
                            Position(name): pair.humidity.newest_at
                            for name, pair in contrasts.positions
                        }
                    water = self._original_water(component, prefix)
                    positive = bool(water) and all(
                        witness["change"] is not None and witness["change"] > 0
                        for witness in water.values()
                    )
                    terminal_negative = bool(
                        component["support_end"] and len(water) == len(positions)
                        and all(witness["complete"] for witness in water.values())
                        and any(witness["change"] is not None and witness["change"] <= 0
                                for witness in water.values())
                    )
                    trace["original_infusion"].update(
                        binding_received_at=component["received_at"],
                        binding_originals=component["newest"],
                        binding_support=self._original_support(component["binding_windows"]),
                        native_onset_intervals={position.value: {
                            "from": edge.previous[0], "to": edge.current[0],
                            "direction": edge.direction, "change": edge.change,
                        } for position, edge in component["onset_intervals"].items()},
                        continued_support=self._original_support(contrasts.positions),
                        followup=self._original_followup_trace(followups),
                        complete_fresh_original_block=_gate(fresh_block),
                        bound_humidity_rise_onset=component["humidity_rise_onset"],
                        bound_humidity_rise_kink=self._original_humidity_kink_gate(
                            component["humidity_kinks"], rising=True,
                        ),
                        branch_floors={position.value: stamp for position, stamp
                                       in component["branch_floors"].items()},
                        water_support_end={position.value: stamp for position, stamp
                                           in component["water_end"].items()},
                        continued_humidity_rise=_gate(
                            contrasts.rise > significance, value=contrasts.rise,
                            operator=">", threshold=significance, unit="contrast",
                        ),
                        strong=component["strong"], threshold=threshold,
                        early_rise_threshold=early_threshold,
                        assignment=component["assignment"],
                        water_positive=positive, water_complete=all(
                            witness["complete"] for witness in water.values()
                        ), terminal_negative=terminal_negative,
                        water_change={position.value: witness["change"]
                                      for position, witness in water.items()},
                    )
                    _decision(trace, "infusion_rise", component["strong"], {
                        "original_rise_contrast": _gate(
                            component["strong"], value=component["rise_score"],
                            operator=">", threshold=threshold, unit="contrast",
                            reference="bound_observed_original_rise_maximum",
                        ),
                        "current_original_rise": _gate(
                            contrasts.rise > early_threshold, value=contrasts.rise,
                            operator=">", threshold=early_threshold, unit="contrast",
                            reference="current_original_first_confirmation_rise",
                        ),
                        "current_original_rise_change": _gate(
                            contrasts.humidity_rise_change > significance,
                            value=contrasts.humidity_rise_change,
                            operator=">", threshold=significance, unit="contrast",
                        ),
                        "later_original_confirmation": _gate(later_original),
                        "complete_fresh_original_block": _gate(fresh_block),
                        "episode_assignment_known": _gate(component["assignment"] != "unknown"),
                        "positive_available_water": _gate(positive),
                        "water": {position.value: _gate(
                            witness["change"] > 0 if witness["change"] is not None else None,
                            value=witness["change"], operator=">", threshold=0.0, unit="g/m³",
                        ) for position, witness in water.items()},
                    })
                    _decision(trace, "infusion_decay", component["fall_ready"], {
                        "current_original_fall": _gate(
                            contrasts.fall > significance, value=contrasts.fall,
                            operator=">", threshold=significance, unit="contrast",
                        ),
                        "strong_original_rise": _gate(component["strong"]),
                        "current_window_after_peak": _gate(all(
                            pair.humidity.current[0][0] > component["peaks"][Position(name)][0]
                            for name, pair in contrasts.positions
                        )),
                        "bound_fall_retained": _gate(component["fall_ready"]),
                    })
                    if terminal_negative and not component["qualified"]:
                        state["infusion_candidate"] = component = None
                    elif (component["strong"] and positive and not component["qualified"]
                          and component["assignment"] != "unknown"):
                        if episode is None or (
                            not component["continuing"]
                            and episode.get("original_component") is not component
                        ):
                            state["infusion"] = self._new_infusion_episode(
                                positions, min(component["onsets"].values()), now,
                                rise_key=None, water=water, onsets=component["onsets"],
                            )
                            episode = state["infusion"]
                        else:
                            state["infusion_updates"].append((episode, None))
                            episode["phase"] = "active" if episode["confirmed_at"] is not None else "pending"
                            episode["level_end_at"] = None
                            episode["end_basis"] = None
                            episode["decays"].clear()
                            episode["levels"].clear()
                            episode["level_status"].clear()
                            episode["backgrounds"].clear()
                            episode["level_evaluated"].clear()
                            episode["interrupted"].difference_update(positions)
                        component["qualified"] = True
                        episode["original"] = True
                        episode["water"] = water
                        episode["last_rise_at"] = dict(component["onsets"])
                        episode["original_component"] = component
                        if (episode["confirmed_at"] is None
                                and episode["original_start_component"] is None):
                            episode["original_start_component"] = component
                        episode["original_background"] = None
                    if component is not None and component["qualified"]:
                        if component["fall_ready"]:
                            component["fall_confirmed"] = True
                        early_rise = bool(
                            later_original and fresh_block and contrasts.rise > early_threshold
                            and contrasts.humidity_rise_change > significance and positive
                        )
                        pending_confirmation = bool(
                            episode["phase"] == "pending"
                            and episode["original_component"] is component
                            and episode["confirmed_at"] is None
                            and episode["confirmed_detection"] is None
                        )
                        trace["original_infusion"]["early_rise_confirmation"] = _gate(
                            pending_confirmation and early_rise,
                        )
                        if pending_confirmation and (early_rise or component["fall_ready"]):
                            # Der frische Anstiegswechsel darf vor dem Peak
                            # bestätigen. Fallbelege führen die Episode danach
                            # unverändert weiter und bleiben ein später Fallback.
                            episode["confirmation_basis"] = (
                                "early_rise" if early_rise else "after_fall"
                            )
                            if early_rise:
                                component["early_confirmation_windows"] = contrasts.positions
                            episode["confirmation_ready"] = True
                            episode["phase"] = "active"
                            trace["original_infusion"].update(
                                confirmation_basis=episode["confirmation_basis"],
                            )
                            trace["decisions"]["infusion"]["gates"].update(
                                early_rise_confirmation=_gate(early_rise),
                                after_fall_confirmation=_gate(
                                    not early_rise and component["fall_ready"],
                                ),
                            )
                            trace["conditions"]["infusion"] = True
                            trace["decisions"]["infusion"]["result"] = True
            self._original_infusion_background(prefix, now, decision_at, trace, contrasts, state=state)

    def _original_infusion_background(self, prefix, now, decision_at, trace, contrasts=None, *, state):
        """RH-Abschluss fortführen; unbekannte T-Formen widerlegen ihn nicht."""
        episode = state["infusion"]
        components = [state["infusion_candidate"]]
        if episode is not None:
            components.extend((episode.get("original_component"),
                               episode.get("original_start_component")))
        for component in components:
            self._original_infusion_return_evidence(component, prefix)
        if episode is None or episode["phase"] not in ("pending", "active"):
            return
        positions = episode["positions"]
        if (not self._original_prefix_valid(prefix, positions, (Quantity.HUMIDITY,))
                or not all(self._position_available(position, (Quantity.HUMIDITY,), now)
                           for position in positions)):
            return
        if contrasts is None:
            forms = original_humidity_contrasts(
                dict(prefix.humidities),
                expected_positions=tuple(position.value for position in positions),
            )
            if forms is None:
                # Kein neuer Beleg: die bisherigen Originale bleiben erhalten.
                return
            windows = forms.positions
        else:
            forms = contrasts
            windows = tuple((name, pair.humidity) for name, pair in contrasts.positions)
        last = episode.get("original_component")
        peak_source_matches = last is not None and all(
            dict(last["context"])[position][1]
            == dict(prefix.context)[position][1] for position in positions
        )
        recovered_confirmed_episode = (
            episode["confirmed_detection"] is not None
            and all(position in episode["interrupted"] for position in positions)
        )
        # Nach echtem Quellenausfall kann ein vollständig eigener neuer
        # RH-Hintergrund die gebuchte Episode beenden. Er bestätigt keinen
        # alten Peak oder Fall und verschiebt kein Ende in die Datenlücke.
        if not peak_source_matches and not recovered_confirmed_episode:
            _decision(trace, "infusion_background", None, {}, evaluated=False,
                      reason="own_peak_support_unavailable")
            return
        component = state["infusion_candidate"]
        pending = episode["phase"] == "pending"
        fallen = bool(last is not None and (
            last["fall_confirmed"] or pending and last["rise_ended"]
        ) or episode["interrupted"])
        no_rise = component is None or component["fall_confirmed"] or (
            pending and component["rise_ended"]
        ) or bool(not pending and component.get("connection_unknown") and self._original_resume_ready(
            prefix, positions, (Quantity.HUMIDITY,), component,
        ))
        background = bool(
            fallen and no_rise
            and forms.background_slope <= self.p["curve_original_background_slope"]
            and forms.background_residual <= self.p["curve_original_background_residual"]
            and forms.rise <= self.p["curve_original_significance"]
        )
        _decision(trace, "infusion_background", background, {
            "current_original_background": _gate(background),
            "original_background_slope": _gate(
                forms.background_slope <= self.p["curve_original_background_slope"],
                value=forms.background_slope, operator="<=",
                threshold=self.p["curve_original_background_slope"], unit="contrast",
            ),
            "original_background_residual": _gate(
                forms.background_residual <= self.p["curve_original_background_residual"],
                value=forms.background_residual, operator="<=",
                threshold=self.p["curve_original_background_residual"], unit="contrast",
            ),
            "original_rise_absent": _gate(
                forms.rise <= self.p["curve_original_significance"], value=forms.rise,
                operator="<=", threshold=self.p["curve_original_significance"], unit="contrast",
            ),
            "completed_fall_or_interruption": _gate(fallen),
            "no_open_rise_component": _gate(no_rise),
        })
        newest = tuple((name, window.newest_at) for name, window in windows)
        if not background:
            episode["original_background"] = None
        elif episode.get("original_background") is None:
            if prefix.original:
                episode["original_background"] = newest
                episode["original_end_windows"] = windows
        elif prefix.original and all(
            stamp > dict(episode["original_background"])[name] for name, stamp in newest
        ):
            references = episode["original_end_windows"]
            # Der neue aktuelle RH-Block muss selbst vollständig auswertbar
            # sein. Eine vorherige Lücke entwertet den alten Beleg nicht; das
            # Ende wird ausschließlich am jetzt belegten Raster ausgegeben.
            current = dict(windows)
            followups = {(name, Quantity.HUMIDITY): original_followup(current[name], reference)
                         for name, reference in references}
            fresh_block = all(form is not None and form.fresh_current == self.p["curve_original_samples"]
                              for form in followups.values())
            trace.setdefault("original_infusion", {}).update(
                background_followup=self._original_followup_trace(
                    {key: form for key, form in followups.items() if form is not None}
                ), complete_fresh_background_block=_gate(fresh_block),
            )
            if fresh_block:
                if pending:
                    state["infusion"] = state["infusion_candidate"] = None
                    return
                episode["phase"] = "ended"
                episode["level_end_at"] = now
                episode["end_basis"] = "original_background"
                episode["evidence_end"] = InfusionEnd(
                    now, decision_at, now, episode["end_basis"], "evidence_grid",
                )
                state["infusion_updates"].append((episode, episode["evidence_end"]))
                episode["original_background"] = None
                episode["original_end_windows"] = windows
                self._infusion_segment_start.update({
                    Position(name): window.current[0][0] for name, window in windows
                })

    def _clear_curve_route(self, route, *, keep_status=False, reason=None, position=None):
        if reason is not None:
            self._invalidate_curve_proposals(reason, route=route, position=position)
        mappings = (
            self._curve_candidates, self._curve_evaluated, self._curve_proposals,
        )
        if not keep_status:
            mappings += (self._curve_status,)
            if reason is None:
                mappings += (self._curve_proposal_status,)
        for mapping in mappings:
            for key in tuple(mapping):
                if key[0] == route and (
                    position is None or key[1] == position
                    or isinstance(key[1], tuple) and position in key[1]
                ):
                    mapping.pop(key, None)

    def _curve_points(self, position, quantity, now, *, points_not_before=None):
        window_start = now - timedelta(seconds=self.p["curve_window_seconds"])
        samples = tuple(self._curve_series[position, quantity]["points"])
        support = self._original_time_support(position, quantity, samples, now)
        if support is None or self._value(position, quantity, now) is None:
            return ()
        window_start = max(window_start, support.start_at)
        return tuple(
            point for point in samples if window_start <= point[0] <= now
            and (points_not_before is None or point[0] >= points_not_before)
        )

    @staticmethod
    def _curve_onset_available(candidate, points):
        if isinstance(candidate.features, CurveFormFeatures):
            # Ein stationärer Polynomturn muss kein Originalzeitstempel sein.
            # Die feste Referenz benötigt weiterhin ihre echte erste Stütze.
            reference = candidate.features.reference
            return reference is not None and any(
                point[0] == reference.start_at for point in points
            ) and points[0][0] <= candidate.onset_at <= points[-1][0]
        return any(point[0] == candidate.onset_at for point in points)

    def _expire_held_curve_proposals(self, now):
        """Historisch gebundene, noch ungezählte Belege behalten den Ablauf."""
        episode = self._infusion_episode
        pending_key = (
            episode["rise_key"]
            if episode is not None and episode["phase"] == "pending" else None
        )
        for key, proposal in tuple(self._curve_proposals.items()):
            if proposal.evidence_bound_at is None and key != pending_key:
                continue
            if any(
                not self._curve_onset_available(candidate, self._curve_points(
                    candidate_key[1], candidate_key[2], now,
                    points_not_before=candidate.context[3],
                )) for candidate_key, candidate in proposal.candidates
            ):
                # Nur der aktuelle Arbeitsfit endet. Native Originalbelege
                # gehören weiterhin ihrem Ereignis und werden dort fortgeführt.
                self._invalidate_curve_proposal(key, "candidate_expired")

    def _curve_candidate(
        self, route, position, quantity, now, observed_at, floor, context,
        *, points_not_before=None,
    ):
        """Einen eingefrorenen Beginn nur mit neuen Originalproben nachprüfen."""
        key = (route, position, quantity)
        series = self._curve_series[position, quantity]
        dependencies = (
            (Quantity.TEMPERATURE, Quantity.HUMIDITY)
            if route.startswith("infusion") else (quantity,)
        )
        epochs = tuple(
            self._curve_series[position, q]["epoch"]
            for q in dependencies
        )
        floor = max(
            floor,
            *(self._curve_series[position, q]["started_at"] or floor
              for q in dependencies),
        )
        context = (context, epochs, floor, points_not_before)
        evaluation = (series["version"], context)
        previous_evaluation = self._curve_evaluated.get(key)
        candidate = self._curve_candidates.get(key)
        if candidate is not None and candidate.context != context:
            self._curve_candidates.pop(key, None)
            candidate = None
        points = self._curve_points(
            position, quantity, now, points_not_before=points_not_before,
        )
        if candidate is not None and not self._curve_onset_available(candidate, points):
            self._invalidate_curve_identity(candidate, "candidate_expired")
            self._curve_candidates.pop(key, None)
            self._curve_status[key] = {
                **self._curve_status.get(key, {}), "status": "expired",
            }
            candidate = None
            self._curve_evaluated[key] = evaluation
            if previous_evaluation is not None and previous_evaluation[0] == series["version"]:
                return
        if previous_evaluation == evaluation:
            return
        self._curve_evaluated[key] = evaluation
        features = self._fit_candidate_form(
            route, points, floor, quantity, candidate=candidate,
            fit_context=(
                position, quantity, series["source"], series["time_basis"],
                epochs, series["version"],
            ),
        )
        status = {
            "status": "insufficient",
            "source": series["source"],
            "time_basis": series["time_basis"],
            "candidate_id": candidate.candidate_id if candidate is not None else None,
            "first_observed_at": (
                candidate.first_observed_at if candidate is not None else None
            ),
            "onset_at": candidate.onset_at if candidate is not None else None,
            "newest_at": points[-1][0] if points else None,
            "first_newest_at": (
                candidate.first_newest_at if candidate is not None else None
            ),
            "fresh_newest_at": (
                candidate.fresh_newest_at if candidate is not None else None
            ),
            "reason": "owner_supplied_no_evaluable_form",
        }
        if features is not None:
            gates = self._curve_support_gates(route, features, floor, quantity)
            supported = all(gate["result"] for gate in gates.values())
            direction_missing = (
                features.evidence == "unresolved"
                or features.evidence == "supports" and features.after_trend is None
                and (route != "door_close" or getattr(features, "hinge_order", 1) == 2)
            )
            feature_onset = (
                features.turn_at if isinstance(features, CurveFormFeatures) else features.onset_at
            )
            status.update(
                status=("supported" if supported else
                        "insufficient" if direction_missing else "rejected"),
                onset_at=feature_onset,
                newest_at=features.newest_at,
                **self._curve_feature_diagnostic(features),
                gates=gates,
                reason=(features.reason if isinstance(features, CurveFormFeatures) else
                        None if supported else "model_unresolved"
                        if features.evidence == "unresolved" else "after_direction_unresolved"
                        if direction_missing else "shape_or_context_counterevidence"),
                after_trend=(
                    {
                        "direction": features.after_trend.direction,
                        "slope": features.after_trend.slope,
                        "noise_scale": features.after_trend.noise_scale,
                        "score_gain": features.after_trend.score_gain,
                        "exact_constant": features.after_trend.exact_constant,
                        "exact_linear": features.after_trend.exact_linear,
                    } if features.after_trend is not None else None
                ),
            )
            if supported:
                first_observed_at = (
                    candidate.first_observed_at if candidate is not None else observed_at
                )
                candidate_id = (
                    candidate.candidate_id if candidate is not None else
                    f"{route}:{position.value}:{quantity.value}:{series['source']}:"
                    f"{epochs}:{feature_onset.isoformat()}:{first_observed_at.isoformat()}"
                )
                first_newest_at = (
                    candidate.first_newest_at if candidate is not None
                    else features.newest_at
                )
                fresh_newest_at = (
                    candidate.fresh_newest_at if candidate is not None else None
                )
                fresh_observed_at = (
                    candidate.fresh_observed_at if candidate is not None else None
                )
                if (
                    features.newest_at > first_newest_at
                    and series["last_original_version"] == series["version"]
                ):
                    fresh_newest_at = features.newest_at
                    fresh_observed_at = now
                candidate = _CurveCandidate(
                    candidate_id, feature_onset, first_observed_at,
                    context, features, points, first_newest_at, fresh_newest_at,
                    fresh_observed_at,
                )
                self._curve_candidates[key] = candidate
                status["first_observed_at"] = candidate.first_observed_at
                status["candidate_id"] = candidate.candidate_id
                status["first_newest_at"] = candidate.first_newest_at
                status["fresh_newest_at"] = candidate.fresh_newest_at
                status["fresh_observed_at"] = candidate.fresh_observed_at
            elif not direction_missing:
                self._invalidate_curve_identity(candidate, "shape_counterevidence")
                self._curve_candidates.pop(key, None)
        self._curve_status[key] = status

    def _fit_candidate_form(
        self, route, points, floor, quantity, *, candidate=None, fit_context=None,
    ):
        """Die erste Form wählen und spätere Originale an genau ihr nachprüfen."""
        if candidate is not None and isinstance(candidate.features, CurveFormFeatures):
            return curve_form(
                points, policy=self.curve_policy, form="peak",
                reference=candidate.features.reference,
                current_not_before=candidate.features.reference.current_not_before,
            )
        direction = -1 if route in (
            "door_open", "door_open_heating", "infusion_decay",
        ) else 1
        onset_at = candidate.onset_at if candidate is not None else None

        def fit_change(hinge_order):
            # Nur mathematisch gleiche Aufträge teilen den Fit. Die Route
            # besitzt ihre Alternativen weiterhin selbst; Quellen und Version
            # verhindern eine Übernahme aus einem anderen Originalkontext.
            fit_key = (
                "curve_change", fit_context, self.curve_policy, points,
                onset_at, direction, floor, hinge_order,
            )
            if fit_context is None or fit_key not in self._curve_fit_cache:
                options = {"hinge_order": hinge_order} if hinge_order != 1 else {}
                fitted = curve_change(
                    points, policy=self.curve_policy, onset_at=onset_at,
                    direction=direction, onset_not_before=floor, **options,
                )
                if fit_context is None:
                    return fitted
                self._curve_fit_cache[fit_key] = fitted
            return self._curve_fit_cache[fit_key]

        features = fit_change(candidate.features.hinge_order if candidate is not None else 1)
        if (
            route == "door_close" and candidate is None
            and self.open and self.door_episode is not None
            and self.door_episode["opening_humidity"]
            and not self._curve_supports(route, features, floor, quantity)
        ):
            braking = fit_change(2)
            if braking is not None and self._curve_supports(route, braking, floor, quantity):
                return braking
        if route == "infusion_decay" and candidate is None and not self._curve_supports(
            route, features, floor, quantity,
        ):
            # Ein glatter Peak ist dieselbe erwartete Anstieg-Fall-Form. Der
            # öffentliche Owner prüft Gegenformen und bindet seine Geometrie;
            # erst neue Originale bestätigen diese bestehende Aufgussepisode.
            peak = curve_form(
                points, policy=self.curve_policy, form="peak", current_not_before=floor,
            )
            if peak is not None:
                return peak
        return features

    @staticmethod
    def _curve_feature_diagnostic(features):
        result = {
            "before_count": features.before_count, "after_count": features.after_count,
            "noise_scale": features.noise_scale, "score_gain": features.score_gain,
        }
        if isinstance(features, CurveFormFeatures):
            def score_gate(gain, resolution, reference):
                return _gate(
                    gain > resolution if gain is not None and resolution is not None else None,
                    value=gain, operator=">", threshold=resolution,
                    unit="score", reference=reference,
                )

            def evidence_gate(evidence, reference):
                return _gate(
                    evidence == "supports" if evidence not in ("unresolved", "not_assessed")
                    else None,
                    value=evidence, operator="==", threshold="supports", reference=reference,
                    reason=evidence if evidence != "supports" else None,
                )

            result.update(
                form=features.form, positive_model=features.positive_model,
                counter_model=features.counter_model, positive_score=features.positive_score,
                counter_score=features.counter_score, turn_at=features.turn_at,
                reference_model=features.reference.model if features.reference is not None else None,
                reference_start_at=features.reference.start_at if features.reference is not None else None,
                reference_initial_newest_at=(
                    features.reference.initial_newest_at if features.reference is not None else None
                ),
                fresh_original_count=features.fresh_original_count,
                owner_reason=features.reason,
                owner_gates={
                    # Scorevergleich und Ownerbefund bleiben getrennt: eine
                    # unklare Geometrie, verdrängte Referenz oder Gegenflanke
                    # kann trotz positivem Scoreabstand die Form entwerten.
                    "form_competition": score_gate(
                        features.score_gain, features.score_resolution,
                        "counterform_minus_positive_form",
                    ),
                    "form_evidence": evidence_gate(
                        features.form_evidence, "curve_form.form_evidence",
                    ),
                    "fixed_reference": score_gate(
                        features.reference_score_gain, features.reference_score_resolution,
                        "counterform_minus_frozen_geometry",
                    ),
                    "fixed_reference_evidence": evidence_gate(
                        features.reference_evidence, "curve_form.reference_evidence",
                    ),
                    "one_point_removal": score_gate(
                        features.influence_score_gain, features.influence_score_resolution,
                        "counterform_minus_frozen_geometry_after_removal",
                    ),
                    "one_point_removal_evidence": evidence_gate(
                        features.influence_evidence, "curve_form.influence_evidence",
                    ),
                    "reference_not_displaced": _gate(
                        features.replacement_gain <= features.replacement_cost + (
                            features.score_resolution or 0.0
                        ) if features.replacement_gain is not None else None,
                        value=features.replacement_gain, operator="<=",
                        threshold=(features.replacement_cost + (features.score_resolution or 0.0)
                                   if features.replacement_cost is not None else None),
                        unit="score", reference="replacement_gain_vs_geometry_cost",
                        reason="no_displaced_positive_geometry"
                        if features.replacement_gain is None else None,
                    ),
                },
            )
        else:
            result.update(
                slope_before=features.slope_before, slope_after=features.slope_after,
                slope_change=features.slope_change, signed_strength=features.signed_strength,
                smooth_score=features.smooth_score, change_score=features.change_score,
                smooth_model=features.smooth_model, hinge_order=features.hinge_order,
                curvature_change=features.curvature_change,
            )
        return result

    @classmethod
    def _curve_supports(cls, route, features, floor, quantity=None):
        if features is None:
            return False
        return all(gate["result"] for gate in cls._curve_support_gates(
            route, features, floor, quantity,
        ).values())

    @staticmethod
    def _curve_support_gates(route, features, floor, quantity=None):
        """Dieselben Formbedingungen für Erstbefund und Originalkorrektur."""
        if isinstance(features, CurveFormFeatures):
            return {
                "peak_owner_evidence": _gate(
                    features.evidence == "supports"
                    if features.evidence != "unresolved" else None, reason=features.reason,
                ),
                "peak_onset_in_context": _gate(
                    features.turn_at >= floor if features.turn_at is not None else None,
                    value=features.turn_at, operator=">=", threshold=floor, unit="s",
                    reference="bound_infusion_context_floor",
                ),
            }
        after_direction = (
            features.after_trend.direction if features.after_trend is not None else None
        )
        decreasing = route in ("door_open", "door_open_heating", "infusion_decay")
        curvature_braking = route == "door_close" and features.hinge_order == 2
        change = features.curvature_change if curvature_braking else features.slope_change
        gates = {
            "model_evidence": _gate(
                features.evidence == "supports"
                if features.evidence != "unresolved" else None, value=features.evidence,
                operator="==", threshold="supports", reference="curve_change.evidence",
                reason=None if features.evidence == "supports" else "model_unresolved"
                if features.evidence == "unresolved" else "model_counterevidence",
            ),
            "change_direction": _gate(
                change < 0 if decreasing else change > 0,
                value=change, operator="<" if decreasing else ">",
                threshold=0.0, unit=(
                    "°C/s²" if quantity == Quantity.TEMPERATURE else "%RH/s²"
                ) if curvature_braking else (
                    "°C/s" if quantity == Quantity.TEMPERATURE else "%RH/s"
                ),
                reference="curvature_change" if curvature_braking else "slope_change",
            ),
            "onset_in_context": _gate(
                features.onset_at >= floor, value=features.onset_at, operator=">=",
                threshold=floor, unit="s", reference="current_context_floor",
            ),
        }
        if route == "infusion_decay" or (
            route == "door_close" and quantity != Quantity.HUMIDITY
        ) or curvature_braking:
            rising_before = route == "infusion_decay"
            gates["before_direction"] = _gate(
                features.slope_before > 0 if rising_before else features.slope_before < 0,
                value=features.slope_before, operator=">" if rising_before else "<",
                threshold=0.0, unit="°C/s" if quantity == Quantity.TEMPERATURE else "%RH/s",
                reference="slope_before",
            )
        if route != "door_close":
            gates["after_direction"] = _gate(
                after_direction == (-1 if decreasing else 1)
                if after_direction is not None else None,
                value=after_direction, operator="==", threshold=-1 if decreasing else 1,
                reference="original_after_trend.direction",
                reason="original_after_trend_unresolved" if after_direction is None else None,
            )
        elif curvature_braking:
            gates["original_after_trend"] = _gate(
                after_direction is not None,
                value=after_direction, operator="in", threshold=(-1, 0, 1),
                reference="resolved_original_after_trend.direction",
                reason="original_after_trend_unresolved" if after_direction is None else None,
            )
        return gates

    def _curve_coherence_window(self, route):
        return (
            self.p["infusion_window_seconds"] if route.startswith("infusion")
            else max(self.p["door_window_seconds"], self.p["door_humidity_seconds"])
        )

    def _temperature_recovery(self, position, candidate, now):
        """Denselben aktuellen Originaltrend für beide T-Erholungsgates verwenden."""
        return self._curve_recovery(position, Quantity.TEMPERATURE, candidate, now)

    def _curve_recovery(self, position, quantity, candidate, now):
        """Den aktuellen Originaltrend am gebundenen T-Beginn prüfen.

        Der alte Knick bleibt am festen Beginn gebunden. Nur ein neuer Original-
        oder Korrekturstand veranlasst die Auswertung des jüngsten festen Fensters;
        Rasterzeit allein verschiebt keine Evidenz. Eine Korrektur darf einen
        gerichteten Nachweis verwerfen, aber keinen neuen Originalbeleg erwerben.
        Verlässt eine tragende Probe das aktuelle Fenster, verfällt die Freigabe
        ohne Neuberechnung; auch eine Korrektur kann sie dann nicht erneuern.
        """
        series = self._curve_series[position, quantity]
        key = (position, quantity)
        evaluation = (candidate.candidate_id, series["version"])
        record = self._curve_recovery_evidence.get(key, {})
        previous = record if "evaluation" in record else None
        start = max(candidate.onset_at, now - timedelta(
            seconds=self.p[
                "door_window_seconds" if quantity == Quantity.TEMPERATURE
                else "door_humidity_seconds"
            ]
        ))

        def within_window(evidence):
            return (
                evidence is not None and evidence["start_at"] is not None
                and evidence["newest_at"] is not None
                and start <= evidence["start_at"] <= evidence["newest_at"] <= now
            )

        if previous is None or previous["evaluation"] != evaluation:
            points = tuple(
                point for point in series["points"] if start <= point[0] <= now
            )
            trend = curve_trend(points, policy=self.curve_policy)
            same_candidate = (
                previous is not None
                and previous["evaluation"][0] == candidate.candidate_id
            )
            directed = trend is not None and trend.direction == 1
            new_original = series["last_original_version"] == series["version"]
            original_supported = directed and (
                new_original
                or (same_candidate and previous["original_supported"]
                    and within_window(previous))
            )
            previous = {
                **record,
                "evaluation": evaluation, "trend": trend,
                "original_supported": original_supported,
                "start_at": points[0][0] if points else None,
                "newest_at": points[-1][0] if points else None,
                "fresh_observed_at": (
                    now if directed and new_original else
                    previous["fresh_observed_at"] if same_candidate else None
                ),
            }
            self._curve_recovery_evidence[key] = previous
        trend = previous["trend"]
        current_supported = previous["original_supported"] and within_window(previous)
        key = ("door_close", position, quantity)
        self._curve_status[key] = {
            **self._curve_status.get(key, {}),
            "recovery_trend": {
                "start_at": previous["start_at"], "newest_at": previous["newest_at"],
                "direction": trend.direction if trend is not None else None,
                "slope": trend.slope if trend is not None else None,
                "noise_scale": trend.noise_scale if trend is not None else None,
                "level_score": trend.level_score if trend is not None else None,
                "trend_score": trend.trend_score if trend is not None else None,
                "score_gain": trend.score_gain if trend is not None else None,
                "exact_linear": trend.exact_linear if trend is not None else None,
                "exact_constant": trend.exact_constant if trend is not None else None,
                "original_supported": current_supported,
                "within_window": within_window(previous),
                "fresh_observed_at": previous["fresh_observed_at"],
                "gates": {
                    "rising_original_trend": _gate(
                        trend.direction == 1 if trend is not None else None,
                        value=trend.direction if trend is not None else None,
                        operator="==", threshold=1, reference="curve_trend.direction",
                        reason="original_trend_unresolved" if trend is None else None,
                    ),
                    "original_support": _gate(previous["original_supported"]),
                    "start_in_current_window": _gate(
                        previous["start_at"] >= start if previous["start_at"] is not None else None,
                        value=previous["start_at"], operator=">=", threshold=start,
                        unit="s", reference="recovery_window_start",
                    ),
                },
            },
        }
        return current_supported

    def _smooth_close_confirmation(self, positions, episode, *, floor, now):
        """Die volle Fall-Tal-Erholung binden und ihre Originalerholung fortführen.

        Der Formbeleg führt weder einen Türzustand noch einen Ereignisbeginn.
        Zwei vollständige Originalbefunde binden die beobachteten Flanken.
        Danach bestätigt nur ein aktueller, überlappender Originaltrend die
        Fortsetzung. Das wandernde Analysefenster widerlegt keine alte Fallflanke.
        """
        supports = []
        confirmations = []
        fresh_original = False
        window_start = now - timedelta(seconds=self.p["curve_window_seconds"])
        recovery_start = max(floor, now - timedelta(
            seconds=self.p["door_window_seconds"],
        ))

        def within_window(features):
            return (
                features is not None
                and features.evidence == "supports"
                and max(floor, window_start) <= features.decline_start_at
                <= features.decline_newest_at <= now
                and recovery_start <= features.recovery_start_at
                <= features.recovery_newest_at <= now
            )

        def current_within_window(current):
            return (
                current is not None and current["start_at"] is not None
                and current["newest_at"] is not None
                and recovery_start <= current["start_at"] <= current["newest_at"] <= now
            )

        for position in positions:
            series = self._curve_series[position, Quantity.TEMPERATURE]
            record = self._curve_recovery_evidence.setdefault(
                (position, Quantity.TEMPERATURE), {},
            )
            previous = record.get("smooth_form")
            context = (
                self._curve_door_context, episode["started_at"], floor,
                series["source"], series["time_basis"], series["epoch"],
            )
            evaluation = (series["version"], context)
            same_source = (
                series["source"] == episode["sources"][position][0]
                and series["epoch"] == episode["epochs"][position][0]
            )
            original_received = series["last_original_received_at"]
            new_original = (
                series["last_original_version"] == series["version"]
                and original_received is not None
                and now - timedelta(seconds=self.grid_seconds)
                < original_received <= now
            )
            if previous is not None and (
                previous["evaluation"][1] != context or not same_source
            ):
                previous = None
            invalid_reason = None
            if previous is not None and previous.get("bound_at") is not None:
                features = previous["features"]
                points = tuple(
                    point for point in series["points"]
                    if max(recovery_start, features.recovery_start_at) <= point[0] <= now
                )
                frontier = previous["last_positive_at"]
                if not any(stamp == frontier for stamp, _ in points):
                    previous = None
                    invalid_reason = "recovery_frontier_expired"
                elif previous["evaluation"] != evaluation:
                    trend = curve_trend(points, policy=self.curve_policy)
                    if trend is not None and trend.direction == -1:
                        previous = None
                        invalid_reason = "recovery_counterevidence"
                    else:
                        old_current = previous["current_recovery"]
                        directed = trend is not None and trend.direction == 1
                        fresh = directed and new_original and points[-1][0] > frontier
                        current = {
                            "points": points, "trend": trend,
                            "start_at": points[0][0] if points else None,
                            "newest_at": points[-1][0] if points else None,
                            "original_supported": directed and (
                                fresh or old_current["original_supported"]
                                and current_within_window(old_current)
                            ),
                            "fresh_observed_at": (
                                now if fresh else old_current["fresh_observed_at"]
                            ),
                        }
                        previous = {
                            **previous, "evaluation": evaluation,
                            "current_recovery": current,
                            "last_positive_at": points[-1][0] if fresh else frontier,
                        }
            if (
                previous is None or previous.get("bound_at") is None
                and previous["evaluation"] != evaluation
            ):
                points = self._curve_points(
                    position, Quantity.TEMPERATURE, now,
                    points_not_before=max(floor, series["started_at"] or floor),
                )
                features = curve_recovery(
                    points, policy=self.curve_policy,
                    current_not_before=recovery_start,
                ) if same_source and new_original and invalid_reason != (
                    "recovery_counterevidence"
                ) else None
                supported = features is not None and features.evidence == "supports"
                old_features = previous["features"] if previous is not None else None
                same_form = (
                    supported and previous is not None
                    and previous["evaluation"][1] == context
                    and previous["original_supported"] and old_features is not None
                    and features.decline_start_at <= old_features.decline_newest_at
                    <= features.decline_newest_at
                    and features.recovery_start_at <= old_features.recovery_newest_at
                    <= features.recovery_newest_at
                )
                original_supported = supported and (
                    new_original or same_form and within_window(old_features)
                )
                first_newest_at = (
                    previous["first_newest_at"] if same_form else
                    features.newest_at if original_supported else None
                )
                fresh_newest_at = previous["fresh_newest_at"] if same_form else None
                fresh_observed_at = previous["fresh_observed_at"] if same_form else None
                if (
                    original_supported and new_original
                    and features.newest_at > first_newest_at
                ):
                    fresh_newest_at = features.newest_at
                    fresh_observed_at = now
                if (
                    features is None and previous is not None
                    and previous["original_supported"] and old_features is not None
                    and recovery_start <= old_features.recovery_newest_at <= now
                    and any(stamp == old_features.recovery_newest_at for stamp, _ in points)
                ):
                    # Ein unaufgelöster Zwischenstand ist kein zweiter Formbeleg
                    # und widerlegt die erste belegte Originalform nicht.
                    previous = {
                        **previous, "evaluation": evaluation,
                        "current_form_supported": False,
                    }
                else:
                    previous = {
                        "evaluation": evaluation, "features": features,
                        "original_supported": original_supported,
                        "current_form_supported": original_supported,
                        "original_points": points if original_supported else (),
                        "first_newest_at": first_newest_at,
                        "fresh_newest_at": fresh_newest_at,
                        "fresh_observed_at": fresh_observed_at,
                        "bound_at": None, "current_recovery": None,
                        "last_positive_at": None, "invalid_reason": invalid_reason,
                    }
                    if (
                        original_supported and fresh_newest_at is not None
                        and fresh_newest_at > first_newest_at
                    ):
                        recovery_points = tuple(
                            point for point in points
                            if features.recovery_start_at <= point[0]
                            <= features.recovery_newest_at
                        )
                        previous.update(
                            bound_at=now,
                            last_positive_at=features.recovery_newest_at,
                            current_recovery={
                                "points": recovery_points, "trend": features.after_trend,
                                "start_at": features.recovery_start_at,
                                "newest_at": features.recovery_newest_at,
                                "original_supported": True, "fresh_observed_at": now,
                            },
                        )
            record["smooth_form"] = previous
            features = previous["features"]
            current = previous.get("current_recovery")
            bound = previous.get("bound_at") is not None
            current_window = (
                current_within_window(current) if bound else within_window(features)
            )
            supported = current_window and (
                current["original_supported"] if bound else
                previous["original_supported"] and previous["current_form_supported"]
            )
            confirmed = bound and supported
            supports.append(supported)
            confirmations.append(confirmed)
            fresh_original |= confirmed and current["fresh_observed_at"] == now
            key = ("door_close", position, Quantity.TEMPERATURE)
            self._curve_status[key] = {
                **self._curve_status.get(key, {}),
                "smooth_recovery": {
                    "supported": supported, "within_window": current_window,
                    "bound_at": previous.get("bound_at"),
                    "historical_within_window": within_window(features),
                    "invalid_reason": previous.get("invalid_reason"),
                    "source": series["source"], "time_basis": series["time_basis"],
                    "source_epochs": (series["epoch"],),
                    "start_at": features.start_at if features is not None else None,
                    "newest_at": features.newest_at if features is not None else None,
                    "valley_at": features.valley_at if features is not None else None,
                    "decline_start_at": (
                        features.decline_start_at if features is not None else None
                    ),
                    "decline_newest_at": (
                        features.decline_newest_at if features is not None else None
                    ),
                    "recovery_start_at": (
                        features.recovery_start_at if features is not None else None
                    ),
                    "recovery_newest_at": (
                        features.recovery_newest_at if features is not None else None
                    ),
                    "first_newest_at": previous["first_newest_at"],
                    "fresh_newest_at": previous["fresh_newest_at"],
                    "fresh_observed_at": previous["fresh_observed_at"],
                    "original_count": len(previous["original_points"]),
                    "model": features.model if features is not None else None,
                    "noise_scale": features.noise_scale if features is not None else None,
                    "recovery_score": features.recovery_score if features is not None else None,
                    "counter_score": features.other_score if features is not None else None,
                    "score_gain": features.score_gain if features is not None else None,
                    "gates": {
                        "same_opening_source": _gate(same_source),
                        "form_supported": _gate(
                            features.evidence == "supports" if features is not None else None,
                            reason="fall_valley_recovery_unresolved" if features is None else None,
                        ),
                        "current_window": _gate(current_window),
                        "bound_original_form": _gate(bound),
                        "current_original_support": _gate(supported),
                        "new_original_at_this_check": _gate(
                            confirmed and current["fresh_observed_at"] == now
                            if current is not None else None,
                            reason="form_not_bound" if current is None else None,
                        ),
                    },
                    "current_recovery": ({
                        "start_at": current["start_at"], "newest_at": current["newest_at"],
                        "direction": (
                            current["trend"].direction if current["trend"] is not None else None
                        ),
                        "original_supported": supported, "within_window": current_window,
                        "fresh_observed_at": current["fresh_observed_at"],
                        "last_positive_at": previous["last_positive_at"],
                    } if current is not None else None),
                },
            }
        return (
            bool(supports) and all(supports),
            bool(confirmations) and all(confirmations) and fresh_original,
        )

    def _invalidate_curve_proposal(self, key, reason):
        self._curve_proposals.pop(key, None)
        if key in self._curve_proposal_status:
            self._curve_proposal_status[key] = {
                **self._curve_proposal_status[key], "status": "invalid", "reason": reason,
            }

    def _invalidate_curve_identity(self, candidate, reason):
        """Ein abgelaufener oder widerlegter Zeuge blockiert keinen Ersatz."""
        if candidate is None:
            return
        for key, proposal in tuple(self._curve_proposals.items()):
            if (
                reason == "shape_counterevidence"
                and proposal.evidence_bound_at is not None
                and key[0] == "door_close" and key[2] == (Quantity.TEMPERATURE,)
            ):
                # Ein später verschobenes Arbeitsfenster widerlegt nicht die
                # bereits frisch bestätigten historischen Originalstützen.
                continue
            if any(
                witness.candidate_id == candidate.candidate_id
                for _, witness in proposal.candidates
            ):
                self._invalidate_curve_proposal(key, reason)

    def _invalidate_curve_proposals(
        self, reason, *, route=None, position=None, quantities=None,
    ):
        for key in tuple(self._curve_proposals):
            if (
                (route is None or key[0] == route)
                and (position is None or position in key[1])
                and (quantities is None or key[2] == quantities)
            ):
                self._invalidate_curve_proposal(key, reason)

    def _curve_proposal_diagnostic(self, key, proposal):
        _, positions, quantities = key
        candidate_ids = tuple(candidate.candidate_id for _, candidate in proposal.candidates)
        identity = "|".join((proposal.first_observed_at.isoformat(), *candidate_ids))
        return {
            "proposal_id": f"{key[0]}:{sha256(identity.encode()).hexdigest()}",
            "status": (
                "ambiguous" if proposal.ambiguous else
                "bound" if proposal.evidence_bound_at is not None else "available"
            ),
            "owner_context": proposal.context[0],
            "positions": tuple(position.value for position in positions),
            "quantities": tuple(quantity.value for quantity in quantities),
            "first_observed_at": proposal.first_observed_at,
            "evidence_bound_at": proposal.evidence_bound_at,
            "onset_at": min(candidate.onset_at for _, candidate in proposal.candidates),
            "candidate_ids": candidate_ids,
            "onsets": tuple(candidate.onset_at for _, candidate in proposal.candidates),
            "witnesses": tuple({
                "position": candidate_key[1].value,
                "quantity": candidate_key[2].value,
                "candidate_id": candidate.candidate_id,
                "source": self._curve_series[candidate_key[1], candidate_key[2]]["source"],
                "time_basis": self._curve_series[candidate_key[1], candidate_key[2]]["time_basis"],
                "source_epochs": candidate.context[1],
                "onset_at": candidate.onset_at,
                "first_observed_at": candidate.first_observed_at,
                "first_at": candidate.original_points[0][0],
                "newest_at": candidate.features.newest_at,
                "first_newest_at": candidate.first_newest_at,
                "fresh_newest_at": candidate.fresh_newest_at,
                "fresh_observed_at": candidate.fresh_observed_at,
                "point_count": len(candidate.original_points),
            } for candidate_key, candidate in proposal.candidates),
        }

    def _record_curve_proposal_check(self, key, **check):
        """Aktuelle Gates zeigen; nur echte Pakete besitzen eine Historie."""
        _, positions, quantities = key
        check = {
            "positions": tuple(position.value for position in positions),
            "quantities": tuple(quantity.value for quantity in quantities),
            **check,
        }
        self._curve_proposal_checks[key] = {
            **self._curve_proposal_checks.get(key, {}), **check,
        }
        history = self._curve_proposal_status.get(key)
        if history is not None and history.get("proposal_id") is not None:
            self._curve_proposal_status[key] = {**history, **check}

    def _remember_curve_proposal(
        self, route, positions, quantities, *, floor, context, observed_at,
    ):
        """Nur einen zugleich vollständigen Satz als historischen Vorschlag halten."""
        if not positions:
            return False
        key = (route, tuple(positions), tuple(quantities))
        context = (context, tuple(
            (position, tuple(
                self._curve_series[position, quantity]["epoch"]
                for quantity in (
                    (Quantity.TEMPERATURE, Quantity.HUMIDITY)
                    if route.startswith("infusion") else quantities
                )
            )) for position in positions
        ))
        proposal = self._curve_proposals.get(key)
        if proposal is not None and (
            proposal.context != context
            or any(candidate.onset_at < floor for _, candidate in proposal.candidates)
        ):
            self._invalidate_curve_proposal(key, "owner_context")
            proposal = None
        candidates = []
        assembly = {}
        for position in positions:
            for quantity in quantities:
                candidate_key = (route, position, quantity)
                candidate = self._curve_candidates.get(candidate_key)
                label = f"{position.value}:{quantity.value}"
                assembly[label] = _gate(
                    candidate is not None and candidate.onset_at >= floor
                    and self._curve_status.get(candidate_key, {}).get("status") == "supported",
                    reason="candidate_missing" if candidate is None else
                    "candidate_before_context" if candidate.onset_at < floor else
                    "current_shape_not_supported" if self._curve_status.get(
                        candidate_key, {},
                    ).get("status") != "supported" else None,
                )
                if (
                    candidate is None or candidate.onset_at < floor
                    or self._curve_status.get(candidate_key, {}).get("status") != "supported"
                ):
                    self._record_curve_proposal_check(
                        key, assembly_gates=assembly, assembly_result=False,
                    )
                    return False
                candidates.append((candidate_key, candidate))
        onsets = [candidate.onset_at for _, candidate in candidates]
        window = self._curve_coherence_window(route)
        spread = (max(onsets) - min(onsets)).total_seconds()
        assembly["onsets_coherent"] = _gate(
            spread <= window, value=spread, operator="<=", threshold=window,
            unit="s", reference="required_candidate_onset_spread",
        )
        if spread > window:
            self._record_curve_proposal_check(
                key, assembly_gates=assembly, assembly_result=False,
            )
            return False
        if proposal is None:
            proposal = _CurveProposal(context, tuple(candidates), observed_at)
            self._curve_proposals[key] = proposal
            self._curve_proposal_status[key] = self._curve_proposal_diagnostic(key, proposal)
        elif not proposal.ambiguous:
            onsets.extend(candidate.onset_at for _, candidate in proposal.candidates)
            if (max(onsets) - min(onsets)).total_seconds() > window:
                proposal = replace(proposal, ambiguous=True)
                self._curve_proposals[key] = proposal
                self._curve_proposal_status[key] = {
                    **self._curve_proposal_status[key], "status": "ambiguous",
                    "reason": "competing_complete_proposal",
                    "competing_onsets": tuple(candidate.onset_at for _, candidate in candidates),
                }
        self._record_curve_proposal_check(
            key, assembly_gates=assembly, assembly_result=True,
        )
        return True

    def _bind_close_evidence(self, positions, *, floor, now):
        """Den vollständigen T-Knick nach echter Originalfortbestätigung binden."""
        key = ("door_close", tuple(positions), (Quantity.TEMPERATURE,))
        proposal = self._curve_proposals.get(key)
        if (
            proposal is None or proposal.ambiguous
            or proposal.evidence_bound_at is not None
            or not self._curve_confirmation(
                "door_close", positions, (Quantity.TEMPERATURE,), floor=floor,
            )
        ):
            return
        proposal = replace(
            proposal,
            candidates=tuple(
                (candidate_key, self._curve_candidates[candidate_key])
                for candidate_key, _ in proposal.candidates
            ),
            evidence_bound_at=now,
        )
        self._curve_proposals[key] = proposal
        self._curve_proposal_status[key] = {
            **self._curve_proposal_status[key],
            **self._curve_proposal_diagnostic(key, proposal),
            "anchor_confirmation": self._curve_proposal_status[key]["confirmation"],
        }

    def _curve_confirmation(
        self, route, positions, quantities, *, floor, recovery=False,
        recovery_quantity=Quantity.TEMPERATURE,
    ):
        """Den historischen Satz mit neuen Originalen am festen Beginn bestätigen.

        Jeder Zeuge braucht einen erneuten unterstützenden Modellvergleich nach
        seinem ersten Befund. Ein Rasterpunkt oder die Korrektur derselben Probe
        erwirbt diesen Nachweis nicht. Asynchrone Zeugen behalten ihren eigenen
        ersten Befund; der letzte vollständige Satz startet keine neue Wartezeit.
        Beim gebundenen T-Schluss ist dieser Knicknachweis abgeschlossen. Seine
        Ausgabe braucht anschließend den aktuellen Originalnachweis der Erholung.
        Eine RH-Erholung ergänzt nur einen weiterhin aktuell belegten T-Knick
        derselben Identität; dessen historische Bindung allein genügt dafür nicht.
        """
        key = (route, tuple(positions), tuple(quantities))
        proposal = self._curve_proposals.get(key)
        gates = {
            "complete_proposal": _gate(
                proposal is not None,
                reason="no_complete_proposal" if proposal is None else None,
            ),
            "unambiguous_proposal": _gate(
                not proposal.ambiguous if proposal is not None else None,
                reason="no_complete_proposal" if proposal is None else
                "competing_complete_proposal" if proposal.ambiguous else None,
            ),
        }

        def finish(result, reason=None):
            self._record_curve_proposal_check(
                key, confirmation_result=result, confirmation_gates=gates,
                confirmation_reason=reason,
            )
            return result

        if proposal is None or proposal.ambiguous:
            return finish(False, "no_complete_proposal" if proposal is None else
                          "competing_complete_proposal")
        confirmed = []
        new_original = False
        now = self.origin + timedelta(seconds=self.index * self.grid_seconds)
        bound_recovery = (
            recovery and proposal.evidence_bound_at is not None
            and route == "door_close" and tuple(quantities) == (Quantity.TEMPERATURE,)
        )
        if recovery_quantity == Quantity.HUMIDITY and not bound_recovery:
            gates["bound_temperature_anchor"] = _gate(False)
            return finish(False, "humidity_recovery_requires_bound_temperature_anchor")
        for candidate_key, original in proposal.candidates:
            witness_gates = {
                "onset_in_context": _gate(
                    original.onset_at >= floor, value=original.onset_at,
                    operator=">=", threshold=floor, unit="s",
                    reference="current_context_floor",
                ),
            }
            gates[original.candidate_id] = witness_gates
            if original.onset_at < floor:
                self._invalidate_curve_proposal(key, "outside_current_context")
                return finish(False, "outside_current_context")
            if bound_recovery:
                position = candidate_key[1]
                supported = (
                    self._temperature_recovery(position, original, now)
                    if recovery_quantity == Quantity.TEMPERATURE else
                    self._curve_recovery(position, recovery_quantity, original, now)
                )
                evidence = self._curve_recovery_evidence[position, recovery_quantity]
                series = self._curve_series[position, recovery_quantity]
                current = self._curve_candidates.get(candidate_key)
                current_temperature = (
                    current is not None and current.candidate_id == original.candidate_id
                    and self._curve_status.get(candidate_key, {}).get("status") == "supported"
                    and current.fresh_newest_at is not None
                    and current.fresh_newest_at > original.first_newest_at
                    and now - timedelta(seconds=self.p["door_window_seconds"])
                    <= current.features.newest_at <= now
                )
                if recovery_quantity == Quantity.HUMIDITY:
                    # Der gebundene historische T-Knick allein darf keinen
                    # späteren freien RH-Puls zum Schluss machen.
                    supported = supported and current_temperature
                    witness_gates["current_temperature_anchor"] = _gate(
                        current_temperature,
                        reason=None if current_temperature else "temperature_anchor_not_current",
                    )
                witness_gates["current_original_recovery"] = _gate(
                    supported,
                    reason=None if supported else "recovery_unresolved_or_not_current",
                )
                confirmed.append({
                    "candidate_id": original.candidate_id,
                    "newest_at": original.features.newest_at,
                    "fresh_newest_at": original.fresh_newest_at,
                    "fresh_observed_at": original.fresh_observed_at,
                    "recovery_quantity": recovery_quantity.value,
                    "recovery_source": series["source"],
                    "recovery_time_basis": series["time_basis"],
                    "recovery_source_epochs": (series["epoch"],),
                    "recovery_start_at": evidence["start_at"],
                    "recovery_newest_at": evidence["newest_at"],
                    "recovery_observed_at": evidence["fresh_observed_at"],
                    "supported": supported,
                    **({
                        "current_temperature_supported": current_temperature,
                        "current_temperature_newest_at": (
                            current.features.newest_at if current is not None else None
                        ),
                        "current_temperature_fresh_observed_at": (
                            current.fresh_observed_at if current is not None else None
                        ),
                    } if recovery_quantity == Quantity.HUMIDITY else {}),
                })
                if recovery_quantity == Quantity.TEMPERATURE:
                    new_original |= supported and evidence["fresh_observed_at"] == now
                else:
                    new_original |= supported and (
                        current.fresh_observed_at == now
                        or evidence["fresh_observed_at"] == now
                        and series["last_original_version"] == series["version"]
                        and series["received_at"] is not None
                        and now - timedelta(seconds=self.grid_seconds)
                        < series["received_at"] <= now
                    )
                continue
            candidate = self._curve_candidates.get(candidate_key)
            status = self._curve_status.get(candidate_key, {})
            if candidate is None or candidate.candidate_id != original.candidate_id:
                if status.get("status") in ("rejected", "expired", "supported"):
                    self._invalidate_curve_proposal(key, "shape_counterevidence")
                witness_gates["same_candidate"] = _gate(
                    False, reason="candidate_missing" if candidate is None else
                    "candidate_identity_changed",
                )
                return finish(False, "candidate_missing_or_changed")
            supported = (
                status.get("status") == "supported"
                and candidate.fresh_newest_at is not None
                and candidate.fresh_newest_at > original.first_newest_at
                and (not recovery or self._temperature_recovery(
                    candidate_key[1], candidate, now,
                ))
            )
            witness_gates.update(
                same_candidate=_gate(True),
                current_shape=_gate(status.get("status") == "supported"),
                later_original=_gate(
                    candidate.fresh_newest_at > original.first_newest_at
                    if candidate.fresh_newest_at is not None else None,
                    value=candidate.fresh_newest_at, operator=">",
                    threshold=original.first_newest_at, unit="s",
                    reference="candidate_first_newest_original",
                    reason="no_later_supporting_original"
                    if candidate.fresh_newest_at is None else None,
                ),
                recovery=_gate(
                    supported if recovery else None,
                    reason="not_required" if not recovery else None,
                ),
            )
            confirmed.append({
                "candidate_id": candidate.candidate_id,
                "newest_at": candidate.features.newest_at,
                "fresh_newest_at": candidate.fresh_newest_at,
                "fresh_observed_at": candidate.fresh_observed_at,
                "supported": supported,
            })
            new_original |= candidate.fresh_observed_at == now
        self._curve_proposal_status[key] = {
            **self._curve_proposal_status[key], "confirmation": tuple(confirmed),
        }
        gates["new_original_at_this_check"] = _gate(
            new_original, reason=None if new_original else "no_new_supporting_original_at_this_check",
        )
        return finish(
            bool(confirmed) and new_original
            and all(witness["supported"] for witness in confirmed),
        )

    def _pause_infusion_episode(self):
        """Ein unbekannter Verlauf bestätigt keinen noch unbelegten Puls."""
        for state in self._position_evidence.values():
            self._pause_position_infusion(state)
        if self._infusion_owner is not None:
            self._infusion_episode = self._position_state(self._infusion_owner)["infusion"]
        episode = self._infusion_episode
        self._original_infusion_candidate = None
        if episode is not None and episode["phase"] == "pending":
            self._finish_infusion_context("pending_admission_context")
            return
        if episode is not None and episode["phase"] == "active":
            episode["interrupted"].update(episode["positions"])
            episode["context"] = None
            episode["decays"].clear()
            episode["levels"].clear()
            episode["level_status"].clear()
            episode["backgrounds"].clear()
            episode["level_evaluated"].clear()
            episode["original_background"] = None
        self._clear_curve_route("infusion_decay", reason="admission_context")

    def _update_infusion_end(self, episode, end):
        """Jeden echten Übergang erhalten, auch vor dem Löschen des Owners."""
        detection = episode.get("confirmed_detection")
        if end is not None and detection is not None and end.confirmed_at < detection.detected_at:
            end = replace(end, confirmed_at=detection.detected_at)
        if detection is None or episode.get("reported_end") == end:
            return
        if episode is self._infusion_episode:
            for state in self._position_evidence.values():
                local = state["infusion"]
                if local is not None and local.get("confirmed_detection") is detection:
                    if end is None:
                        local.pop("booked_end_at", None)
                    else:
                        local["booked_end_at"] = end.effective_at
        episode["reported_end"] = end
        self._infusion_updates.append(InfusionEpisodeUpdate(detection, end))

    def _finish_infusion_context(self, reason):
        """Eine tatsächliche neue Tür-/Betriebsfolge besitzt eigene Aufgüsse."""
        for state in self._position_evidence.values():
            state["infusion"] = state["infusion_candidate"] = None
            state["infusion_updates"].clear()
        self._infusion_owner = None
        self._infusion_episode = None
        self._original_infusion_candidate = None
        self._infusion_segment_start.clear()
        self._curve_infusion_since = None
        self._clear_curve_route("infusion", reason=reason)
        self._clear_curve_route("infusion_decay", reason=reason)

    def _new_infusion_episode(
        self, positions, beginning, now, *, rise_key, water, onsets=None,
    ):
        return {
            "phase": "pending",
            "positions": tuple(positions),
            "onset_at": beginning,
            "observed_at": now,
            "context": None,
            "decay_floor": {position: beginning for position in positions},
            "last_rise_at": dict(onsets) if onsets is not None else {
                key[1]: candidate.onset_at
                for key, candidate in self._curve_proposals[rise_key].candidates
            },
            "rise_ids": set(),
            "decays": {},
            "levels": {},
            "level_status": {},
            "backgrounds": {},
            "background_start": {},
            "level_evaluated": {},
            "level_end_at": None,
            "interrupted": set(),
            "end_basis": None,
            "rise_key": rise_key,
            "water": water,
            "confirmed_at": None,
            "confirmed_detection": None,
            "reported_end": None,
            "confirmation_basis": None,
            "original": onsets is not None,
            "original_component": None,
            "original_start_component": None,
            "original_background": None,
        }

    def _infusion_water_change(self, witness):
        """Den gebundenen AH-Vergleich aus genau seinen Medianstützen lesen."""
        values = []
        for endpoint in witness["endpoints"]:
            if any(
                cell[key] is None or not isfinite(cell[key])
                for cell in endpoint for key in ("T", "H")
            ):
                return None
            value = self._absolute_humidity({
                "Tm": median(cell["T"] for cell in endpoint),
                "Hm": median(cell["H"] for cell in endpoint),
            })
            if value is None:
                return None
            values.append(value)
        change = values[-1] - values[0]
        return (
            0.0 if isclose(
                change, 0.0, abs_tol=self.p["humidity_change_absolute_tolerance"],
            ) else change
        )

    def _bind_pending_infusion(self, positions, floor, now, trace=None):
        """Anstieg und damaligen Wasserzuwachs vor der späteren Spitze binden."""
        key = ("infusion", tuple(positions), (Quantity.HUMIDITY,))
        proposal = self._curve_proposals[key]
        water = {}
        need = int(self.p["infusion_window_seconds"] / self.grid_seconds) + 1
        median_samples = int(self.p["median_seconds"] / self.grid_seconds)
        gates = {}
        if trace is not None:
            trace["decisions"]["infusion_rise"]["gates"]["water_binding"] = gates
        for position in positions:
            frames = list(self.frames[position])
            gates[f"{position.value}:frame_count"] = _gate(
                len(frames) >= need + median_samples - 1,
                value=len(frames), operator=">=", threshold=need + median_samples - 1,
                unit="frames", reference="complete_water_comparison_median_supports",
            )
            if len(frames) < need + median_samples - 1:
                return False
            endpoints = []
            for end in (len(frames) - need, len(frames) - 1):
                supports = frames[end - median_samples + 1:end + 1]
                originals_available = not any(
                    cell.get(name) is None for cell in supports for name in ("To", "Ho")
                )
                gates[f"{position.value}:endpoint_{end}:originals"] = _gate(originals_available)
                if not originals_available:
                    return False
                epochs_coherent = not any(
                    cell[key + "e"] != self._curve_series[position, quantity]["epoch"]
                    for cell in supports for key, quantity in (
                        ("T", Quantity.TEMPERATURE), ("H", Quantity.HUMIDITY)
                    )
                )
                gates[f"{position.value}:endpoint_{end}:source_epochs"] = _gate(epochs_coherent)
                if not epochs_coherent:
                    return False
                endpoints.append(tuple({
                    name: cell[name]
                    for name in ("T", "H", "To", "Ho", "Te", "He", "Ts", "Hs")
                } for cell in supports))
            witness = {"endpoints": tuple(endpoints)}
            change = self._infusion_water_change(witness)
            gates[f"{position.value}:water_increase"] = _gate(
                change > 0 if change is not None else None,
                value=change, operator=">", threshold=0.0, unit="g/m³",
                reference="bound_original_median_endpoint_water_change",
                reason="water_comparison_unavailable" if change is None else None,
            )
            if change is None or change <= 0:
                return False
            water[position] = {**witness, "change": change}
        # The later expected decline must not be fitted back into this historical
        # rise. Bind its latest fresh fixed-onset support and leave it unchanged
        # until a correction or a source/context boundary requires validation.
        candidates = tuple(
            (candidate_key, self._curve_candidates[candidate_key])
            for candidate_key, _ in proposal.candidates
        )
        proposal = replace(proposal, candidates=candidates)
        self._curve_proposals[key] = proposal
        self._curve_proposal_status[key] = {
            **self._curve_proposal_status[key],
            **self._curve_proposal_diagnostic(key, proposal), "status": "pending_peak",
        }
        beginning = min(candidate.onset_at for _, candidate in candidates)
        self._start_infusion_episode(positions, beginning, now, rise_key=key, water=water)
        self._infusion_episode["context"] = self._infusion_context(positions, floor)
        return True

    def _infusion_context(self, positions, floor):
        return (self._curve_door_context, floor, tuple(
            (position, tuple(
                self._curve_series[position, quantity]["epoch"]
                for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY)
            )) for position in positions
        ))

    def _correct_infusion_tail(self, position, quantity, corrected_at, value):
        """Gebundene Wasser-, Abfall- und Endzeugen am Original nachprüfen."""
        position_state = self._position_state(position)
        episode = position_state["infusion"]
        if position_state["infusion_candidate"] is not None or (
            episode is not None and episode.get("original")
        ):
            self._correct_original_infusion(position, quantity, corrected_at, value)
            return
        if episode is None:
            return
        if (
            episode["phase"] == "pending"
            and episode["rise_key"] not in self._curve_proposals
        ):
            self._finish_infusion_context("rise_original_corrected")
            return
        water = episode["water"].get(position)
        if water is not None:
            key = "T" if quantity == Quantity.TEMPERATURE else "H"
            epoch = self._curve_series[position, quantity]["epoch"]
            changed = any(
                cell[key + "o"] == corrected_at and cell[key + "e"] == epoch
                for endpoint in water["endpoints"] for cell in endpoint
            )
            if changed:
                water["endpoints"] = tuple(tuple(
                    {**cell, key: value} if (
                        cell[key + "o"] == corrected_at and cell[key + "e"] == epoch
                    ) else cell
                    for cell in endpoint
                ) for endpoint in water["endpoints"])
                change = self._infusion_water_change(water)
                if change is None or change <= 0:
                    if episode["phase"] == "pending":
                        self._finish_infusion_context("water_original_corrected")
                    else:
                        self._update_infusion_end(episode, None)
                        episode["phase"] = "active"
                        episode["interrupted"].update(episode["positions"])
                        episode["context"] = None
                        episode["decays"].clear()
                        episode["levels"].clear()
                        episode["level_status"].clear()
                        episode["backgrounds"].clear()
                        episode["level_evaluated"].clear()
                        episode["level_end_at"] = None
                        episode["end_basis"] = None
                    return
                water["change"] = change
        if quantity != Quantity.HUMIDITY:
            return
        invalid = False
        decay = episode["decays"].get(position)
        if decay is not None and any(
            stamp == corrected_at for stamp, _ in decay.original_points
        ):
            points = tuple(
                (stamp, value if stamp == corrected_at else original)
                for stamp, original in decay.original_points
            )
            features = self._fit_candidate_form(
                "infusion_decay", points, decay.context[2], quantity, candidate=decay,
            )
            if self._curve_supports(
                "infusion_decay", features, decay.context[2], quantity,
            ):
                episode["decays"][position] = replace(
                    decay, features=features, original_points=points,
                )
            else:
                episode["decays"].pop(position, None)
                invalid = True
        witness = episode["levels"].get(position)
        if witness is not None and any(
            stamp == corrected_at for stamp, _ in witness["original_points"]
        ):
            points = tuple(
                (stamp, value if stamp == corrected_at else original)
                for stamp, original in witness["original_points"]
            )
            level = curve_level(points, policy=self.curve_policy)
            if level is not None and level.evidence == "supports":
                witness.update(features=level, original_points=points)
            else:
                invalid = True
        background = episode["backgrounds"].get(position)
        if background is not None and any(
            stamp == corrected_at for stamp, _ in background["original_points"]
        ):
            points = tuple(
                (stamp, value if stamp == corrected_at else original)
                for stamp, original in background["original_points"]
            )
            reference = background["reference"]
            form = curve_form(
                points, policy=self.curve_policy, form="recovery", reference=reference,
                current_not_before=reference.current_not_before,
            ) if reference is not None else None
            if form is not None and form.evidence == "supports":
                background.update(features=form, original_points=points)
            else:
                # Ein ungebundener optionaler Formbefund war kein Endzeuge.
                # Nur der tatsächlich verwendete Hintergrund kann sein Ende
                # durch eine Korrektur verlieren; ein unabhängiges Niveau bleibt gültig.
                episode["backgrounds"].pop(position, None)
                invalid |= (
                    episode["phase"] == "ended"
                    and episode["end_basis"] == "fixed_recovery_background"
                )
        if invalid:
            if episode["phase"] == "pending":
                episode["decays"].clear()
                self._clear_curve_route("infusion_decay", reason="decay_original_corrected")
                return
            self._update_infusion_end(episode, None)
            episode["phase"] = "active"
            episode["level_end_at"] = None
            episode["end_basis"] = None
            episode["levels"].clear()
            episode["level_status"].clear()
            episode["backgrounds"].clear()
            episode["level_evaluated"].clear()
            self._clear_curve_route("infusion", reason="level_original_corrected")

    def _correct_original_windows(self, windows, position, quantity, corrected_at, value):
        changed = False
        result = []
        for name, pair in windows:
            if name != position.value:
                result.append((name, pair))
                continue
            window = pair.temperature if quantity == Quantity.TEMPERATURE else pair.humidity
            points = window.previous + window.current
            if not any(stamp == corrected_at for stamp, _ in points):
                result.append((name, pair))
                continue
            changed = True
            window = original_window(
                tuple((stamp, value if stamp == corrected_at else old) for stamp, old in points),
                count=self.p["curve_original_samples"], scale=window.scale,
                max_gap_seconds=self.p[
                    f"curve_original_gap_{quantity.value}_{position.value}_seconds"
                ], at=window.newest_at, time_support=window.time_support,
            )
            if window is None:
                return (), True
            result.append((name, replace(
                pair, **{quantity.value: window},
            )))
        return tuple(result), changed

    def _correct_position_infusion(self, position, quantity, corrected_at, value, position_state):
        """Gespeicherte OLS-Zeugen korrigieren, niemals neue Evidenz erzeugen."""
        episode = position_state["infusion"]
        components = []
        for component in (
            position_state["infusion_candidate"],
            episode.get("original_component") if episode is not None else None,
            episode.get("original_start_component") if episode is not None else None,
        ):
            if component is not None and not any(component is item for item in components):
                components.append(component)
        episode_invalid = False
        for component in components:
            if position not in component["positions"]:
                continue
            component_invalid = False
            expected = tuple(item.value for item in component["positions"])
            if component["context"] != self._original_bound_context(
                self._original_context(), component["positions"],
            ):
                continue
            if quantity == Quantity.HUMIDITY:
                held = component.get("humidity_returns", {}).get(position.value)
                if held is not None:
                    window = held["window"]
                    if any(stamp == corrected_at for stamp, _ in window.previous + window.current):
                        corrected_window = original_window(
                            tuple((stamp, value if stamp == corrected_at else old)
                                  for stamp, old in window.previous + window.current),
                            count=self.p["curve_original_samples"], scale=window.scale,
                            max_gap_seconds=self.p[f"curve_original_gap_humidity_{position.value}_seconds"],
                            at=window.newest_at, time_support=window.time_support,
                        )
                        if (corrected_window is not None and corrected_window.v
                                < -self.p["curve_original_significance"] * corrected_window.e):
                            held["window"] = corrected_window
                        else:
                            component_invalid = True
                    if held["peak"][0] == corrected_at:
                        # Die bestehende Komponenteninvalidierung verwirft
                        # einen unbekannten Peakbezug ohne neue Dauersperre.
                        component_invalid = True
            kink = dict(component.get("humidity_kinks", ())).get(position.value)
            if (quantity == Quantity.HUMIDITY and kink is not None and kink.factor > 0
                    and kink.status == "history"
                    and kink.history_start <= corrected_at <= kink.history_end
                    and any(stamp == corrected_at for stamp, _ in dict(
                        component["humidity_kink_originals"],
                    ).get(position.value, ()))):
                # Der unveränderlich gebundene Hintergrund ist korrigiert.
                # Eine spätere Historie darf diesen Beginn nicht neu zulassen.
                component_invalid = True
            end_windows = dict(component.get("rise_end_windows", ()))
            end_pair = end_windows.get(position.value)
            if end_pair is not None:
                windows, changed = self._correct_original_windows(
                    ((position.value, end_pair),), position, quantity, corrected_at, value,
                )
                if changed:
                    end_pair = windows[0][1] if windows else None
                    if (end_pair is not None and end_pair.humidity.v
                            <= self.p["curve_original_significance"] * end_pair.humidity.e):
                        end_windows[position.value] = end_pair
                    else:
                        # Der korrigierte Anstieg hat keinen Endbeleg mehr.
                        # Vorhandene Wasserpaare behalten ihre Identität; neue
                        # zugehörige RH dürfen wieder ab seen_through folgen.
                        end_windows.pop(position.value)
                        component["water_end"].pop(position, None)
                        component["rise_ended"] = False
                    component["rise_end_windows"] = tuple(end_windows.items())
            for key, score, threshold in (
                ("binding_windows", "humidity_rise_onset", self.p["curve_original_significance"]),
                ("rise_windows", "rise", component.get("threshold")),
                ("fall_windows", "fall", self.p["curve_original_significance"]),
            ):
                if key not in component:
                    continue
                windows, changed = self._correct_original_windows(
                    component[key], position, quantity, corrected_at, value,
                )
                if not changed:
                    continue
                component[key] = windows
                contrasts = original_contrasts(dict(windows), expected_positions=expected)
                if key == "rise_windows" and contrasts is not None:
                    component["rise_score"] = contrasts.rise
                if key == "binding_windows" and contrasts is not None:
                    component["humidity_rise_onset"] = contrasts.humidity_rise_onset
                    if quantity == Quantity.HUMIDITY:
                        intervals = self._original_local_rise_intervals(
                            windows, component["humidity_kinks"], component["branch_floors"],
                        )
                        previous_interval = component.get("onset_intervals", {}).get(position)
                        current_interval = intervals.get(position)
                        if previous_interval is not None:
                            component_invalid |= current_interval is None or (
                                previous_interval.previous[0], previous_interval.current[0]
                            ) != (current_interval.previous[0], current_interval.current[0])
                        # Recompute only the corrected immutable support. No
                        # later block may replace this original onset interval.
                        component["onset_intervals"] = intervals
                    if quantity == Quantity.HUMIDITY and kink is not None and kink.factor > 0:
                        humidity = dict(windows)[position.value].humidity
                        # Nur den korrigierten gebundenen Anstieg mit seiner
                        # ursprünglichen Toleranz vergleichen; Beleg und
                        # historische Originale bleiben unverändert.
                        component_invalid |= humidity.v - humidity.b <= kink.tolerance
                if contrasts is None or getattr(contrasts, score) <= threshold:
                    component_invalid = True
                    if key == "fall_windows":
                        component["fall_confirmed"] = False
                        component["fall_ready"] = False
                    else:
                        component["strong"] = False
                        component["rise_score"] = None
            if "early_confirmation_windows" in component:
                windows, changed = self._correct_original_windows(
                    component["early_confirmation_windows"],
                    position, quantity, corrected_at, value,
                )
                if changed:
                    component["early_confirmation_windows"] = windows
                    contrasts = original_contrasts(dict(windows), expected_positions=expected)
                    component_invalid |= contrasts is None or not (
                        contrasts.rise > self.p["curve_original_new_infusion_contrast"]
                        and contrasts.humidity_rise_change > self.p["curve_original_significance"]
                    )
            state = component["water_state"].get(position)
            witness = component.get("onset_witnesses", {}).get(position)
            if (quantity == Quantity.HUMIDITY and witness is not None
                    and any(stamp == corrected_at for stamp, _ in witness.original_points)):
                points = tuple((stamp, value if stamp == corrected_at else old)
                               for stamp, old in witness.original_points)
                features = self._fit_candidate_form(
                    "infusion", points, witness.context[2], quantity, candidate=witness,
                )
                if self._curve_supports("infusion", features, witness.context[2], quantity):
                    # Derselbe zulässige Beginn bleibt nach Wertkorrektur
                    # erhalten; seine Original-/Frischezeiten wachsen nicht.
                    component["onset_witnesses"][position] = replace(
                        witness, features=features, original_points=points,
                    )
                else:
                    component["onset_witnesses"].pop(position)
                    component_invalid = True
            if state is None:
                if component_invalid:
                    component["strong"] = component["qualified"] = False
                    component.pop("humidity_returns", None)
                    if position_state["infusion_candidate"] is component:
                        position_state["infusion_candidate"] = None
                    episode_invalid |= episode is not None and any(
                        episode.get(key) is component
                        for key in ("original_component", "original_start_component")
                    )
                continue
            if quantity == Quantity.HUMIDITY and corrected_at in state["pending"]:
                state["pending"][corrected_at] = (corrected_at, value)
            for key in ("base", "maximum"):
                pair = state[key]
                if pair is None:
                    continue
                humidity = pair.humidity_sample
                temperatures = tuple(dict.fromkeys((pair.temperature_before, pair.temperature_after)))
                affected = (quantity == Quantity.HUMIDITY and humidity[0] == corrected_at) or (
                    quantity == Quantity.TEMPERATURE
                    and any(stamp == corrected_at for stamp, _ in temperatures)
                )
                if not affected:
                    continue
                if quantity == Quantity.HUMIDITY:
                    humidity = (corrected_at, value)
                else:
                    temperatures = tuple((stamp, value if stamp == corrected_at else old)
                                         for stamp, old in temperatures)
                replacement = original_water_pair(
                    humidity, temperatures,
                    temperature_scale=self.p[f"curve_original_noise_temperature_{position.value}"],
                    humidity_scale=self.p[f"curve_original_noise_humidity_{position.value}"],
                    max_gap_seconds=self.p[f"curve_original_gap_temperature_{position.value}_seconds"],
                    at=max(stamp for stamp, _ in temperatures), time_support=pair.time_support,
                )
                state[key] = replacement
                if key == "maximum":
                    # Ein korrigiertes Maximum kennt keinen erfundenen
                    # historischen Zweitbesten. Neue Paare dürfen nachführen.
                    state["overflow"] = True
                component_invalid |= replacement is None
            base, maximum = state["base"], state["maximum"]
            change = (maximum.lower_density - base.upper_density
                      if base is not None and maximum is not None else None)
            if change is not None and change <= 0:
                component_invalid = True
            if (episode is not None and episode.get("original_component") is component
                    and position in episode["water"]):
                episode["water"][position] = {
                    **episode["water"][position], "change": change,
                }
            if component_invalid:
                # Auch gehaltene Tür- und Startreferenzen sehen dieselbe
                # widerrufene Qualifikation; die gebuchte Detection bleibt.
                component["strong"] = component["qualified"] = False
                component.pop("humidity_returns", None)
                if position_state["infusion_candidate"] is component:
                    position_state["infusion_candidate"] = None
                episode_invalid |= episode is not None and any(
                    episode.get(key) is component
                    for key in ("original_component", "original_start_component")
                )
        if (episode is not None and position in episode["positions"]
                and quantity == Quantity.HUMIDITY and episode.get("original_end_windows")):
            windows, changed = [], False
            for name, window in episode["original_end_windows"]:
                points = window.previous + window.current
                if name == position.value and any(stamp == corrected_at for stamp, _ in points):
                    changed = True
                    window = original_window(
                        tuple((stamp, value if stamp == corrected_at else old) for stamp, old in points),
                        count=self.p["curve_original_samples"], scale=window.scale,
                        max_gap_seconds=self.p[f"curve_original_gap_humidity_{name}_seconds"],
                        at=window.newest_at, time_support=window.time_support,
                    )
                if window is not None:
                    windows.append((name, window))
            if changed:
                episode["original_end_windows"] = tuple(windows)
                forms = original_humidity_contrasts(
                    dict(windows), expected_positions=tuple(item.value for item in episode["positions"]),
                )
                episode_invalid |= forms is None or not (
                    forms.background_slope <= self.p["curve_original_background_slope"]
                    and forms.background_residual <= self.p["curve_original_background_residual"]
                    and forms.rise <= self.p["curve_original_significance"]
                )
        if not episode_invalid:
            return
        position_state["infusion_candidate"] = None
        if episode is None:
            return
        if episode["confirmed_detection"] is None:
            position_state["infusion"] = None
            return
        position_state["infusion_updates"].append((episode, None))
        episode["phase"] = "active"
        episode["level_end_at"] = None
        episode["end_basis"] = None
        episode["original_background"] = None
        episode["original_component"] = None
        episode["interrupted"].add(position)

    def _infusion_background(self, episode, floor, now, trace):
        """Einen neuen Hintergrund nach dem gezählten Peak fest und frisch belegen.

        Der Formwechsel zählt nichts. Erst ein später im abgetrennten Hintergrund
        beginnender und erneut durch seinen Peak bestätigter Anstieg kann einen
        weiteren Aufguss liefern. Bloße negative Fallbremsung erfüllt recovery
        nicht. Alle Referenzen bleiben Teil derselben Quellen-/Aufgussepisode.
        """
        gates = {}
        new_support = False
        for position in episode["positions"]:
            series = self._curve_series[position, Quantity.HUMIDITY]
            start = max(
                floor, episode["background_start"].get(position, floor),
                series["started_at"] or floor,
            )
            points = self._curve_points(
                position, Quantity.HUMIDITY, now, points_not_before=start,
            )
            context = (
                self._infusion_context(episode["positions"], floor), start,
                series["source"], series["time_basis"],
            )
            previous = episode["backgrounds"].get(position)
            reference = previous["reference"] if previous is not None else None
            reference_current = (
                previous is not None and previous["context"] == context
                and (reference is None or bool(points) and reference.start_at == points[0][0])
                and all(point in points for point in previous["original_points"])
            )
            if previous is not None and not reference_current:
                reference = None
                previous = {
                    **previous, "reference": None, "fresh_newest_at": None,
                    "features": None,
                    "diagnostic": {
                        "result": None, "reason": "recovery_reference_expired_or_context_changed",
                        "gates": {"current_original_reference": _gate(False)},
                    },
                }
                episode["backgrounds"][position] = previous
            if previous is not None and previous["version"] == series["version"]:
                gates[position.value] = previous["diagnostic"]
                continue
            new_original = series["last_original_version"] == series["version"]
            # Ein Rastertick oder eine Korrektur erwirbt nach Ablauf keine
            # neue Geometrie. Nur ein neuer Originalstand kann sie binden.
            if reference is None and not new_original:
                gates[position.value] = {
                    "result": None, "reason": "no_new_original_for_recovery_reference",
                    "gates": {"new_original_update": _gate(False)},
                }
                episode["backgrounds"][position] = {
                    "context": context, "version": series["version"], "reference": None,
                    "features": None, "original_points": points,
                    "fresh_newest_at": None, "diagnostic": gates[position.value],
                }
                continue
            features = curve_form(
                points, policy=self.curve_policy, form="recovery", reference=reference,
                current_not_before=reference.current_not_before if reference is not None else start,
            )
            supported = features is not None and features.evidence == "supports"
            later_original = bool(
                supported and reference is not None and new_original
                and features.fresh_original_count > 0
                and features.newest_at > reference.initial_newest_at
            )
            fresh_newest_at = (
                features.newest_at if later_original else
                previous["fresh_newest_at"] if supported and previous is not None else None
            )
            new_support |= later_original
            diagnostic = {
                "result": supported and fresh_newest_at is not None
                if features is not None and features.evidence != "unresolved" else None,
                "reason": features.reason if features is not None else
                "owner_supplied_no_evaluable_recovery",
                "gates": {
                    "recovery_form": _gate(
                        supported if features is not None and features.evidence != "unresolved"
                        else None,
                    ),
                    "new_original_update": _gate(new_original),
                    "later_original": _gate(
                        features.fresh_original_count > 0 if features is not None else None,
                        value=features.fresh_original_count if features is not None else None,
                        operator=">", threshold=0, unit="originals",
                        reference="frozen_recovery_initial_newest",
                    ),
                },
                **(self._curve_feature_diagnostic(features) if features is not None else {}),
            }
            gates[position.value] = diagnostic
            if features is not None and features.evidence == "rejects":
                reference = None
                fresh_newest_at = None
            elif reference is None and supported and new_original:
                reference = features.reference
            episode["backgrounds"][position] = {
                "context": context, "version": series["version"], "reference": reference,
                "features": features, "original_points": points,
                "fresh_newest_at": fresh_newest_at, "diagnostic": diagnostic,
            }
        complete = bool(gates) and new_support and all(gate["result"] for gate in gates.values())
        gates["new_original_at_this_check"] = _gate(
            new_support, reason=None if new_support else
            "no_new_supporting_original_at_this_check",
        )
        return _decision(trace, "infusion_background", complete, gates)

    def _advance_infusion_episode(self, channels, now, decision_at, floor, trace):
        """Nach dem Anstieg erst Abfall und ein neues ruhiges Niveau belegen.

        Weitere Spitzen gehören bis dahin zum gezählten Aufguss. Der jüngste
        zusammenhängende Tail hat genau einen festen Zeitbezug; es wird weder
        nach einem günstigen Plateau gesucht noch das Ausgangsniveau verlangt.
        """
        episode = self._infusion_episode
        positions = episode["positions"]
        if not all(position in channels for position in positions):
            self._pause_infusion_episode()
            return False
        context = self._infusion_context(positions, floor)
        if episode["phase"] == "pending" and (
            episode["rise_key"] not in self._curve_proposals
            or episode["context"] != context
        ):
            self._finish_infusion_context("pending_owner_context")
            return False
        if episode["context"] != context:
            if episode["context"] is not None:
                episode["interrupted"].update(positions)
            episode["context"] = context
            episode["decays"].clear()
            episode["levels"].clear()
            episode["level_status"].clear()
            episode["backgrounds"].clear()
            episode["level_evaluated"].clear()
            self._clear_curve_route("infusion_decay", reason="source_context")
        new_rises = {}
        for position in positions if episode["phase"] == "active" else ():
            key = ("infusion", position, Quantity.HUMIDITY)
            candidate = self._curve_candidates.get(key)
            if (
                candidate is not None
                and self._curve_status.get(key, {}).get("status") == "supported"
                and candidate.candidate_id not in episode["rise_ids"]
            ):
                episode["rise_ids"].add(candidate.candidate_id)
                if candidate.onset_at > episode["last_rise_at"][position]:
                    new_rises[position] = candidate.onset_at
        for position, beginning in new_rises.items():
            episode["interrupted"].discard(position)
            episode["last_rise_at"][position] = beginning
            episode["decay_floor"][position] = beginning
            episode["decays"].pop(position, None)
            episode["levels"].pop(position, None)
            episode["level_status"].pop(position, None)
            episode["backgrounds"].pop(position, None)
            episode["background_start"][position] = beginning
            episode["level_evaluated"].pop(position, None)
            self._clear_curve_route(
                "infusion_decay", position=position, reason="continued_infusion",
            )
        for position in positions:
            decay_floor = max(floor, episode["decay_floor"][position])
            self._curve_candidate(
                "infusion_decay", position, Quantity.HUMIDITY, now, decision_at,
                decay_floor, (context, episode["onset_at"], decay_floor),
                points_not_before=self._infusion_segment_start.get(position),
            )
            key = ("infusion_decay", position, Quantity.HUMIDITY)
            candidate = self._curve_candidates.get(key)
            if (
                candidate is not None
                and self._curve_status.get(key, {}).get("status") == "supported"
            ):
                if position not in episode["decays"]:
                    episode["decays"][position] = candidate
                    episode["background_start"][position] = candidate.onset_at
            if episode["phase"] == "pending":
                continue
            if position not in episode["decays"] and position not in episode["interrupted"]:
                continue
            series = self._curve_series[position, Quantity.HUMIDITY]
            if episode["level_evaluated"].get(position) == series["version"]:
                continue
            episode["level_evaluated"][position] = series["version"]
            tail_floor = (
                max(floor, *(
                    self._curve_series[position, quantity]["started_at"] or floor
                    for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY)
                )) if position in episode["interrupted"]
                else episode["decays"][position].onset_at
            )
            points = tuple(
                point for point in series["points"]
                if tail_floor <= point[0] <= now
            )
            tail = ()
            if points:
                boundary = points[-1][0] - timedelta(
                    seconds=self.p["infusion_window_seconds"]
                )
                # The last original at/before the fixed left time boundary
                # gives a full span without synthesizing a boundary value.
                start = next((
                    index for index in reversed(range(len(points)))
                    if points[index][0] <= boundary
                ), None)
                if start is not None:
                    tail = points[start:]
            level = (
                curve_level(tail, policy=self.curve_policy)
                if len(tail) >= 2 * self.curve_policy.min_side_samples else None
            )
            previous = episode["levels"].get(position)
            status = {
                "result": level.evidence == "supports"
                if level is not None and level.evidence != "unresolved" else None,
                "reason": "owner_supplied_no_evaluable_level" if level is None else
                "level_unresolved" if level.evidence == "unresolved" else
                "level_counterevidence" if level.evidence != "supports" else None,
                "noise_scale": level.noise_scale if level is not None else None,
                "level_score": level.level_score if level is not None else None,
                "trend_score": level.trend_score if level is not None else None,
                "score_gain": level.score_gain if level is not None else None,
                "counter_model": level.trend_model if level is not None else None,
                "exact_constant": level.exact_constant if level is not None else None,
                "gates": {
                    "original_tail_count": _gate(
                        len(tail) >= 2 * self.curve_policy.min_side_samples,
                        value=len(tail), operator=">=",
                        threshold=2 * self.curve_policy.min_side_samples,
                        unit="originals", reference="fixed_infusion_tail",
                    ),
                    "level_evidence": _gate(
                        level.evidence == "supports"
                        if level is not None and level.evidence != "unresolved" else None,
                        reason="owner_supplied_no_evaluable_level" if level is None else None,
                    ),
                },
            }
            episode["level_status"][position] = status
            if level is None or level.evidence != "supports":
                episode["levels"].pop(position, None)
                continue
            original_update = series["last_original_version"] == series["version"]
            status["gates"]["original_update"] = _gate(original_update)
            if previous is None:
                if not original_update:
                    continue
                previous = {
                    "first_newest_at": level.newest_at,
                    "fresh_newest_at": None,
                }
            if original_update and level.newest_at > previous["first_newest_at"]:
                previous["fresh_newest_at"] = level.newest_at
            episode["levels"][position] = {
                **previous, "features": level, "original_points": tail,
            }
            status["gates"]["later_original"] = _gate(
                previous["fresh_newest_at"] is not None,
                value=previous["fresh_newest_at"], operator=">",
                threshold=previous["first_newest_at"], unit="s",
                reference="first_supported_level_original",
                reason="no_later_supporting_original"
                if previous["fresh_newest_at"] is None else None,
            )
        if episode["phase"] == "pending":
            decay_floor = max(floor, episode["onset_at"])
            self._remember_curve_proposal(
                "infusion_decay", positions, (Quantity.HUMIDITY,),
                floor=decay_floor,
                context=(context, episode["onset_at"], decay_floor),
                observed_at=decision_at,
            )
            rise = self._curve_proposals[episode["rise_key"]]
            after_rise = all(
                (candidate := self._curve_candidates.get((
                    "infusion_decay", candidate_key[1], Quantity.HUMIDITY,
                ))) is not None
                and candidate.features.newest_at > witness.features.newest_at
                for candidate_key, witness in rise.candidates
            )
            ready = after_rise and self._curve_confirmation(
                "infusion_decay", positions, (Quantity.HUMIDITY,), floor=decay_floor,
            )
            trace["conditions"]["infusion_decay"] = ready
            _decision(trace, "infusion_decay", ready, {
                "newest_original_after_bound_rise": _gate(after_rise),
                "fixed_original_decay_confirmation": _gate(
                    ready if after_rise else None,
                    reason="decay_not_after_bound_rise" if not after_rise else None,
                ),
            })
            return ready
        level_complete = all(
            position in episode["levels"]
            and episode["levels"][position]["fresh_newest_at"] is not None
            for position in positions
        )
        trace["conditions"].update(
            infusion_decay=all(position in episode["decays"] for position in positions),
            infusion_level=level_complete,
            infusion_level_after_interruption=(
                level_complete if episode["interrupted"] else None
            ),
        )
        _decision(trace, "infusion_level", level_complete, {
            position.value: episode["level_status"].get(position, {
                "result": None, "reason": "no_evaluable_decay_tail",
            }) for position in positions
        })
        background_complete = self._infusion_background(episode, floor, now, trace)
        if level_complete or background_complete:
            episode["phase"] = "ended"
            episode["level_end_at"] = now
            episode["end_basis"] = (
                "level_after_interruption" if episode["interrupted"] else "decay_and_level"
            ) if level_complete else "fixed_recovery_background"
            self._update_infusion_end(episode, InfusionEnd(
                episode["level_end_at"], decision_at, now,
                episode["end_basis"], "evidence_grid",
            ))
            self._infusion_segment_start = {
                position: (
                    episode["levels"][position]["features"].start_at if level_complete else
                    episode["backgrounds"][position]["features"].newest_at
                )
                for position in positions
            }
            self._clear_curve_route("infusion", keep_status=True)
            self._clear_curve_route("infusion_decay", keep_status=True)
        return False

    def _correct_curve_proposals(self, position, quantity, corrected_at, value):
        """Nur geänderte Zeugnisstützen am unverrückten Beginn neu prüfen."""
        for key, proposal in tuple(self._curve_proposals.items()):
            corrected = []
            changed = False
            for candidate_key, candidate in proposal.candidates:
                if candidate_key[1:] != (position, quantity) or not any(
                    stamp == corrected_at for stamp, _ in candidate.original_points
                ):
                    corrected.append((candidate_key, candidate))
                    continue
                points = tuple(
                    (stamp, value if stamp == corrected_at else original)
                    for stamp, original in candidate.original_points
                )
                features = self._fit_candidate_form(
                    key[0], points, candidate.context[2], candidate_key[2], candidate=candidate,
                )
                if not self._curve_supports(
                    key[0], features, candidate.context[2], candidate_key[2],
                ):
                    self._invalidate_curve_proposal(key, "original_corrected")
                    break
                corrected.append((candidate_key, replace(
                    candidate, features=features, original_points=points,
                )))
                changed = True
            else:
                if changed:
                    proposal = replace(proposal, candidates=tuple(corrected))
                    self._curve_proposals[key] = proposal
                    self._curve_proposal_status[key] = {
                        **self._curve_proposal_status[key],
                        **self._curve_proposal_diagnostic(key, proposal),
                    }

    def _curve_beginning(
        self, route, positions, quantities, fallback, *, floor, kind,
        now, decision_at, onset_allowed=None,
        boundary_reason="outside_current_context",
        require_bound=False,
        bound_candidates=None,
    ):
        """Den passenden Kandidaten am allein führenden Bestätigungspfad binden."""
        proposal_key = (route, tuple(positions), tuple(quantities))
        proposal = self._curve_proposals.get(proposal_key)
        if bound_candidates is None:
            if (
                proposal is None or proposal.ambiguous
                or require_bound and proposal.evidence_bound_at is None
            ):
                return fallback
            candidates = proposal.candidates
        else:
            candidates = bound_candidates
            if not candidates:
                self._invalidate_curve_proposal(proposal_key, "no_bound_original_onset")
                return fallback
        if any(candidate.onset_at < floor for _, candidate in candidates):
            self._invalidate_curve_proposal(proposal_key, boundary_reason)
            return fallback
        if require_bound:
            # Ein historischer T-Knick gehört nur dann zur bestätigten Form,
            # wenn seine Originalzeit noch in deren beobachteter Fallflanke liegt.
            for candidate_key, candidate in candidates:
                form = self._curve_recovery_evidence.get(candidate_key[1:], {}).get(
                    "smooth_form", {},
                )
                features = form.get("features")
                if (
                    features is None or not form.get("original_supported", False)
                    or not features.decline_start_at <= candidate.onset_at
                    <= features.decline_newest_at
                    or not self._curve_onset_available(candidate, self._curve_points(
                        candidate_key[1], candidate_key[2], now,
                        points_not_before=features.decline_start_at,
                    ))
                ):
                    return fallback
        beginning = min(candidate.onset_at for _, candidate in candidates)
        if beginning > fallback:
            return fallback
        if (
            beginning < fallback and onset_allowed is not None
            and not onset_allowed(kind, beginning, now)
        ):
            self._invalidate_curve_proposal(proposal_key, "controller_context")
            return fallback
        status = self._curve_proposal_status.get(proposal_key, {})
        if bound_candidates is not None:
            status = {
                **status, "proposal_id": None,
                "onset_at": beginning,
                "onsets": tuple(candidate.onset_at for _, candidate in candidates),
                "candidate_ids": tuple(candidate.candidate_id for _, candidate in candidates),
                "witnesses": tuple({
                    "position": key[1].value, "quantity": key[2].value,
                    "candidate_id": candidate.candidate_id, "onset_at": candidate.onset_at,
                    "first_at": candidate.original_points[0][0],
                    "newest_at": candidate.features.newest_at,
                } for key, candidate in candidates),
            }
        self._curve_proposal_status[proposal_key] = {
            **status, "status": "consumed",
            "confirmed_at": decision_at, "consumed_onset_at": beginning,
            "attribution_basis": (f"original_{'close' if route == 'door_close' else 'open'}_binding"
                                  if bound_candidates is not None
                                  else "curve_proposal"),
            "consumed_candidate_ids": tuple(candidate.candidate_id for _, candidate in candidates),
        }
        for key, candidate in candidates:
            status = self._curve_status.get(key, {})
            if (
                status.get("candidate_id") == candidate.candidate_id
                and status.get("status") == "supported"
            ):
                self._curve_status[key] = {
                    **status, "status": "confirmed", "confirmed_at": decision_at,
                }
        return beginning

    def report_heating(self, heating, at):
        """Zusatzregel nur bei durchgehend bestätigtem Heizbetrieb verwenden."""
        if heating is not True:
            self.heating_since = None
            self.counts["door_heating"] = 0
            self._clear_curve_route("door_open_heating", reason="heating_context")
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
            and measurement.received_at < self.origin + timedelta(seconds=self.index * self.grid_seconds)
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
                and old.source_identity == m.source_identity
                and m.measured_at is not None
                and old.measured_at is not None
                and m.measured_at < old.measured_at
            ):
                self.rejected += 1
                continue
            if old and old.source_identity != m.source_identity:
                self.frames[m.position].clear()
                self._clear_curve_series(m.position, m.quantity)
                if self.open and m.position in self.ventilation_positions:
                    self.ventilation_invalid.add(m.position)
                    self.ventilation_degraded = True
                else:
                    self.baseline.pop(m.position, None)
                self._clear_position_person(m.position)
            self.latest[key] = m
            self._record_curve_sample(m, now)

    def _value_state(self, position, quantity, now):
        """Aktuellen Wert und Ursache seiner Unverfügbarkeit einmal bestimmen."""
        m = self.latest.get((position, quantity))
        timeout = self.p.get("sensor_timeout_seconds")
        if m is None:
            return None, "missing"
        if (m.value is None or not isfinite(m.value)
                or quantity == Quantity.HUMIDITY and not 0 <= m.value <= 100
                or quantity == Quantity.TEMPERATURE and not -KELVIN_OFFSET_C < m.value):
            return None, "invalid"
        if timeout is None:
            return None, "unconfigured"
        if ((now - m.received_at).total_seconds() > timeout
                or m.measured_at is not None
                and (now - m.measured_at).total_seconds() > timeout):
            return None, "expired"
        return m.value, None

    def _value(self, position, quantity, now):
        return self._value_state(position, quantity, now)[0]

    def _series(self, position, key, window, step=None):
        frames = self.frames[position]
        step = self.grid_seconds if step is None else step
        need = int(window / self.grid_seconds) + 1
        if len(frames) < need:
            return []
        return [frame[key] for frame in list(frames)[-need :: int(step / self.grid_seconds)]]

    def _slope(self, position, key, window, step=None):
        step = self.grid_seconds if step is None else step
        return robust_slope(self._series(position, key, window, step), step)

    def _difference(self, position, key, window):
        series = self._series(position, key, window)
        if not series or series[0] is None or series[-1] is None:
            return None
        difference = series[-1] - series[0]
        return difference if isfinite(difference) else None

    def _sustain(self, name, condition, seconds, step=None):
        step = self.grid_seconds if step is None else step
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

    def _absolute_humidity_difference(self, position, window, step=None):
        """Nur ein vollständiger T/RH-Quellenzug darf Feuchteanstieg belegen."""
        frames = self.frames[position]
        step = self.grid_seconds if step is None else step
        need = int(window / self.grid_seconds) + 1
        if len(frames) < need:
            return None
        selected = list(frames)[-need :: int(step / self.grid_seconds)]
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
        return (
            0.0
            if isclose(change, 0.0, abs_tol=self.p["humidity_change_absolute_tolerance"])
            else change
        )

    def _moisture_rise(self, route, features, *, gates=None):
        """RH-Schwelle und AH-Ausgleich beschreiben denselben Feuchteweg."""
        condition = True
        for position, (humidity_slope, absolute_change) in features.items():
            humidity_gate = _gate(
                humidity_slope >= self.p[f"{route}_humidity_{position.value}"]
                if humidity_slope is not None else None,
                value=humidity_slope, operator=">=",
                threshold=self.p[f"{route}_humidity_{position.value}"],
                unit="%RH/min", reference="median_humidity_robust_slope",
                reason="humidity_window_unavailable" if humidity_slope is None else None,
            )
            water_gate = _gate(
                absolute_change > 0 if absolute_change is not None else None,
                value=absolute_change, operator=">", threshold=0.0,
                unit="g/m³", reference="coherent_absolute_humidity_window_change",
                reason="absolute_humidity_window_unavailable"
                if absolute_change is None else None,
            )
            if gates is not None:
                gates[f"{position.value}:humidity_slope"] = humidity_gate
                gates[f"{position.value}:water_increase"] = water_gate
            if humidity_slope is None or absolute_change is None:
                return None
            condition &= humidity_gate["result"] and water_gate["result"]
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
        """Historischen Vorhervergleich für Diagnose halten, ohne Erkennungsgate."""
        feature_window = int(
            max(self.p["door_window_seconds"], self.p["door_humidity_seconds"])
            / self.grid_seconds
        )
        window = list(self.frames[position])[
            -int(self.p["vent_baseline_seconds"] / self.grid_seconds) - feature_window - 1 : -feature_window
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

    def _new_door_episode(self, channels, now, beginning):
        """Am ersten Kurvenkandidaten Rollen, Quellen und Beginn festhalten."""
        return {
            "started_at": beginning,
            "first_observed_at": now,
            "positions": tuple(channels),
            "sources": {
                position: (
                    self.frames[position][-1].get("Ts"),
                    self.frames[position][-1].get("Hs"),
                )
                for position in channels
            },
            "epochs": {
                position: tuple(
                    self._curve_series[position, quantity]["epoch"]
                    for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY)
                ) for position in channels
            },
            "opening_humidity": False,
            "temperatures": {
                position: max((
                    frame["Tm"]
                    for frame in list(self.frames[position])[-int(self.p["door_window_seconds"] / self.grid_seconds) - 1:]
                    if frame["Tm"] is not None
                    and frame.get("Ts") == self.frames[position][-1].get("Ts")
                ), default=None)
                for position in channels
            },
            "invalid": set(),
        }

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

    def advance(
        self, at, *, enabled, allowed=None, on_detection=None,
        heating_intervals=None, heating_after=None, heating_gates=None,
        recognition_context=None, allowed_at=None,
        before_sample=None, after_sample=None, include_current=True, detected_at=None,
        onset_allowed=None, on_infusion_update=None,
    ):
        """Laufzeitkontext vor jeder Prüfung lesen; Ereignisse sofort zurückmelden.

        Ohne Kontext bleibt der reine Messvergleich zum Referenzkandidaten möglich.
        Im Betrieb liefert ausschließlich der Controller die Erkennungsfreigaben.
        Übergebene Heizintervalle gelten an ihrer jeweiligen Rasterzeit;
        Die zur Eingangszeit gebuchten Laufzeit-Gates begrenzen sie zusätzlich.
        heating_after bleibt für reine Detektoraufrufe ohne Gate-Historie.
        recognition_context liest bereits gebuchte Controllerfreigaben je Raster;
        die aktuelle allowed-Freigabe gilt zusätzlich zwischen den Signalen.
        Runtime-Hooks buchen den führenden Controller vor/nach jedem Raster;
        False vor einer Probe beendet die Zustellung einer abgeschlossenen Session.
        include_current=False lässt das Raster für gleichzeitige Eingänge offen.
        detected_at kann die reale Erkennungsuhr liefern, getrennt vom Rasterende.
        onset_allowed prüft allein die frühere Zuordnung; die Bestätigung bleibt
        bei einer Ablehnung mit ihrem bisherigen Ereignisbeginn bestehen.
        on_infusion_update erhält Endübergänge nach den Ereignissen und vor
        after_sample; None in einem Update widerruft einen früheren Endbeleg.
        """
        at = utc(at)
        final = int((at - self.origin).total_seconds() // self.grid_seconds)
        if not include_current and self.origin + timedelta(seconds=final * self.grid_seconds) == at:
            final -= 1
        if final < self.index:
            if at < self.origin + timedelta(seconds=self.index * self.grid_seconds):
                raise ValueError("Detektoruhr darf nicht rückwärts laufen")
            return []
        output = []
        while self.index < final:
            now = self.origin + timedelta(seconds=(self.index + 1) * self.grid_seconds)
            if before_sample is not None and before_sample(now) is False:
                break
            self.index += 1
            sample_enabled, sample_allowed = enabled, allowed
            gang_permitted, recognition_since = True, None
            operation_restarted = False
            if recognition_context is not None:
                sample_enabled, blocked, recognition_since = recognition_context(now)
                gang_permitted = blocked is None
                gate_enabled, gate_at = sample_enabled, recognition_since
                previous_sample_at = now - timedelta(seconds=self.grid_seconds)
                while gate_at is not None and gate_at > previous_sample_at:
                    previous_enabled, _, previous_gate_at = recognition_context(
                        gate_at - timedelta.resolution
                    )
                    if gate_enabled and not previous_enabled:
                        operation_restarted = True
                        break
                    if previous_gate_at is not None and previous_gate_at >= gate_at:
                        break
                    gate_enabled, gate_at = previous_enabled, previous_gate_at

                def sample_allowed(kind):
                    return (
                        blocked is None and (allowed is None or allowed(kind))
                        and (allowed_at is None or allowed_at(kind, now))
                    )

            if heating_intervals is not None:
                intervals = (heating_intervals() if callable(heating_intervals)
                             else heating_intervals)
                interval = next((
                    interval for interval in reversed(intervals)
                    if interval.started_at <= now
                    and (interval.ended_at is None or now < interval.ended_at)
                ), None)
                since = interval.started_at if interval is not None else None
                if since is not None and heating_gates is not None:
                    permitted, permitted_since = False, None
                    for gate_at, gate in heating_gates:
                        if gate_at > now:
                            break
                        if gate and not permitted:
                            permitted_since = gate_at
                        elif not gate:
                            permitted_since = None
                        permitted = gate
                    if not permitted:
                        since = None
                    else:
                        # OFF/ON can interrupt continuous physical heating.
                        since = max(since, permitted_since)
                elif since is not None and heating_after is not None:
                    since = max(since, heating_after)
                if recognition_context is not None:
                    if not sample_enabled:
                        since = None
                    elif since is not None and recognition_since is not None:
                        # Received OFF/ON remains an interruption even when a
                        # completed service has already booked a later heat gate.
                        since = max(since, recognition_since)
                if since is not None and since > now:
                    since = None
                if since != self.heating_since:
                    # An interruption may lie entirely between raster points.
                    # The canonical interval start still invalidates old proof.
                    self.report_heating(False, now)
                    if since is not None:
                        self.report_heating(True, since)
            self._consume(now)
            output.extend(self._sample(
                now, utc(detected_at() if callable(detected_at) else detected_at or at),
                sample_enabled, sample_allowed, on_detection,
                gang_permitted=gang_permitted, recognition_since=recognition_since,
                onset_allowed=onset_allowed,
                operation_restarted=operation_restarted,
            ))
            while self._infusion_updates:
                if on_infusion_update is not None:
                    on_infusion_update(self._infusion_updates[0])
                self._infusion_updates.popleft()
            if after_sample is not None:
                after_sample(now)
        return output

    def _sample(
        self, now, decision_at, enabled, allowed=None, on_detection=None,
        *, gang_permitted=True, recognition_since=None, onset_allowed=None,
        operation_restarted=False,
    ):
        p = self.p
        prefixes = tuple(self._original_prefixes)
        self._original_prefixes.clear()
        prefix_overflow = self._original_prefix_overflow
        self._original_prefix_overflow = False
        if prefix_overflow:
            for position in self._original_prefix_overflow_positions or self.positions:
                state = self._position_state(position)
                state["infusion_candidate"] = None
                if state["infusion"] is not None:
                    state["infusion"]["original_background"] = None
                episode = state["door"]
                if episode is not None:
                    episode.pop("original_open", None)
                    episode.pop("original_close", None)
                    episode.pop("original_temperature_falls", None)
                    if not self.open:
                        episode.pop("original_ventilation_phase", None)
                    self._reset_original_ventilation(episode, "original_prefix_overflow")
            if self._infusion_owner is not None:
                self._original_infusion_candidate = self._position_state(self._infusion_owner)["infusion_candidate"]
        self._original_prefix_overflow_positions.clear()
        self._curve_fit_cache = {}
        self._curve_proposal_checks = {}
        if operation_restarted:
            self._finish_infusion_context("operation_restarted")
        if not enabled or not gang_permitted:
            # An operation-OFF stretch cannot leave a later weak-person opportunity.
            # Door observation itself continues without inventing an edge.
            self.weak_anchor_at = None
            if not enabled:
                self._finish_infusion_context("operation_off")
        elif (
            self.weak_anchor_at is not None
            and recognition_since is not None
            and self.weak_anchor_at < recognition_since
        ):
            # A complete OFF/ON can fall between raster points. Its booked
            # boundary still retires an entry anchor from before the pause.
            self.weak_anchor_at = None
        available, faults = [], []
        for position in self.positions:
            frames = self.frames[position]
            raw = {
                "T": self._value(position, Quantity.TEMPERATURE, now),
                "H": self._value(position, Quantity.HUMIDITY, now),
            }
            for name, quantity in (
                ("T", Quantity.TEMPERATURE), ("H", Quantity.HUMIDITY)
            ):
                if (raw[name] is None and self._curve_series[position, quantity]["points"]
                        and self._value_state(position, quantity, now)[1] == "invalid"):
                    self._clear_curve_series(position, quantity)
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
            for key, quantity in (
                ("T", Quantity.TEMPERATURE), ("H", Quantity.HUMIDITY)
            ):
                series = self._curve_series[position, quantity]
                frame[key + "o"] = (
                    series["points"][-1][0]
                    if raw[key] is not None and series["points"] else None
                )
                frame[key + "e"] = series["epoch"] if raw[key] is not None else None
            for key in ("T", "H"):
                median_samples = int(p["median_seconds"] / self.grid_seconds)
                last = (
                    [f[key] for f in list(frames)[-(median_samples - 1) :]]
                    if median_samples > 1
                    else []
                )
                values = last + [raw[key]]
                frame[key + "m"] = (
                    median(values)
                    if len(values) == median_samples
                    and all(v is not None for v in values)
                    else None
                )
            frames.append(frame)
            if valid:
                available.append(position)
            else:
                faults.append(position.value + "_unavailable")
        channels = tuple(available)
        for position in set(self.active_positions) - set(channels):
            self._clear_position_person(position)
        self.active_positions, self.faults = channels, tuple(faults)
        self._expire_held_curve_proposals(now)
        trace = {
            "at": now,
            "original_prefix_overflow": prefix_overflow,
            "channels": tuple(c.value for c in channels),
            "metrics": {c.value: {} for c in channels},
            "conditions": {},
            "checks": {},
            "decisions": {
                route: {"evaluated": False, "result": None, "gates": {},
                        "reason": "route_not_reached"}
                for route in (
                    "door_open", "door_open_heating", "door_close_temperature",
                    "door_close_mixed", "door_close_humidity", "door_close_smooth",
                    "ventilation", "ventilation_complete", "infusion", "infusion_rise", "infusion_decay",
                    "infusion_level", "infusion_background", "strong", "weak",
                )
            },
        }
        output = []

        def observe():
            curve_candidates = {}
            for (route, position, quantity), status in self._curve_status.items():
                curve_candidates.setdefault(route, {}).setdefault(position.value, {})[
                    quantity.value
                ] = dict(status)
            curve_proposals = {}
            for (route, _, _), status in self._curve_proposal_status.items():
                curve_proposals.setdefault(route, []).append(dict(status))
            proposal_checks = {}
            for (route, positions, quantities), check in self._curve_proposal_checks.items():
                proposal_checks.setdefault(route, {}).setdefault(
                    ",".join(position.value for position in positions), {},
                )[",".join(quantity.value for quantity in quantities)] = deepcopy(check)
            _decision(
                trace, "curve_proposal_checks", None, proposal_checks,
                evaluated=bool(proposal_checks),
            )
            trace.update(
                holds=dict(self.counts),
                signals=tuple(d.kind for d in output),
                door_open=self.open,
                ventilation_context=self.ventilated,
                curve_candidates=curve_candidates,
                curve_proposals=curve_proposals,
                infusion_episode=(
                    {
                        "phase": self._infusion_episode["phase"],
                        "onset_at": self._infusion_episode["onset_at"],
                        "level_end_at": self._infusion_episode["level_end_at"],
                        "end_basis": self._infusion_episode["end_basis"],
                        "interrupted": bool(self._infusion_episode["interrupted"]),
                        "confirmed_at": self._infusion_episode["confirmed_at"],
                        "confirmation_basis": self._infusion_episode["confirmation_basis"],
                        "water_change": {
                            position.value: witness["change"]
                            for position, witness in self._infusion_episode["water"].items()
                        },
                        "decays": {
                            position.value: candidate.onset_at for position, candidate
                            in self._infusion_episode["decays"].items()
                        },
                        "levels": {
                            position.value: {
                                "first_newest_at": level["first_newest_at"],
                                "fresh_newest_at": level["fresh_newest_at"],
                                "newest_at": level["features"].newest_at,
                                "start_at": level["features"].start_at,
                            }
                            for position, level in self._infusion_episode["levels"].items()
                        },
                    } if self._infusion_episode is not None else None
                ),
            )
            self.diagnostic = trace
            if self.observer:
                self.observer(trace)

        def emit(kind, effective_at=None, event_channels=None, *, door_close_evidence=None):
            detection = Detection(
                kind,
                effective_at or now,
                decision_at,
                tuple(c.value for c in (event_channels or channels)),
                now,
                door_close_evidence,
            )
            output.append(detection)
            if kind == Kind.INFUSION:
                self._infusion_episode["confirmed_detection"] = detection
            if on_detection:
                on_detection(detection)

        curve_route = "door_close" if self.open else "door_open"
        self._clear_curve_route("door_open" if self.open else "door_close")
        if self.open:
            self._clear_curve_route("door_open_heating")
        curve_positions = self.positions
        curve_floor = (
            self._curve_open_effective_at or self.opened_at or self.origin
            if self.open else self._curve_closed_since
        )
        if self.open and self._curve_ventilation_at is not None:
            curve_floor = max(curve_floor, self._curve_ventilation_at)
        for position in curve_positions:
            for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY):
                self._curve_candidate(
                    curve_route, position, quantity, now, decision_at, curve_floor,
                    (self._curve_door_context, curve_floor),
                )
            if not self.open and enabled and self.heating_since is not None:
                heating_floor = max(
                    curve_floor, self.heating_since,
                    self._heating_rejection_floor(position) or self.heating_since,
                )
                self._curve_candidate(
                    "door_open_heating", position, Quantity.TEMPERATURE,
                    now, decision_at, heating_floor,
                    (self._curve_door_context, self.heating_since),
                    points_not_before=max(
                        self.heating_since,
                        self._heating_rejection_floor(position) or self.heating_since,
                    ),
                )

        # Rastermerkmale bleiben Diagnose; OLS-Originalformen entscheiden.
        for c in channels:
            trace["metrics"][c.value].update(
                door_temperature_slope=self._slope(c, "Tm", p["door_window_seconds"]),
                door_humidity_delta=(self._difference(c, "Hm", p["door_humidity_seconds"])
                                     if not self.open else None),
            )
        mixed_toggle, closing, close_toggle, closing_positions = self._original_door_evidence(
            prefixes, channels, now, decision_at, trace, emit,
        )
        # Der vorhandene bedingte Heiz-Sonderweg behält seine eigene
        # kalibrierte Form und Zulassung im selben Türbestand.
        thermal_opening, thermal_toggle = self._heating_position_evidence(
            prefixes, now, decision_at, enabled, curve_floor, mixed_toggle, trace,
        )

        episode = self.door_episode
        mixed_opening = bool(episode and episode.get("original_open")) if not self.open else None
        closing_quantities = (Quantity.TEMPERATURE,)
        if mixed_toggle or close_toggle:
            route = "door_close" if self.open else "door_open"
            positions = closing_positions if self.open else episode["positions"]
            self._remember_curve_proposal(
                route, positions, closing_quantities if self.open else
                (Quantity.TEMPERATURE, Quantity.HUMIDITY), floor=curve_floor,
                context=(self._curve_door_context, curve_floor), observed_at=decision_at,
            )
        if episode and not self.open:
            for position in episode["positions"]:
                reference = episode["temperatures"][position]
                current = self.frames[position][-1]["Tm"]
                trace["metrics"].setdefault(position.value, {})["door_temperature_loss"] = (
                    reference - current if reference is not None and current is not None
                    else None
                )
        trace["checks"].update(door_open=not self.open, door_close=self.open)
        for route in ("door_open", "door_open_heating"):
            if self.open or episode is None:
                _decision(trace, route, None if self.open else False, {
                    "door_closed": _gate(not self.open),
                    "opening_episode": _gate(episode is not None),
                }, evaluated=not self.open,
                          reason="door_already_open" if self.open else
                          "no_supported_opening_candidate")
        for route in (
            "door_close_temperature", "door_close_mixed",
            "door_close_humidity", "door_close_smooth",
        ):
            if not self.open or episode is None:
                _decision(trace, route, None, {
                    "door_open": _gate(self.open),
                    "opening_episode": _gate(episode is not None),
                }, evaluated=False,
                          reason="door_closed" if not self.open else "opening_episode_missing")
        if trace["decisions"]["ventilation"].get("reason") == "route_not_reached":
            _decision(trace, "ventilation", None, {}, evaluated=False, reason=(
                "door_closed" if not self.open else
                "already_proven_for_opening" if self.ventilated else
                "door_close_takes_precedence" if close_toggle else
                "awaiting_original_water_form"
            ))
        trace["conditions"].update(
            door_open=mixed_opening if not self.open else None,
            door_heating=thermal_opening if not self.open else None,
            door_close=closing if self.open else None,
        )
        # Erholung beendet zuerst die kurze Öffnung. Ein gerade am selben Raster
        # sichtbarer Verlust darf sie nicht nachträglich zur Lüftung machen.
        if self.open and close_toggle:
            close_floor = self._curve_open_effective_at or self.opened_at or self.origin
            if self._curve_ventilation_at is not None:
                close_floor = max(close_floor, self._curve_ventilation_at)
            close_effective = self._curve_beginning(
                "door_close", closing_positions,
                closing_quantities, now,
                floor=close_floor,
                kind=Kind.DOOR_CLOSE, now=now, decision_at=decision_at,
                onset_allowed=onset_allowed,
                boundary_reason=(
                    "ventilation_after_onset" if self._curve_ventilation_at is not None
                    else "outside_door_episode"
                ),
                # Native confirmation gives onset authority only to its own
                # bound T forms. An unrelated available proposal is not one.
                bound_candidates=episode.get("closing_onset_witnesses", ()),
            )
            self.open = False
            self.closed_at = now
            self.weak_anchor_at = now if enabled and gang_permitted else None
            self.counts["door"] = 0
            self.counts["door_heating"] = 0
            self._curve_close_effective_at = close_effective
            self._curve_closed_since = now
            self._curve_door_context += 1
            self._clear_curve_route("door_close", keep_status=True)
            emit(Kind.DOOR_CLOSE, close_effective, closing_positions,
                 door_close_evidence=episode.get("closing_evidence"))
            self.baseline = {}
            self.ventilation_degraded = False
            self.ventilation_positions = ()
            self.ventilation_invalid = set()
            self.door_episode = None
            self._reset_position_doors()
        elif self.open and closing:
            # Den aktuellen Schließkandidaten weiter diagnostizieren; die
            # Lüftungsentscheidung ist bereits präfixkausal erfolgt.
            trace["conditions"]["ventilation_recovery"] = True
        elif not self.open and (mixed_toggle or thermal_toggle):
            onset_route = "door_open" if mixed_toggle else "door_open_heating"
            onset_quantities = ((Quantity.HUMIDITY,) if mixed_toggle else
                                (Quantity.TEMPERATURE,))
            open_floor = self._curve_closed_since
            if onset_route == "door_open_heating" and self.heating_since is not None:
                open_floor = max(open_floor, self.heating_since)
            open_effective = self._curve_beginning(
                onset_route, episode["positions"], onset_quantities,
                now, floor=open_floor,
                kind=Kind.DOOR_OPEN, now=now, decision_at=decision_at,
                onset_allowed=onset_allowed,
                bound_candidates=(self._localize_original_opening(episode, trace)
                                  if mixed_toggle else None),
            )
            onset_status = self._curve_proposal_status.get(
                (onset_route, episode["positions"], onset_quantities), {},
            )
            trace["door_open_onset_attribution"] = {
                "confirmation_basis": "original_ols" if mixed_toggle else "retained_heating_only",
                "route": onset_route, "quantities": tuple(q.value for q in onset_quantities),
                "thermal_fallback": False,
                "attribution_basis": ("original_open_binding" if mixed_toggle else "curve_proposal"),
                "bound_original_onsets": tuple(
                    (key[1].value, key[2].value, witness.onset_at, witness.candidate_id)
                    for key, witness in episode["original_open"]["onset_witnesses"]
                ) if mixed_toggle else (),
                "used_proposal": (onset_status.get("status") == "consumed"
                                  and onset_status.get("confirmed_at") == decision_at),
                "effective_at": open_effective,
            }
            self.open = True
            self._door_published_stages.clear()
            episode["event_identity"] = {"effective_at": open_effective,
                                         "positions": episode["positions"],
                                         "sources": dict(episode["sources"]),
                                         "epochs": dict(episode["epochs"])}
            self._bind_opening_identity(episode, open_effective)
            episode["opening_humidity"] = bool(mixed_toggle)
            episode["started_at"] = open_effective
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
            self._curve_open_effective_at = open_effective
            self._curve_close_effective_at = None
            self._curve_ventilation_at = None
            self._curve_door_context += 1
            self._clear_curve_route("door_open", keep_status=True)
            self._clear_curve_route("door_open_heating", keep_status=True)
            infusion = self._infusion_episode
            infusion_detection = infusion.get("confirmed_detection") if infusion is not None else None
            if (infusion is not None and infusion["phase"] == "active"
                    and infusion_detection is not None):
                if open_effective >= infusion_detection.effective_at:
                    self._update_infusion_end(infusion, InfusionEnd(
                        open_effective, decision_at, now, "door_open", "physical_effective",
                    ))
                else:
                    trace["infusion_end_conflict"] = {
                        "reason": "door_before_infusion_onset",
                        "door_effective_at": open_effective,
                        "infusion_effective_at": infusion_detection.effective_at,
                    }
            self._finish_infusion_context("door_open")
            emit(Kind.DOOR_OPEN, open_effective, episode["positions"])
        elif not self.open and episode:
            expiry = p["curve_window_seconds"]
            if (
                (now - episode["first_observed_at"]).total_seconds() > expiry
                and not (mixed_opening or thermal_opening)
            ):
                self._clear_curve_route(
                    "door_open", keep_status=True, reason="door_episode_expired",
                )
                self._clear_curve_route(
                    "door_open_heating", keep_status=True, reason="door_episode_expired",
                )
                self.door_episode = None
                self.counts["door"] = 0
                self.counts["door_heating"] = 0
                self.baseline = {}
                self.ventilation_positions = ()
                self.ventilation_invalid = set()
        if not channels:
            if self._infusion_episode is not None and self._infusion_episode["phase"] == "active":
                self._original_infusion_evidence(
                    prefixes, channels, now, decision_at,
                    self._curve_infusion_since or self.origin, trace, onset_allowed, emit, allowed,
                )
            self._update_moisture_state(None, now)
            self._curve_infusion_since = None
            self._clear_curve_route("infusion", reason="unavailable")
            # Aktuelle Messwerte fehlen. Native historische Bindungen bleiben
            # bestehen; ihre Fortsetzung wird an neuen Originalen entschieden.
            for route in (
                "infusion", "infusion_rise", "infusion_decay", "infusion_level",
                "infusion_background", "strong", "weak",
            ):
                _decision(trace, route, None, {}, evaluated=False,
                          reason="no_valid_temperature_humidity_position")
            observe()
            return output

        def admitted_window(route):
            # Every contributing smoothed frame must belong to the current
            # permitted stretch. Old blocked rises cannot cross its boundary.
            return recognition_since is None or (
                (now - recognition_since).total_seconds()
                >= p[f"{route}_window_seconds"] + p["median_seconds"] - self.grid_seconds
            )

        eligible = bool(enabled and gang_permitted and not self.open)
        infusion_window_admitted = admitted_window("infusion")
        infusion_allowed = None
        infusion_check = (
            eligible and infusion_window_admitted
            and (allowed is None or (infusion_allowed := allowed(Kind.INFUSION)))
        )
        infusion_gates = {
            "operation_enabled": _gate(bool(enabled)),
            "gang_permitted": _gate(bool(gang_permitted)),
            "door_closed": _gate(not self.open),
            "admission_window": _gate(
                infusion_window_admitted,
                value=(now - recognition_since).total_seconds()
                if recognition_since is not None else None,
                operator=">=", threshold=(
                    p["infusion_window_seconds"] + p["median_seconds"] - self.grid_seconds
                ), unit="s", reference="current_recognition_stretch",
                reason="no_restricted_recognition_stretch" if recognition_since is None else None,
            ),
            "controller_admission": _gate(
                True if allowed is None else infusion_allowed,
                reason="preceding_admission_gate_failed"
                if allowed is not None and infusion_allowed is None else None,
            ),
        }
        _decision(trace, "infusion", False if infusion_check else None, infusion_gates,
                  evaluated=bool(infusion_check),
                  reason=None if infusion_check else "infusion_admission_blocked")
        phase = self._infusion_episode["phase"] if self._infusion_episode is not None else "none"
        for route in ("infusion_rise", "infusion_decay", "infusion_level", "infusion_background"):
            _decision(trace, route, None, {}, evaluated=False, reason=(
                "infusion_admission_blocked" if not infusion_check else
                f"current_infusion_episode_{phase}"
            ))
        trace["checks"]["infusion"] = infusion_check
        if infusion_check:
            if self._curve_infusion_since is None:
                self._curve_infusion_since = now
            infusion_floor = max(
                self._curve_infusion_since,
                self._curve_close_effective_at or self.origin,
                recognition_since or self.origin,
            )
        else:
            self._curve_infusion_since = None
            self._clear_curve_route("infusion", reason="admission_context")
            self._pause_infusion_episode()
        water_rise = infusion_check
        water_gates = {}
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
                water_gates[c.value] = _gate(
                    da > 0 if da is not None else None,
                    value=da, operator=">", threshold=0.0, unit="g/m³",
                    reference="coherent_infusion_window_water_change",
                    reason="water_window_unavailable" if da is None else None,
                )
                water_rise &= bool(water_gates[c.value]["result"])
        trace["conditions"]["infusion"] = False if infusion_check else None
        if infusion_check:
            context = (self._curve_door_context, infusion_floor)
            # Alte Formkandidaten dienen ausschließlich belastbarer früherer
            # Zuordnung. Ihre BIC-/M1-Bestätigung ist kein OLS-Zulassungsfilter.
            for position in channels:
                self._curve_candidate(
                    "infusion", position, Quantity.HUMIDITY,
                    now, decision_at, infusion_floor, context,
                    points_not_before=self._infusion_segment_start.get(position),
                )
            self._original_infusion_evidence(
                prefixes, channels, now, decision_at, infusion_floor, trace,
                onset_allowed, emit, allowed,
            )
        # In operation the controller owns entry and its admission window.
        # A cold entry may wait for the minimum temperature beyond the original
        # closure window. The standalone measurement replay has no controller.
        weak_opportunity = allowed(Kind.PERSON_WEAK) if allowed is not None else (
            self.weak_anchor_at is not None
            and now
            <= self.weak_anchor_at + timedelta(minutes=p["confirmation_minutes"])
        )
        if not weak_opportunity:
            self.counts["weak"] = 0
            self.levels["weak"] = False
        for route in ("strong", "weak"):
            _decision(trace, route, None, {}, evaluated=False, reason="outside_person_step")
        if (self.index * self.grid_seconds) % p["person_step_seconds"] == 0:
            for route, kind in (
                ("strong", Kind.PERSON_STRONG),
                ("weak", Kind.PERSON_WEAK),
            ):
                window_admitted = admitted_window(route)
                controller_allowed = None
                checking = (
                    eligible
                    and window_admitted
                    and (route != "weak" or weak_opportunity)
                    and (allowed is None or (controller_allowed := allowed(kind)))
                )
                trace["checks"][route] = checking
                # Schwache Signale behalten ihre Feuchteflanke auch dann, wenn
                # der Kontext ihre Ausgabe gerade sperrt. Starke Signale und
                # deren Diagnose werden nur für tatsächlich prüfbare Routen
                # berechnet.
                moisture_features = {
                    c: (
                        self._slope(c, "Hm", p[f"{route}_window_seconds"],
                                    p["person_step_seconds"]),
                        self._absolute_humidity_difference(
                            c, p[f"{route}_window_seconds"], p["person_step_seconds"]
                        ),
                    )
                    for c in channels
                } if checking or route == "weak" else {}
                (person_position, moisture_rise, moisture_state, fresh_weak,
                 condition, sustained, edge, moisture_gates) = self._person_position_evidence(
                    route, moisture_features, checking, now, trace,
                )
                if checking:
                    for c in channels:
                        humidity_slope, absolute_change = moisture_features[c]
                        trace["metrics"][c.value].update({
                            f"{route}_temperature_slope": self._slope(
                                c, "Tm", p[f"{route}_window_seconds"],
                                p["person_step_seconds"],
                            ),
                            f"{route}_humidity_slope": humidity_slope,
                            f"{route}_absolute_humidity_delta": absolute_change,
                        })
                trace["conditions"][route] = condition if checking else None
                moisture_gates.update(
                    operation_enabled=_gate(bool(enabled)),
                    gang_permitted=_gate(bool(gang_permitted)),
                    door_closed=_gate(not self.open),
                    admission_window=_gate(
                        window_admitted,
                        value=(now - recognition_since).total_seconds()
                        if recognition_since is not None else None,
                        operator=">=", threshold=(
                            p[f"{route}_window_seconds"] + p["median_seconds"] - self.grid_seconds
                        ), unit="s", reference="current_recognition_stretch",
                        reason="no_restricted_recognition_stretch"
                        if recognition_since is None else None,
                    ),
                    controller_admission=_gate(
                        True if allowed is None else controller_allowed,
                        reason="preceding_admission_gate_failed"
                        if allowed is not None and controller_allowed is None else None,
                    ),
                    weak_opportunity=_gate(
                        weak_opportunity if route == "weak" else None,
                        reason="not_required_for_strong" if route != "weak" else None,
                    ),
                    fresh_weak_onset=_gate(
                        fresh_weak if route == "weak" else None,
                        value=moisture_state["onset_at"] if moisture_state else None,
                        operator=">=", threshold=self.opened_at, unit="s",
                        reference="last_confirmed_opening",
                        reason="not_required_for_strong" if route != "weak" else
                        "weak_onset_or_opening_unavailable" if (
                            moisture_state["onset_at"] is None or self.opened_at is None
                        ) else None,
                    ),
                    sustained=_gate(
                        sustained, value=self.counts[route], operator=">=",
                        threshold=ceil(p[f"{route}_hold_seconds"] / p["person_step_seconds"]) + 1,
                        unit="checks", reference="consecutive_person_checks",
                    ),
                    rising_signal_edge=_gate(edge),
                )
                _decision(trace, route, edge, moisture_gates, evaluated=bool(checking),
                          reason=None if checking else "person_admission_blocked")
                if edge:
                    emit(kind, event_channels=(person_position,))
        observe()
        return output
