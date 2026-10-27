"""Zustandsmodell und Erkennung: gemeinsame Kanalschätzung, Kausalität und Ereignisfolge."""

from datetime import UTC, datetime, timedelta
import gzip
import json
import math
from pathlib import Path
import unittest

import numpy as np

from custom_components.ha_sauna.core.models import Measurement, Position, Quantity
from custom_components.ha_sauna.core.moisture import absolute_humidity
from custom_components.ha_sauna.core.recognition import (
    ClimateRecognizer, InfusionUpdate, _OpenDoor, flattening_probability, recognition_event_id,
)
from custom_components.ha_sauna.core.recognition_model import (
    CLOSED, DECAY, INFUSION, NM, OPEN, RECOVERY, SHIFT, ModelParameters, StateModel,
)
from custom_components.ha_sauna.core.timeline import Kind

T0 = datetime(2026, 1, 10, 18, tzinfo=UTC)


def parameters(*, coupled=True) -> dict:
    """Synthetic but physically ordered quantities (no learned file)."""
    c = 4
    prior = np.zeros((NM, c, 2))
    prior[OPEN] = [[-6.5, 0.4], [-6.5, 0.4], [-4.2, 0.35], [-4.6, 0.4]]
    prior[INFUSION, 2:] = [[1.1, 0.45], [0.55, 0.35]]
    prior[DECAY, 2:] = [[0.9, 0.3], [0.2, 0.4]]
    prior[RECOVERY] = [[0.007, 0.004], [0.01, 0.005], [0.003, 0.018], [-0.003, 0.016]]
    zcorr = np.repeat(np.eye(c)[None], NM, 0)
    if coupled:
        zcorr[OPEN] = [[1, .97, .45, .5], [.97, 1, .47, .6], [.45, .47, 1, .85], [.5, .6, .85, 1]]
        zcorr[INFUSION, 2, 3] = zcorr[INFUSION, 3, 2] = 0.89
        zcorr[DECAY, 2, 3] = zcorr[DECAY, 3, 2] = 0.7
    ql = np.zeros((NM, c))
    ql[CLOSED] = [0.0, 1.4e-5, 8e-5, 3e-4]
    ql[OPEN] = [0.0, 0.0, 1.8e-4, 8e-5]
    ql[INFUSION, 2:] = [0.84, 0.87]
    ql[DECAY, 2:] = [0.33, 0.75]
    ql[RECOVERY] = [0.0, 3.8e-5, 4e-5, 2.2e-4]
    qe = np.zeros((NM, c))
    qe[INFUSION, 2:] = 0.12
    qe[DECAY, 2:] = 0.09
    qe[RECOVERY] = [1.7e-7, 2.9e-7, 2e-8, 3.3e-8]
    rates = np.zeros((NM, NM))
    rates[CLOSED, [OPEN, INFUSION, SHIFT]] = [5.4e-4, 5.2e-4, 6.3e-3]
    rates[OPEN, RECOVERY] = 3.7e-3
    rates[INFUSION, DECAY] = 0.12
    rates[DECAY, [CLOSED, OPEN, INFUSION]] = [5.2e-2, 7.8e-4, 1.6e-3]
    rates[RECOVERY, [CLOSED, OPEN, INFUSION]] = [1.6e-3, 6.6e-4, 3.5e-4]
    rates[SHIFT, CLOSED] = 0.5
    return {
        "channels": ["Tu", "Tl", "Au", "Al"],
        "decision": {"q": 0.9, "lag_door": 30, "lag_infusion": 10, "flat_min_samples": 20,
                     "flat_rise_fraction": 0.05},
        "sv": [0.015, 0.0106, 0.0296, 0.0214], "ql": ql.tolist(), "prior": prior.tolist(),
        "zcorr": zcorr.tolist(), "beta": [0.0034, 0.0039, 0.0091, 0.003], "qe": qe.tolist(),
        "rates": rates.tolist(), "jump": [0.0039, 0.0047, 0.0022, 0.002],
        "nu": [[30.0] * c for _ in range(NM)], "nus": [[1.0] * c for _ in range(NM)],
        "qd": [1e-6] * c, "qlam": 1e-3,
    }


