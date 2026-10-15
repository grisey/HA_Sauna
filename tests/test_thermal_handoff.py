"""Synthetic handoffs from the real warm-up owners; no notices or live hooks."""

import unittest
from dataclasses import FrozenInstanceError, replace

from test_warmup import T0, at

from custom_components.ha_sauna.core.model_handoff import HandoffContext, ModelHandoff
from custom_components.ha_sauna.core.models import Measurement, Position, Quantity
from custom_components.ha_sauna.core.thermal_handoff import (
    HeatingProgressEpisodeEvaluation,
    HeatingProgressEpisodeSnapshot,
    HeatingProgressEvaluation,
    HeatingProgressEvidence,
    WarmupEstimateObservation,
    WarmupEstimateSnapshot,
    WarmupOwnerKey,
    WarmupSourceContext,
    WarmupTrendEvaluation,
    WarmupTrendSnapshot,
)
from custom_components.ha_sauna.core.warmup import (
    HeatingProgress,
    HeatingProgressEpisode,
    WarmupEstimate,
    WarmupTrend,
)


class ThermalHandoffTests(unittest.TestCase):
    def handoff(self, value, before=None):
        context = HandoffContext("entry", "thermal-observation", T0, session_id="session")
        result = ModelHandoff(context, before, value)
        self.assertIs(result.after, value)
        return result

    def test_progress_preserves_entire_actual_evidence_and_detaches_trend(self):
        progress = HeatingProgress(300)
        first = progress.accept(at(0), 40)
        initial = HeatingProgressEvaluation.capture(progress, at(0), 40, first)
        self.assertIsNone(initial.evidence)
        progress.accept(at(75), 40)
        evidence = progress.accept(at(150), 40)
        points_before = progress.trend.observations
        observed = HeatingProgressEvaluation.capture(progress, at(150), 40, evidence)
        self.handoff(observed, initial)
        self.assertEqual(progress.trend.observations, points_before)
        for name, value in evidence.items():
            self.assertEqual(getattr(observed.evidence, name), value)
        with self.assertRaises(ValueError):
            replace(observed, received_at=at(151))
        with self.assertRaises(ValueError):
            replace(observed, evidence=replace(observed.evidence, end_temperature_c=41))
        evidence["no_rise"] = False
        progress.trend.reset()
        self.assertTrue(observed.evidence.no_rise)
        self.assertEqual(observed.trend.observations, points_before)
        with self.assertRaises(FrozenInstanceError):
            observed.evidence.no_rise = False

    def test_episode_keeps_startup_response_and_fresh_analysis_window(self):
        episode = HeatingProgressEpisode(300, at(0))
        episode.startup_delay_seconds = 330
        for second in range(0, 631, 30):
            evidence = episode.accept(at(second), 40)
        observed = HeatingProgressEpisodeEvaluation.capture(episode, at(630), 40, evidence)
        self.handoff(observed)
        self.assertEqual(observed.state.startup_delay_seconds, 330)
        self.assertEqual(observed.state.analysis_started_at, episode._analysis_started_at)
        self.assertEqual(observed.state.response_at, episode.response_at)
        self.assertTrue(observed.evidence.no_rise)
        snapshot = HeatingProgressEpisodeSnapshot.capture(episode)
        self.assertEqual(snapshot, observed.state)
        episode.progress.trend.reset()
        self.assertTrue(snapshot.trend.observations)

    def test_trend_readout_keeps_actual_rate_and_age_query_without_refitting(self):
        trend = WarmupTrend(300)
        for second, value in ((0, 20), (70, 27), (210, 41)):
            trend.accept(at(second), value)
        rate = trend.rate(at(210), maximum_age_seconds=180)
        before = dict(vars(trend))
        state = WarmupTrendSnapshot.capture(trend)
        observed = WarmupTrendEvaluation(state, at(210), 180, rate)
        self.handoff(observed)
        self.assertEqual(vars(trend), before)
        self.assertEqual(observed.rate, rate)
        stale = WarmupTrendEvaluation(
            state, at(391), 180, trend.rate(at(391), maximum_age_seconds=180)
        )
        self.handoff(stale, observed)
        self.assertIsNone(stale.rate)
        self.assertEqual(state.accepted_at, at(210))

    def test_eta_observation_retains_full_cache_source_and_real_optional_readout(self):
        estimate = WarmupEstimate(300)
        for second, value in ((0, 20), (75, 27.5), (150, 35)):
            estimate.accept(at(second), value, target_c=80, historical_rate=0.1)
        measurement = Measurement(
            Position.UPPER, Quantity.TEMPERATURE, 35, "35", "sensor.synthetic", at(150)
        )
        source = WarmupSourceContext(
            WarmupOwnerKey.from_key(
                ("session", "synthetic-feedback", Position.UPPER, measurement.source)
            ),
            measurement, 180, "historical-session", 0.1,
        )
        result = estimate.remaining_seconds(at(150), maximum_age_seconds=180)
        before = dict(vars(estimate))
        state = WarmupEstimateSnapshot.capture(estimate)
        observed = WarmupEstimateObservation(state, source, at(150), result)
        self.handoff(observed)
        self.assertEqual(vars(estimate), before)
        self.assertIsNotNone(observed.remaining_seconds)
        self.assertIs(observed.source.measurement, measurement)
        self.assertEqual(observed.source.historical_session_id, "historical-session")
        wrong_context = HandoffContext(
            "entry", "wrong-scope", at(150), session_id="other-session"
        )
        with self.assertRaises(ValueError):
            ModelHandoff(wrong_context, None, observed)
        self.assertEqual(state.stable_fit_seconds, estimate._stable_fit_seconds)
        self.assertEqual(state.reference_live_rate, estimate._reference_live_rate)
        stale_result = estimate.remaining_seconds(at(331), maximum_age_seconds=180)
        stale = WarmupEstimateObservation(state, source, at(331), stale_result)
        self.handoff(stale, observed)
        self.assertIsNone(stale.remaining_seconds)
        estimate.reset()
        self.assertEqual(state.accepted_at, at(150))
        self.assertTrue(state.trend.observations)

    def test_warmup_source_key_rejects_conflicting_measurement_evidence(self):
        original_key = ("session", "feedback", Position.UPPER, "sensor.synthetic")
        key = WarmupOwnerKey.from_key(original_key)
        self.assertEqual(
            (key.session_id, key.heating_source, key.position, key.measurement_source),
            original_key,
        )
        measurement = Measurement(
            Position.UPPER, Quantity.TEMPERATURE, 40, "40", "sensor.synthetic", T0
        )
        source = WarmupSourceContext(key, measurement, 180, "different-history", 0.1)
        self.handoff(source)
        for change in (
            {"position": Position.LOWER}, {"source": "sensor.other"},
            {"quantity": Quantity.HUMIDITY},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(source, measurement=replace(measurement, **change))
        with self.assertRaises(TypeError):
            WarmupOwnerKey.from_key(original_key[:3])
        with self.assertRaises(TypeError):
            WarmupOwnerKey.from_key(list(original_key))

    def test_unknown_startup_and_invalid_eta_remain_explicit(self):
        episode = HeatingProgressEpisode(300, at(0))
        evidence = episode.accept(at(0), 40)
        observed = HeatingProgressEpisodeEvaluation.capture(episode, at(0), 40, evidence)
        self.handoff(observed)
        self.assertIsNone(observed.evidence)
        self.assertIsNone(observed.state.startup_delay_seconds)
        estimate = WarmupEstimate(300)
        estimate.accept(at(0), 40, target_c=None, historical_rate=None)
        state = WarmupEstimateSnapshot.capture(estimate)
        self.assertTrue(state.source_invalid)
        value = WarmupEstimateObservation(
            state, WarmupSourceContext(None, None, None, None, None), at(0),
            estimate.remaining_seconds(at(0)),
        )
        self.handoff(value)
        self.assertIsNone(value.remaining_seconds)

    def test_thermal_forms_reject_incomplete_or_wrong_typed_values(self):
        with self.assertRaises(ValueError):
            HeatingProgressEvidence.from_result({"no_rise": True})
        with self.assertRaises(TypeError):
            HeatingProgressEvaluation.capture(object(), at(0), 40, None)
        with self.assertRaises(TypeError):
            WarmupTrendSnapshot(300, [(at(0), 40)], None, at(0))
        with self.assertRaises(TypeError):
            HeatingProgressEvidence(at(0), at(150), 40, 40, "true", False)
        with self.assertRaises(TypeError):
            WarmupSourceContext(("session", "source", "upper", "sensor"), None, 180, None, None)
        state = WarmupEstimateSnapshot.capture(WarmupEstimate(300))
        with self.assertRaises(TypeError):
            replace(state, source_invalid="true")
        with self.assertRaises(TypeError):
            WarmupEstimateObservation(state, None, at(0), None)
