"""Kausaler laufender Tür-/Gangdetektor ohne HA, NumPy oder Recorderwissen."""

from collections import deque
from dataclasses import dataclass, replace
from datetime import timedelta
from hashlib import sha256
from math import ceil, isclose, isfinite
from statistics import median

from .curve_change import (
    CurveFeatures,
    CurveFormFeatures,
    CurvePolicy,
    curve_change,
    curve_form,
    curve_level,
    curve_recovery,
    curve_trend,
)
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
        series["last_original_received_at"] = None
        series["epoch"] += 1
        self._curve_recovery_evidence.pop((position, quantity), None)
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
            episode["interrupted"].update(episode["positions"])
            episode["context"] = None
            episode["decays"].clear()
            episode["levels"].clear()
            episode["level_status"].clear()
            episode["backgrounds"].clear()
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
            self._correct_smooth_close_evidence(position, quantity, point_at)
            self._correct_curve_proposals(position, quantity, point_at, measurement.value)
            self._correct_infusion_tail(position, quantity, point_at, measurement.value)
        else:
            series["last_original_version"] = series["version"]
            series["last_original_received_at"] = measurement.received_at

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
        return tuple(
            point for point in self._curve_series[position, quantity]["points"]
            if window_start <= point[0] <= now
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
                self._invalidate_curve_proposal(key, "candidate_expired")
                if key == pending_key:
                    self._finish_infusion_context("candidate_expired")

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
                    self._curve_proposal_status[key] = {
                        "status": "incomplete",
                        **self._curve_proposal_status.get(key, {}),
                        "positions": tuple(position.value for position in positions),
                        "quantities": tuple(quantity.value for quantity in quantities),
                        "assembly_gates": assembly, "assembly_result": False,
                    }
                    return
                candidates.append((candidate_key, candidate))
        onsets = [candidate.onset_at for _, candidate in candidates]
        window = self._curve_coherence_window(route)
        spread = (max(onsets) - min(onsets)).total_seconds()
        assembly["onsets_coherent"] = _gate(
            spread <= window, value=spread, operator="<=", threshold=window,
            unit="s", reference="required_candidate_onset_spread",
        )
        if spread > window:
            self._curve_proposal_status[key] = {
                "status": "incomplete",
                **self._curve_proposal_status.get(key, {}),
                "positions": tuple(position.value for position in positions),
                "quantities": tuple(quantity.value for quantity in quantities),
                "assembly_gates": assembly, "assembly_result": False,
            }
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
        self._curve_proposal_status[key] = {
            **self._curve_proposal_status[key],
            "assembly_gates": assembly, "assembly_result": True,
        }

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
            self._curve_proposal_status[key] = {
                "status": (
                    "incomplete" if proposal is None else
                    "ambiguous" if proposal.ambiguous else
                    "bound" if proposal.evidence_bound_at is not None else "available"
                ),
                **self._curve_proposal_status.get(key, {}),
                "positions": tuple(position.value for position in positions),
                "quantities": tuple(quantity.value for quantity in quantities),
                "confirmation_result": result, "confirmation_gates": gates,
                "confirmation_reason": reason,
            }
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
        episode = self._infusion_episode
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
            "decay_floor": {position: beginning for position in positions},
            "last_rise_at": {
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
            episode["phase"] = "active"
            episode["level_end_at"] = None
            episode["end_basis"] = None
            episode["levels"].clear()
            episode["level_status"].clear()
            episode["backgrounds"].clear()
            episode["level_evaluated"].clear()
            self._clear_curve_route("infusion", reason="level_original_corrected")

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
    ):
        """Den passenden Kandidaten am allein führenden Bestätigungspfad binden."""
        proposal_key = (route, tuple(positions), tuple(quantities))
        proposal = self._curve_proposals.get(proposal_key)
        if (
            proposal is None or proposal.ambiguous
            or require_bound and proposal.evidence_bound_at is None
        ):
            return fallback
        candidates = proposal.candidates
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
        unavailable = {}
        for position in self.ventilation_positions:
            if position not in channels:
                # Eine kurze Lücke pausiert nur diesen Vergleich; die beim
                # ersten Hinweis gemerkte Referenz bleibt für die Rückkehr.
                evidence[position] = None
                unavailable[position] = "episode_position_unavailable"
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
                unavailable[position] = (
                    "opening_baseline_unavailable" if baseline is None else
                    "opening_source_invalidated" if position in self.ventilation_invalid else
                    "absolute_humidity_unavailable" if current_water is None else
                    "opening_source_changed"
                )
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
        gates = {}
        for position in self.ventilation_positions:
            current = evidence.get(position)
            drop = current[0] if current is not None else None
            loss = current[1] if current is not None else None
            gates[f"{position.value}:temperature_loss"] = _gate(
                drop >= self.p[f"vent_drop_{position.value}"] if drop is not None else None,
                value=drop, operator=">=", threshold=self.p[f"vent_drop_{position.value}"],
                unit="°C", reference="opening_baseline_temperature_minus_current",
                reason=unavailable.get(position),
            )
            gates[f"{position.value}:water_loss"] = _gate(
                loss >= self.p["vent_absolute_humidity_loss_percent"] / 100
                if loss is not None else None,
                value=loss, operator=">=",
                threshold=self.p["vent_absolute_humidity_loss_percent"] / 100,
                unit="fraction", reference="opening_baseline_relative_absolute_humidity_loss",
                reason=unavailable.get(position) or (
                    "nonpositive_baseline_absolute_humidity" if loss is None else None
                ),
            )
        temperature_drop = all(
            gates[f"{position.value}:temperature_loss"]["result"]
            for position in self.ventilation_positions
        )
        water_loss = all(
            gates[f"{position.value}:water_loss"]["result"]
            for position in self.ventilation_positions
        )
        duration = (now - self.opened_at).total_seconds()
        time_held = duration >= self.p[
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
        gates.update(
            sources_coherent=_gate(not self.ventilation_degraded),
            required_position_count=_gate(
                dual_sensor or single_sensor, value=len(self.ventilation_positions),
                operator="in", threshold=(1, 2), reference="opening_episode_positions",
            ),
            single_sensor_hold=_gate(
                time_held if not dual_sensor else None,
                value=duration, operator=">=", threshold=self.p["vent_hold_seconds"],
                unit="s", reference="time_since_confirmed_opening",
                reason="not_required_with_two_coherent_positions" if dual_sensor else None,
            ),
        )
        _decision(trace, "ventilation", proven, gates)
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
        self._expire_held_curve_proposals(now)
        trace = {
            "at": now,
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
                    "ventilation", "infusion", "infusion_rise", "infusion_decay",
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
        smooth_close_only = False
        closing_positions = ()
        closing_quantities = (Quantity.TEMPERATURE,)
        if episode and self.open:
            closing_positions = tuple(
                position for position in episode["positions"]
                if self._value(position, Quantity.TEMPERATURE, now) is not None
            )
            close_context = (self._curve_door_context, episode["started_at"], curve_floor)
            self._remember_curve_proposal(
                "door_close", closing_positions, (Quantity.TEMPERATURE,),
                floor=curve_floor, context=close_context, observed_at=decision_at,
            )
            self._bind_close_evidence(closing_positions, floor=curve_floor, now=now)
            close_proposal = self._curve_proposals.get((
                "door_close", closing_positions, (Quantity.TEMPERATURE,),
            ))
            bound_temperatures = (
                {candidate_key[1]: candidate
                 for candidate_key, candidate in close_proposal.candidates}
                if close_proposal is not None and not close_proposal.ambiguous
                and close_proposal.evidence_bound_at is not None else {}
            )
            temperature_recovery = bool(closing_positions) and all(
                (candidate := bound_temperatures.get(position) or supported_curve(
                    position, Quantity.TEMPERATURE,
                ))
                is not None and self._temperature_recovery(position, candidate, now)
                for position in closing_positions
            )
            # Ein früher Schluss vor der Talsohle benötigt die RH-Erholung
            # der belegten Öffnung. Eine nach Quellenbruch neu gebildete
            # RH-Reihe kann sie nicht ersetzen.
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
            humidity_close_toggle = (
                not (recovery_toggle or mixed_close_toggle)
                and closing_positions == episode["positions"]
                and same_opening_humidity
                and self._curve_confirmation(
                    "door_close", closing_positions, (Quantity.TEMPERATURE,),
                    floor=curve_floor, recovery=True,
                    recovery_quantity=Quantity.HUMIDITY,
                )
            )
            smooth_recovery, smooth_close_toggle = self._smooth_close_confirmation(
                closing_positions, episode, floor=curve_floor, now=now,
            )
            closing = (
                temperature_recovery or mixed_recovery or humidity_close_toggle
                or smooth_recovery
            )
            close_toggle = recovery_toggle or mixed_close_toggle or humidity_close_toggle
            smooth_close_only = smooth_close_toggle and not close_toggle
            close_toggle |= smooth_close_toggle
            if mixed_close_toggle and not recovery_toggle:
                closing_quantities = (Quantity.TEMPERATURE, Quantity.HUMIDITY)
            _decision(trace, "door_close_temperature", recovery_toggle, {
                "temperature_positions_available": _gate(bool(closing_positions)),
                "current_original_recovery": _gate(temperature_recovery),
                "fixed_anchor_confirmation": _gate(
                    recovery_toggle if temperature_recovery else None,
                    reason="temperature_recovery_not_supported" if not temperature_recovery else None,
                ),
            })
            _decision(trace, "door_close_mixed", mixed_close_toggle, {
                "all_opening_positions": _gate(closing_positions == episode["positions"]),
                "opening_humidity_sources": _gate(same_opening_humidity),
                "temperature_and_humidity_shapes": _gate(mixed_recovery),
                "fixed_original_confirmation": _gate(
                    mixed_close_toggle if mixed_recovery else None,
                    reason="mixed_recovery_not_supported" if not mixed_recovery else None,
                ),
            })
            humidity_evaluated = (
                not (recovery_toggle or mixed_close_toggle)
                and closing_positions == episode["positions"] and same_opening_humidity
            )
            _decision(trace, "door_close_humidity", humidity_close_toggle, {
                "preceding_close_paths_unconfirmed": _gate(not (recovery_toggle or mixed_close_toggle)),
                "all_opening_positions": _gate(closing_positions == episode["positions"]),
                "opening_humidity_sources": _gate(same_opening_humidity),
                "bound_anchor_and_current_humidity_recovery": _gate(
                    humidity_close_toggle if humidity_evaluated else None,
                    reason="preceding_close_gate_failed_or_prior_path_confirmed"
                    if not humidity_evaluated else None,
                ),
            })
            _decision(trace, "door_close_smooth", smooth_close_toggle, {
                "fall_valley_recovery": _gate(smooth_recovery),
                "bound_form_and_new_original_recovery": _gate(smooth_close_toggle),
            })
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
            _decision(trace, "door_open", mixed_toggle, {
                "all_episode_positions": _gate(complete_episode),
                "episode_temperature_sources": _gate(coherent_temperature),
                "episode_humidity_sources": _gate(coherent_pair),
                "temperature_and_humidity_shapes": _gate(mixed_opening),
                "fixed_original_confirmation": _gate(
                    mixed_toggle if mixed_opening else None,
                    reason="opening_shapes_not_supported" if not mixed_opening else None,
                ),
            })
            heating_gates = {
                "operation_enabled": _gate(bool(enabled)),
                "heating_context": _gate(self.heating_since is not None),
                "episode_temperature_sources": _gate(coherent_temperature),
                "heating_shape_and_original_support": _gate(thermal_opening),
                "fixed_original_confirmation": _gate(
                    thermal_toggle if thermal_opening else None,
                    reason="heating_opening_not_supported" if not thermal_opening else None,
                ),
            }
            if self.heating_since is not None:
                for position in episode["positions"]:
                    candidate = supported_curve(position, Quantity.TEMPERATURE, "door_open_heating")
                    for name, value in (
                        ("onset", candidate.onset_at if candidate else None),
                        ("first_original", candidate.original_points[0][0] if candidate else None),
                    ):
                        heating_gates[f"{position.value}:{name}_since_heating"] = _gate(
                            value >= self.heating_since if value is not None else None,
                            value=value, operator=">=", threshold=self.heating_since,
                            unit="s", reference="heating_started_at",
                            reason="heating_candidate_missing" if candidate is None else None,
                        )
            _decision(trace, "door_open_heating", thermal_toggle, heating_gates)
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
        _decision(trace, "ventilation", None, {}, evaluated=False, reason=(
            "door_closed" if not self.open else
            "already_proven_for_opening" if self.ventilated else
            "door_close_takes_precedence" if close_toggle or closing else
            "awaiting_opening_loss_comparison"
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
                require_bound=smooth_close_only,
            )
            if smooth_close_only:
                proposal_key = (
                    "door_close", closing_positions, (Quantity.TEMPERATURE,),
                )
                proposal_status = self._curve_proposal_status.get(proposal_key, {})
                anchor_consumed = (
                    proposal_status.get("status") == "consumed"
                    and proposal_status.get("confirmed_at") == decision_at
                    and proposal_status.get("consumed_onset_at") == close_effective
                )
                confirmation = {
                    "confirmation_method": "smooth_recovery",
                    "confirmed_at": decision_at,
                    "effective_at": close_effective,
                    "onset_basis": (
                        "bound_temperature_anchor" if anchor_consumed else "now_fallback"
                    ),
                    "anchor_proposal_id": (
                        proposal_status["proposal_id"] if anchor_consumed else None
                    ),
                    "anchor_confirmation": (
                        proposal_status["anchor_confirmation"] if anchor_consumed else ()
                    ),
                    "confirmation": tuple({
                        "position": position.value,
                        "quantity": Quantity.TEMPERATURE.value,
                        **self._curve_status[
                            "door_close", position, Quantity.TEMPERATURE
                        ]["smooth_recovery"],
                    } for position in closing_positions),
                }
                trace["door_close_confirmation"] = confirmation
                if anchor_consumed:
                    self._curve_proposal_status[proposal_key] = {
                        **proposal_status,
                        "unused_confirmation": proposal_status.get("confirmation", ()),
                        **confirmation,
                    }
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
                    episode["background_start"] = {
                        position: candidate.onset_at
                        for position, candidate in episode["decays"].items()
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
                    trace["decisions"]["infusion"]["result"] = True
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
                _decision(trace, "infusion_rise", rise, {
                    "water_increase": water_gates,
                    "fixed_original_rise_confirmation": _gate(
                        rise if water_rise else None,
                        reason="water_increase_not_supported" if not water_rise else None,
                    ),
                })
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
                        episode["decay_floor"] = {
                            key[1]: candidate.onset_at
                            for key, candidate in proposal.candidates
                        }
                        episode["last_rise_at"] = dict(episode["decay_floor"])
                        episode["interrupted"].difference_update(episode["last_rise_at"])
                        episode["rise_ids"] = {
                            candidate.candidate_id for _, candidate in proposal.candidates
                        }
                        episode["decays"].clear()
                        episode["levels"].clear()
                        episode["level_status"].clear()
                        episode["backgrounds"].clear()
                        episode["level_evaluated"].clear()
                        episode["level_end_at"] = None
                        episode["end_basis"] = None
                        self._clear_curve_route(
                            "infusion_decay", reason="continued_infusion",
                        )
                        self._invalidate_curve_proposals(
                            "continued_infusion", route="infusion",
                        )
                    else:
                        self._bind_pending_infusion(channels, infusion_floor, now, trace)
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
                moisture_gates = {}
                moisture_rise = (self._moisture_rise(
                    route, moisture_features, gates=moisture_gates,
                )
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
                edge = self._edge(route, sustained)
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
                    emit(kind)
        observe()
        return output