def cabin(n=900, dt=2.0, seed=0, door=None, infusion=None, lower_only=None):
    """Synthetic cabin course: heating drift, optional opening, infusion or lower-only humidity bump."""
    rng = np.random.default_rng(seed)
    t = np.arange(n) * dt
    tu = 80 + 0.004 * t
    tl = 72 + 0.004 * t
    au = np.full(n, 30.0)
    al = np.full(n, 28.0)
    if door is not None:
        a, b = door
        for k in range(1, n):
            step = dt if a <= t[k] < b else 0.0
            tu[k:] -= step * 0.0015 * (tu[k - 1] - 22)
            tl[k:] -= step * 0.0015 * (tl[k - 1] - 22)
            au[k:] -= step * 0.015 * (au[k - 1] - 8)
            al[k:] -= step * 0.01 * (al[k - 1] - 8)
    if infusion is not None:
        rise = np.clip((t - infusion) / 8.0, 0, 1)
        fall = np.exp(-np.clip(t - infusion - 8.0, 0, None) / 40.0)
        au += 25 * rise * fall
        al += 17 * rise * fall
    if lower_only is not None:
        rise = np.clip((t - lower_only) / 6.0, 0, 1)
        fall = np.exp(-np.clip(t - lower_only - 6.0, 0, None) / 10.0)
        al += 4 * rise * fall
    noise = rng.normal(0, 1, (n, 4)) * [0.015, 0.011, 0.06, 0.04]
    return t, np.column_stack([tu, tl, au, al]) + noise


ROOM = {"Tu": 22.0, "Tl": 22.0, "Au": 8.0, "Al": 8.0}


def model_run(values, times, params):
    model = StateModel(ModelParameters.from_dict(params), values[0], list(ROOM.values()))
    mus = []
    for k in range(1, len(times)):
        mus.append(model.step(times[k] - times[k - 1], values[k]).copy())
    return model, np.array(mus)


class StateModelTests(unittest.TestCase):
    def test_quiet_cabin_stays_closed_with_valid_covariances(self):
        t, y = cabin()
        model, mus = model_run(y, t, parameters())
        self.assertGreater(mus[-200:, CLOSED].min() + mus[-200:, SHIFT].min(), 0.9)
        for j in range(NM):
            cov = model.P[j]
            np.testing.assert_allclose(cov, cov.T, atol=1e-12)
            self.assertGreaterEqual(np.linalg.eigvalsh(cov).min(), -1e-9)

    def test_infusion_and_opening_are_recognised(self):
        t, y = cabin(infusion=600.0)
        _, mus = model_run(y, t, parameters())
        self.assertGreater(mus[(t[1:] > 600) & (t[1:] < 612), INFUSION].max(), 0.9)
        t, y = cabin(door=(800.0, 980.0))
        _, mus = model_run(y, t, parameters())
        self.assertGreater(mus[(t[1:] > 820) & (t[1:] < 980), OPEN].mean(), 0.9)

    def test_one_steam_source_couples_both_sensors(self):
        # a humidity bump at the lower sensor alone is not the shared rise of an infusion
        t, y = cabin(lower_only=600.0)
        _, coupled = model_run(y, t, parameters(coupled=True))
        _, independent = model_run(y, t, parameters(coupled=False))
        window = (t[1:] > 596) & (t[1:] < 630)
        self.assertLess(coupled[window, INFUSION].max(), independent[window, INFUSION].max())

    def test_transient_moisture_source_stays_part_of_the_opening(self):
        # a short humidity bump while the door is open (a person in the doorway) is no closing and no infusion
        t, y = cabin(n=700, door=(700.0, 1100.0), seed=5)
        bump = np.clip((t - 860) / 20.0, 0, 1) * np.exp(-np.clip(t - 880, 0, None) / 6.0)
        y[:, 2] += 3.0 * bump
        y[:, 3] += 5.0 * bump
        window = (t[1:] > 850) & (t[1:] < 920)
        params = parameters()
        params["source"] = [[0.0, 0.0], [0.0, 0.0], [0.3, 0.5], [0.3, 0.55]]
        _, mus = model_run(y, t, dict(params, source_q=0.0))       # constant source
        self.assertLess(mus[window, OPEN].min(), 0.5)
        _, mus = model_run(y, t, dict(params, source_q=1e-3))
        self.assertGreater(mus[window, OPEN].min(), 0.9)

    def test_moisture_source_quantities_are_read_per_channel(self):
        params = parameters()
        params["source"] = [[0.0, 0.0], [0.0, 0.0], [0.3, 0.5], [0.2, 0.4]]
        params["source_corr"] = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0.9], [0, 0, 0.9, 1]]
        params["source_q"] = 1e-4
        p = ModelParameters.from_dict(params, ("Tu", "Au", "Al"))
        np.testing.assert_allclose(p.source, [[0.0, 0.0], [0.3, 0.5], [0.2, 0.4]])
        self.assertAlmostEqual(p.source_corr[1, 2], 0.9)
        self.assertEqual(p.source_q, 1e-4)
        self.assertEqual(ModelParameters.from_dict(parameters()).source_q, 0.0)
        model = StateModel(p, [80.0, 30.0, 28.0], [22.0, 8.0, 8.0])
        np.testing.assert_allclose(model.source_cov, [[0.25, 0.9 * 0.5 * 0.4], [0.9 * 0.5 * 0.4, 0.16]])

    def test_channel_subset_uses_the_matching_quantities(self):
        params = ModelParameters.from_dict(parameters(), ("Tu", "Au"))
        self.assertEqual(params.channels, ("Tu", "Au"))
        self.assertEqual(params.zcorr.shape, (NM, 2, 2))
        self.assertAlmostEqual(params.zcorr[OPEN, 0, 1], 0.45)


