"""Kausaler laufender Tür-/Gangdetektor ohne HA, NumPy oder Recorderwissen."""

from collections import deque
from dataclasses import dataclass, replace
from datetime import timedelta
from hashlib import sha256
from math import ceil, isclose, isfinite
from statistics import median

from .curve_change import CurveFeatures, CurvePolicy, curve_change, curve_level, curve_trend
from .models import Measurement, Position, Quantity
from .moisture import KELVIN_OFFSET_C, absolute_humidity
from .parameters import BY_KEY
from .timeline import Kind, utc


@dataclass(frozen=True)
class Detection:
    kind: Kind
    effective_at: object
    detected_at: object
    channels: tuple[str, ...]
    trace_at: object | None = None


@dataclass(frozen=True)
class _CurveCandidate:
    """Vorläufiger Kurvenbeginn bis zur bestehenden Ereignisbestätigung."""

    candidate_id: str
    onset_at: object
    first_observed_at: object
    context: tuple
    features: CurveFeatures
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
        self._curve_fit_cache = {}
        self._curve_recovery_evidence = {}
        self._curve_door_context = 0
        self._curve_closed_since = self.origin
        self._curve_open_effective_at = None
        self._curve_close_effective_at = None
        self._curve_ventilation_at = None
        self._curve_infusion_since = None
        self._infusion_episode = None
        self._infusion_segment_start = {}

    def _clear_curve_series(self, position, quantity):
        series = self._curve_series[position, quantity]
        series["points"].clear()
        series.update(source=None, time_basis=None, started_at=None, received_at=None)
        series["version"] += 1
        series["last_original_version"] = None
        series["epoch"] += 1
        if quantity == Quantity.TEMPERATURE:
            self._curve_recovery_evidence.pop(position, None)
        episode = self._infusion_episode
        if (
            episode is not None and episode["phase"] == "pending"
            and position in episode["positions"]
        ):
            self._finish_infusion_context("pending_source_context")
            episode = None
        if (
            episode is not None and episode["phase"] == "active"
            and position in episode["positions"]
        ):
            episode["interrupted"] = True
            episode["context"] = None
            episode["decays"].clear()
            episode["levels"].clear()
            episode["level_evaluated"].clear()
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

    def _record_curve_sample(self, measurement, now):
        """Originale einmal zählen; Geräte- und Empfangsuhr nicht vermischen."""
        position, quantity = measurement.position, measurement.quantity
        measured = measurement.measured_at
        time_basis = (
            "measured" if measured is not None and measured <= measurement.received_at
            else "received"
        )
        point_at = measured if time_basis == "measured" else measurement.received_at
        if self._value(position, quantity, now) is None:
            self._correct_curve_proposals(position, quantity, point_at, measurement.value)
            self._correct_infusion_tail(position, quantity, point_at, measurement.value)
            self._clear_curve_series(position, quantity)
            return
        series = self._curve_series[position, quantity]
        timeout = self.p["sensor_timeout_seconds"]
        if series["points"] and (
            series["source"] != measurement.source
            or series["time_basis"] != time_basis
            or point_at < series["points"][-1][0]
            or (point_at - series["points"][-1][0]).total_seconds() > timeout
            or (measurement.received_at - series["received_at"]).total_seconds() > timeout
        ):
            self._clear_curve_series(position, quantity)
        points = series["points"]
        corrected = bool(points and points[-1][0] == point_at)
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
            self._correct_curve_proposals(position, quantity, point_at, measurement.value)
            self._correct_infusion_tail(position, quantity, point_at, measurement.value)
        else:
            series["last_original_version"] = series["version"]

    def _clear_curve_route(self, route, *, keep_status=False, reason=None):
        if reason is not None:
            self._invalidate_curve_proposals(reason, route=route)
        mappings = (
            self._curve_candidates, self._curve_evaluated, self._curve_proposals,
        )
        if not keep_status:
            mappings += (self._curve_status,)
            if reason is None:
                mappings += (self._curve_proposal_status,)
        for mapping in mappings:
            for key in tuple(mapping):
                if key[0] == route:
                    mapping.pop(key, None)

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
        window_start = now - timedelta(seconds=self.p["curve_window_seconds"])
        points = tuple(
            point for point in series["points"]
            if window_start <= point[0] <= now
            and (points_not_before is None or point[0] >= points_not_before)
        )
        if candidate is not None and not any(
            point[0] == candidate.onset_at for point in points
        ):
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
        onset_at = candidate.onset_at if candidate is not None else None
        curve_direction = -1 if route in (
            "door_open", "door_open_heating", "infusion_decay",
        ) else 1
        fit_key = (position, quantity, points, onset_at, curve_direction, floor)
        if fit_key not in self._curve_fit_cache:
            self._curve_fit_cache[fit_key] = curve_change(
                points, policy=self.curve_policy, onset_at=onset_at,
                direction=curve_direction, onset_not_before=floor,
            )
        features = self._curve_fit_cache[fit_key]
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
        }
        if features is not None:
            supported = self._curve_supports(route, features, floor, quantity)
            direction_missing = (
                features.evidence == "supports" and features.after_trend is None
                and route != "door_close"
            )
            status.update(
                status=("supported" if supported else
                        "insufficient" if direction_missing else "rejected"),
                onset_at=features.onset_at,
                newest_at=features.newest_at,
                before_count=features.before_count,
                after_count=features.after_count,
                noise_scale=features.noise_scale,
                slope_before=features.slope_before,
                slope_after=features.slope_after,
                slope_change=features.slope_change,
                signed_strength=features.signed_strength,
                score_gain=features.score_gain,
                smooth_model=features.smooth_model,
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
                    f"{epochs}:{features.onset_at.isoformat()}:{first_observed_at.isoformat()}"
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
                    candidate_id, features.onset_at, first_observed_at,
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

    @staticmethod
    def _curve_supports(route, features, floor, quantity=None):
        if features is None:
            return False
        after_direction = (
            features.after_trend.direction if features.after_trend is not None else None
        )
        direction = (
            features.slope_change > 0
            and (quantity == Quantity.HUMIDITY or features.slope_before < 0)
            if route == "door_close"
            else features.slope_change < 0
            and features.slope_before > 0 and after_direction == -1
            if route == "infusion_decay"
            else features.slope_change < 0 and after_direction == -1
            if route in ("door_open", "door_open_heating")
            else features.slope_change > 0 and after_direction == 1
        )
        return features.evidence == "supports" and direction and features.onset_at >= floor

    def _curve_coherence_window(self, route):
        return (
            self.p["infusion_window_seconds"] if route.startswith("infusion")
            else max(self.p["door_window_seconds"], self.p["door_humidity_seconds"])
        )

    def _temperature_recovery(self, position, candidate, now):
        """Denselben aktuellen Originaltrend für beide Erholungsgates verwenden.

        Der alte Knick bleibt am festen Beginn gebunden. Nur ein neuer Original-
        oder Korrekturstand veranlasst die Auswertung des jüngsten festen Fensters;
        Rasterzeit allein verschiebt keine Evidenz. Eine Korrektur darf einen
        gerichteten Nachweis verwerfen, aber keinen neuen Originalbeleg erwerben.
        Verlässt eine tragende Probe das aktuelle Fenster, verfällt die Freigabe
        ohne Neuberechnung; auch eine Korrektur kann sie dann nicht erneuern.
        """
        series = self._curve_series[position, Quantity.TEMPERATURE]
        evaluation = (candidate.candidate_id, series["version"])
        previous = self._curve_recovery_evidence.get(position)
        start = max(candidate.onset_at, now - timedelta(
            seconds=self.p["door_window_seconds"]
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
            original_supported = directed and (
                series["last_original_version"] == series["version"]
                or (same_candidate and previous["original_supported"]
                    and within_window(previous))
            )
            previous = {
                "evaluation": evaluation, "trend": trend,
                "original_supported": original_supported,
                "start_at": points[0][0] if points else None,
                "newest_at": points[-1][0] if points else None,
            }
            self._curve_recovery_evidence[position] = previous
        trend = previous["trend"]
        current_supported = previous["original_supported"] and within_window(previous)
        key = ("door_close", position, Quantity.TEMPERATURE)
        self._curve_status[key] = {
            **self._curve_status.get(key, {}),
            "recovery_trend": {
                "start_at": previous["start_at"], "newest_at": previous["newest_at"],
                "direction": trend.direction if trend is not None else None,
                "slope": trend.slope if trend is not None else None,
                "original_supported": current_supported,
                "within_window": within_window(previous),
            },
        }
        return current_supported

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
            "status": "ambiguous" if proposal.ambiguous else "available",
            "owner_context": proposal.context[0],
            "positions": tuple(position.value for position in positions),
            "quantities": tuple(quantity.value for quantity in quantities),
            "first_observed_at": proposal.first_observed_at,
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

    def _remember_curve_proposal(
        self, route, positions, quantities, *, floor, context, observed_at,
    ):
        """Nur einen zugleich vollständigen Satz als historischen Vorschlag halten."""
        if not positions:
            return
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
        for position in positions:
            for quantity in quantities:
                candidate_key = (route, position, quantity)
                candidate = self._curve_candidates.get(candidate_key)
                if (
                    candidate is None or candidate.onset_at < floor
                    or self._curve_status.get(candidate_key, {}).get("status") != "supported"
                ):
                    return
                candidates.append((candidate_key, candidate))
        onsets = [candidate.onset_at for _, candidate in candidates]
        window = self._curve_coherence_window(route)
        if (max(onsets) - min(onsets)).total_seconds() > window:
            return
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

    def _curve_confirmation(self, route, positions, quantities, *, floor, recovery=False):
        """Den historischen Satz mit neuen Originalen am festen Beginn bestätigen.

        Jeder Zeuge braucht einen erneuten unterstützenden Modellvergleich nach
        seinem ersten Befund. Ein Rasterpunkt oder die Korrektur derselben Probe
        erwirbt diesen Nachweis nicht. Asynchrone Zeugen behalten ihren eigenen
        ersten Befund; der letzte vollständige Satz startet keine neue Wartezeit.
        """
        key = (route, tuple(positions), tuple(quantities))
        proposal = self._curve_proposals.get(key)
        if proposal is None or proposal.ambiguous:
            return False
        confirmed = []
        new_original = False
        now = self.origin + timedelta(seconds=self.index * self.grid_seconds)
        for candidate_key, original in proposal.candidates:
            candidate = self._curve_candidates.get(candidate_key)
            status = self._curve_status.get(candidate_key, {})
            if original.onset_at < floor:
                self._invalidate_curve_proposal(key, "outside_current_context")
                return False
            if candidate is None or candidate.candidate_id != original.candidate_id:
                if status.get("status") in ("rejected", "expired", "supported"):
                    self._invalidate_curve_proposal(key, "shape_counterevidence")
                return False
            supported = (
                status.get("status") == "supported"
                and candidate.fresh_newest_at is not None
                and candidate.fresh_newest_at > original.first_newest_at
                and (not recovery or self._temperature_recovery(
                    candidate_key[1], candidate, now,
                ))
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
        return (
            bool(confirmed) and new_original
            and all(witness["supported"] for witness in confirmed)
        )

    def _pause_infusion_episode(self):
        """Ein unbekannter Verlauf bestätigt keinen noch unbelegten Puls."""
        episode = self._infusion_episode
        if episode is not None and episode["phase"] == "pending":
            self._finish_infusion_context("pending_admission_context")
            return
        if episode is not None and episode["phase"] == "active":
            episode["interrupted"] = True
            episode["context"] = None
            episode["decays"].clear()
            episode["levels"].clear()
            episode["level_evaluated"].clear()
        self._clear_curve_route("infusion_decay", reason="admission_context")

    def _finish_infusion_context(self, reason):
        """Eine tatsächliche neue Tür-/Betriebsfolge besitzt eigene Aufgüsse."""
        self._infusion_episode = None
        self._infusion_segment_start.clear()
        self._curve_infusion_since = None
        self._clear_curve_route("infusion", reason=reason)
        self._clear_curve_route("infusion_decay", reason=reason)

    def _start_infusion_episode(self, positions, beginning, now, *, rise_key, water):
        self._infusion_episode = {
            "phase": "pending",
            "positions": tuple(positions),
            "onset_at": beginning,
            "observed_at": now,
            "context": None,
            "decay_floor": beginning,
            "last_rise_at": beginning,
            "rise_ids": set(),
            "decays": {},
            "levels": {},
            "level_evaluated": {},
            "level_end_at": None,
            "interrupted": False,
            "end_basis": None,
            "rise_key": rise_key,
            "water": water,
            "confirmed_at": None,
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

    def _bind_pending_infusion(self, positions, floor, now):
        """Anstieg und damaligen Wasserzuwachs vor der späteren Spitze binden."""
        key = ("infusion", tuple(positions), (Quantity.HUMIDITY,))
        proposal = self._curve_proposals[key]
        water = {}
        need = int(self.p["infusion_window_seconds"] / self.grid_seconds) + 1
        median_samples = int(self.p["median_seconds"] / self.grid_seconds)
        for position in positions:
            frames = list(self.frames[position])
            if len(frames) < need + median_samples - 1:
                return False
            endpoints = []
            for end in (len(frames) - need, len(frames) - 1):
                supports = frames[end - median_samples + 1:end + 1]
                if any(cell.get(name) is None for cell in supports for name in ("To", "Ho")):
                    return False
                if any(
                    cell[key + "e"] != self._curve_series[position, quantity]["epoch"]
                    for cell in supports for key, quantity in (
                        ("T", Quantity.TEMPERATURE), ("H", Quantity.HUMIDITY)
                    )
                ):
                    return False
                endpoints.append(tuple({
                    name: cell[name]
                    for name in ("T", "H", "To", "Ho", "Te", "He", "Ts", "Hs")
                } for cell in supports))
            witness = {"endpoints": tuple(endpoints)}
            change = self._infusion_water_change(witness)
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
        episode = self._infusion_episode
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
                        episode["phase"] = "active"
                        episode["interrupted"] = True
                        episode["context"] = None
                        episode["decays"].clear()
                        episode["levels"].clear()
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
            features = curve_change(
                points, policy=self.curve_policy, onset_at=decay.onset_at,
                direction=-1, onset_not_before=decay.context[2],
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
        if invalid:
            if episode["phase"] == "pending":
                episode["decays"].clear()
                self._clear_curve_route("infusion_decay", reason="decay_original_corrected")
                return
            episode["phase"] = "active"
            episode["level_end_at"] = None
            episode["end_basis"] = None
            episode["levels"].clear()
            episode["level_evaluated"].clear()
            self._clear_curve_route("infusion", reason="level_original_corrected")

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
                episode["interrupted"] = True
            episode["context"] = context
            episode["decays"].clear()
            episode["levels"].clear()
            episode["level_evaluated"].clear()
            self._clear_curve_route("infusion_decay", reason="source_context")
        new_rises = []
        for position in positions if episode["phase"] == "active" else ():
            key = ("infusion", position, Quantity.HUMIDITY)
            candidate = self._curve_candidates.get(key)
            if (
                candidate is not None
                and self._curve_status.get(key, {}).get("status") == "supported"
                and candidate.candidate_id not in episode["rise_ids"]
            ):
                episode["rise_ids"].add(candidate.candidate_id)
                if candidate.onset_at > episode["last_rise_at"]:
                    new_rises.append(candidate.onset_at)
        if new_rises:
            episode["interrupted"] = False
            episode["last_rise_at"] = max(new_rises)
            episode["decay_floor"] = episode["last_rise_at"]
            episode["decays"].clear()
            episode["levels"].clear()
            episode["level_evaluated"].clear()
            self._clear_curve_route("infusion_decay", reason="continued_infusion")
        decay_floor = max(floor, episode["decay_floor"])
        for position in positions:
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
                episode["decays"].setdefault(position, candidate)
            if episode["phase"] == "pending":
                continue
            if position not in episode["decays"] and not episode["interrupted"]:
                continue
            series = self._curve_series[position, Quantity.HUMIDITY]
            if episode["level_evaluated"].get(position) == series["version"]:
                continue
            episode["level_evaluated"][position] = series["version"]
            tail_floor = (
                max(floor, *(
                    self._curve_series[position, quantity]["started_at"] or floor
                    for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY)
                )) if episode["interrupted"]
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
            if level is None or level.evidence != "supports":
                episode["levels"].pop(position, None)
                continue
            original_update = series["last_original_version"] == series["version"]
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
        if episode["phase"] == "pending":
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
        if level_complete:
            episode["phase"] = "ended"
            episode["level_end_at"] = now
            episode["end_basis"] = (
                "level_after_interruption" if episode["interrupted"] else "decay_and_level"
            )
            self._infusion_segment_start = {
                position: episode["levels"][position]["features"].start_at
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
                features = curve_change(
                    points, policy=self.curve_policy, onset_at=candidate.onset_at,
                    direction=-1 if key[0] in (
                        "door_open", "door_open_heating", "infusion_decay",
                    ) else 1,
                    onset_not_before=candidate.context[2],
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
    ):
        """Den passenden Kandidaten am allein führenden Bestätigungspfad binden."""
        proposal_key = (route, tuple(positions), tuple(quantities))
        proposal = self._curve_proposals.get(proposal_key)
        if proposal is None or proposal.ambiguous:
            return fallback
        candidates = proposal.candidates
        if any(candidate.onset_at < floor for _, candidate in candidates):
            self._invalidate_curve_proposal(proposal_key, boundary_reason)
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
        self._curve_proposal_status[proposal_key] = {
            **self._curve_proposal_status[proposal_key], "status": "consumed",
            "confirmed_at": decision_at, "consumed_onset_at": beginning,
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
                and old.source == m.source
                and m.measured_at is not None
                and old.measured_at is not None
                and m.measured_at < old.measured_at
            ):
                self.rejected += 1
                continue
            if old and old.source != m.source:
                self.frames[m.position].clear()
                self._clear_curve_series(m.position, m.quantity)
                if self.open and m.position in self.ventilation_positions:
                    self.ventilation_invalid.add(m.position)
                    self.ventilation_degraded = True
                else:
                    self.baseline.pop(m.position, None)
                self.counts.clear()
                self.levels.clear()
            self.latest[key] = m
            self._record_curve_sample(m, now)

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
        if quantity == Quantity.TEMPERATURE and not -KELVIN_OFFSET_C < m.value:
            return None
        return m.value

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

    def _moisture_rise(self, route, features):
        """RH-Schwelle und AH-Ausgleich beschreiben denselben Feuchteweg."""
        condition = True
        for position, (humidity_slope, absolute_change) in features.items():
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

    def _start_door_episode(self, channels, now, beginning):
        """Am ersten Kurvenkandidaten Rollen, Quellen und Beginn festhalten."""
        self.door_episode = {
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

    def advance(
        self, at, *, enabled, allowed=None, on_detection=None,
        heating_intervals=None, heating_after=None, heating_gates=None,
        recognition_context=None, allowed_at=None,
        before_sample=None, after_sample=None, include_current=True, detected_at=None,
        onset_allowed=None,
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
            if after_sample is not None:
                after_sample(now)
        return output

    def _sample(
        self, now, decision_at, enabled, allowed=None, on_detection=None,
        *, gang_permitted=True, recognition_since=None, onset_allowed=None,
        operation_restarted=False,
    ):
        p = self.p
        self._curve_fit_cache = {}
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
                if raw[name] is None and self._curve_series[position, quantity]["points"]:
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
        if channels != self.active_positions:
            # Kein Haltebeweis über eine Änderung der benutzten Quellen hinweg.
            if self.active_positions:
                self._update_moisture_state(None, now)
            self.counts.clear()
            self.levels.clear()
            episode = self._infusion_episode
            if not (
                episode is not None and episode["phase"] in ("pending", "active")
                and all(position in channels for position in episode["positions"])
            ):
                self._curve_infusion_since = None
                self._clear_curve_route("infusion", reason="positions_changed")
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
            curve_candidates = {}
            for (route, position, quantity), status in self._curve_status.items():
                curve_candidates.setdefault(route, {}).setdefault(position.value, {})[
                    quantity.value
                ] = dict(status)
            curve_proposals = {}
            for (route, _, _), status in self._curve_proposal_status.items():
                curve_proposals.setdefault(route, []).append(dict(status))
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
                        "interrupted": self._infusion_episode["interrupted"],
                        "confirmed_at": self._infusion_episode["confirmed_at"],
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

        def emit(kind, effective_at=None, event_channels=None):
            detection = Detection(
                kind,
                effective_at or now,
                decision_at,
                tuple(c.value for c in (event_channels or channels)),
                now,
            )
            output.append(detection)
            if on_detection:
                on_detection(detection)

        curve_route = "door_close" if self.open else "door_open"
        self._clear_curve_route("door_open" if self.open else "door_close")
        if self.open:
            self._clear_curve_route("door_open_heating")
        curve_positions = (
            self.door_episode["positions"] if self.door_episode
            else channels
        )
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
                heating_floor = max(curve_floor, self.heating_since)
                self._curve_candidate(
                    "door_open_heating", position, Quantity.TEMPERATURE,
                    now, decision_at, heating_floor,
                    (self._curve_door_context, self.heating_since),
                    points_not_before=self.heating_since,
                )

        # Die bisherigen Rastermerkmale bleiben Diagnosewerte. Die fachliche
        # Bestätigung stammt aus der erneut belegten Form am festen Beginn.
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
        def supported_curve(position, quantity, route=None):
            key = (route or curve_route, position, quantity)
            candidate = self._curve_candidates.get(key)
            return candidate if (
                candidate is not None
                and self._curve_status.get(key, {}).get("status") == "supported"
            ) else None

        beginnings = [
            candidate.onset_at
            for position in channels
            for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY)
            if (candidate := supported_curve(position, quantity)) is not None
        ]
        beginnings.extend(
            candidate.onset_at for position in channels
            if (candidate := supported_curve(
                position, Quantity.TEMPERATURE, "door_open_heating",
            )) is not None
        )
        if (
            not self.open
            and self.door_episode is None
            and beginnings
        ):
            self._start_door_episode(channels, now, min(beginnings))

        episode = self.door_episode
        complete_episode = bool(
            episode and all(position in channels for position in episode["positions"])
        )
        if episode:
            for position in episode["positions"]:
                frame = self._episode_frame(position, channels)
                if frame is None:
                    continue
                if not self.open and frame["Tm"] is not None:
                    reference = episode["temperatures"][position]
                    episode["temperatures"][position] = (
                        frame["Tm"] if reference is None else max(reference, frame["Tm"])
                    )
        mixed_opening = thermal_opening = closing = None
        mixed_toggle = thermal_toggle = close_toggle = False
        closing_positions = ()
        closing_quantities = (Quantity.TEMPERATURE,)
        if episode and self.open:
            closing_positions = tuple(
                position for position in episode["positions"]
                if self._value(position, Quantity.TEMPERATURE, now) is not None
            )
            temperature_recovery = bool(closing_positions) and all(
                (candidate := supported_curve(position, Quantity.TEMPERATURE))
                is not None and self._temperature_recovery(position, candidate, now)
                for position in closing_positions
            )
            # Ein früher Schluss vor der Talsohle benötigt die RH-Abbremsung
            # der belegten Öffnung. Ein späterer freier Feuchtepuls oder eine
            # nach Quellenbruch neu gebildete RH-Reihe kann sie nicht ersetzen.
            same_opening_humidity = bool(episode["opening_humidity"]) and all(
                self._value(position, Quantity.HUMIDITY, now) is not None
                and self._curve_series[position, Quantity.HUMIDITY]["source"]
                == episode["sources"][position][1]
                and self._curve_series[position, Quantity.HUMIDITY]["epoch"]
                == episode["epochs"][position][1]
                for position in closing_positions
            )
            mixed_recovery = (
                closing_positions == episode["positions"]
                and same_opening_humidity and all(
                    supported_curve(position, quantity) is not None
                    for position in episode["positions"]
                    for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY)
                )
            )
            closing = temperature_recovery or mixed_recovery
            close_context = (self._curve_door_context, episode["started_at"], curve_floor)
            self._remember_curve_proposal(
                "door_close", closing_positions, (Quantity.TEMPERATURE,),
                floor=curve_floor, context=close_context, observed_at=decision_at,
            )
            recovery_toggle = temperature_recovery and self._curve_confirmation(
                "door_close", closing_positions, (Quantity.TEMPERATURE,),
                floor=curve_floor, recovery=True,
            )
            if same_opening_humidity:
                self._remember_curve_proposal(
                    "door_close", closing_positions,
                    (Quantity.TEMPERATURE, Quantity.HUMIDITY),
                    floor=curve_floor, context=close_context, observed_at=decision_at,
                )
            mixed_close_toggle = mixed_recovery and self._curve_confirmation(
                "door_close", closing_positions,
                (Quantity.TEMPERATURE, Quantity.HUMIDITY), floor=curve_floor,
            )
            close_toggle = recovery_toggle or mixed_close_toggle
            if mixed_close_toggle and not recovery_toggle:
                closing_quantities = (Quantity.TEMPERATURE, Quantity.HUMIDITY)
        elif episode:
            proposal_floor = self._curve_closed_since
            coherent_temperature = all(
                self._value(position, Quantity.TEMPERATURE, now) is not None
                and self._curve_series[position, Quantity.TEMPERATURE]["source"]
                == episode["sources"][position][0]
                and self._curve_series[position, Quantity.TEMPERATURE]["epoch"]
                == episode["epochs"][position][0]
                for position in episode["positions"]
            )
            coherent_pair = complete_episode and coherent_temperature and all(
                self._curve_series[position, Quantity.HUMIDITY]["source"]
                == episode["sources"][position][1]
                and self._curve_series[position, Quantity.HUMIDITY]["epoch"]
                == episode["epochs"][position][1]
                for position in episode["positions"]
            )
            mixed_opening = coherent_pair and all(
                supported_curve(position, quantity) is not None
                for position in episode["positions"]
                for quantity in (Quantity.TEMPERATURE, Quantity.HUMIDITY)
            )
            thermal_opening = bool(
                enabled and self.heating_since is not None and coherent_temperature
                and all(
                    (candidate := supported_curve(
                        position, Quantity.TEMPERATURE, "door_open_heating",
                    ))
                    is not None and candidate.onset_at >= self.heating_since
                    and candidate.original_points[0][0] >= self.heating_since
                    for position in episode["positions"]
                )
            )
            if coherent_pair:
                self._remember_curve_proposal(
                    "door_open", episode["positions"],
                    (Quantity.TEMPERATURE, Quantity.HUMIDITY),
                    floor=proposal_floor,
                    context=(self._curve_door_context, episode["started_at"], proposal_floor),
                    observed_at=decision_at,
                )
            mixed_toggle = mixed_opening and self._curve_confirmation(
                "door_open", episode["positions"],
                (Quantity.TEMPERATURE, Quantity.HUMIDITY), floor=proposal_floor,
            )
            if thermal_opening:
                thermal_floor = max(proposal_floor, self.heating_since)
                self._remember_curve_proposal(
                    "door_open_heating", episode["positions"], (Quantity.TEMPERATURE,),
                    floor=thermal_floor,
                    context=(
                        self._curve_door_context, episode["started_at"],
                        thermal_floor, self.heating_since,
                    ),
                    observed_at=decision_at,
                )
                thermal_toggle = self._curve_confirmation(
                    "door_open_heating", episode["positions"], (Quantity.TEMPERATURE,),
                    floor=thermal_floor,
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
            emit(Kind.DOOR_CLOSE, close_effective, closing_positions)
            self.baseline = {}
            self.ventilation_degraded = False
            self.ventilation_positions = ()
            self.ventilation_invalid = set()
            self.door_episode = None
        elif self.open and closing:
            # Der belegte Erholungsknick pausiert den Lüftungsnachweis schon
            # vor seiner Bestätigung durch weitere Originalmessungen.
            trace["conditions"]["ventilation_recovery"] = True
        elif self.open and not self.ventilated:
            if self._ventilation_proof(channels, now, trace):
                self.ventilated = True
                self._curve_ventilation_at = now
                self._clear_curve_route(
                    "door_close", keep_status=True, reason="ventilation_after_onset",
                )
                emit(Kind.VENTILATION)
        elif not self.open and (mixed_toggle or thermal_toggle):
            open_floor = self._curve_closed_since
            if thermal_toggle and not mixed_toggle and self.heating_since is not None:
                open_floor = max(open_floor, self.heating_since)
            open_effective = self._curve_beginning(
                "door_open" if mixed_toggle else "door_open_heating", episode["positions"],
                (Quantity.TEMPERATURE, Quantity.HUMIDITY) if mixed_toggle else
                (Quantity.TEMPERATURE,),
                now, floor=open_floor,
                kind=Kind.DOOR_OPEN, now=now, decision_at=decision_at,
                onset_allowed=onset_allowed,
            )
            self.open = True
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
            self._update_moisture_state(None, now)
            self._curve_infusion_since = None
            self._clear_curve_route("infusion", reason="unavailable")
            self._pause_infusion_episode()
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
        infusion_check = (
            eligible and admitted_window("infusion")
            and (allowed is None or allowed(Kind.INFUSION))
        )
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
                water_rise &= da is not None and da > 0
        trace["conditions"]["infusion"] = False if infusion_check else None
        if infusion_check:
            context = (self._curve_door_context, infusion_floor)
            episode = self._infusion_episode
            if episode is None or episode["phase"] != "pending":
                for position in channels:
                    self._curve_candidate(
                        "infusion", position, Quantity.HUMIDITY,
                        now, decision_at, infusion_floor, context,
                        points_not_before=self._infusion_segment_start.get(position),
                    )
            if episode is not None and episode["phase"] in ("pending", "active"):
                confirmed = self._advance_infusion_episode(
                    channels, now, decision_at, infusion_floor, trace,
                )
                if confirmed:
                    infusion_effective = self._curve_beginning(
                        "infusion", episode["positions"], (Quantity.HUMIDITY,), now,
                        floor=infusion_floor, kind=Kind.INFUSION,
                        now=now, decision_at=decision_at, onset_allowed=onset_allowed,
                    )
                    episode["phase"] = "active"
                    episode["onset_at"] = infusion_effective
                    episode["confirmed_at"] = decision_at
                    episode["decays"] = {
                        position: self._curve_candidates[
                            "infusion_decay", position, Quantity.HUMIDITY
                        ] for position in episode["positions"]
                    }
                    decay_key = (
                        "infusion_decay", episode["positions"], (Quantity.HUMIDITY,),
                    )
                    self._curve_proposal_status[decay_key] = {
                        **self._curve_proposal_status[decay_key],
                        "status": "consumed", "confirmed_at": decision_at,
                    }
                    self._clear_curve_route("infusion", keep_status=True)
                    trace["conditions"]["infusion"] = True
                    emit(Kind.INFUSION, infusion_effective, episode["positions"])
            else:
                self._remember_curve_proposal(
                    "infusion", channels, (Quantity.HUMIDITY,), floor=infusion_floor,
                    context=context, observed_at=decision_at,
                )
                rise = water_rise and self._curve_confirmation(
                    "infusion", channels, (Quantity.HUMIDITY,), floor=infusion_floor,
                )
                trace["conditions"]["infusion_rise"] = rise
                if rise:
                    proposal = self._curve_proposals[
                        "infusion", tuple(channels), (Quantity.HUMIDITY,)
                    ]
                    beginning = min(
                        candidate.onset_at for _, candidate in proposal.candidates
                    )
                    if episode is not None and beginning <= episode["level_end_at"]:
                        # A rise first recognized later can already have begun
                        # inside the supposed final tail. It extends the same
                        # infusion; an onset floor must not turn it into a new one.
                        episode["phase"] = "active"
                        episode["decay_floor"] = beginning
                        episode["last_rise_at"] = beginning
                        episode["rise_ids"] = {
                            candidate.candidate_id for _, candidate in proposal.candidates
                        }
                        episode["decays"].clear()
                        episode["levels"].clear()
                        episode["level_evaluated"].clear()
                        self._clear_curve_route(
                            "infusion_decay", reason="continued_infusion",
                        )
                        self._invalidate_curve_proposals(
                            "continued_infusion", route="infusion",
                        )
                    else:
                        self._bind_pending_infusion(channels, infusion_floor, now)
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
        if (self.index * self.grid_seconds) % p["person_step_seconds"] == 0:
            for route, kind in (
                ("strong", Kind.PERSON_STRONG),
                ("weak", Kind.PERSON_WEAK),
            ):
                checking = (
                    eligible
                    and admitted_window(route)
                    and (route != "weak" or weak_opportunity)
                    and (allowed is None or allowed(kind))
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
                moisture_rise = (self._moisture_rise(route, moisture_features)
                                 if moisture_features else None)
                moisture_state = (
                    self._update_moisture_state(moisture_rise, now)
                    if route == "weak"
                    else None
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