def measurements(times, values, origin=T0):
    out = []
    for k, (t, row) in enumerate(zip(times, values)):
        at = origin + timedelta(seconds=float(t))
        tu, tl, au, al = row
        for position, temperature, ah in ((Position.UPPER, tu, au), (Position.LOWER, tl, al)):
            from custom_components.ha_sauna.core.moisture import saturation_vapor_pressure  # noqa: PLC0415

            sat = saturation_vapor_pressure(temperature) * 1000 / (461.52 * (temperature + 273.15))
            rh = min(max(100 * ah / sat, 0.0), 100.0)
            out.append(Measurement(position, Quantity.TEMPERATURE, float(temperature), str(temperature),
                                   f"sensor.{position.value}_t", at))
            out.append(Measurement(position, Quantity.HUMIDITY, float(rh), str(rh), f"sensor.{position.value}_h", at))
    return out


class RecognizerTests(unittest.TestCase):
    def run_recognizer(self, items, chunks, params=None):
        # the synthetic cabin is already warm: the surrounding air comes from an earlier session
        recognizer = ClimateRecognizer(params or parameters(), T0, outside_reference=ROOM)
        size = max(1, len(items) // chunks)
        for start in range(0, len(items), size):
            part = items[start:start + size]
            for m in part:
                recognizer.accept(m)
            # values of the newest time may still be incomplete (other channels of the same reading)
            recognizer.advance(part[-1].received_at - timedelta.resolution)
        recognizer.advance(items[-1].received_at)
        return recognizer

    def test_one_reading_is_evaluated_with_all_its_channels(self):
        t, y = cabin(n=40)
        items = measurements(t, y)
        recognizer = ClimateRecognizer(parameters(), T0, outside_reference=ROOM)
        for m in items:
            recognizer.accept(m)
        recognizer.advance(items[-1].received_at)
        self.assertEqual(len(recognizer.record.times), len(t))
        np.testing.assert_allclose(recognizer.record.values, y, rtol=1e-6)

    def test_incremental_delivery_equals_one_delivery(self):
        t, y = cabin(n=1100, door=(700.0, 900.0), infusion=1400.0, seed=4)
        items = measurements(t, y)
        whole = self.run_recognizer(items, 1)
        pieces = self.run_recognizer(items, 97)
        self.assertEqual(whole.detections, pieces.detections)
        self.assertEqual(whole.record.door_prob, pieces.record.door_prob)
        kinds = [d.kind for d in whole.detections]
        self.assertIn(Kind.DOOR_OPEN, kinds)
        self.assertIn(Kind.DOOR_CLOSE, kinds)
        self.assertIn(Kind.INFUSION, kinds)
        for d in whole.detections:
            self.assertLessEqual(d.effective_at, d.detected_at)

    def test_values_from_before_the_start_count_from_the_start(self):
        recognizer = ClimateRecognizer(parameters(), T0)
        recognizer.accept(Measurement(Position.UPPER, Quantity.TEMPERATURE, 22.0, "22", "s", T0 - timedelta(hours=1)))
        self.assertEqual(recognizer._pending[0][0], T0)

    def test_warm_start_uses_the_earlier_surrounding_air(self):
        t, y = cabin(n=60)
        reference = {"Tu": 21.0, "Tl": 21.0, "Au": 8.0, "Al": 8.0}
        recognizer = ClimateRecognizer(parameters(), T0, outside_reference=reference)
        for m in measurements(t, y):
            recognizer.accept(m)
        recognizer.advance(T0 + timedelta(seconds=float(t[-1])))
        self.assertTrue(recognizer.outside_from_reference)
        np.testing.assert_allclose(recognizer.outside, [21.0, 21.0, 8.0, 8.0])

    def test_episodes_are_confirmed_by_evidence_ratio_and_timed_at_their_first_step(self):
        def row(closed=0.0, opened=0.0, infusion=0.0, decay=0.0, recovery=0.0):
            r = np.zeros(NM)
            r[CLOSED], r[OPEN], r[INFUSION], r[DECAY], r[RECOVERY] = closed, opened, infusion, decay, recovery
            return r / r.sum()

        def script(k):                                  # smoothed probabilities of steps 0..k seen at step k
            rows = [row(closed=1)] * 3
            rows += [row(closed=0.4, infusion=0.6) if k < 7 else row(closed=0.001, infusion=0.999)]
            rows += [row(closed=0.3, opened=0.7)] * 3       # door candidate 4..6, confirmed from step 7 on
            rows += [row(closed=0.001, opened=0.999)] * 3
            rows += [row(recovery=0.6, opened=0.4)] * 2     # closing candidate at 10: the recovery sets in
            rows += [row(recovery=0.999, opened=0.001)] * 3
            return np.array(rows[:k + 1])

        recognizer = ClimateRecognizer(parameters(), T0)
        recognizer._grid_t = [float(i) for i in range(15)]
        events = []
        for k in range(15):
            recognizer._steps = k + 1
            recognizer._post, recognizer._post_base = script(k), 0
            at = T0 + timedelta(seconds=100 + k)
            events.extend(recognizer._decide_door(at))
            events.extend(recognizer._decide_infusion(at))
        found = [(e.kind, (e.effective_at - T0).total_seconds()) for e in events if not isinstance(e, InfusionUpdate)]
        # the infusion confirmed while the door was undecided follows the confirmed closing
        self.assertEqual(found, [(Kind.DOOR_OPEN, 4.0), (Kind.DOOR_CLOSE, 10.0), (Kind.INFUSION, 10.0)])

    def test_a_candidate_without_the_evidence_ratio_is_no_event(self):
        recognizer = ClimateRecognizer(parameters(), T0)
        rows = [np.eye(NM)[CLOSED]] * 3 + [np.array([0.2, 0.8, 0, 0, 0, 0])] * 4 + [np.eye(NM)[CLOSED] * 0.999
                                                                                   + np.eye(NM)[OPEN] * 0.001] * 4
        recognizer._grid_t = [float(i) for i in range(len(rows))]
        events = []
        for k in range(len(rows)):
            recognizer._steps = k + 1
            recognizer._post, recognizer._post_base = np.array(rows[:k + 1]), 0
            events.extend(recognizer._decide_door(T0 + timedelta(seconds=50 + k)))
        self.assertEqual(events, [])
        self.assertFalse(recognizer._door_pending())

    def test_pours_are_one_infusion_until_the_decay_evidently_flattens(self):
        class Decay:                                   # filtered humidity slope in the decay mode
            slope, var = -0.05, 1e-6

            def humidity_decay_slope(self):
                return self.slope, self.var

        def run(flat_slope):
            eye = np.eye(NM)
            recognizer = ClimateRecognizer(parameters(), T0)
            recognizer._model = Decay()
            plan = ([CLOSED] * 2 + [INFUSION] * 2 + [DECAY] * 4 + [INFUSION] * 2 + [DECAY] * 2)
            slopes = {4: -0.05, 5: -0.06, 6: flat_slope, 7: flat_slope}
            recognizer._grid_t = [float(i) for i in range(len(plan))]
            events = []
            for k, state in enumerate(plan):
                recognizer._steps = k + 1
                recognizer._model.slope = slopes.get(k, -0.05)
                recognizer._watch_decay(eye[state], T0 + timedelta(seconds=100 + k))
                rows = [eye[s] * 0.999 + eye[CLOSED] * 0.001 for s in plan[:k + 1]]
                recognizer._post, recognizer._post_base = np.array(rows), 0
                events.extend(recognizer._decide_infusion(T0 + timedelta(seconds=100 + k)))
            return [(e.effective_at - T0).total_seconds() for e in events if not isinstance(e, InfusionUpdate)
                    and e.kind == Kind.INFUSION], recognizer.decisions

        separate, log = run(-0.01)                     # the slope is evidently less steep: a new infusion
        self.assertEqual(separate, [2.0, 8.0])
        self.assertIn("infusion_decay_flattened", [d["event"] for d in log])
        joined, log = run(-0.0599)                     # no evident flattening between the pours: one infusion
        self.assertEqual(joined, [2.0])
        self.assertIn("infusion_pour", [d["event"] for d in log])

    def test_event_identity_is_stable(self):
        at = T0 + timedelta(seconds=12.5)
        self.assertEqual(recognition_event_id("s1", Kind.INFUSION, at),
                         recognition_event_id("s1", "infusion", at.astimezone()))


if __name__ == "__main__":
    unittest.main()
